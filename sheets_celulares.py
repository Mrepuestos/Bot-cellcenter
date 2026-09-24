"""
SHEETS CELULARES — capa de datos del catálogo
Cell Center 4620

Lee dos hojas del Google Sheet:
  Catalogo       — un modelo por fila: specs, foto y precio paralelo
  Disponibilidad — una fila por modelo y origen

No arma texto de venta ni decide qué mostrar: solo devuelve datos.
Los precios los calcula precios.py; el texto lo arma app.py.
"""

import os
import re
from datetime import datetime, timedelta

import gspread
from google.oauth2.service_account import Credentials

from repertorio import CORRECCIONES_MARCAS, PALABRAS_IGNORAR

# ─── Credenciales ─────────────────────────────────────────────────────────────
GOOGLE_CLIENT_EMAIL = os.environ.get("GOOGLE_CLIENT_EMAIL", "")
GOOGLE_PRIVATE_KEY  = os.environ.get("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")
GOOGLE_PROJECT_ID   = os.environ.get("GOOGLE_PROJECT_ID", "")
GOOGLE_SHEET_ID_CELULARES = os.environ.get("GOOGLE_SHEET_ID_CELULARES", "")

CACHE_MINUTOS = 5

# Orden de preferencia: menor número gana
PRIORIDAD_ORIGEN = {"tienda": 0, "aliada": 1, "proveedor": 2}
ENTREGA = {"tienda": "inmediata", "aliada": "inmediata", "proveedor": "24-48h"}

_cache = {"equipos": None, "timestamp": None}
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


# ─── Normalización ────────────────────────────────────────────────────────────

def normalizar(texto):
    """Minúsculas, sin puntuación, con las marcas corregidas."""
    t = str(texto or "").lower().strip()
    for a, b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")):
        t = t.replace(a, b)
    t = re.sub(r"[^\w\s]", " ", t)
    # Une letra suelta con número: "A 07" -> "a07" (Samsung A07, Moto G17, Poco X8)
    t = re.sub(r"\b([acgpx])\s+(\d)", r"\1\2", t)
    t = re.sub(r"([a-z]{3,})(\d)", r"\1 \2", t)
    t = re.sub(r"(\d)([a-z]{3,})", r"\1 \2", t)
    for error, correcto in CORRECCIONES_MARCAS.items():
        t = re.sub(r"\b" + re.escape(error) + r"\b", correcto, t)
    t = re.sub(r"\b([acgpx])\s+(\d)", r"\1\2", t)
    return re.sub(r"\s+", " ", t).strip()


def _clave(marca, modelo, almacen, ram):
    """Clave única de un equipo. Enlaza Catalogo con Disponibilidad."""
    return "|".join(str(x or "").strip().lower() for x in (marca, modelo, almacen, ram))


def _num(valor):
    """'$1,250' -> 1250.0 ; '' -> None"""
    if valor is None:
        return None
    limpio = re.sub(r"[^\d.]", "", str(valor))
    if not limpio:
        return None
    try:
        return float(limpio)
    except ValueError:
        return None


# ─── Carga desde el Sheet ─────────────────────────────────────────────────────

def _cargar():
    """Lee las dos hojas y arma la lista de equipos. Usa caché."""
    global _cache
    ahora = datetime.now()
    if (_cache["equipos"] is not None
            and _cache["timestamp"] is not None
            and ahora - _cache["timestamp"] < timedelta(minutes=CACHE_MINUTOS)):
        return _cache["equipos"]

    try:
        sh = _get_sheet().open_by_key(GOOGLE_SHEET_ID_CELULARES)

        # ── Catalogo: encabezados en la fila 4, datos desde la 5 ──
        filas_cat = sh.worksheet("Catalogo").get_all_values()
        equipos = {}
        for fila in filas_cat[4:]:
            if len(fila) < 9:
                fila = fila + [""] * (9 - len(fila))
            marca, modelo, almacen, ram = (x.strip() for x in fila[0:4])
            if not marca or not modelo:
                continue
            camara, bateria, foto = (x.strip() for x in fila[4:7])
            precio = _num(fila[7])
            verificado = fila[8].strip().upper() in ("SI", "SÍ")

            equipos[_clave(marca, modelo, almacen, ram)] = {
                "marca": marca, "modelo": modelo,
                "almacenamiento": almacen, "ram": ram,
                "camara": camara, "bateria": bateria, "foto": foto,
                "precio_paralelo": precio,
                "precio_verificado": verificado,
                "origenes": [],
            }

        # ── Disponibilidad: encabezados en la fila 5, datos desde la 6 ──
        filas_disp = sh.worksheet("Disponibilidad").get_all_values()
        for fila in filas_disp[5:]:
            if len(fila) < 7:
                continue
            marca, modelo, almacen, ram, origen, proveedor, disponible = (
                x.strip() for x in fila[0:7])
            if not marca or not modelo:
                continue
            if disponible.upper() not in ("SI", "SÍ"):
                continue
            origen = origen.lower()
            if origen not in PRIORIDAD_ORIGEN:
                continue
            eq = equipos.get(_clave(marca, modelo, almacen, ram))
            if eq is None:
                continue  # está en Disponibilidad pero no en Catalogo
            eq["origenes"].append({"origen": origen, "proveedor": proveedor})

        # Solo equipos que estén disponibles en algún lado
        lista = []
        for eq in equipos.values():
            if not eq["origenes"]:
                continue
            mejor = min(eq["origenes"], key=lambda o: PRIORIDAD_ORIGEN[o["origen"]])
            eq["origen"] = mejor["origen"]
            eq["proveedor"] = mejor["proveedor"]
            eq["entrega"] = ENTREGA[mejor["origen"]]
            eq["inmediato"] = mejor["origen"] in ("tienda", "aliada")
            eq["_busqueda"] = normalizar(
                f"{eq['marca']} {eq['modelo']} {eq['almacenamiento']}")
            lista.append(eq)

        _cache["equipos"] = lista
        _cache["timestamp"] = ahora
        print(f"✅ Catálogo cargado: {len(lista)} equipos disponibles")
        return lista

    except Exception as e:
        print(f"❌ Error leyendo catálogo de celulares: {e}")
        return _cache["equipos"] or []


