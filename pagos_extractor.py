"""
PAGOS EXTRACTOR — Google Sheets
Todas las mejoras incluidas
"""

import os
import base64
import logging
import threading
from datetime import datetime

import requests
from anthropic import Anthropic
import gspread
from google.oauth2.service_account import Credentials

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────
WHAPI_TOKEN         = os.environ.get("WHAPI_TOKEN", "")
WHAPI_API_URL       = os.environ.get("WHAPI_API_URL", "https://gate.whapi.cloud")
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_SHEET_ID     = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_CLIENT_EMAIL = os.environ.get("GOOGLE_CLIENT_EMAIL", "")
GOOGLE_PRIVATE_KEY  = os.environ.get("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")
GOOGLE_PROJECT_ID   = os.environ.get("GOOGLE_PROJECT_ID", "controlpagos-497014")

ALERTA_NUMERO = "584149202844"
CLAUDE_MODEL  = "claude-haiku-4-5-20251001"

logger = logging.getLogger("pagos_extractor")
_anthropic_client = None
_sheets_client    = None
_ultimo_dia_registrado = None


# ─── ALERTA POR WHATSAPP ──────────────────────────────────────────────
def _enviar_alerta(error, remitente="Desconocido"):
    try:
        hora = datetime.now().strftime("%d/%m/%Y %H:%M")
        mensaje = (
            f"⚠️ *ERROR en Extractor de Pagos*\n"
            f"Tipo: {error}\n"
            f"Mensaje de: {remitente}\n"
            f"Hora: {hora}"
        )
        url = f"{WHAPI_API_URL}/messages/text"
        headers = {"Authorization": f"Bearer {WHAPI_TOKEN}", "Content-Type": "application/json"}
        requests.post(url, json={"to": ALERTA_NUMERO, "body": mensaje}, headers=headers, timeout=10)
        print(f"🔔 Alerta enviada a {ALERTA_NUMERO}")
    except Exception as e:
        print(f"Error enviando alerta: {e}")


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
        valores = hoja.row_values(1)
        if not valores:
            hoja.append_row(["#", "Nombre", "Monto", "Remitente", "Fecha", "Estado", "msg_id"])
            print("✅ Encabezados creados en Google Sheets")
        print("✅ Conexión con Google Sheets verificada")
    except Exception as e:
        print(f"⚠️ Error conectando a Google Sheets: {e}")


# ─── SEPARADOR DE DÍA NUEVO ───────────────────────────────────────────
def _insertar_separador_si_es_dia_nuevo():
    global _ultimo_dia_registrado
    hoy = datetime.now().strftime("%d/%m/%Y")
    if _ultimo_dia_registrado == hoy:
        return
    try:
        gc = _get_sheet()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        hoja = sh.sheet1
        todas = hoja.get_all_values()
        # Buscar en TODAS las filas si ya existe el separador de hoy
        ya_existe = any(
            any("DÍA NUEVO" in str(celda) and hoy in str(celda) for celda in fila)
            for fila in todas
        )
        if ya_existe:
            _ultimo_dia_registrado = hoy
            return
        hoja.append_row(["", f"📅 DÍA NUEVO — {hoy}", "", "", "", "", ""])
        _ultimo_dia_registrado = hoy
        print(f"📅 Separador de día insertado: {hoy}")
    except Exception as e:
        print(f"Error insertando separador de día: {e}")


# ─── GUARDAR PAGO ─────────────────────────────────────────────────────
def _guardar_pago(msg_id, fecha_msg, remitente_nombre, nombre, monto, estado):
    try:
        gc = _get_sheet()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        hoja = sh.sheet1
        num = len(hoja.get_all_values())
        hoja.append_row([num, nombre, monto, remitente_nombre, fecha_msg, estado, msg_id])
        print(f"✅ Pago guardado en Sheets: {nombre} → {monto}")
        return True
    except Exception as e:
        print(f"❌ Error guardando en Google Sheets: {e}")
        return False


