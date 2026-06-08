import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime, timedelta

# ── Credenciales (mismo patrón que pagos_extractor.py) ───────────────────────
GOOGLE_CLIENT_EMAIL = os.environ.get("GOOGLE_CLIENT_EMAIL", "")
GOOGLE_PRIVATE_KEY  = os.environ.get("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")
GOOGLE_PROJECT_ID   = os.environ.get("GOOGLE_PROJECT_ID", "")
GOOGLE_SHEET_ID_CELULARES = os.environ.get("GOOGLE_SHEET_ID_CELULARES", "")

# ── Cache: evita llamar a Sheets en cada mensaje ──────────────────────────────
_cache = {"data": None, "fotos": {}, "timestamp": None}
_sheets_client = None
CACHE_MINUTOS = 5

def _get_sheet():
    """Reutiliza el cliente de Sheets o crea uno nuevo."""
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

def obtener_catalogo_celulares() -> str:
    """
    Lee el Panel del Google Sheet y devuelve texto con
    productos disponibles y sus precios para inyectar
    en el system prompt del bot de celulares.
    """
    global _cache
    ahora = datetime.now()

    # Devolver cache si es reciente
    if (_cache["data"] is not None
            and _cache["timestamp"] is not None
            and ahora - _cache["timestamp"] < timedelta(minutes=CACHE_MINUTOS)):
        return _cache["data"]

    try:
        gc = _get_sheet()
        sh = gc.open_by_key(GOOGLE_SHEET_ID_CELULARES)
        panel = sh.worksheet("Panel")
        filas = panel.get_all_values()

        # Tasa BCV está en celda B3 (índice fila 2, columna 1)
        tasa_bcv = filas[2][1] if len(filas) > 2 else "N/D"

        # Productos desde fila 9 (índice 8)
        # Columnas Panel:
        # B=disponible(1), C=marca(2), D=modelo(3),
        # E=almac(4), F=ram(5), G=camara(6), H=bateria(7),
        # J=paralelo$(9), K=BCV$(10), L=BCVBs(11),
        # M=CrediIni(12), N=CrediCuota(13),
        # O=CasheaTotal(14), P=CasheaIni(15), Q=CasheaCuota(16),
        # R=KreceTotal(17), S=KreceIni(18), T=KreceCuota(19)

        productos = []
        fotos = {}
        for fila in filas[8:]:
            if len(fila) < 20:
                continue
            if fila[1].strip().upper() != "SÍ":
                continue

            marca        = fila[2].strip()
            modelo       = fila[3].strip()
            almac        = fila[4].strip()
            ram          = fila[5].strip()
            camara       = fila[6].strip()
            bateria      = fila[7].strip()
            foto         = fila[8].strip()
            precio_par   = fila[9].strip()
            precio_bcv   = fila[10].strip()
            precio_bs    = fila[11].strip()
            credi_ini    = fila[12].strip()
            credi_cuota  = fila[13].strip()
            cashea_ini   = fila[15].strip()
            cashea_cuota = fila[16].strip()
            krece_ini    = fila[18].strip()
            krece_cuota  = fila[19].strip()

            if not marca or not modelo:
                continue

            if foto:
                 clave = " ".join(f"{marca} {modelo}".lower().split())
                 fotos[clave] = foto

            productos.append(
                f"• {marca} {modelo}\n"
                f"  Specs: {almac} | {ram} RAM | {camara} | {bateria}\n"
                f"  En divisas: ${precio_par} | "
                f"Contado BCV: ${precio_bcv} (Bs {precio_bs})\n"
                f"  CASHEA (60% ini + 3 cuotas): "
                f"Ini ${cashea_ini} + cuotas de ${cashea_cuota}\n"
                f"  KRECE (ini sobre BCV + 4 cuotas): "
                f"Ini ${krece_ini} + cuotas de ${krece_cuota}\n"
                f"  CREDITIENDA (40% ini + 4 cuotas): "
                f"Ini ${credi_ini} + cuotas de ${credi_cuota}\n"
            )

        if not productos:
            resultado = "No hay celulares disponibles en este momento."
        else:
            resultado = (
                f"CATÁLOGO DE CELULARES DISPONIBLES\n"
                f"Tasa BCV: Bs {tasa_bcv} por $1\n"
                f"Cuotas cada 15 días desde la fecha de compra.\n"
                f"Recibimos Zelle y USDT "
                f"(pago en divisas tiene descuento especial).\n\n"
                + "\n".join(productos)
            )

        _cache["data"] = resultado
        _cache["fotos"] = fotos
        _cache["timestamp"] = ahora
        return resultado

    except Exception as e:
        print(f"Error leyendo Sheets celulares: {e}")
        return "Catálogo no disponible temporalmente."

def buscar_foto_celular(modelo_texto):
    """Devuelve la URL de la foto de un modelo, o None si no hay.
    Refresca el cache si está vencido (no recarga si está fresco)."""
    obtener_catalogo_celulares()
    fotos = _cache.get("fotos") or {}
    clave = " ".join(modelo_texto.lower().split())

    if clave in fotos:
        return fotos[clave]

    # Coincidencia parcial por si el modelo trae texto extra
    for nombre, url in fotos.items():
        if clave in nombre or nombre in clave:
            return url

    return None