# ─── Catálogo para que la IA interprete ──────────────────────────────────────

def catalogo_para_ia():
    """
    Lista compacta de todos los equipos disponibles, para que Sonnet
    interprete qué pide el cliente. Incluye los que no tienen precio
    verificado, marcados, para poder decir que existen pero no cotizarlos.
    """
    lineas = []
    for eq in _cargar():
        clave = _clave(eq["marca"], eq["modelo"], eq["almacenamiento"], eq["ram"])
        nombre = nombre_completo(eq)
        partes = [f"{clave} | {nombre}"]
        if eq["precio_verificado"] and eq["precio_paralelo"]:
            partes.append(f"${int(eq['precio_paralelo'])}")
        else:
            partes.append("SIN PRECIO")
        if eq.get("camara"):
            partes.append(f"cam {eq['camara']}")
        partes.append("ya" if eq["inmediato"] else "24-48h")
        lineas.append(" | ".join(partes))
    return "\n".join(lineas)


def obtener_por_clave(clave, solo_con_precio=True):
    """Devuelve el equipo exacto que la IA eligió, o None."""
    clave = str(clave).strip().lower()
    for eq in _cargar():
        if _clave(eq["marca"], eq["modelo"], eq["almacenamiento"], eq["ram"]) == clave:
            if solo_con_precio and not (eq["precio_verificado"] and eq["precio_paralelo"]):
                return None
            return eq
    return None


def existe_clave(clave):
    """True si la clave corresponde a un equipo real, tenga precio o no."""
    return obtener_por_clave(clave, solo_con_precio=False) is not None


def ordenar_equipos(equipos):
    """Primero tienda, luego aliada, luego proveedor. Si hay entrega
    inmediata, oculta lo que tarda 24-48h."""
    inmediatos = [e for e in equipos if e["inmediato"]]
    if inmediatos:
        equipos = inmediatos
    return sorted(equipos, key=lambda e: (PRIORIDAD_ORIGEN[e["origen"]],
                                          e["precio_paralelo"] or 0))


def listar_por_rango(excluir_iphone=False):
    """
    Tres equipos de entrega inmediata: económico, intermedio y gama alta.
    Para cuando el cliente pregunta qué hay sin decir modelo.
    excluir_iphone=True para clientes Krece Azul.
    """
    equipos = [e for e in _cargar()
               if e["inmediato"] and e["precio_verificado"] and e["precio_paralelo"]]
    if excluir_iphone:
        equipos = [e for e in equipos if e["marca"].lower() != "iphone"]
    if not equipos:
        return []
    equipos.sort(key=lambda e: e["precio_paralelo"])
    n = len(equipos)
    if n <= 3:
        return equipos
    return [equipos[n // 6], equipos[n // 2], equipos[-(n // 6) - 1]]

def listar_mas_baratos(cantidad=8, excluir_iphone=False):
    """
    Los equipos más baratos de entrega inmediata y con precio verificado.
    Para la lista corta y para cuando el cliente dice que está caro.
    """
    equipos = [e for e in _cargar()
               if e["inmediato"] and e["precio_verificado"] and e["precio_paralelo"]]
    if excluir_iphone:
        equipos = [e for e in equipos if e["marca"].lower() != "iphone"]
    equipos.sort(key=lambda e: e["precio_paralelo"])
    return equipos[:cantidad]

def buscar_foto(marca, modelo, almacenamiento=""):
    """URL de la foto, o None. Se usa solo si el cliente la pide."""
    for eq in _cargar():
        if (eq["marca"].lower() == str(marca).lower()
                and eq["modelo"].lower() == str(modelo).lower()):
            if almacenamiento and eq["almacenamiento"].lower() != str(almacenamiento).lower():
                continue
            return eq["foto"] or None
    return None


def nombre_completo(eq):
    """'Redmi 17 256GB · 6GB RAM'"""
    partes = [eq["marca"], eq["modelo"]]
    if eq["almacenamiento"]:
        partes.append(eq["almacenamiento"])
    texto = " ".join(partes)
    if eq["ram"]:
        texto += f" · {eq['ram']}GB RAM"
    return texto


def refrescar():
    """Fuerza recarga en la próxima consulta."""
    _cache["timestamp"] = None
