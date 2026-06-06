import os
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

GOOGLE_SHEET_ID_NO_ENCONTRADOS = "1axPmGkV26u7Ptxx8bq_5AYEtInzx95FUlfq-1Gy0ByU"
GOOGLE_CLIENT_EMAIL = os.environ.get("GOOGLE_CLIENT_EMAIL", "")
GOOGLE_PRIVATE_KEY = os.environ.get("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")
GOOGLE_PROJECT_ID = os.environ.get("GOOGLE_PROJECT_ID", "controlpagos-497014")

_sheets_client = None


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


def inicializar_hoja_no_encontrados():
    try:
        gc = _get_sheet()
        sh = gc.open_by_key(GOOGLE_SHEET_ID_NO_ENCONTRADOS)
        hoja = sh.sheet1
        valores = hoja.row_values(1)
        if not valores:
            hoja.append_row(["Fecha", "Número", "Lo que escribió"])
            print("✅ Encabezados creados en hoja de productos no encontrados")
        print("✅ Conexión con hoja de productos no encontrados verificada")
    except Exception as e:
        print(f"⚠️ Error conectando a hoja no encontrados: {e}")


def registrar_producto_no_encontrado(numero, mensaje):
    try:
        gc = _get_sheet()
        sh = gc.open_by_key(GOOGLE_SHEET_ID_NO_ENCONTRADOS)
        hoja = sh.sheet1
        fecha = datetime.now().strftime("%Y-%m-%d")
        hoja.append_row([fecha, numero, mensaje])
        print(f"✅ Producto no encontrado registrado: {mensaje}")
    except Exception as e:
        print(f"❌ Error registrando producto no encontrado: {e}")
