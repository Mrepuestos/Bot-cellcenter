"""
═══════════════════════════════════════════════════════════════════════
  PAGOS EXTRACTOR — Módulo independiente
  Procesa imágenes de comprobantes de pago de un grupo de WhatsApp
  Extrae Nombre + Monto con Claude AI y los guarda en SQLite

  USO:
    from pagos_extractor import procesar_imagen_pago, inicializar_db

    # Al iniciar tu app Flask:
    inicializar_db()

    # En tu webhook handler, cuando llegue un mensaje:
    if msg.get('chat_id') == GRUPO_PAGOS_ID and msg.get('type') == 'image':
        procesar_imagen_pago(msg)

  INSTALACIÓN:
    pip install anthropic requests
═══════════════════════════════════════════════════════════════════════
"""

import os
import sqlite3
import base64
import logging
import threading
from datetime import datetime
from pathlib import Path

import requests
from anthropic import Anthropic

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────
# Lee las claves de variables de entorno (recomendado) o ponlas aquí
WHAPI_TOKEN = os.getenv("WHAPI_TOKEN", "TU_TOKEN_DE_WHAPI")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "sk-ant-api03-...")
GRUPO_PAGOS_ID = os.getenv("GRUPO_PAGOS_ID", "120363XXXXXXXX@g.us")

# Archivo de base de datos SQLite (se crea automáticamente)
DB_PATH = Path(__file__).parent / "pagos.db"

# Modelo de Claude para análisis de imágenes
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# Logger
logger = logging.getLogger("pagos_extractor")
logger.setLevel(logging.INFO)

# Cliente de Anthropic (se inicializa una sola vez)
_anthropic_client = None


# ─── BASE DE DATOS ────────────────────────────────────────────────────
def inicializar_db():
    """Crea la tabla de pagos si no existe. Llamar al iniciar la app."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pagos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            mensaje_id      TEXT UNIQUE,
            fecha_mensaje   TEXT,
            fecha_procesado TEXT DEFAULT CURRENT_TIMESTAMP,
            remitente_id    TEXT,
            remitente_nombre TEXT,
            nombre_pagador  TEXT,
            monto           TEXT,
            estado          TEXT,
            error           TEXT
        )
    """)
    conn.commit()
    conn.close()
    logger.info(f"Base de datos de pagos lista en {DB_PATH}")


def _guardar_pago(mensaje_id, fecha_msg, remitente_id, remitente_nombre,
                  nombre, monto, estado, error=None):
    """Inserta un pago en la DB. Ignora duplicados por mensaje_id."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO pagos
            (mensaje_id, fecha_mensaje, remitente_id, remitente_nombre,
             nombre_pagador, monto, estado, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (mensaje_id, fecha_msg, remitente_id, remitente_nombre,
              nombre, monto, estado, error))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error guardando pago en DB: {e}")
        return False


# ─── DESCARGA DE IMAGEN DESDE WHAPI ───────────────────────────────────
def _descargar_imagen(image_data):
    """Descarga la imagen del mensaje de Whapi. Retorna (base64, mime_type)."""
    url = image_data.get("link") or image_data.get("url")
    if not url:
        raise ValueError("No se encontró URL de imagen en el mensaje")

    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    mime_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
    b64_data = base64.standard_b64encode(resp.content).decode("utf-8")
    return b64_data, mime_type


# ─── EXTRACCIÓN CON CLAUDE ────────────────────────────────────────────
def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


def _extraer_datos_con_claude(image_b64, mime_type):
    """Envía la imagen a Claude y extrae nombre + monto. Retorna dict."""
    client = _get_anthropic_client()

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
    # Limpiar posibles bloques markdown
    text = text.replace("```json", "").replace("```", "").strip()

    import json
    try:
        data = json.loads(text)
        return {
            "nombre": data.get("nombre", "No encontrado"),
            "monto": data.get("monto", "No encontrado"),
        }
    except json.JSONDecodeError:
        logger.warning(f"Respuesta de Claude no es JSON válido: {text}")
        return {"nombre": "Error de parseo", "monto": "Error de parseo"}


# ─── FUNCIÓN PRINCIPAL (la que llamas desde tu webhook) ───────────────
def procesar_imagen_pago(mensaje, async_mode=True):
    """
    Procesa un mensaje de imagen de WhatsApp y guarda el pago extraído.

    Parámetros:
        mensaje (dict): el objeto de mensaje recibido en el webhook de Whapi.
        async_mode (bool): si True, procesa en background sin bloquear
                          la respuesta al webhook (recomendado).

    Estructura esperada del mensaje:
        {
            "id": "...",
            "type": "image",
            "chat_id": "...@g.us",
            "from": "...",
            "from_name": "...",
            "timestamp": 1715000000,
            "image": {"link": "https://..."}
        }
    """
    if async_mode:
        thread = threading.Thread(target=_procesar_sync, args=(mensaje,), daemon=True)
        thread.start()
    else:
        _procesar_sync(mensaje)


def _procesar_sync(mensaje):
    """Procesamiento real (síncrono). Se ejecuta en un thread aparte."""
    msg_id = mensaje.get("id", "sin_id")
    remitente_id = mensaje.get("from", "desconocido")
    remitente_nombre = mensaje.get("from_name", "Desconocido")
    ts = mensaje.get("timestamp")
    fecha_msg = (
        datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        if ts else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    image_data = mensaje.get("image", {})

    logger.info(f"📥 Procesando pago de {remitente_nombre} (msg {msg_id})")

    try:
        # 1) Descargar imagen
        img_b64, mime = _descargar_imagen(image_data)

        # 2) Extraer con Claude
        datos = _extraer_datos_con_claude(img_b64, mime)

        # 3) Guardar en DB
        _guardar_pago(
            mensaje_id=msg_id,
            fecha_msg=fecha_msg,
            remitente_id=remitente_id,
            remitente_nombre=remitente_nombre,
            nombre=datos["nombre"],
            monto=datos["monto"],
            estado="ok",
        )
        logger.info(f"✅ Pago guardado: {datos['nombre']} → {datos['monto']}")

    except Exception as e:
        logger.error(f"❌ Error procesando msg {msg_id}: {e}")
        _guardar_pago(
            mensaje_id=msg_id,
            fecha_msg=fecha_msg,
            remitente_id=remitente_id,
            remitente_nombre=remitente_nombre,
            nombre="Error",
            monto="Error",
            estado="error",
            error=str(e),
        )


# ─── FUNCIONES DE CONSULTA (opcional) ─────────────────────────────────
def listar_pagos_hoy():
    """Retorna lista de pagos procesados hoy."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM pagos
        WHERE DATE(fecha_mensaje) = DATE('now', 'localtime')
        ORDER BY fecha_mensaje DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def listar_pagos_rango(fecha_inicio, fecha_fin):
    """Pagos entre dos fechas (formato YYYY-MM-DD)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM pagos
        WHERE DATE(fecha_mensaje) BETWEEN ? AND ?
        ORDER BY fecha_mensaje DESC
    """, (fecha_inicio, fecha_fin))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def exportar_csv(ruta_archivo="pagos_export.csv"):
    """Exporta todos los pagos a CSV."""
    import csv
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM pagos ORDER BY fecha_mensaje DESC")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None

    with open(ruta_archivo, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))

    return ruta_archivo
