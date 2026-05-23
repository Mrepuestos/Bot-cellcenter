"""
═══════════════════════════════════════════════════════════════════════
  PAGOS EXTRACTOR — Módulo independiente
  Procesa imágenes de comprobantes de pago de un grupo de WhatsApp
  Extrae Nombre + Monto con Claude AI y los guarda en Google Sheets
═══════════════════════════════════════════════════════════════════════
"""

import os
import base64
import logging
import threading
import json
import re
from datetime import datetime

import requests
from anthropic import Anthropic
import gspread
from google.oauth2.service_account import Credentials

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────
WHAPI_TOKEN       = os.environ.get("WHAPI_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_SHEET_ID   = os.environ.get("GOOGLE_SHEET_ID", "")

# Credenciales de Google (se arman desde variables de entorno)
GOOGLE_CLIENT_EMAIL  = os.environ.get("GOOGLE_CLIENT_EMAIL", "")
GOOGLE_PRIVATE_KEY   = os.environ.get("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")
GOOGLE_PROJECT_ID    = os.environ.get("GOOGLE_PROJECT_ID", "controlpagos-497014")

CLAUDE_MODEL = "claude-haiku-4-5-20251001"

logger = logging.getLogger("pagos_extractor")
logger.setLevel(logging.INFO)

_anthropic_client = None
_sheets_client    = None


# ─── CLIENTES ─────────────────────────────────────────────────────────
def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


def _get_sheet():
    global _sheets_client
    if _sheets_client is None:
        creds_dict = {
            "type": "service_account",
            "project_id": GOOGLE_PROJECT_ID,
            "private_key_id": "",
            "private_key": GOOGLE_PRIVATE_KEY,
            "client_email": GOOGLE_CLIENT_EMAIL,
            "client_id": "",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        _sheets_client = gspread.authorize(creds)
    return _sheets_client


# ─── INICIALIZAR ──────────────────────────────────────────────────────
def inicializar_db():
    try:
        gc = _get_sheet()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        hoja = sh.sheet1

        # Si la hoja está vacía, agrega encabezados
        if hoja.row_count == 0 or not hoja.row_values(1):
            hoja.append_row(["#", "Nombre", "Monto", "Remitente", "Fecha", "Estado"])
            print("✅ Encabezados creados en Google Sheets")

        print("✅ Conexión con Google Sheets verificada")
    except Exception as e:
        print(f"⚠️ Error conectando a Google Sheets: {e}")


# ─── GUARDAR PAGO ─────────────────────────────────────────────────────
def _guardar_pago(fecha_msg, remitente_nombre, nombre, monto, estado):
    try:
        gc = _get_sheet()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        hoja = sh.sheet1

        # Número de fila actual
        num = max(1, len(hoja.get_all_values()))

        hoja.append_row([
            num,
            nombre,
            monto,
            remitente_nombre,
            fecha_msg,
            estado,
        ])
        print(f"✅ Pago guardado en Sheets: {nombre} → {monto}")
        return True
    except Exception as e:
        print(f"❌ Error guardando en Google Sheets: {e}")
        return False


# ─── DESCARGA DE IMAGEN ───────────────────────────────────────────────
def _descargar_imagen(image_data):
    url = (
        image_data.get("link") or
        image_data.get("url") or
        image_data.get("body") or
        image_data.get("mediaUrl") or
        image_data.get("media_url")
    )
    if not url:
        print(f"image_data keys: {list(image_data.keys())}")
        raise ValueError("No se encontró URL de imagen en el mensaje")

    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    mime_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
    b64_data = base64.standard_b64encode(resp.content).decode("utf-8")
    return b64_data, mime_type


# ─── EXTRACCIÓN CON CLAUDE ────────────────────────────────────────────
def _extraer_datos_con_claude(image_b64, mime_type):
    client = _get_anthropic()

    prompt = """Analiza este comprobante de pago/transferencia bancaria y extrae SOLO:
1. NOMBRE del remitente/pagador (quien hizo el pago)
2. MONTO pagado con su moneda (ej: $50.000 COP, $100 USD, Bs 200)

Responde ÚNICAMENTE en este formato JSON exacto, sin texto adicional, sin markdown:
{"nombre": "...", "monto": "..."}

Si no puedes identificar algún dato, usa "No encontrado"."""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )

    text = response.content[0].text.strip()
    text = re.sub(r'```json|```', '', text).strip()
    try:
        data = json.loads(text)
        return {
            "nombre": data.get("nombre", "No encontrado"),
            "monto":  data.get("monto",  "No encontrado"),
        }
    except Exception:
        return {"nombre": "Error de parseo", "monto": "Error de parseo"}


# ─── FUNCIÓN PRINCIPAL ────────────────────────────────────────────────
def procesar_imagen_pago(mensaje, async_mode=True):
    if async_mode:
        thread = threading.Thread(target=_procesar_sync, args=(mensaje,), daemon=True)
        thread.start()
    else:
        _procesar_sync(mensaje)


def _procesar_sync(mensaje):
    msg_id           = mensaje.get("id", "sin_id")
    remitente_nombre = mensaje.get("from_name", "Desconocido")
    ts               = mensaje.get("timestamp")
    fecha_msg        = (
        datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        if ts else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    image_data = mensaje.get("image", {})

    print(f"📥 Procesando pago de {remitente_nombre}")

    try:
        img_b64, mime = _descargar_imagen(image_data)
        datos = _extraer_datos_con_claude(img_b64, mime)
        _guardar_pago(
            fecha_msg=fecha_msg,
            remitente_nombre=remitente_nombre,
            nombre=datos["nombre"],
            monto=datos["monto"],
            estado="OK",
        )
    except Exception as e:
        print(f"❌ Error procesando msg {msg_id}: {e}")
        _guardar_pago(
            fecha_msg=fecha_msg,
            remitente_nombre=remitente_nombre,
            nombre="Error",
            monto="Error",
            estado=f"Error: {str(e)[:50]}",
        )