# ─── BORRAR PAGO ──────────────────────────────────────────────────────
def borrar_pago_por_msg_id(msg_id):
    try:
        gc = _get_sheet()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        hoja = sh.sheet1
        todas = hoja.get_all_values()
        for i, fila in enumerate(todas):
            if len(fila) > 6 and fila[6] == msg_id:
                hoja.delete_rows(i + 1)
                print(f"🗑️ Pago eliminado del Sheet (msg_id: {msg_id})")
                return True
        print(f"⚠️ No se encontró fila con msg_id: {msg_id}")
        return False
    except Exception as e:
        print(f"❌ Error borrando pago: {e}")
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
        file_id = image_data.get("id")
        if file_id:
            try:
                headers = {"Authorization": f"Bearer {WHAPI_TOKEN}"}
                r = requests.get(
                    f"{WHAPI_API_URL}/media/{file_id}",
                    headers=headers, timeout=30
                )
                if r.ok:
                    data = r.json()
                    url = data.get("url") or data.get("link")
            except Exception as e:
                print(f"Error obteniendo URL de media: {e}")
    if not url:
        print(f"image_data keys: {list(image_data.keys())}")
        raise ValueError("No se encontró URL de imagen")
    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    mime_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
    b64_data = base64.standard_b64encode(resp.content).decode("utf-8")
    return b64_data, mime_type


# ─── EXTRACCIÓN CON CLAUDE ────────────────────────────────────────────
def _extraer_nombre_con_claude(image_b64, mime_type):
    client = _get_anthropic()
    prompt = """Eres un extractor de datos. Responde SIEMPRE con UNA SOLA PALABRA O NOMBRE. Nunca expliques, nunca uses "Paso 1", nunca uses "Respuesta:". Solo el resultado.

PRIMERO: ¿Qué tipo de imagen es?
- Si es una pantalla de Excel o tabla con celdas y columnas → responde: NO_ES_PAGO
- Si es consulta de saldo bancario → responde: NO_ES_PAGO
- Si es pantalla de inicio de app bancaria → responde: NO_ES_PAGO
- Si es un comprobante de transferencia ("Operación Exitosa" o "Operación en Proceso") → continúa

SEGUNDO: Busca el campo CONCEPTO del comprobante.
- "Pago a Efrain" → responde: Efrain
- "Pago a Maria Lopez" → responde: Maria Lopez
- "Pago a adelson" → responde: adelson
- "pago" sin nombre → responde: sin_nombre
- Sin campo CONCEPTO → responde: sin_nombre

IMPORTANTE:
- NUNCA respondas "Pago a [nombre]"
- NUNCA respondas con explicaciones
- NUNCA uses "Paso", "Respuesta", "El concepto dice"
- Solo el nombre o sin_nombre o NO_ES_PAGO"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=50,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    texto = response.content[0].text.strip()
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    if lineas:
        texto = lineas[-1]
    for prefijo in ["Respuesta:", "Nombre:", "Resultado:", "El nombre es:", "Pago a "]:
        if texto.lower().startswith(prefijo.lower()):
            texto = texto[len(prefijo):].strip()
    print(f"Resultado Claude (concepto): '{texto}'")
    return texto


def _extraer_identificacion_con_claude(image_b64, mime_type):
    client = _get_anthropic()
    prompt = """Extrae SOLO el valor del campo "IDENTIFICACIÓN RECEPTOR" de este comprobante.
Ejemplo: "V-11691262" → responde: V-11691262
Responde ÚNICAMENTE con el valor. Si no existe responde: No encontrado"""
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=50,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    identificacion = response.content[0].text.strip()
    print(f"Identificación receptor: '{identificacion}'")
    return identificacion


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
    monto = (
        image_data.get("caption") or
        mensaje.get("caption") or
        "Sin monto"
    )
    monto = monto.strip() or "Sin monto"
    print(f"📥 Procesando imagen de {remitente_nombre} | Caption: {monto}")

    try:
        img_b64, mime = _descargar_imagen(image_data)
        nombre = _extraer_nombre_con_claude(img_b64, mime)

        if nombre == "NO_ES_PAGO":
            print("⏭️ Imagen ignorada: no es un comprobante de pago")
            return

        if not nombre or nombre.lower() == "sin_nombre":
            nombre = _extraer_identificacion_con_claude(img_b64, mime)

        _insertar_separador_si_es_dia_nuevo()

        _guardar_pago(
            msg_id=msg_id,
            fecha_msg=fecha_msg,
            remitente_nombre=remitente_nombre,
            nombre=nombre,
            monto=monto,
            estado="OK",
        )

    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error procesando msg {msg_id}: {error_msg}")
        _enviar_alerta(error_msg, remitente_nombre)
        _guardar_pago(
            msg_id=msg_id,
            fecha_msg=fecha_msg,
            remitente_nombre=remitente_nombre,
            nombre="Error",
            monto=monto,
            estado=f"Error: {error_msg[:50]}",
        )
