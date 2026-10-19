from flask import Flask, request, jsonify
import unicodedata
import base64
import requests
import anthropic
import os
import time
import threading
import xmlrpc.client
import json
import re
import random
from datetime import datetime, timedelta
import pytz
from supabase import create_client

# ── Módulo de pagos ────────────────────────────────────────────────────────────
from pagos_extractor import procesar_imagen_pago, inicializar_db, borrar_pago_por_msg_id

# ── Repertorio de correcciones ─────────────────────────────────────────────────
from repertorio import CORRECCIONES_MARCAS, MODELOS_ABREVIADOS, PALABRAS_IGNORAR

# ── Catálogo de celulares (hojas Catalogo + Disponibilidad) ───────────────────
from sheets_celulares import (
    catalogo_para_ia, obtener_por_clave, existe_clave,
    ordenar_equipos, listar_por_rango, listar_mas_baratos, buscar_foto, nombre_completo,
    inventario_admin, promo_krece, equipos_en_promo,
)

# ── Motor de precios de celulares ─────────────────────────────────────────────
import precios

from productos_no_encontrados import inicializar_hoja_no_encontrados, registrar_producto_no_encontrado

app = Flask(__name__)

BOT_START_TIME = time.time()

# ── ID del grupo de pagos ──────────────────────────────────────────────────────
GRUPO_PAGOS_ID = os.environ.get("GRUPO_PAGOS_ID", "")

NUMEROS_AUTORIZADOS = [
    "584241564298",
    "584125429180",
    "584126047270",
    "584142050748",
    "584121620025",
    "584128192709",
    "584242418728",
    "584124675930",
    "584241217113",
    "584129785352",
    "584129618012",
    "584127828783",
    "584125591811",
    "584120150926",
    "584141261194",
    "584262136531",
    "584264372938",
    "584129626743",
    "584127564125",
    "584242519892",
    "584125519041",
    "584242418059",
    "584243025656",
    "584143087012",
    "584123874634",
    "584123601132",
    "584142413910",
    "584125572583",
    "584127279315",
    "584126036858",
    "584125850277",
    "584241747266",
    "584141242469",
    "584126093756",
    "584241614444",
    "584124146374",
    "584242667571",
    "584241813522",
    "584128042214",
    "584241628873",
    "584129334810",
    "584241346346",
    "584120276194",
    "584164504076",
    "584242343191",
    "584241790339",
    "584142648310",
    "584241365232",
    "584120286234",
    "584125887854",
    "584142767523",
    "584120129903",
    "584126229524",
    "584126093756",
    "584241464083",
    "584128092936",
    "584241255279"
]

ASESOR_TECNICO = "584126093756"
ASESOR_ACCESORIOS = "584126093756"
ASESOR_STOCK = "584126093756"

# ── Asesores del flujo de celulares ───────────────────────────────────────────
ASESOR_CELULARES   = "584126093756"   # intención de compra y precios sin verificar
ASESOR_CEL_TECNICO = "584220392375"   # servicio técnico y reparaciones
ASESOR_CEL_OTROS   = "584126093756"   # accesorios y todo lo demás

# ── Números de prueba: van al flujo de celulares como cliente y pueden usar
#    el comando "reset" para empezar de cero. 573208112456 = número colombiano de pruebas.
NUMEROS_PRUEBA_CELULARES = ["573208112456"]
modo_prueba_pantallas = set()   # números de prueba probando el flujo de pantallas
krece_supuesto = set()          # clientes cotizados con Azul/$300 supuesto (solo en memoria)
nivel1_pendiente = set()        # clientes que dijeron "nivel 1" sin decir Cashea o Krece (solo en memoria)
krece_linea_dudosa = {}         # cliente → monto chico que parecía la "Línea disponible"
krece_linea_disponible = {}     # cliente → su "Línea disponible" (tope del iPhone, solo en memoria)
tecnico_celulares = {}   # técnico → hasta cuándo sigue en el flujo de celulares (30 min)
confirmar_celular = {}   # técnico → (mensaje, hora) mientras confirma teléfono o pantalla
PALABRAS_PANTALLA = ("pantalla", "display", "repuesto", "modulo", "módulo", "lcd", "oled",
                     "incell", "tactil", "táctil", "flex", "pin de carga", "tapa")
PALABRAS_TELEFONO = ("telefono", "teléfono", "celular", "equipo", "completo", "comprar")
PALABRAS_COMPRA = ("comprar", "compro", "venden", "vendes", "para mi", "para mí")

# ── Administradores: consultas de precios y comandos especiales ───────────────
ADMINISTRADORES = ["584149202844", "584241369824"]

# ── Aviso que recibe cada cliente la primera vez que escribe ──────────────────
AVISO_IA = ("👋 ¡Hola! Te atiende el asistente virtual de *Cell Center 4620*, "
            "con inteligencia artificial 🤖. Te doy precios y disponibilidad "
            "al momento, pero puedo cometer errores.")

# ── Ubicación de la tienda ────────────────────────────────────────────────────
TIENDA_LAT = 10.2325
TIENDA_LNG = -66.664972
TIENDA_NOMBRE = "Cell Center 4620"
TIENDA_DIRECCION = ("Centro, Av San Rafael entre calle El Carmen y Sucre, "
                    "frente al CC La Asunción, a 30 mtrs de la parada de Mopia")
FOTO_FACHADA_URL = "https://i.postimg.cc/rwZJRKzD/Whats-App-Image-2026-10-07-at-9-08-11-AM.jpg"

# ── Modelo que atiende el flujo de celulares ──────────────────────────────────
MODELO_CELULARES = "claude-haiku-5-5"
# Respaldo: la IA rescata un dato que Python no entendió (deja la marca RESPALDO en el log)
MODELO_RESPALDO = "claude-haiku-5-5"

# ── Mensaje predefinido con el que llegan los clientes de Krece ───────────────
MENSAJE_KRECE = "quiero comprar con krece"

# ── Marcas que Krece no aprueba (no les puede instalar su seguridad) ──────────
MARCAS_NO_KRECE = {"logic", "zte"}

WHAPI_TOKEN = os.environ.get("WHAPI_TOKEN", "")
WHAPI_API_URL = os.environ.get("WHAPI_API_URL", "https://gate.whapi.cloud")
ODOO_URL = os.environ.get("ODOO_URL", "")
ODOO_DB = os.environ.get("ODOO_DB", "")
ODOO_USER = os.environ.get("ODOO_USER", "")
ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

TABLA_PRECIOS = {
    10: 13, 11: 14, 12: 15, 13: 17, 14: 18,
    15: 19, 16: 21, 21: 27, 26: 31
}

tasa_bcv_cache = {"tasa": None, "fecha": ""}
tasa_euro_cache = {"tasa": None, "fecha": ""}
stock_bajo_pendiente = {}
pausas_activas = {}


# ── Agrupador de mensajes seguidos (flujo celulares) ──────────────────────────
ESPERA_AGRUPAR = 6  # segundos que espera a que el cliente termine de escribir
buffer_mensajes = {}
buffer_lock = threading.Lock()

procesando = set()   # clientes que se están atendiendo en este momento

def procesar_buffer(numero_limpio):
    with buffer_lock:
        if numero_limpio in procesando:
            # Aún se responde un mensaje anterior: se espera 2 s y se reintenta
            datos = buffer_mensajes.get(numero_limpio)
            if datos:
                t = threading.Timer(2, procesar_buffer, args=[numero_limpio])
                t.daemon = True
                datos["timer"] = t
                t.start()
            return
        datos = buffer_mensajes.pop(numero_limpio, None)
        if not datos:
            return
        procesando.add(numero_limpio)
    body_junto = "\n".join(datos["textos"])
    from_number = datos["from"]
    print(f"📦 Procesando {len(datos['textos'])} mensaje(s) agrupados de {numero_limpio}")
    try:
        atender_celulares(from_number, numero_limpio, body_junto)
    except Exception as e:
        print(f"Error en flujo de celulares: {e}")
        notificar_asesor(ASESOR_CELULARES, "error del bot", from_number)
        send_whapi_message(from_number, msg_asesor(
            "Dame un momento, un asesor te atiende enseguida",
            "Un asesor te atenderá mañana a partir de las 6:00 am"))
    finally:
        with buffer_lock:
            procesando.discard(numero_limpio)

def agregar_al_buffer(from_number, numero_limpio, body):
    with buffer_lock:
        datos = buffer_mensajes.get(numero_limpio)
        if datos:
            datos["timer"].cancel()
            datos["textos"].append(body)
        else:
            datos = {"textos": [body], "from": from_number}
            buffer_mensajes[numero_limpio] = datos
        t = threading.Timer(ESPERA_AGRUPAR, procesar_buffer, args=[numero_limpio])
        t.daemon = True
        datos["timer"] = t
        t.start()

PALABRAS_SI = ["si","sí","yes","claro","dale","ok","okay","quiero","aparta","reserva","separa","confirmado","afirmativo","me interesa","la quiero"]

# ── Marcas conocidas para búsqueda sin marca ──────────────────────────────────
MARCAS_CONOCIDAS = {
    "samsung", "redmi", "xiaomi", "infinix", "iphone", "huawei",
    "tecno", "motorola", "alcatel", "honor", "realme"
}

# ── Palabras que distinguen variantes de modelo (Pro ≠ normal) ────────────────
PALABRAS_VARIANTE = {"pro", "plus", "max", "ultra", "lite", "play", "prime", "go"}


# ── Utilidades generales ──────────────────────────────────────────────────────

def normalizar_texto(texto):
    texto = texto.lower().strip()
    texto = re.sub(r'\b3/4\b', '', texto)
    texto = re.sub(r'[^\w\s]', ' ', texto)
    for error, correcto in CORRECCIONES_MARCAS.items():
        texto = re.sub(r'\b' + re.escape(error) + r'\b', correcto, texto)
    texto = re.sub(r'\b([acgpx])\s+(\d)', r'\1\2', texto)
    texto = re.sub(r'([a-zA-Z]{3,})(\d)', r'\1 \2', texto)
    texto = re.sub(r'(\d)([a-zA-Z]{3,})', r'\1 \2', texto)
    for error, correcto in CORRECCIONES_MARCAS.items():
        texto = re.sub(r'\b' + re.escape(error) + r'\b', correcto, texto)
    texto = re.sub(r'\b([acgpx])\s+(\d)', r'\1\2', texto)
    # Tecno Spark Go: "go1", "go 1", "tecno go1", "sparkgo1" -> "spark go 1"; "go 24" -> "spark go 2024"
    texto = re.sub(r'\b(?:spark\s*)?go\s*(\d+)\b', r'spark go \1', texto)
    texto = re.sub(r'\bspark go (2[0-4])\b', r'spark go 20\1', texto)
    return texto


def limpiar_html(texto):
    if not texto:
        return ""
    texto_limpio = re.sub(r'<[^>]+>', ' ', str(texto))
    texto_limpio = texto_limpio.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').replace('&nbsp;',' ')
    return re.sub(r'\s+', ' ', texto_limpio).strip()


def obtener_tasa_bcv():
    try:
        tz = pytz.timezone("America/Caracas")
        fecha_hoy = time.strftime("%Y-%m-%d")
        if tasa_bcv_cache["fecha"] == fecha_hoy and tasa_bcv_cache["tasa"]:
            return tasa_bcv_cache["tasa"]
        r = requests.get("https://ve.dolarapi.com/v1/dolares/oficial", timeout=5)
        tasa = float(r.json()["promedio"])
        tasa_bcv_cache["tasa"] = tasa
        tasa_bcv_cache["fecha"] = fecha_hoy
        print(f"Tasa BCV actualizada: {tasa}")
        return tasa
    except Exception as e:
        print(f"Error obteniendo tasa BCV: {e}")
        return tasa_bcv_cache["tasa"]


def obtener_tasa_euro():
    try:
        tz = pytz.timezone("America/Caracas")
        fecha_hoy = datetime.now(tz).strftime("%Y-%m-%d")
        if tasa_euro_cache["fecha"] == fecha_hoy and tasa_euro_cache["tasa"]:
            return tasa_euro_cache["tasa"]
        r = requests.get("https://ve.dolarapi.com/v1/euros/oficial", timeout=5)
        tasa = float(r.json()["promedio"])
        tasa_euro_cache["tasa"] = tasa
        tasa_euro_cache["fecha"] = fecha_hoy
        print(f"Tasa Euro actualizada: {tasa}")
        return tasa
    except Exception as e:
        print(f"Error obteniendo tasa Euro: {e}")
        return tasa_euro_cache["tasa"]


def calcular_precio_bs(precio_usd_odoo):
    tasa_euro = obtener_tasa_euro()
    precio_bs = round(precio_usd_odoo * tasa_euro) if tasa_euro else None
    return precio_usd_odoo, precio_bs


def esta_abierto():
    tz = pytz.timezone("America/Caracas")
    ahora = datetime.now(tz)
    hora = ahora.hour + ahora.minute / 60
    return (9.0 <= hora < 14.0) if ahora.weekday() == 6 else (8.5 <= hora < 17.5)


def cargar_historial(numero):
    try:
        resultado = supabase.table("Clientes").select("historial").eq("numero", numero).execute()
        if resultado.data and resultado.data[0].get("historial"):
            return json.loads(resultado.data[0]["historial"])
        return []
    except Exception as e:
        print(f"Error cargando historial: {e}")
        return []


def guardar_historial(numero, historial):
    try:
        historial_str = json.dumps(historial[-4:])
        resultado = supabase.table("Clientes").select("numero").eq("numero", numero).execute()
        if resultado.data:
            supabase.table("Clientes").update({
                "historial": historial_str,
                "ultima_visita": datetime.utcnow().isoformat()
            }).eq("numero", numero).execute()
        else:
            supabase.table("Clientes").insert({
                "numero": numero,
                "historial": historial_str,
                "ultima_visita": datetime.utcnow().isoformat()
            }).execute()
    except Exception as e:
        print(f"Error guardando historial: {e}")


# ── Perfil del cliente de celulares (Supabase) ────────────────────────────────

def cargar_perfil(numero):
    """canal_pago, canal_extra, nivel_cliente, linea_krece y modelo_interes."""
    vacio = {"canal_pago": None, "canal_extra": None, "nivel_cliente": None,
             "linea_krece": None, "modelo_interes": None, "nombre": None,
             "nombre_preguntado": None, "respondio": None}
    try:
        r = supabase.table("Clientes").select(
            "canal_pago,canal_extra,nivel_cliente,linea_krece,modelo_interes,nombre,nombre_preguntado,respondio"
        ).eq("numero", numero).execute()
        if r.data:
            return {k: r.data[0].get(k) for k in vacio}
        return vacio
    except Exception as e:
        print(f"Error cargando perfil: {e}")
        return vacio


def guardar_perfil(numero, borrar=(), **campos):
    """Guarda los campos que vengan con valor y vacía los que estén en borrar."""
    datos = {k: v for k, v in campos.items() if v is not None}
    for campo in borrar:
        datos[campo] = None
    if not datos:
        return
    try:
        r = supabase.table("Clientes").select("numero").eq("numero", numero).execute()
        if r.data:
            supabase.table("Clientes").update(datos).eq("numero", numero).execute()
        else:
            datos["numero"] = numero
            supabase.table("Clientes").insert(datos).execute()
    except Exception as e:
        print(f"Error guardando perfil: {e}")
        

def enviar_aviso_ia(from_number, numero_limpio):
    """Manda el aviso de IA solo la primera vez que el cliente escribe."""
    try:
        r = supabase.table("Clientes").select("aviso_ia").eq(
            "numero", numero_limpio).execute()
        if r.data and r.data[0].get("aviso_ia"):
            return
        send_whapi_message(from_number, AVISO_IA)
        guardar_perfil(numero_limpio, aviso_ia=True)
    except Exception as e:
        print(f"Error con el aviso de IA: {e}")


# ── Detección de canal, nivel y línea en lo que escribe el cliente ────────────

def detectar_canal(texto):
    t = "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                if unicodedata.category(c) != "Mn")
    if MENSAJE_KRECE in t or "krece" in t or "krese" in t or "crece" in t:
        return "krece"
    if "cashea" in t or "cashe" in t or "cachea" in t:
        return "cashea"
    if "creditienda" in t or "credi tienda" in t:
        return "creditienda"
    if any(p in t for p in ("contado", "efectivo", "divisa", "dolar", "d\u00f3lar",
                            "zelle", "usdt", "cash", "precio normal",
                            "sin financiamiento", "sin financiar", "sin cuotas",
                            "pago de una vez", "pagar de una vez",
                            "pagarlo de una vez", "un solo pago", "pago completo")):
        return "contado"
    if (re.search(r"\bnivel\s*(azul|plata|oro|platino)\b|\bsoy\s+(?:nivel\s+)?(?:azul|plata|oro|platino)\b|\b(?:azul|plata|oro|platino)\s+con\s+\$?\s*\d{2,5}\b", t)
            or re.search(r"\b\d{2,4}\s*(?:pts?|puntos)\b", t)
            or re.search(r"\blinea\s+(aprobada|de\s+celulares)\b", t)):
        return "krece"
    if re.search(r"\bnivel\s*[2-6]\b", t):
        return "cashea"
    return None


def detectar_canales(texto):
    """Todos los canales que nombra el cliente, por ejemplo
    'con krece y creditienda' -> ['krece', 'creditienda']."""
    t = "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                if unicodedata.category(c) != "Mn")
    encontrados = []
    if MENSAJE_KRECE in t or "krece" in t or "krese" in t or "crece" in t:
        encontrados.append("krece")
    if "cashea" in t or "cashe" in t or "cachea" in t:
        encontrados.append("cashea")
    if "creditienda" in t or "credi tienda" in t:
        encontrados.append("creditienda")
    return encontrados

def detectar_nivel_krece(texto):
    t = texto.lower()
    for nivel in ("platino", "oro", "plata", "azul"):
        if nivel in t:
            return nivel
    # App nueva: el nivel sale de los puntos ("tengo 150 pts")
    m = re.search(r"\b(\d{1,4})\s*(?:pts?|puntos)\b", t)
    if m:
        p = int(m.group(1))
        return "azul" if p < 121 else "plata" if p < 241 else "oro" if p < 500 else "platino"
    if re.search(r"\b(nivel\s*)?1\b", t) or "primero" in t or "nuevo" in t:
        return "azul"
    return None


def detectar_nivel_cashea(texto):
    t = texto.lower()
    mapa = {"semilla": "1", "raiz": "2", "ra\u00edz": "2", "hoja": "3",
            "tronco": "4", "arbol": "5", "\u00e1rbol": "5", "araguaney": "6"}
    for palabra, num in mapa.items():
        if palabra in t:
            return num
    palabras = {"uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6"}
    m = re.search(r"\bnivel\s*(uno|dos|tres|cuatro|cinco|seis)\b", t)
    if m:
        return palabras[m.group(1)]
    if t.strip() in palabras:
        return palabras[t.strip()]
    m = re.search(r"\bnivel\s*([1-6])\b", t) or re.search(
        r"\b([1-6])\b(?!\s*(?:cuotas?|pagos?|meses|veces|x\b|\$|d[oó]lares))", t)
    return m.group(1) if m else None


def detectar_nivel_cashea_claro(texto):
    """Nivel de Cashea dicho sin dudas: 'nivel 2', 'raíz', 'cashea 3' o solo '2'."""
    t = texto.lower().strip(" .!¿?")
    claro = (any(p in t for p in ("semilla", "raiz", "ra\u00edz", "hoja", "tronco",
                                  "arbol", "\u00e1rbol", "araguaney"))
             or re.search(r"\bnivel\W*([2-6]|dos|tres|cuatro|cinco|seis)\b", t)
             or (("cashe" in t or "cachea" in t)
                 and re.search(r"\b([1-6]|uno|dos|tres|cuatro|cinco|seis)\b", t))
             or t in ("2", "3", "4", "5", "6", "dos", "tres", "cuatro", "cinco", "seis"))
    return detectar_nivel_cashea(t) if claro else None


def detectar_sin_cuenta(texto):
    """True si el cliente dice que no tiene cuenta en Krece o Cashea."""
    t = "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                if unicodedata.category(c) != "Mn")
    return bool(re.search(
        r"no (tengo|uso|manejo) (ni |cuenta|la app|la aplicacion|krece|crece|cashea|cashe)"
        r"|no (estoy|me he|he) (registrad|inscrit|afiliad)"
        r"|no (estoy|tengo) en (krece|crece|cashea|cashe)"
        r"|nunca (lo |la )?he usado|no (lo |la )?he usado", t))


def detectar_linea(texto):
    """Busca un monto que parezca la línea aprobada de Krece."""
    texto = texto.replace("@", "")   # "800@$" es 800$
    # "inicial de 40$" o "40$ de inicial" es lo que quiere pagar de inicial, NO su línea
    texto = re.sub(r"inicial(?:\s+de)?\s*\$?\s*\d+(?:[.,]\d+)?\s*(?:\$|d[oó]lares|usd)?"
                   r"|\$?\s*\d+(?:[.,]\d+)?\s*(?:\$|d[oó]lares|usd)?\s*de\s+inicial",
                   " ", texto, flags=re.IGNORECASE)
    # "75$ al mes", "cuota de 75$", "75 cada 15 días" es una cuota, NO su línea
    texto = re.sub(r"\$?\s*\d+(?:[.,]\d+)?\s*(?:\$|d[oó]lares|usd)?\s*"
                   r"(?:al mes|por mes|mensual\w*|quincenal\w*|cada\b|por cuota|de cuota|la cuota|x cuota)"
                   r"|cuotas?\s+(?:de\s+)?\$?\s*\d+(?:[.,]\d+)?\s*(?:\$|d[oó]lares|usd)?",
                   " ", texto, flags=re.IGNORECASE)
    # App nueva: "disponible 40 y celulares 300" -> vale el de Celulares
    # "celulares es de 400", "línea de celulares de 500" (no "celulares de 128gb")
    m = (re.search(r"celulares\s*(?:(?:me sale|me aparece|tengo|es|son)(?:\s+de)?|:)?\s*\$?\s*(\d{2,5})(?!\d|\s*(?:gb|g\b|tb))",
                   texto.lower())
         or re.search(r"l[ií]nea\s+(?:de|para)\s+(?:los\s+)?celulares\s+de\s+\$?\s*(\d{2,5})(?!\d|\s*(?:gb|g\b|tb))",
                      texto.lower())
         or re.search(r"\b(\d{2,5})\s*\$?\s*(?:(?:en|de|para)\s+)?(?:los\s+)?celulares",
                      texto.lower()))
    if m:
        return float(m.group(1))
    # "mi monto era 300$ y ahora me aparece 40$": vale el ÚLTIMO monto
    montos = re.findall(r"\$\s*(\d{2,5}(?:[.,]\d{1,2})?)|(?<![\d.,])(\d{2,5}(?:[.,]\d{1,2})?)\s*(?:\$|d[oó]lares\b|usd\b)", texto.lower())
    if len(montos) >= 2:
        return float(next(n for n in montos[-1] if n).replace(",", "."))
    m = re.search(r"(?:linea|l\u00ednea|aprobad[oa]|credito|cr\u00e9dito|limite|l\u00edmite)"
                  r"[^\d]{0,15}(\d{2,5})", texto.lower())
    if m:
        return float(m.group(1))
    m = re.search(r"\b(\d{2,5})\s*(?:\$|d[oó]lares|usd)?\s*(?:de\s+)?"
                  r"(?:linea|línea|cr[eé]dito|l[ií]mite)", texto.lower())
    if m:
        return float(m.group(1))
    # Con decimales: "83.11$" es 83.11 (antes se leía 11)
    m = (re.search(r"\$\s*(\d{2,5}(?:[.,]\d{1,2})?)", texto)
         or re.search(r"(?<![\d.,])(\d{2,5}(?:[.,]\d{1,2})?)\s*\$", texto))
    return float(m.group(1).replace(",", ".")) if m else None


RELLENO_LINEA = {"y", "mi", "es", "de", "la", "linea", "línea", "tengo", "son", "me",
                 "aprobaron", "aprobado", "aprobada", "limite", "límite", "dolares",
                 "dólares", "usd", "oro", "plata", "azul", "platino", "nivel", "ok",
                 "si", "sí", "con", "krece", "soy", "como",
                 "ah", "aja", "ajá", "dice", "bueno", "pues", "oye", "listo", "aparece", "aparecen",
                 "sale", "salen", "monto", "ahora", "ahorita"}


def detectar_linea_suelta(texto):
    """Número solo como línea de Krece: "400", "400 nivel oro", "$400".
    Solo si en ese renglón no hay nada más (ni modelo ni GB)."""
    for renglon in texto.lower().split("\n"):
        t = re.sub(r"nivel\s+\w+", " ", renglon)
        # Quita signos, pero deja el punto o la coma de un decimal ("83.11")
        t = re.sub(r"[^\w\s.,]|(?<!\d)[.,]|[.,](?!\d)", " ", t)
        palabras = [p for p in t.split() if p not in RELLENO_LINEA]
        if len(palabras) == 1 and re.fullmatch(r"\d+(?:[.,]\d{1,2})?", palabras[0]):
            n = float(palabras[0].replace(",", "."))
            if 50 <= n <= 5000 and n not in (64, 128, 256, 512):
                return n
    return None


def linea_por_ia(numero_limpio, texto):
    """Respaldo: la IA lee la línea de Krece cuando Python no la entendió.
    Siempre deja la marca RESPALDO en el log para la revisión diaria."""
    crudo, valor = "", None
    try:
        r = client.messages.create(
            model=MODELO_RESPALDO, max_tokens=20,
            extra_body={"output_config": {"effort": "low"}},
            messages=[{"role": "user", "content":
                "Un cliente de una tienda de celulares en Venezuela respondió cuánto tiene "
                "de línea aprobada en Krece para Celulares (en dólares). Si su mensaje trae "
                "ese monto, responde SOLO el número (ej. 800 o 83.11). Si el número son "
                "puntos, GB, un modelo de teléfono, cuotas o no es la línea, responde SOLO: nada.\n"
                f'Mensaje: "{texto}"'}],
        )
        registrar_uso("respaldo-linea", r)
        crudo = texto_respuesta(r).strip().lower()
        m = re.fullmatch(r"\$?\s*(\d{1,5}(?:[.,]\d{1,2})?)\s*\$?", crudo)
        valor = float(m.group(1).replace(",", ".")) if m else None
        if valor is not None and not (10 <= valor <= 5000):
            valor = None
    except Exception as e:
        print(f"Error en respaldo IA de línea: {e}")
        crudo = "error"
    print(f'🛟 RESPALDO IA [linea] | cliente: {numero_limpio} | '
          f'mensaje: "{texto[:120]}" | resultado: {valor if valor is not None else (crudo or "nada")}')
    return valor


def linea_celulares_dudosa(numero_limpio, linea, texto):
    """True si el monto parece la 'Línea disponible' (general) y no la de Celulares.
    Si el cliente repite el mismo monto, se le cree."""
    if not linea:
        return False
    if linea >= 150 or "celular" in texto.lower() or krece_linea_dudosa.get(numero_limpio) == linea:
        krece_linea_dudosa.pop(numero_limpio, None)
        return False
    krece_linea_dudosa[numero_limpio] = linea
    return True


def detectar_linea_disponible(texto):
    """'disponible 150', 'línea disponible: $150' o '150 disponible'."""
    t = texto.lower()
    # Ojo: "¿el iPhone 13 está disponible?" o "disponible 128gb" NO son la línea
    m = (re.search(r"disponible\s*(?:es|son|de|tengo|me sale|me aparece|:)?\s*\$?\s*"
                   r"(\d{1,5})(?!\d|\s*(?:gb|g\b|tb|pro|plus|max))", t)
         or re.search(r"\b(\d{1,5})\s*(?:\$|d[oó]lares)?\s*(?:de\s+)?l[ií]nea\s+disponible", t)
         or re.search(r"(?:\$\s*(\d{1,5})|\b(\d{1,5})\s*\$)\s*(?:de\s+)?disponible", t))
    if m:
        return float(next(g for g in m.groups() if g))
    return None


def lineas_iphone_krece(p, nivel, disponible):
    """iPhone con Krece (Plata o más): va por la Línea disponible, tipo 'Otros'."""
    if not nivel or disponible is None:
        falta = ("su nivel y su Línea disponible" if not nivel and disponible is None
                 else "su nivel" if not nivel else "su Línea disponible")
        return [f"  El iPhone SÍ tiene precio. Con Krece el iPhone se paga con la 'Línea "
                f"disponible' (el monto grande de la app, lo que le queda libre; no el Límite "
                f"ni el de Celulares). Falta {falta}. Pídeselo en una frase. "
                f"NO respondas DERIVAR_PRECIO."]
    if float(disponible) <= 0:
        return ["  No le queda Línea disponible libre (la tiene usada en otras compras): "
                "por Krece no aplica ahora. Dile eso con amabilidad."]
    try:
        plazos = precios.plazos_krece(nivel, "otros")
    except (ValueError, KeyError):
        return [f"  El nivel '{nivel}' de Krece no es válido. Pídele que confirme: "
                f"Plata, Oro o Platino."]
    salida = []
    for plazo in plazos:
        k = precios.krece(p, nivel, plazo, linea=disponible, tipo="otros")
        if not k.get("aplica"):
            continue
        extra = " (inicial subida por su Línea disponible)" if k["topado_por_linea"] else ""
        salida.append(f"  Krece {plazo} cuotas: inicial ${k['inicial']} "
                      f"+ {plazo} x ${k['monto_cuota']}{extra}")
    return salida or ["  Su Línea disponible no alcanza para este iPhone por Krece."]


def dato_faltante(canal, perfil):
    """Pregunta para pedir el dato que falta para cotizar, o None."""
    if canal == "krece" and not perfil.get("nivel_cliente"):
        return ("Para cotizarte con Krece, ¿qué nivel tienes? (Azul, Plata, Oro o Platino) "
                "Lo ves en la app tocando tus puntos, arriba a la derecha 📲")
    if canal == "krece" and not perfil.get("linea_krece"):
        return ("Para darte las cuotas con Krece solo me falta el monto que te aparece "
                "debajo del ícono *Celulares* en la app (no el de Línea disponible). "
                "¿Cuánto es? 📲")
    if canal == "cashea" and not perfil.get("nivel_cliente"):
        return ("Para cotizarte con Cashea, ¿en qué nivel estás? "
                "Va del 1 Semilla al 6 Araguaney 😊")
    return None


# ── Armado de precios para el prompt de celulares ────────────────────────────

def bloque_equipo(equipos, canal, perfil):
    """
    Texto con los precios SOLO del canal que corresponde.
    Es lo único que ve el modelo: nunca el catálogo completo.
    """
    if not equipos:
        return "No se encontró ningún equipo con lo que escribió el cliente."

    lineas = []
    for eq in equipos[:4]:
        p = eq["precio_paralelo"]
        entrega = "disponible ya" if eq["inmediato"] else "llega en 24 a 48 horas"
        lineas.append(f"\n▸ {nombre_completo(eq)} — {entrega}")

        if canal == "krece":
            nivel = perfil.get("nivel_cliente")
            linea = perfil.get("linea_krece")
            if eq["marca"].lower() in MARCAS_NO_KRECE:
                lineas.append("  Esta marca NO se vende por Krece por ahora (Krece no la "
                              "aprueba). NO des cuotas de Krece: díselo con amabilidad en una "
                              "frase y ofrécele otra marca de la LISTA CORTA por Krece; si "
                              "prefiere este equipo, se lo puede llevar de contado o con Cashea.")
                continue
            if eq["marca"].lower() == "iphone" and nivel != "azul":
                lineas.extend(lineas_iphone_krece(p, nivel, perfil.get("_linea_disponible")))
                continue
            if not nivel or not linea:
                if not nivel and not linea:
                    falta = "su nivel y su línea aprobada"
                elif not nivel:
                    falta = f"su nivel (ya sabes que su línea es ${float(linea):.0f})"
                elif perfil.get("_nivel_recien"):
                    falta = (f"su línea aprobada. Te ACABA de decir su nivel: empieza con "
                             f"'¡Perfecto, nivel {nivel.capitalize()}!' (nunca digas que ya "
                             f"lo tenías anotado) y luego pídele la línea")
                else:
                    falta = (f"su línea aprobada (su nivel {nivel} ya está anotado: no se lo "
                             f"pidas ni lo menciones)")
                lineas.append(f"  El equipo SÍ tiene precio, pero para cotizar Krece falta "
                              f"{falta}. Pídeselo en una frase. NO respondas DERIVAR_PRECIO.")
                continue
            if eq["marca"].lower() == "iphone" and nivel == "azul":
                if perfil.get("_krece_supuesto"):
                    lineas.append("  Los iPhone requieren Krece Plata o superior y tú SUPUSISTE "
                                  "Azul: NO des cuotas de Krece; pregúntale su nivel y su Línea "
                                  "disponible (el monto grande de la app).")
                else:
                    lineas.append("  Los iPhone NO aplican con Krece Azul (requieren Plata o "
                                  "superior). NO des cuotas de Krece para este equipo: "
                                  "para el iPhone ofrécele Cashea (regla IPHONE CON KRECE AZUL).")
                continue
            hubo = False
            promo = promo_krece(eq, nivel)
            try:
                plazos = precios.plazos_krece(nivel, promo=promo)
                precios.plazos_krece(nivel)   # valida el nivel también con promo
            except (ValueError, KeyError):
                lineas.append(f"  El nivel '{nivel}' de Krece no es válido. "
                              f"Pídele que confirme: Azul, Plata, Oro o Platino.")
                continue
            if promo:
                lineas.append(f"  🔥 PROMO KRECE hasta el {promo['hasta']:%d/%m} "
                              f"(dilo como dato, sin presionar)")
            for plazo in plazos:
                k = precios.krece(p, nivel, plazo, linea=linea, promo=promo)
                if not k.get("aplica"):
                    continue
                hubo = True
                extra = " (inicial ajustada a su línea)" if k["topado_por_linea"] else ""
                inicial = "sin inicial" if k["inicial"] == 0 else f"inicial ${k['inicial']}"
                lineas.append(f"  Krece {plazo} cuotas: {inicial} "
                              f"+ {plazo} x ${k['monto_cuota']}{extra}")
            if promo and hubo:
                # Para responder "¿y después de la promo?" sin DERIVAR_PRECIO falso
                try:
                    cuotas_n, inicial_n = [], None
                    for pz in precios.plazos_krece(nivel):
                        kn = precios.krece(p, nivel, pz, linea=linea)
                        if kn.get("aplica"):
                            inicial_n = kn["inicial"]
                            cuotas_n.append(f"{pz} x ${kn['monto_cuota']}")
                    if cuotas_n:
                        lineas.append(
                            f"  Después de la promo (desde el "
                            f"{promo['hasta'] + timedelta(days=1):%d/%m}): inicial "
                            f"${inicial_n} + " + " / ".join(cuotas_n)
                            + " (dilo solo si pregunta por el precio sin promo)")
                except (ValueError, KeyError):
                    pass
            if not hubo:
                lineas.append("  Este equipo supera la línea aprobada del cliente. "
                              "No aplica para Krece.")

        elif canal == "cashea":
            nivel = perfil.get("nivel_cliente")
            if not nivel:
                lineas.append("  El equipo SÍ tiene precio, pero falta el nivel de Cashea "
                              "del cliente. Pídeselo. NO respondas DERIVAR_PRECIO.")
                continue
            try:
                c = precios.cashea(p, nivel)
                lineas.append(f"  Cashea: inicial ${c['inicial']} + 3 x ${c['monto_cuota']}")
            except ValueError:
                lineas.append(f"  El nivel '{nivel}' no es válido para Cashea. "
                              f"Pídele que confirme: 1 Semilla, 2 Raíz, 3 Hoja, "
                              f"4 Tronco, 5 Árbol o 6 Araguaney.")

        elif canal == "creditienda" and eq["marca"].lower() == "iphone":
            lineas.append("  Los iPhone NO se venden por CrediTienda. Ofrécele "
                          "contado, Cashea o Krece (nivel Plata o superior).")

        elif canal == "creditienda":
            moneda = perfil.get("nivel_cliente")
            variantes = [moneda] if moneda in ("divisas", "bs") else ["divisas", "bs"]
            for m in variantes:
                c = precios.creditienda(p, m)
                etiqueta = "en divisas" if m == "divisas" else "en bolívares"
                lineas.append(f"  CrediTienda {etiqueta}: inicial ${c['inicial']} "
                              f"+ 4 x ${c['monto_cuota']}")
                if m == "bs":
                    tasa = obtener_tasa_bcv()
                    if tasa:
                        ini_bs = round(c['inicial'] * tasa)
                        cuota_bs = round(c['monto_cuota'] * tasa)
                        lineas.append(f"  [solo si pide el monto en Bs] inicial Bs {ini_bs:,} "
                                      f"+ 4 x Bs {cuota_bs:,}, a la tasa BCV de hoy")

        else:  # contado o canal sin definir
            tasa = obtener_tasa_bcv()
            bcv = precios.precio_bcv(p)
            lineas.append(f"  En divisas (Zelle, USDT, efectivo): ${int(p)}")
            if tasa:
                bs = precios.precio_bolivares(p, tasa)
                bs_txt = f"{bs:,}".replace(",", ".")
                tasa_txt = f"{tasa:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                lineas.append(f"  En bolívares a tasa BCV: ${bcv}, que son Bs {bs_txt} "
                              f"(tasa BCV de hoy: Bs {tasa_txt}). Escríbelo SIEMPRE así: "
                              f"'${bcv} a tasa BCV (Bs {bs_txt})'. Nunca pongas 'Bs' "
                              f"delante del monto en dólares.")
            else:
                lineas.append(f"  En bolívares a tasa BCV: ${bcv}. La tasa no está "
                              f"disponible ahora: no des el monto en Bs.")

        if eq.get("camara") or eq.get("bateria"):
            lineas.append(f"  [solo si las pide] Cámara {eq.get('camara','-')} · "
                          f"Batería {eq.get('bateria','-')} · RAM {eq.get('ram','-')}GB")
        if eq.get("foto"):
            lineas.append("  Foto: disponible")
        else:
            lineas.append("  Foto: SIN FOTO por ahora")

    return "\n".join(lineas)


def pide_foto(texto):
    """'Puedes enviarme una foto', 'cómo se ve', 'me lo muestras': pide ver el equipo."""
    t = "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                if unicodedata.category(c) != "Mn")
    if re.search(r"\b(para|de|buenas?|mejores?) (tomar |sacar |hacer )?fotos?\b", t):
        return False   # habla de la cámara, no pide ver el equipo
    return re.search(r"\bfotos?\b|\bfotito|\bimagen|\bimagenes\b|\bpics?\b|\bfotografia"
                     r"|como se ve|me lo (muestras|mandas|envias|pasas)"
                     r"|lo puedo ver|puedo verlo|quiero verlo", t) is not None


def pide_ambas(texto):
    """'Ambas', 'anbas', 'los dos', 'cualquiera': responde a '¿contado o financiamiento?'"""
    t = "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                if unicodedata.category(c) != "Mn")
    return (len(t.split()) <= 4 and re.search(
        r"\ba[mn]b[ao]s\b|\b(los|las) (dos|2)\b|\bcualquiera\b", t) is not None)


def es_no_corto(canal, perfil, body):
    """'No', 'no tengo', 'todavía no' cuando se le preguntó si tiene Cashea."""
    return bool(canal == "cashea" and not perfil.get("nivel_cliente")
                and re.fullmatch(r"\s*(no|nop|no tengo|no lo tengo|no la tengo|"
                                 r"todav[ií]a no|a[uú]n no)\s*[.!]*\s*", body.lower()))


SIN_EQUIPO_PALABRAS = {
    "gracias", "grax", "graciass", "muchas", "mil", "ok", "okay", "oki", "okey",
    "listo", "perfecto", "dale", "vale", "excelente", "bueno", "bien", "genial",
    "claro", "ah", "aja", "ya", "entiendo", "entendido", "buenos", "buenas", "dias",
    "tardes", "noches", "saludos", "cordiales", "hola", "amigo", "amiga", "pana",
    "estan", "ubicado", "ubicados", "ubicada", "ubicadas", "donde", "y",
    "esta", "todo"}


def es_mensaje_sin_equipo(texto):
    """'Gracias', 'buenos días', 'ok están ubicados': no nombra ningún equipo.
    Solo da True si TODAS las palabras son de cortesía, saludo o ubicación."""
    t = "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                if unicodedata.category(c) != "Mn")
    palabras = re.sub(r"[^a-zñ ]", " ", t).split()
    return (bool(palabras) and len(palabras) <= 8
            and all(p in SIN_EQUIPO_PALABRAS for p in palabras))


def texto_iphone_cashea(equipos, tipo):
    """Respuesta fija al cliente Krece Azul que pide iPhone: se le ofrece Cashea."""
    texto = ""
    if tipo == "recomendacion":
        nombres = "\n".join(f"• {nombre_completo(e)}" for e in equipos[:4])
        texto = f"Ese modelo no lo tenemos disponible 😕 pero tenemos estos iPhone:\n{nombres}\n\n"
    return texto + ("Con Krece nivel Azul el iPhone todavía no se habilita 🙏 pero te lo "
                    "puedes llevar con *Cashea* en 3 cuotas.\n\n"
                    "¿Tienes Cashea? Si es así, dime tu nivel: 1 Semilla, 2 Raíz, 3 Hoja, "
                    "4 Tronco, 5 Árbol o 6 Araguaney.\n"
                    "Si no lo tienes no hay problema: en la tienda te hacemos el registro "
                    "en el momento 😊")


def equipos_de_interes(modelo_interes):
    """Los equipos guardados en modelo_interes (nombres unidos con ' / ')."""
    from sheets_celulares import _cargar
    nombres = [n.strip() for n in str(modelo_interes).split(" / ")]
    equipos = [eq for eq in _cargar()
               if nombre_completo(eq) in nombres
               and eq["precio_verificado"] and eq["precio_paralelo"]]
    return ordenar_equipos(equipos) if equipos else []


def sin_iphone(perfil):
    """True si al cliente no se le muestran iPhone: Krece Azul o CrediTienda."""
    if perfil.get("canal_pago") == "creditienda":
        return True
    return (perfil.get("canal_pago") == "krece"
            and perfil.get("nivel_cliente") == "azul")


def es_krece(perfil):
    return (perfil or {}).get("canal_pago") == "krece"


def filtros_listas(perfil):
    """Lo que no se le muestra al cliente en las listas: iPhone (Krece Azul o
    CrediTienda) y las marcas que Krece no aprueba (Logic y ZTE)."""
    perfil = perfil or {}
    return {"excluir_iphone": sin_iphone(perfil),
            "excluir_marcas": MARCAS_NO_KRECE if es_krece(perfil) else ()}


PIDE_PROMO = re.compile(r"promo|oferta|descuento", re.IGNORECASE)


def bloque_promos(perfil, texto=""):
    """Promos de Krece vigentes con entrega inmediata. Solo se arma si el
    cliente es Krece o pregunta por promos (así no se gastan tokens de más)."""
    if not es_krece(perfil) and not PIDE_PROMO.search(texto or ""):
        return ""
    equipos = equipos_en_promo(MARCAS_NO_KRECE)
    if not equipos:
        return "  No hay promociones activas ahora."
    nivel = perfil.get("nivel_cliente") if es_krece(perfil) else None
    grupos = {}
    for eq in equipos:
        p = promo_krece(eq)
        grupos.setdefault(p["nombre"], (p, []))[1].append(eq)
    salida = []
    for p, eqs in grupos.values():
        nombres = ", ".join(nombre_completo(e) for e in eqs)   # sin precio de contado: confunde
        condiciones = []
        for niv in ("azul", "plata", "oro", "platino"):
            c = p["niveles"].get(niv)
            if c and (not nivel or niv == nivel):
                ini = "sin inicial" if c["inicial"] == 0 else f"{c['inicial'] * 100:.0f}% de inicial"
                condiciones.append(f"{niv.capitalize()}: {ini}, hasta {max(c['factores'])} cuotas")
        salida.append(f"  🔥 Hasta el {p['hasta']:%d/%m}: {nombres}. " + "; ".join(condiciones))
    return "\n".join(salida)


def inicial_corta(eq, canal, perfil):
    """Una línea corta con lo que paga de entrada. None si no aplica."""
    p = eq["precio_paralelo"]
    try:
        if canal == "krece":
            if eq["marca"].lower() in MARCAS_NO_KRECE:
                return None
            nivel, linea = perfil.get("nivel_cliente"), perfil.get("linea_krece")
            tipo = "celular"
            if eq["marca"].lower() == "iphone":   # iPhone: va por la Línea disponible
                linea, tipo = perfil.get("_linea_disponible"), "otros"
                if not nivel or not linea:
                    return None
            if not nivel or not linea:
                return f"de contado ${int(p)}"
            promo = promo_krece(eq, nivel) if tipo == "celular" else None
            plazo = precios.plazos_krece(nivel, tipo, promo)[-1]   # el plazo más largo = cuota más baja
            k = precios.krece(p, nivel, plazo, linea=linea, tipo=tipo, promo=promo)
            if not k.get("aplica"):
                return None
            inicial = "sin inicial" if k["inicial"] == 0 else f"inicial ${k['inicial']}"
            texto = f"Krece: {inicial} + {plazo} x ${k['monto_cuota']}"
            if promo:
                texto = f"🔥 promo Krece hasta el {promo['hasta']:%d/%m} — {texto}"
            return texto
        if canal == "cashea":
            nivel = perfil.get("nivel_cliente")
            if not nivel:
                return f"de contado ${int(p)}"
            c = precios.cashea(p, nivel)
            return f"Cashea: inicial ${c['inicial']} + 3 x ${c['monto_cuota']}"
        if canal == "creditienda":
            c = precios.creditienda(p, "divisas")
            return f"CrediTienda: inicial ${c['inicial']} + 4 x ${c['monto_cuota']}"
    except (ValueError, KeyError):
        return f"de contado ${int(p)}"
    return f"de contado ${int(p)}"


def bloque_lista_corta(canal, perfil, cantidad=8):
    """Los equipos más baratos, una línea cada uno, en el canal del cliente."""
    salida = []
    for eq in listar_mas_baratos(cantidad + 4, **filtros_listas(perfil)):
        texto = inicial_corta(eq, canal, perfil)
        if texto:
            salida.append(f"  • {nombre_completo(eq)} — {texto}")
        if len(salida) >= cantidad:
            break
    return "\n".join(salida) or "  No hay equipos disponibles en este momento."


def bloque_rangos(perfil=None):
    """Tres equipos por rango de precio, para cuando no dice modelo."""
    equipos = listar_por_rango(**filtros_listas(perfil), con_promo=es_krece(perfil))
    if not equipos:
        return "  No hay equipos disponibles en este momento."
    etiquetas = ["Económico", "Intermedio", "Gama alta"]
    salida = []
    for i, eq in enumerate(equipos):
        etiqueta = etiquetas[i] if i < len(etiquetas) else ""
        canal = (perfil or {}).get("canal_pago")
        texto = (inicial_corta(eq, canal, perfil) if canal
                 else f"desde ${int(eq['precio_paralelo'])} en divisas")
        salida.append(f"  {etiqueta}: {nombre_completo(eq)}"
                      + (f" — {texto}" if texto else ""))
    return "\n".join(salida)


# ── Extracción de palabras clave ──────────────────────────────────────────────

def extraer_palabras_clave(mensaje):
    normalizado = normalizar_texto(mensaje)
    palabras = [p for p in normalizado.split() if p not in PALABRAS_IGNORAR]
    print(f"Mensaje normalizado: '{normalizado}' | Palabras clave: {palabras}")
    return palabras, normalizado


def expandir_abreviacion(mensaje):
    """Expande abreviaciones de 1, 2 o 3 palabras"""
    palabras_temp, _ = extraer_palabras_clave(mensaje)

    # Buscar combinacion de 3 palabras con espacio
    for i in range(len(palabras_temp) - 2):
        combinacion = " ".join(palabras_temp[i:i+3])
        if combinacion in MODELOS_ABREVIADOS:
            expandido = MODELOS_ABREVIADOS[combinacion]
            print(f"Abreviación expandida: '{mensaje}' → '{expandido}'")
            return expandido

    # Buscar combinacion de 2 palabras con espacio
    for i in range(len(palabras_temp) - 1):
        combinacion = " ".join(palabras_temp[i:i+2])
        if combinacion in MODELOS_ABREVIADOS:
            expandido = MODELOS_ABREVIADOS[combinacion]
            print(f"Abreviación expandida: '{mensaje}' → '{expandido}'")
            return expandido

    # Buscar combinacion de 2 palabras unidas sin espacio
    # Solo si el mensaje NO tiene marca conocida (evita reemplazar "Infinix note 12" por "Redmi Note 12")
    tiene_marca = any(p in MARCAS_CONOCIDAS for p in palabras_temp)
    if not tiene_marca:
        for i in range(len(palabras_temp) - 1):
            combinacion = palabras_temp[i] + palabras_temp[i+1]
            if combinacion in MODELOS_ABREVIADOS:
                expandido = MODELOS_ABREVIADOS[combinacion]
                print(f"Abreviación expandida: '{mensaje}' → '{expandido}'")
                return expandido

    # Buscar palabra sola (solo si no hay marca conocida)
    if not tiene_marca:
        for palabra in palabras_temp:
            if palabra in MODELOS_ABREVIADOS:
                expandido = MODELOS_ABREVIADOS[palabra]
                print(f"Abreviación expandida: '{mensaje}' → '{expandido}'")
                return expandido

    return mensaje


def dividir_mensaje(mensaje):
    separadores = r'\by también\b|\by\b|,'
    partes = re.split(separadores, mensaje, flags=re.IGNORECASE)
    partes = [p.strip() for p in partes if p.strip()]
    print(f"Referencias divididas: {partes}")
    return partes if len(partes) > 1 else None


# ── Búsqueda exacta ───────────────────────────────────────────────────────────

def buscar_exacto(todos, palabras_clave):
    """Búsqueda exacta — todas las palabras clave deben estar en el nombre."""
    if not palabras_clave:
        return []

    encontrados = []
    for producto in todos:
        nombre_norm = normalizar_texto(producto['name'])
        palabras_nombre = [p for p in nombre_norm.split() if p not in PALABRAS_IGNORAR]

        if not all(p in palabras_nombre for p in palabras_clave):
            continue

        palabras_extra = len(palabras_nombre) - len(palabras_clave)
        if palabras_extra > 0:
            continue

        encontrados.append(producto)
        print(f"Match exacto: {producto['name']}")

    return encontrados


def buscar_sin_marca(todos, palabras_clave, max_resultados=5):
    """
    Búsqueda secundaria — quita las marcas del nombre del producto
    y busca solo por modelo. Retorna hasta max_resultados productos
    con stock > 0 primero, luego sin stock.
    """
    if not palabras_clave:
        return []

    # Si las palabras clave ya incluyen una marca, no aplicar esta búsqueda
    if any(p in MARCAS_CONOCIDAS for p in palabras_clave):
        return []

    encontrados_con_stock = []
    encontrados_sin_stock = []

    for producto in todos:
        nombre_norm = normalizar_texto(producto['name'])
        # Quitar marcas conocidas del nombre del producto
        palabras_nombre = [p for p in nombre_norm.split()
                          if p not in PALABRAS_IGNORAR and p not in MARCAS_CONOCIDAS]

        if not palabras_nombre:
            continue

        if not all(p in palabras_nombre for p in palabras_clave):
            continue

        palabras_extra = len(palabras_nombre) - len(palabras_clave)
        if palabras_extra > 0:
            continue

        stock = int(producto['qty_available'])
        print(f"Match sin marca: {producto['name']} | Stock: {stock}")

        if stock > 0:
            encontrados_con_stock.append(producto)
        else:
            encontrados_sin_stock.append(producto)

    # Combinar con stock primero
    resultado = encontrados_con_stock + encontrados_sin_stock
    return resultado[:max_resultados]


def buscar_sin_espacios(todos, palabras_clave):
    if not palabras_clave:
        return []

    clave_junta = "".join(palabras_clave)
    encontrados_con_stock = []
    encontrados_sin_stock = []

    for producto in todos:
        nombre_norm = normalizar_texto(producto['name'])
        palabras_nombre = [p for p in nombre_norm.split() if p not in MARCAS_CONOCIDAS and p not in PALABRAS_IGNORAR]
        nombre_junto = "".join(palabras_nombre)

        if clave_junta == nombre_junto:
            stock = int(producto['qty_available'])
            print(f"Match sin espacios: {producto['name']} | Stock: {stock}")
            if stock > 0:
                encontrados_con_stock.append(producto)
            else:
                encontrados_sin_stock.append(producto)

    return (encontrados_con_stock + encontrados_sin_stock)[:5]


def buscar_compatible_exacto(todos, palabras_clave, excluir_nombre=None):
    if not palabras_clave:
        return None

    compatibles_con_stock = []
    compatibles_sin_stock = []

    for producto in todos:
        # No ofrecer el mismo modelo que el cliente pidió como su propio compatible
        if excluir_nombre and producto['name'].strip().lower() == excluir_nombre.strip().lower():
            continue
        notas = limpiar_html(producto.get('description') or "")
        if 'COMPATIBLE:' not in notas.upper():
            continue

        for linea in notas.split('\n'):
            if 'COMPATIBLE:' not in linea.upper():
                continue

            compatible_texto = linea.replace('COMPATIBLE:', '').replace('Compatible:', '').strip().lower()

            for modelo_odoo in compatible_texto.split(','):
                modelo_norm = normalizar_texto(modelo_odoo.strip())
                palabras_modelo = [p for p in modelo_norm.split() if p not in PALABRAS_IGNORAR]

                if not palabras_modelo:
                    continue

                palabras_extra = len(palabras_modelo) - len(palabras_clave)
                modelo_junto = "".join([p for p in palabras_modelo if p not in MARCAS_CONOCIDAS])
                clave_junta = "".join(palabras_clave)
                if (all(p in palabras_modelo for p in palabras_clave) and palabras_extra <= 0) or clave_junta == modelo_junto:
                    producto_copia = dict(producto)
                    producto_copia['_compatible_con'] = modelo_odoo.strip()
                    stock = int(producto['qty_available'])
                    if stock > 0:
                        compatibles_con_stock.append(producto_copia)
                    else:
                        compatibles_sin_stock.append(producto_copia)

    if compatibles_con_stock:
        return compatibles_con_stock[0]
    elif compatibles_sin_stock:
        return compatibles_sin_stock[0]
    return None


def buscar_similares(todos, palabras_clave, max_resultados=5):
    if not palabras_clave:
        return []

    similares = []
    nombres_vistos = set()

    for producto in todos:
        nombre_norm = normalizar_texto(producto['name'])
        palabras_nombre = nombre_norm.split()
        coincidencias = sum(1 for p in palabras_clave if p in palabras_nombre)
        if coincidencias > 0:
            if producto['name'] not in nombres_vistos:
                nombres_vistos.add(producto['name'])
                similares.append((coincidencias, producto['name'], int(producto['qty_available']), False, ""))

    for producto in todos:
        notas = limpiar_html(producto.get('description') or "")
        if 'COMPATIBLE:' not in notas.upper():
            continue

        for linea in notas.split('\n'):
            if 'COMPATIBLE:' not in linea.upper():
                continue

            compatible_texto = linea.replace('COMPATIBLE:', '').replace('Compatible:', '').strip()

            for modelo_odoo in compatible_texto.split(','):
                modelo_norm = normalizar_texto(modelo_odoo.strip())
                palabras_modelo = modelo_norm.split()

                if not palabras_modelo:
                    continue

                coincidencias = sum(1 for p in palabras_clave if p in palabras_modelo)
                if coincidencias > 0:
                    display = modelo_odoo.strip()
                    if display not in nombres_vistos:
                        nombres_vistos.add(display)
                        similares.append((coincidencias, producto['name'], int(producto['qty_available']), True, modelo_odoo.strip()))

    similares.sort(key=lambda x: x[0], reverse=True)
    return similares[:max_resultados]


# ── Rescate con IA: interpreta el modelo cuando la búsqueda normal falla ──────
INTERPRETAR_MODELO_ACTIVO = True  # poner en False para apagar el rescate IA

def _lista_repuestos(todos):
    """Nombres de la categoría REPUESTOS y de sus modelos COMPATIBLE:, para anclar la IA."""
    nombres = []
    vistos = set()
    for p in todos:
        categ = p.get('categ_id')
        categ_nombre = categ[1] if isinstance(categ, (list, tuple)) and len(categ) > 1 else ""
        if "REPUESTOS" in str(categ_nombre).upper():
            candidatos = [p.get('name', '')]
            notas = limpiar_html(p.get('description') or "")
            for parte in re.split(r'compatible:', notas, flags=re.IGNORECASE)[1:]:
                candidatos.extend(parte.split(','))
            for nombre in candidatos:
                nombre = nombre.strip()
                if nombre and nombre.lower() not in vistos:
                    vistos.add(nombre.lower())
                    nombres.append(nombre)
    return nombres


def interpretar_modelo(mensaje, todos):
    """
    Se llama SOLO cuando la búsqueda normal ya falló.
    Le pasa a la IA la lista real de repuestos y le pide elegir UNO o NINGUNO.
    Devuelve el nombre exacto de un repuesto real, o None. Nunca inventa.
    """
    if not INTERPRETAR_MODELO_ACTIVO:
        return None

    repuestos = _lista_repuestos(todos)
    if not repuestos:
        return None

    lista_texto = "\n".join(repuestos)
    prompt = f"""Un cliente escribió este mensaje buscando un repuesto de celular:
"{mensaje}"

El mensaje puede tener errores de escritura, letras omitidas, espacios mal puestos
o palabras de relleno (saludos, "disponibilidad", "precio", etc.).

Esta es la lista EXACTA de repuestos disponibles:
{lista_texto}

Tu tarea: corregir SOLO errores de escritura y encontrar el MISMO modelo en la lista.
NO busques el modelo "más parecido". Busca el MISMO modelo bien escrito.

REGLAS ESTRICTAS:
- Solo corrige errores de tipeo: letras cambiadas, letras omitidas, espacios mal
  puestos, marca mal escrita. El modelo debe ser EL MISMO, solo bien escrito.
- Los numeros y sufijos del modelo son SAGRADOS. NO los cambies nunca.
  * "A04E" NO es "A04S" (sufijo distinto) -> NINGUNO
  * "G31" NO es "G30" (numero distinto) -> NINGUNO
  * "Spark Go 1" NO es "Spark 6 Go" (modelo distinto) -> NINGUNO
  * "Note 12" NO es "Note 13" -> NINGUNO
- Si el modelo que pide el cliente NO esta en la lista con ese MISMO numero y
  sufijo, responde: NINGUNO. Aunque haya uno parecido, responde NINGUNO.
- Si el cliente solo saluda o no busca repuesto, responde: NINGUNO.
- Ante CUALQUIER duda, responde: NINGUNO. Es mejor decir NINGUNO que dar un
  modelo equivocado.
- NUNCA inventes un modelo que no este en la lista.
- NO expliques. Solo el nombre exacto de la lista, o NINGUNO."""

    try:
        response = client.messages.create(
            model="claude-haiku-5-5",
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}],
        )
        resultado = texto_respuesta(response).strip()
        registrar_uso("repuestos-rescate-ia", response)
        if not resultado or resultado.upper() == "NINGUNO":
            return None
        # Validación dura 1: solo aceptar si coincide con un repuesto real
        nombre_valido = None
        for nombre in repuestos:
            if resultado.lower() == nombre.lower():
                nombre_valido = nombre
                break
        if not nombre_valido:
            print(f"IA devolvio algo fuera de la lista, descartado: '{resultado}'")
            return None
        # Validacion dura 2: los tokens con numero del cliente deben estar en el modelo elegido
        norm_cliente = normalizar_texto(mensaje)
        norm_modelo = normalizar_texto(nombre_valido)
        tokens_cliente = set(re.findall(r'[a-z]*\d[a-z0-9]*', norm_cliente))
        tokens_modelo = set(re.findall(r'[a-z]*\d[a-z0-9]*', norm_modelo))
        faltantes = tokens_cliente - tokens_modelo
        if faltantes:
            print(f"Freno IA (numero): '{mensaje}' pidio {tokens_cliente}, IA dio '{nombre_valido}' con {tokens_modelo}. Descartado.")
            return None
        # Validacion dura 3: las palabras-variante (pro, plus, max...) deben coincidir exacto
        var_cliente = {p for p in norm_cliente.split() if p in PALABRAS_VARIANTE}
        var_modelo = {p for p in norm_modelo.split() if p in PALABRAS_VARIANTE}
        if var_cliente != var_modelo:
            print(f"Freno IA (variante): '{mensaje}' tiene {var_cliente or 'ninguna'}, IA dio '{nombre_valido}' con {var_modelo or 'ninguna'}. Descartado.")
            return None
        return nombre_valido
    except Exception as e:
        print(f"Error en interpretar_modelo: {e}")
        return None


def buscar_referencia(todos, ref):
    ref = expandir_abreviacion(ref)
    palabras_clave, _ = extraer_palabras_clave(ref)
    if not palabras_clave:
        return None, None, None

    # Paso 1: búsqueda exacta normal
    encontrados = buscar_exacto(todos, palabras_clave)
    if encontrados:
        con_stock = [p for p in encontrados if int(p['qty_available']) > 0]
        if con_stock:
            return encontrados, None, None
        else:
            compatible = buscar_compatible_exacto(todos, palabras_clave)
            if compatible:
                return None, compatible, None
            return encontrados, None, None

    # Paso 2: búsqueda sin marca
    encontrados_sin_marca = buscar_sin_marca(todos, palabras_clave)
    if encontrados_sin_marca:
        print(f"Resultados sin marca: {[p['name'] for p in encontrados_sin_marca]}")
        con_stock = [p for p in encontrados_sin_marca if int(p['qty_available']) > 0]
        if con_stock:
            return encontrados_sin_marca, None, None
        else:
            compatible = buscar_compatible_exacto(todos, palabras_clave)
            if compatible:
                return None, compatible, None
            return encontrados_sin_marca, None, None

    # Paso 3: búsqueda sin espacios
    encontrados_sin_espacios = buscar_sin_espacios(todos, palabras_clave)
    if encontrados_sin_espacios:
        print(f"Resultados sin espacios: {[p['name'] for p in encontrados_sin_espacios]}")
        con_stock = [p for p in encontrados_sin_espacios if int(p['qty_available']) > 0]
        if con_stock:
            return encontrados_sin_espacios, None, None
        else:
            compatible = buscar_compatible_exacto(todos, palabras_clave)
            if compatible:
                return None, compatible, None
            return encontrados_sin_espacios, None, None

    # Paso 4: buscar compatible
    compatible = buscar_compatible_exacto(todos, palabras_clave)
    if compatible:
        return None, compatible, None

    # Paso 5: similares
    similares = buscar_similares(todos, palabras_clave)
    return None, None, similares


def consultar_odoo(mensaje):
    try:
        import socket
        socket.setdefaulttimeout(15)
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
        print(f"Odoo UID: {uid}")
        if not uid:
            return None, None, None

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        todos = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            'product.product', 'search_read',
            [[]],
            {'fields': ['name', 'list_price', 'qty_available', 'description', 'categ_id'], 'limit': 500}
        )
        print(f"Total productos en Odoo: {len(todos)}")

        referencias = dividir_mensaje(mensaje)

        if referencias:
            productos_todos = []
            compatibles_todos = []
            sugerencias_todas = []

            for ref in referencias:
                print(f"Buscando referencia: '{ref}'")
                prods, comp, sims = buscar_referencia(todos, ref)
                if prods:
                    for p in prods:
                        p['_referencia'] = ref
                    productos_todos.extend(prods)
                if comp:
                    comp['_referencia'] = ref
                    compatibles_todos.append(comp)
                if sims:
                    sugerencias_todas.append((ref, sims))

            return (
                productos_todos if productos_todos else None,
                compatibles_todos if compatibles_todos else None,
                sugerencias_todas if sugerencias_todas else None
            )

        mensaje = expandir_abreviacion(mensaje)
        palabras_clave, _ = extraer_palabras_clave(mensaje)
        if not palabras_clave:
            return None, None, None

        # Paso 1: búsqueda exacta normal
        encontrados = buscar_exacto(todos, palabras_clave)
        if encontrados:
            con_stock = [p for p in encontrados if int(p['qty_available']) > 0]
            if con_stock:
                return encontrados, None, None
            else:
                print("Sin stock, buscando compatible...")
                compatible = buscar_compatible_exacto(todos, palabras_clave, excluir_nombre=encontrados[0]['name'])
                if compatible:
                    return None, compatible, None
                return encontrados, None, None

        # Paso 2: búsqueda sin marca
        encontrados_sin_marca = buscar_sin_marca(todos, palabras_clave)
        if encontrados_sin_marca:
            print(f"Resultados sin marca: {[p['name'] for p in encontrados_sin_marca]}")
            con_stock = [p for p in encontrados_sin_marca if int(p['qty_available']) > 0]
            if con_stock:
                return encontrados_sin_marca, None, None
            else:
                compatible = buscar_compatible_exacto(todos, palabras_clave)
                if compatible:
                    return None, compatible, None
                return encontrados_sin_marca, None, None

        # Paso 3: búsqueda sin espacios
        encontrados_sin_espacios = buscar_sin_espacios(todos, palabras_clave)
        if encontrados_sin_espacios:
            print(f"Resultados sin espacios: {[p['name'] for p in encontrados_sin_espacios]}")
            con_stock = [p for p in encontrados_sin_espacios if int(p['qty_available']) > 0]
            if con_stock:
                return encontrados_sin_espacios, None, None
            else:
                compatible = buscar_compatible_exacto(todos, palabras_clave)
                if compatible:
                    return None, compatible, None
                return encontrados_sin_espacios, None, None

        # Paso 4: buscar compatible
        compatible = buscar_compatible_exacto(todos, palabras_clave)
        if compatible:
            return None, compatible, None

        # Paso 5: similares
        similares = buscar_similares(todos, palabras_clave)

        # Paso 6: rescate con IA (solo si todo lo anterior fallo)
        modelo_ia = interpretar_modelo(mensaje, todos)
        if modelo_ia:
            print(f"Rescate IA: '{mensaje}' -> '{modelo_ia}'")
            prods_ia, comp_ia, _ = buscar_referencia(todos, modelo_ia)
            if prods_ia or comp_ia:
                return prods_ia, comp_ia, None

        return None, None, similares

    except Exception as e:
        print(f"Error consultando Odoo: {e}")
        return None, None, None

    # ── Mensajería y asesores ─────────────────────────────────────────────────────

def send_whapi_message(to: str, text: str):
    url = f"{WHAPI_API_URL}/messages/text"
    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}", "Content-Type": "application/json"}
    try:
        requests.post(url, json={"to": to, "body": text}, headers=headers, timeout=10).raise_for_status()
    except Exception as e:
        print(f"Error enviando mensaje Whapi: {e}")


# ── Fotos de celulares: se descargan una vez y se guardan en memoria ──────────
FOTOS_MAX_EN_MEMORIA = 30
_fotos_cache = {}   # url -> foto lista para Whapi


def _descargar_foto(url_imagen):
    """Trae la foto desde Postimages y la deja lista para Whapi."""
    if url_imagen in _fotos_cache:
        return _fotos_cache[url_imagen]
    resp = requests.get(url_imagen, timeout=20)
    resp.raise_for_status()
    mime = resp.headers.get("Content-Type", "image/png").split(";")[0]
    if not mime.startswith("image/"):
        raise ValueError(f"El enlace no es una imagen directa ({mime})")
    b64 = base64.standard_b64encode(resp.content).decode("utf-8")
    media = f"data:{mime};base64,{b64}"
    if len(_fotos_cache) >= FOTOS_MAX_EN_MEMORIA:
        _fotos_cache.pop(next(iter(_fotos_cache)))   # borra la más vieja
    _fotos_cache[url_imagen] = media
    print(f"📷 Foto descargada ({len(resp.content) // 1024} KB): {url_imagen}")
    return media


def send_whapi_image(to: str, url_imagen: str, caption: str = ""):
    # 1) Descargar la foto. Si esto falla, sabemos que no va a llegar.
    try:
        media = _descargar_foto(url_imagen)
    except Exception as e:
        print(f"Error descargando foto {url_imagen}: {e}")
        send_whapi_message(to, "No pude enviarte la foto en este momento, pero "
                               "puedes pasar por la tienda a verlo en persona 😊")
        return

    # 2) Enviarla ya lista. Sin reintento: si Whapi tarda, igual la entrega.
    url = f"{WHAPI_API_URL}/messages/image"
    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": to, "media": media}
    if caption:
        payload["caption"] = caption
    try:
        requests.post(url, json=payload, headers=headers, timeout=60).raise_for_status()
    except requests.exceptions.Timeout:
        print(f"⏳ Whapi tardó más de 60 s con la foto (puede llegar tarde): {url_imagen}")
    except Exception as e:
        print(f"Error enviando imagen Whapi: {e}")


def notificar_asesor(asesor: str, tema: str, numero_cliente: str):
    numero_formateado = "+" + numero_cliente.replace("@s.whatsapp.net", "")
    send_whapi_message(asesor, f"🔔 *Mensaje pendiente*\nUn cliente está esperando respuesta sobre *{tema}*.\nNúmero: {numero_formateado}")


def notificar_stock_bajo(numero_cliente: str, producto: str, stock: int):
    numero_formateado = "+" + numero_cliente.replace("@s.whatsapp.net", "")
    send_whapi_message(ASESOR_STOCK, f"⚠️ *Stock bajo - Cliente interesado*\nProducto: *{producto}*\nStock: {stock} unidad(es)\nCliente: {numero_formateado}\n\nEl cliente confirmó que quiere apartar esta pantalla.")


def send_whapi_ubicacion(to: str):
    # 1) Foto de la fachada con la dirección escrita en el mismo mensaje
    texto = f"📍 *{TIENDA_NOMBRE}*\n{TIENDA_DIRECCION}\nSanta Teresa del Tuy"
    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}", "Content-Type": "application/json"}
    foto_ok = False
    if FOTO_FACHADA_URL:
        try:
            media = _descargar_foto(FOTO_FACHADA_URL)
            requests.post(f"{WHAPI_API_URL}/messages/image", headers=headers, timeout=60,
                          json={"to": to, "media": media, "caption": texto}).raise_for_status()
            foto_ok = True
        except requests.exceptions.Timeout:
            foto_ok = True   # puede llegar tarde, pero llega
        except Exception as e:
            print(f"Error enviando foto fachada: {e}")
    if not foto_ok:
        send_whapi_message(to, texto)   # sin foto, al menos la dirección escrita
    # 2) Pin de ubicación
    try:
        requests.post(f"{WHAPI_API_URL}/messages/location", headers=headers, timeout=10,
                      json={"to": to, "latitude": TIENDA_LAT, "longitude": TIENDA_LNG,
                            "name": TIENDA_NOMBRE, "address": TIENDA_DIRECCION}).raise_for_status()
    except Exception as e:
        print(f"Error enviando ubicación Whapi: {e}")


def asesor_disponible():
    hora = datetime.now(pytz.timezone("America/Caracas")).hour
    return 6 <= hora < 22


def msg_asesor(de_dia, de_noche):
    return de_dia if asesor_disponible() else de_noche


def equipos_aviso(modelo_interes):
    """modelo_interes sin repetir el modelo: 'Tecno Spark Go 3 64GB/128GB · 4GB RAM'."""
    eqs = equipos_de_interes(modelo_interes)
    if not eqs:
        return modelo_interes
    grupos = {}
    for eq in eqs:
        grupos.setdefault((eq["marca"], eq["modelo"]), []).append(eq)
    lineas = []
    for (marca, modelo), lista in grupos.items():
        alm = "/".join(dict.fromkeys(e["almacenamiento"] for e in lista if e["almacenamiento"]))
        ram = "/".join(dict.fromkeys(str(e["ram"]) for e in lista if e["ram"]))
        t = f"{marca} {modelo} {alm}".strip()
        if ram:
            t += f" · {ram}GB RAM"
        lineas.append(t)
    return " / ".join(lineas)


def notificar_intencion_compra(numero_cliente, perfil, equipos, online=False):
    numero = "+" + numero_cliente.replace("@s.whatsapp.net", "")
    modelo = perfil.get("modelo_interes") or (
        nombre_completo(equipos[0]) if equipos else "sin especificar")
    if perfil.get("modelo_interes"):
        modelo = equipos_aviso(perfil["modelo_interes"])
    partes = ["🟢 *INTENCIÓN DE COMPRA*", f"Cliente: {numero}", f"Equipo: {modelo}"]
    if perfil.get("canal_pago"):
        partes.append(f"Paga con: {perfil['canal_pago']}")
    if perfil.get("nivel_cliente"):
        partes.append(f"Nivel: {perfil['nivel_cliente']}")
    if perfil.get("linea_krece"):
        partes.append(f"Línea celulares: ${float(perfil['linea_krece']):.0f}")
    if perfil.get("_linea_disponible") is not None:
        partes.append(f"Línea disponible: ${float(perfil['_linea_disponible']):.0f}")
    if online:
        partes.append("Compra: 🌐 ONLINE")
    send_whapi_message(ASESOR_CELULARES, "\n".join(partes))


def notificar_precio_sin_verificar(numero_cliente, texto_cliente):
    numero = "+" + numero_cliente.replace("@s.whatsapp.net", "")
    send_whapi_message(
        ASESOR_CELULARES,
        f"🟡 *Equipo sin precio verificado*\nCliente: {numero}\n"
        f"Preguntó: {texto_cliente[:120]}\n\n"
        f"El bot no cotizó. Responde tú o actualiza el precio en el catálogo."
    )
def leer_captura_krece(image_data):
    """Descarga la imagen y le pide a Claude que extraiga nivel y línea aprobada."""
    url = (image_data.get("link") or image_data.get("url")
          or image_data.get("body") or image_data.get("mediaUrl"))
    if not url:
        file_id = image_data.get("id")
        if file_id:
            headers = {"Authorization": f"Bearer {WHAPI_TOKEN}"}
            r = requests.get(f"{WHAPI_API_URL}/media/{file_id}", headers=headers, timeout=30)
            if r.ok:
                url = r.json().get("url") or r.json().get("link")
    if not url:
        return None, None, True, None

    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    mime = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
    b64 = base64.standard_b64encode(resp.content).decode("utf-8")

    prompt = """Si esta imagen es una captura de la app de Krece, extrae:
- "nivel": Azul, Plata, Oro o Platino. En la pantalla de niveles toma SOLO el que diga
  "Este es tu nivel". Si no aparece escrito, sácalo de los puntos del cliente (ej. "0 pts"):
  menos de 121 azul, 121 a 240 plata, 241 a 499 oro, 500 o más platino.
- "linea": el monto en dólares debajo del ícono "Celulares". NO uses el de "Línea disponible".
- "disponible": el monto grande de "Línea disponible" (lo que le queda libre).
Responde SOLO con JSON: {"es_krece": true, "nivel": "azul", "linea": 300, "disponible": 40}
Si no puedes leer alguno de los dos con certeza, pon null en ese campo.
Si la imagen NO es una captura de Krece, responde {"es_krece": false}"""

    r = client.messages.create(
        model=MODELO_CELULARES, max_tokens=200,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": prompt},
        ]}],
    )
    registrar_uso("captura-krece", r)
    try:
        datos = json.loads(texto_respuesta(r))
        return (datos.get("nivel"), datos.get("linea"), bool(datos.get("es_krece")),
                datos.get("disponible"))
    except Exception as e:
        print(f"Error leyendo captura Krece: {e}")
        return None, None, True, None

# ── Cortesía en el flujo de repuestos (técnicos) ──────────────────────────────

CORTESIA_CAMINO = {"voy", "camino", "salgo", "saliendo", "llego", "llegando", "voyy"}
CORTESIA_GRACIAS = {"gracias", "grax", "graciass", "agradecido"}
CORTESIA_DESPEDIDA = {"chao", "chau", "vemos", "bendiciones", "luego"}
CORTESIA_CONFIRMA = {"ok", "okay", "oki", "okey", "listo", "perfecto", "dale", "vale",
                     "excelente", "bueno", "va", "fino", "chevere"}
CORTESIA_RELLENO = {"ya", "para", "alla", "aya", "ahi", "ahorita", "ahora", "en", "de",
                    "muchas", "mil", "nos", "hasta", "amigo", "amiga", "hermano", "pana",
                    "mi", "bro", "jefe", "ok", "y", "si", "sí", "pues", "entonces"}
CORTESIA_EMOJIS = ("👍", "👌", "🙏", "🤝", "✅", "💪")


def respuesta_cortesia_repuestos(body):
    """Si el técnico SOLO avisa que viene, agradece, confirma o se despide,
    devuelve una respuesta fija (sin IA). Si es un pedido, devuelve None."""
    texto = unicodedata.normalize("NFKD", (body or "").lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-zñ ]", " ", texto)
    palabras = texto.split()

    if not palabras:
        return "¡A la orden! 👍" if any(e in (body or "") for e in CORTESIA_EMOJIS) else None
    if len(palabras) > 6:
        return None

    nucleo = CORTESIA_CAMINO | CORTESIA_GRACIAS | CORTESIA_DESPEDIDA | CORTESIA_CONFIRMA
    if not all(p in nucleo or p in CORTESIA_RELLENO for p in palabras):
        return None
    if not any(p in nucleo for p in palabras):
        return None

    if any(p in CORTESIA_CAMINO for p in palabras):
        if esta_abierto():
            tz = pytz.timezone("America/Caracas")
            cierre = "2:00pm" if datetime.now(tz).weekday() == 6 else "5:30pm"
            return f"¡Perfecto! Te esperamos en *Cell Center 4620* 😊 Hoy estamos hasta las {cierre}."
        return ("¡Perfecto! Ahora mismo estamos cerrados 🕐 Abrimos lunes a sábado 8:30am-5:30pm, "
                "domingos y feriados 9:00am-2:00pm.")
    return "¡A la orden! 👍"


# ── System prompt ─────────────────────────────────────────────────────────────

def get_system_prompt():
    estado_tienda = "ABIERTA" if esta_abierto() else "CERRADA"
    tz = pytz.timezone("America/Caracas")
    ahora = datetime.now(tz)
    es_domingo = ahora.weekday() == 6
    horario_hoy = "9:00am a 2:00pm" if es_domingo else "8:30am a 5:30pm"
    dia_hoy = "domingo" if es_domingo else "lunes a sábado"
    return f"""Eres un vendedor directo de Cell Center 4620, tienda de celulares en Venezuela. Solo vendemos PANTALLAS y repuestos de celulares.
La tienda está actualmente: {estado_tienda}
Hoy es {dia_hoy}. El horario de HOY es {horario_hoy}. Usa SOLO este horario cuando te pregunten a qué hora cierran hoy.

REGLA PRINCIPAL: Cuando el inventario muestre productos con stock mayor a 0, SIEMPRE da el precio. NUNCA digas que no está disponible si hay stock. Si solo nombra un modelo, asume que es para pantalla (ver regla 2 para celulares).

1. PANTALLAS: Si el inventario muestra productos disponibles, responde con precio en USD y bolívares. Formato EXACTO:
✅ *Nombre producto*: $XX USD / Bs. XX,XXX

Donde $XX es el precio en USD y Bs. XX,XXX es el precio en bolívares calculado con tasa euro. NO hay precio intermedio.

MÚLTIPLES PRODUCTOS: Si el inventario muestra varios productos, responde en lista:
✅ *Modelo*: $12 USD / Bs. 8,243
✅ *Modelo*: $13 USD / Bs. 8,856

COMPATIBILIDADES: Si el inventario dice "PRODUCTOS COMPATIBLES":
- Si el stock es mayor a 0, responde:
"Tenemos una pantalla compatible para ese modelo 👍
✅ *[nombre exacto del producto]*: $XX USD / Bs. XX,XXX"
- Si el stock es 0, responde solo:
"No tenemos disponible para ese modelo en este momento."

STOCK 1 o 2: da el precio y avisa que queda muy poco. Varía las frases:
"Por cierto, este modelo está casi agotado. ¿Lo reservamos?"
"Nos queda muy poco de este modelo. ¿Lo apartamos?"
"Existencia muy limitada. ¿Lo separamos para ti?"
"Está por agotarse. ¿Lo guardamos?"

STOCK 3 o más: solo da el precio sin comentarios.
STOCK 0: solo di que no está disponible. NUNCA sugieras contactar, reservar o esperar stock.

2. CELULARES: SOLO si dice claramente que quiere comprar un teléfono completo (ej. "quiero comprar un celular", "¿venden teléfonos?", "el Redmi 15C completo para mí") responde exactamente: "DERIVAR_CELULARES". Si dice pantalla, display o un repuesto, o solo nombra un modelo, es pantalla. Si menciona teléfono, celular o equipo pero no está claro si busca el teléfono o la pantalla, responde exactamente: "CONFIRMAR_CELULAR"
3. SERVICIO TÉCNICO o reparaciones: responde exactamente: "DERIVAR_TECNICO"
4. ACCESORIOS: responde exactamente: "DERIVAR_ACCESORIOS"

5. HORARIO o si estamos abiertos:
- ABIERTA: confirmamos que sí estamos. Horario: lunes a sábado 8:30am-5:30pm, domingos y feriados 9:00am-2:00pm.
- CERRADA: avisa que estamos cerrados pero puedes responder preguntas. Varía las frases:
  "En este momento estamos cerrados, pero aquí estoy para ayudarte. Horario: lunes a sábado 8:30am-5:30pm, domingos y feriados 9:00am-2:00pm."
  "La tienda está cerrada, aunque puedo ayudarte con precios. Abrimos lunes a sábado 8:30am-5:30pm, domingos y feriados 9:00am-2:00pm."

6. OTROS TEMAS: responde amablemente que solo manejas productos y servicios de Cell Center 4620.

Responde siempre corto y directo. Muestra el nombre exacto del producto como aparece en el inventario."

7. LISTA DE PRECIOS: Si el cliente pide lista de precios, catálogo o lista, responde: "Por el momento no estamos enviando lista de precios, pero puedes preguntarme por el modelo que necesitas y te respondo de inmediato 😊"

8. PAGO o datos bancarios: Si el cliente pregunta cómo pagar, pide datos de pago, menciona pago móvil, transferencia o cualquier intención de pagar, responde exactamente: "DATOS_PAGO"""

def get_system_prompt_celulares(info_equipo, perfil, rangos, lista_corta, promos=""):
    """Prompt del vendedor de celulares. Recibe solo el equipo consultado,
    nunca el catálogo completo."""
    tz = pytz.timezone("America/Caracas")
    ahora = datetime.now(tz)
    es_domingo = ahora.weekday() == 6
    horario_hoy = "9:00am a 2:00pm" if es_domingo else "8:30am a 5:30pm"
    dia_hoy = "domingo" if es_domingo else "lunes a sábado"
    estado_tienda = "ABIERTA" if esta_abierto() else "CERRADA"
    linea_apertura = ""
    if estado_tienda == "CERRADA":
        hora = ahora.hour + ahora.minute / 60
        if hora < (9 if es_domingo else 8.5):
            proxima = "HOY a las " + ("9:00am" if es_domingo else "8:30am")
        elif ahora.weekday() == 5:
            proxima = "MAÑANA domingo de 9:00am a 2:00pm"
        else:
            proxima = "MAÑANA de 8:30am a 5:30pm"
        linea_apertura = (f"\nPróxima apertura: {proxima}. Si lo invitas a venir, "
                          f"usa SOLO esto (nunca digas 'hoy abrimos' si es mañana).")

    canal = perfil.get("canal_pago")
    canal_extra = perfil.get("canal_extra")
    if canal and canal_extra:
        recordatorio = (f"El cliente quiere comparar *{canal.upper()}* y "
                        f"*{canal_extra.upper()}*. Cuando des precios, muéstrale "
                        f"los dos. No menciones otros medios salvo que él los pida.")
    elif canal:
        recordatorio = (f"El cliente viene por *{canal.upper()}*. "
                        f"Háblale SOLO de ese medio, salvo que él pida otro.")
    else:
        recordatorio = "Todavía no sabes por qué medio quiere pagar."

    datos = []
    if perfil.get("nivel_cliente"):
        if canal == "creditienda":
            datos.append("paga en bolívares" if perfil["nivel_cliente"] == "bs"
                         else "paga en divisas")
        else:
            datos.append(f"nivel {perfil['nivel_cliente']}"
                         + (" (te lo acaba de decir)" if perfil.get("_nivel_recien") else ""))
    if perfil.get("linea_krece"):
        datos.append(f"línea para celulares ${float(perfil['linea_krece']):.0f}")
    if perfil.get("_linea_disponible") is not None:
        datos.append(f"Línea disponible ${float(perfil['_linea_disponible']):.0f} (para iPhone)")
    if perfil.get("nombre"):
        datos.append(f"se llama {perfil['nombre']}")
    if perfil.get("modelo_interes"):
        datos.append(f"le interesa el {perfil['modelo_interes']}")
    conocidos = ("Ya sabes de él: " + ", ".join(datos) +
                 ". No se lo vuelvas a preguntar.") if datos else ""
    if perfil.get("_pedir_nombre"):
        conocidos += " No sabes su nombre (regla NOMBRE)."
    if perfil.get("_nivel1_krece"):
        conocidos += (" OJO: antes te dijo 'nivel 1' y ahora eligió Krece: su nivel de Krece es AZUL. "
                      "Cotiza con los montos de EQUIPO CONSULTADO (calculados con Azul y $300) "
                      "sin pedirle el nivel; al final dile que si la línea de Celulares es distinta, "
                      "te lo diga y recalculas.")
    if perfil.get("_nivel_sin_canal"):
        conocidos += (" OJO: te dijo 'nivel 1' pero no si es de Cashea o de Krece "
                      "(los dos tienen nivel 1). Pregúntale solo eso, con naturalidad: "
                      "'¿Ese nivel es de Cashea o de Krece? 😊'. No des precios todavía "
                      "y no respondas DERIVAR_PRECIO.")
    if perfil.get("_krece_supuesto"):
        conocidos += (" OJO: él NO te dio nivel ni línea; tú supusiste Azul con $300 en Celulares. "
                      "Cotiza con los montos de EQUIPO CONSULTADO sin pedirle esos datos. "
                      "Nunca digas 'tu nivel' ni 'tu línea': di que es calculado con "
                      "Azul y $300 por ser lo más común.")
    if perfil.get("_linea_dudosa"):
        conocidos += (f" OJO: te dio ${float(perfil['_linea_dudosa']):.0f} de línea; eso parece "
                      "su 'Línea disponible' (la general), no la de celulares. Explícaselo en "
                      "una frase y pídele el monto que le aparece debajo del ícono 'Celulares'.")

    fijo = """Eres el asistente de ventas de Cell Center 4620, ...

El estado de la tienda (abierta o cerrada) y el horario de HOY están en DATOS DE ESTA CONVERSACIÓN, al final.
Horario general: lunes a sábado 8:30am-5:30pm · domingos y feriados 9:00am-2:00pm

CÓMO HABLAS
Eres venezolano, cálido y directo. Hablas como un vendedor de confianza, no como un robot.
Emojis con moderación, uno o dos por mensaje.
Máximo 4 o 5 líneas por respuesta. La gente lee WhatsApp con el pulgar.
UNA sola pregunta por mensaje. Nunca dos seguidas.
No tienes que terminar cada mensaje con una pregunta. Conversa, no persigas.
Si pregunta algo que no es del equipo (qué venden, dónde quedan, horario),
respóndele eso y ya: no lo regreses al equipo ni le preguntes si seguimos.
Si pregunta qué venden o si solo venden teléfonos: celulares, accesorios y
servicio técnico (reparaciones y pantallas). Dilo en una frase, sin números
de teléfono, a menos que pida algo de accesorios o servicio técnico.
Nunca uses frases de presión: "¿seguimos con…?", "los equipos rotan rápido",
"si puedes ir pronto, mejor", "¿cuándo pasas?".
Si te preguntan si eres una persona o un robot, di con naturalidad que eres
el asistente virtual de la tienda.
NUNCA uses "hermano", "hermana", "amigo", "pana" ni tratamientos parecidos.
Habla como se habla en Venezuela. NUNCA uses expresiones de otros países como
"te late", "¿qué te late?", "chido", "padre" (por bueno), "órale", "platicar",
"vale" (a la española) o "bacano". En vez de "¿te late?" di "¿te gusta?",
"¿qué te parece?" o "¿te interesa?".
No repitas lo que ya dijiste en el mensaje anterior.
Si sabes cómo se llama el cliente, úsalo con naturalidad al saludar, al cerrar o al dar una buena noticia; NO lo repitas en cada mensaje.
Regla NOMBRE: si los datos dicen que no sabes su nombre, pregúntalo UNA sola vez, casual, en un mensaje donde no hagas otra pregunta, y solo cuando el cliente agradece ("gracias"), dice "lo voy a pensar" o se despide. NUNCA tras un simple "ok" o "está bien", ni mientras está eligiendo equipo o esperando un precio: "Por cierto, ¿con quién tengo el gusto? 😊", y agrega al final PREGUNTE_NOMBRE. Nunca insistas. Si el cliente te dice su nombre, agrega al final NOMBRE:<su nombre>.
No menciones el horario ni si estamos abiertos o cerrados a menos que el cliente
lo pregunte, o que quiera pasar hoy y ya esté cerrado. No lo digas al saludar.

LO PRIMERO: ENTENDER QUÉ QUIERE
- Compra de celular -> lo atiendes tú
- Servicio técnico, reparación, pantallas o repuestos -> responde exactamente: DERIVAR_TECNICO
- Accesorios o cualquier otra cosa -> responde exactamente: DERIVAR_OTROS
Si ya dijo lo que quiere en su primer mensaje, no se lo preguntes de nuevo.

RESPETA EL CANAL QUE ELIGIÓ — regla más importante
El canal que eligió y lo que ya sabes de él están en DATOS DE ESTA CONVERSACIÓN, al final.
Si viene por Krece, le hablas SOLO de Krece: no menciones Cashea, CrediTienda ni contado.
Lo mismo al revés. Solo cambias de canal si él lo pide.

PRECIOS
NUNCA inventes un precio. Solo usas los montos que aparecen abajo en EQUIPO CONSULTADO.
Si no hay precio ahí PARA UN EQUIPO ESPECÍFICO que el cliente pidió, no lo
estimes ni lo deduzcas de otro modelo: responde exactamente DERIVAR_PRECIO.
DERIVAR_PRECIO es solo para eso. Si EQUIPO CONSULTADO dice que el cliente pidió
un modelo que NO tenemos, tampoco es DERIVAR_PRECIO: dile que ese no lo manejas
y ofrécele las alternativas con sus precios.
Si el cliente TODAVÍA no ha elegido equipo
(está empezando, o llegó con el mensaje predefinido de Krece), eso NO es un
caso de DERIVAR_PRECIO: ahí usas las 3 opciones de la sección "SI NO SABE QUÉ
QUIERE" de abajo, que sí tienen precio real y verificado.
Nunca uses la palabra "paralelo". Di "en divisas", "en efectivo" o "en dólares".

KRECE
No cotizas sin dos datos: su NIVEL (Azul, Plata, Oro o Platino) y su LÍNEA APROBADA.
Pídeselos juntos en una sola frase, y ofrécele que te los escriba o te mande captura de la app.
En la app nueva el nivel lo ve tocando sus puntos (arriba a la derecha), y su línea
para celulares es el monto debajo del ícono "Celulares". El monto grande de "Línea
disponible" es otra cosa (iPhone, recargas y préstamos): no es su línea para celulares.
Sin esos datos no das ningún número, ni aproximado.
SIEMPRE hay cuota inicial. Única excepción: una promo de Krece donde EQUIPO
CONSULTADO dice "sin inicial". La línea aprobada es solo un techo:
si el equipo la supera, la inicial sube — nunca la elimina. Usa EXACTAMENTE
los montos de inicial y cuota que aparecen en EQUIPO CONSULTADO. Salvo ese
"sin inicial", NUNCA digas que "no hace falta inicial" ni que el cliente
"no necesita poner inicial".
NUNCA digas cuántas cuotas son antes de tener el cálculo: varía entre 3 y 12.
Si el cliente te da nivel y línea pero todavía no dijo qué equipo quiere,
pregúntaselo: "¿Qué modelo tienes en mente?"
Los iPhone con Krece solo aplican de nivel Plata en adelante y se pagan con su
Línea disponible: si la inicial sale alta, explícale que es porque lo que le queda
libre en esa línea es menor que el equipo. Si el cliente es
nivel Azul y pregunta por un iPhone (regla IPHONE CON KRECE AZUL): dile que con
Krece Azul el iPhone todavía no se habilita, pero que se lo puede llevar con
*Cashea* en 3 cuotas. Pregúntale si tiene Cashea y, si lo tiene, su nivel
(1 Semilla, 2 Raíz, 3 Hoja, 4 Tronco, 5 Árbol o 6 Araguaney). Dile que si no lo
tiene no hay problema: en la tienda le hacemos el registro en el momento (esa
frase va SIEMPRE). Si EQUIPO CONSULTADO dice que ese modelo no lo tenemos, díselo
primero y nombra las alternativas. No le hables de subir de nivel.
Si el cliente llega hablando de Krece sin darte nivel ni línea (con el mensaje
predefinido o con otras palabras), NO le preguntes nivel y línea (salvo que pida
un iPhone: ahí pregúntale su nivel y su Línea disponible, porque el iPhone requiere Plata o superior): asume que es
Azul con $300 de línea (es el caso del 95% de los que llegan así) y muéstrale
de una vez tres opciones —gama baja, media y alta— cotizadas con esos datos.
Al final, deja abierta la corrección: "Si tu nivel o el monto de *Celulares*
en tu app es distinto, dímelo y te recalculo."
Nunca ofrezcas ni sugieras un iPhone a un cliente Azul, ni siquiera como
opción a mostrar. Si él mismo lo pide, ahí sí explícale la restricción.
Logic y ZTE NO se venden por Krece (Krece no los aprueba): nunca los ofrezcas
a un cliente Krece. Si él los pide, sigue lo que dice EQUIPO CONSULTADO.

PROMOS DE KRECE
Solo existen las que aparecen en PROMOS KRECE (DATOS DE ESTA CONVERSACIÓN).
Nunca inventes una promo ni llames promo a un equipo que no esté ahí.
Si un equipo de las TRES OPCIONES, la LISTA CORTA o EQUIPO CONSULTADO trae 🔥,
dile que está en promo de Krece y hasta qué fecha, como dato y sin presionar.
Si dice "sin inicial", dilo así: "sin inicial mientras la promo esté vigente (hasta el dd/mm)". No digas "la única vez".
Si es cliente Krece y pidió un equipo que NO está en promo, cotízalo normal y,
UNA sola vez en toda la conversación, menciona en una frase el equipo de PROMOS
KRECE de precio más parecido. Si ya lo mencionaste antes, no lo repitas.
Si pregunta si hay promociones u ofertas: cuéntale las de PROMOS KRECE (son
solo por Krece) y, si no sabes si tiene Krece, pregúntaselo. Si no hay
PROMOS KRECE, dile que por ahora no hay promociones activas.
Si el cliente dice que no tiene Krece o nunca lo ha usado, NO es un obstáculo:
dile que en la tienda le hacemos el registro en el momento, y que debe ser
mayor de edad y traer su cédula laminada. Dale los montos de EQUIPO CONSULTADO
diciendo SIEMPRE que son aproximados: el monto real lo da Krece cuando quede
registrado.
Si ese cliente sin cuenta pide un iPhone, NO le digas que "tiene" nivel Azul
ni que "suba de nivel": dile que al registrarse empieza en nivel Azul y que el
iPhone se habilita desde nivel Plata, y ofrécele Cashea como en la regla de abajo.

CASHEA
Pregunta primero el NIVEL del cliente: 1 Semilla, 2 Raíz, 3 Hoja, 4 Tronco, 5 Árbol o 6 Araguaney. Al preguntarlo nombra SIEMPRE los 6 con su número, sin saltarte ninguno. Usa SOLO esos nombres. Sin nivel no hay precio. Son 3 cuotas.
Si el cliente dice que no tiene Cashea o nunca lo ha usado, NO es un obstáculo
y NO le preguntes el nivel: dile que en la tienda le hacemos el registro, y que
debe ser mayor de edad y traer su cédula laminada. Dale los montos de EQUIPO
CONSULTADO diciendo SIEMPRE que son aproximados: el monto real lo da Cashea
cuando quede registrado.

CREDITIENDA
No necesita nivel. Pregunta si paga en divisas o en bolívares, porque el precio cambia.
Da la inicial y las cuotas en dólares. Los montos en bolívares solo si el cliente
los pide, aclarando que son a la tasa BCV de hoy.
Los iPhone NO se venden por CrediTienda. Nunca ofrezcas ni sugieras un iPhone a
un cliente CrediTienda. Si él lo pide, dile: "Los iPhone no están disponibles por
CrediTienda 🙏 Puedes llevártelo de contado, con Cashea o con Krece (nivel Plata
o superior). ¿Cuál te interesa?"

CUANDO NO SABES CÓMO VA A PAGAR
Si el cliente pregunta por un equipo y no ha dicho su medio de pago, confirma
la disponibilidad y pregúntale suavemente en qué modalidad le interesa verlo.
Nunca lo presiones ni le pidas que decida ya.
Ejemplo: "Sí, ese lo tenemos. ¿Te lo muestro de contado o prefieres verlo con
financiamiento?"
Pregúntalo UNA sola vez. Si vuelve a pedir el precio sin decir el medio,
dale el de contado y ofrécele verlo con financiamiento.

CONTADO
En divisas es el precio más bajo. Menciónalo como ventaja cuando muestre interés.
Cuando des el precio de contado, di EXACTAMENTE "de contado" o "en divisas",
nada más. Ejemplo correcto: "De contado está en $430."
Ejemplo INCORRECTO que nunca debes escribir: "en divisas (Zelle, USDT o efectivo)".
Solo nombra Zelle o USDT si el cliente los escribe primero en su mensaje.

CUÁNDO ENTREGAS
Si el equipo dice "disponible ya", dilo. Si dice "llega en 24 a 48 horas", dilo también con naturalidad.
Nunca prometas entrega inmediata de algo que no la tiene.

SI FALTA UN DATO
Si EQUIPO CONSULTADO dice que falta un dato del cliente (nivel o línea), pídeselo
en una frase. Nunca digas que ya tienes todo ni inventes cómo se reparten la
inicial o las cuotas: esos números solo salen de EQUIPO CONSULTADO.

ESPECIFICACIONES Y FOTOS
Solo hablas de cámara, batería o RAM si el cliente pregunta. No las enumeres de entrada.
Si pide ver el equipo, mira el campo "Foto" en EQUIPO CONSULTADO:
- Si dice "disponible", incluye el marcador [FOTO] acompañado de una frase. Nunca lo mandes solo.
- Si dice "SIN FOTO", NO pongas [FOTO]. Dile con naturalidad que por ahora no
  tienes la foto de ese modelo y que puede pasar por la tienda a verlo en persona.
  Ejemplo: "Por ahora no tengo la foto de ese modelo, pero puedes pasar por la
  tienda a verlo en persona 😊"
Si preguntan por la batería de un iPhone, aclara que te refieres al NIVEL DE
SALUD de la batería (no a la capacidad en mAh), que varía entre 80% y 98%
según el equipo. Para saber el porcentaje exacto de un equipo en particular,
debe pasar por la tienda a verificarlo.

CONDICIÓN DE LOS EQUIPOS
Todos los equipos son nuevos y sellados, EXCEPTO los iPhone 15 o anteriores
(15, 14, 13, 12, 11, XR, SE y sus versiones Pro, Pro Max, Plus o mini): esos
no vienen sellados. Los iPhone 16 en adelante sí son nuevos y sellados.
No lo menciones por tu cuenta: solo si el cliente pregunta si son nuevos,
sellados o de segunda. NUNCA digas que todos son nuevos o sellados, y nunca
uses las palabras "usado" ni "seminuevo".
Si pregunta por un iPhone 15 o anterior, respóndele así (con el modelo que
consulta; este mensaje puede pasar de 5 líneas):
"El iPhone 13 Pro Max no viene sellado 📱 Es un equipo de una persona que se
cambió a un modelo más nuevo, algo muy común con los iPhone. Está en muy buenas
condiciones y lo verificamos nosotros: pantalla, cámaras, Face ID y
funcionamiento en general. La salud de la batería varía entre 80% y 98% según
el equipo, y puedes pasar por la tienda a revisarlo antes de llevártelo.
Los iPhone 16 en adelante sí son nuevos y sellados."
Si pregunta en general, sin un iPhone de por medio, dile que los equipos son
nuevos y sellados, y que solo los iPhone 15 o anteriores no vienen sellados.
Después sigue con una sola pregunta.

SI NO SABE QUÉ QUIERE
No mandes el catálogo completo. Muéstrale estas tres opciones y deja que se ubique:
(las TRES OPCIONES están en DATOS DE ESTA CONVERSACIÓN, al final)
Después pregúntale para qué lo va a usar.

SI PIDE EL CATÁLOGO O UNA LISTA
La primera vez, muéstrale las tres opciones de arriba. Si lo vuelve a pedir,
NO te niegues: mándale esta LISTA CORTA tal cual, una línea por equipo, y
pregúntale cuál le llama la atención:
(la LISTA CORTA está en DATOS DE ESTA CONVERSACIÓN, al final)
Si pide una marca en particular, muéstrale lo que haya de esa marca en
EQUIPO CONSULTADO.

MAYORES DE EDAD
Todas las ventas son solo para mayores de edad. No lo preguntes de entrada:
menciónalo SOLO al explicar el registro a quien dijo que no tiene Krece o Cashea.
Si ya tiene cuenta (o no dijo que no la tiene), NO le menciones requisitos,
registro ni cédula: solo que pase por la tienda.
Si el cliente dice que es menor de edad, dile con amabilidad que la compra
la debe hacer un adulto, por ejemplo su representante.

CERRAR
El objetivo NO es cerrar la venta por chat: la decisión es del cliente y se
toma en la tienda, viendo el equipo.
No invites a pasar en el primer mensaje. Espera a que muestre interés real
(pregunta precio de un modelo concreto, pide fotos, compara opciones).
Recién ahí, si EQUIPO CONSULTADO dice "disponible ya": "Si quieres pasar a verlo".
Si dice "llega en 24 a 48 horas", NO lo invites a verlo (no está en tienda):
"Este equipo lo pedimos y llega a la tienda en 24 a 48 horas 📦 ¿Quieres que te
lo apartemos? Un asesor te avisa apenas llegue para que pases a buscarlo."
Si dice que sí, es INTENCION_COMPRA.
Si pregunta dónde quedan o si son de algún lugar (ej. "¿son de Santa Teresa?"), escribe ENVIAR_UBICACION (el sistema le manda la foto de la tienda, la dirección y el mapa) y una frase corta que responda ("¡Sí, estamos en Santa Teresa del Tuy! 😊"). Si todavía no sabes qué busca, puedes preguntarle qué equipo le interesa. Si ya vio uno, no le preguntes cuándo va a ir. No digas que un vendedor lo espera ni te despidas: solo quería saber dónde quedan. Si en ese mismo mensaje pregunta otra cosa (por ejemplo si aceptan Krece o Cashea), respóndela en una frase corta junto con ENVIAR_UBICACION.
Si EQUIPO CONSULTADO trae cuotas de varios equipos, NO preguntes cuál quiere antes de dar números: da de cada uno, en una línea, la inicial y la cuota del plazo más largo, y al final pregunta cuál le gusta. Una sola pregunta por mensaje.
Si el equipo dice "disponible ya", NO ofrezcas apartarlo ni reservarlo (eso es solo para los que llegan en 24 a 48 horas) ni des a elegir entre reservar o verlo: basta con "Si quieres pasar a verlo".
Los montos de inicial y cuotas los calculas tú con el nivel y la línea del cliente: nunca digas que se los dio Krece, Cashea o la app.
Las cuotas de Krece y CrediTienda se pagan cada 15 días, y las de Cashea cada 2 semanas (14 días). Al hablar de plazos escribe siempre "N cuotas cada 15 días" (Cashea: "cada 2 semanas"); no uses meses ni otra unidad de tiempo para los plazos.
Cuando detectes intención de compra (dice que lo quiere, pregunta cómo apartarlo,
confirma que va a ir, o ya dijo que viene a comprar hoy o mañana y ahora muestra
interés), responde exactamente: INTENCION_COMPRA
Pero si todavía no le has dado el precio en el medio de pago que acaba de
nombrar (ej. "lo quiero sacar por Krece" y solo vio contado), dale primero
ese cálculo. Todavía no es INTENCION_COMPRA.
Si solo pregunta si se puede comprar online o a distancia, todavía NO decidió:
respóndele que sí, que los detalles se los da un asesor al momento de la compra
y que solo debe estar atento a sus indicaciones. Eso NO es INTENCION_COMPRA.
Si ya dice que lo quiere comprar online, responde exactamente: INTENCION_COMPRA_ONLINE

OBJECIONES
"Está caro", "muy alta la inicial", "¿no hay otras opciones?", o pregunta si
hay iniciales más bajas -> NO insistas con el mismo equipo ni le repitas los
mismos planes, y no lo presiones. Bájale de gama: muéstrale 2 o 3 equipos
de la LISTA CORTA que tengan la inicial más baja que la que ya vio. Si le
dices que hay opciones más económicas, SIEMPRE las muestras en ese mismo
mensaje.
"Lo voy a pensar" u "otro día" -> con calidez: que con gusto, que aquí estás
cuando quiera. Sin preguntas, sin prisa y sin decir que los equipos se acaban.
"En otra tienda está más barato" -> garantía, soporte directo y financiamiento.
Nunca hables mal de la competencia.

LO QUE NUNCA HACES
- Inventar precios, modelos o disponibilidad
- Dar montos de Krece sin nivel y línea
- Dar montos de Cashea sin nivel
- Prometer entrega inmediata de algo que viene de proveedor
- Mandar la lista completa de equipos (la LISTA CORTA sí se puede)
- Decir que hay opciones más baratas sin mostrarlas
- Decir que solo tenemos los equipos que ya le mostraste
- Reusar precios o entrega de mensajes anteriores: usa SOLO lo que dice
  EQUIPO CONSULTADO ahora. Si el mismo modelo sale con otra RAM o
  almacenamiento, es otra versión con otro precio: nunca digas "el mismo"
- Desdecirte de un equipo que ya cotizaste ("me equivoqué", "ignora ese
  precio"): lo que dijiste antes sigue siendo válido aunque ahora hables de
  otro equipo; nunca lo retires
- Despedirte ("que descanses", "hasta luego") cuando el cliente solo saluda
  ("buenas noches", "buen día"): devuélvele el saludo y pregunta en qué le
  ayudas. Solo te despides si él dio las gracias o dijo que ya no necesita nada
- Usar la palabra "paralelo"

Si dice "la aplicación" o "la app" sin nombrarla, sigue con el canal que ya
tiene. Si todavía no tiene canal, pregúntale cuál aplicación usa.

"""

    texto_promos = (f"\nPROMOS KRECE (solo por Krece y con entrega inmediata)\n{promos}\n"
                    if promos else "")
    variable = f"""DATOS DE ESTA CONVERSACIÓN

TIENDA
La tienda está ahora: {estado_tienda}
Hoy es {dia_hoy}. El horario de HOY es {horario_hoy}.{linea_apertura}

CLIENTE
{recordatorio}
{conocidos}

TRES OPCIONES (para cuando no sabe qué quiere)
{rangos}

LISTA CORTA
{lista_corta}
{texto_promos}
EQUIPO CONSULTADO
{info_equipo}
"""

    return [
        # Reglas fijas: iguales para todos los clientes, se guardan 1 hora en caché
        {"type": "text", "text": fijo,
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        # Datos de este cliente y este mensaje: cambian en cada mensaje
        {"type": "text", "text": variable},
    ]


client = anthropic.Anthropic()

# ── Inicializar Google Sheets al arrancar ─────────────────────────────────────
inicializar_db()
inicializar_hoja_no_encontrados()

# ── Texto de la respuesta del modelo ──────────────────────────────────────────

def texto_respuesta(respuesta):
    """
    Devuelve el texto de la respuesta. Los modelos pueden anteponer bloques
    de razonamiento, así que no sirve tomar content[0] a ciegas.
    """
    partes = []
    for bloque in respuesta.content:
        if getattr(bloque, "type", None) == "text":
            partes.append(bloque.text)
        elif hasattr(bloque, "text") and not hasattr(bloque, "thinking"):
            partes.append(bloque.text)
    return "\n".join(partes).strip()

def registrar_uso(etiqueta, respuesta):
    """Imprime en el log cuántos tokens gastó cada llamada."""
    try:
        u = respuesta.usage
        print(f"💰 TOKENS [{etiqueta}] modelo={respuesta.model} "
              f"entrada={u.input_tokens} salida={u.output_tokens} "
              f"cache_leido={getattr(u, 'cache_read_input_tokens', 0) or 0} "
              f"cache_creado={getattr(u, 'cache_creation_input_tokens', 0) or 0}")
    except Exception as e:
        print(f"No se pudo leer el uso de tokens: {e}")


# ── Interpretación del pedido con IA ──────────────────────────────────────────

def interpretar_pedido(mensaje, historial_texto=""):
    """
    Única regla de búsqueda de celulares: Sonnet lee lo que escribió el
    cliente y elige del catálogo real. Nunca inventa un modelo.

    Devuelve (lista_de_equipos, tipo) donde tipo es:
      "exacto"        — es lo que pidió
      "recomendacion" — no tenemos lo que pidió, esto se parece
      "sin_precio"    — lo tenemos pero sin precio verificado
      "ninguno"       — no pide un equipo, o no hay nada que ofrecer
    """
    catalogo, catalogo_completo = catalogo_para_ia(mensaje)
    if not catalogo:
        return [], "ninguno", None

    # Parte fija: catálogo + reglas. Va primero para poder guardarla en caché.
    parte_fija = f"""Interpretas los pedidos de clientes de una tienda de celulares en Venezuela.
Los clientes escriben por WhatsApp.

Este es el catálogo real de la tienda. Cada línea es:
clave | precio | cámara | entrega
La clave es marca|modelo|almacenamiento|RAM en GB.

{catalogo}

Tu tarea: entender QUÉ QUIERE el cliente y elegir hasta 3 equipos del catálogo (hasta 4 si el cliente nombra 4 modelos).

Interpreta libremente. El cliente escribe rápido, con errores, abreviado o
sin saber el nombre exacto. Ejemplos de lo que debes entender:
- "el redmi 17 de 256" -> el Redmi 17 256GB
- "sansung a 07" -> el Samsung A07
- "algo bueno para fotos" -> equipos de gama media con buena cámara
- "el mas barato que tengas" -> el de menor precio
- "uno que no pase de 200" -> los que cuesten hasta $200
- "quiero un iphone" -> los iPhone que haya

REGLAS:
- Solo puedes devolver claves que estén EXACTAMENTE en el catálogo de arriba.
- Si pide un modelo conocido que NO está en el catálogo (por ejemplo un
  iPhone 13, un Samsung S24), elige 2 o 3 parecidos en precio y gama,
  empezando por el más cercano en precio; si hay uno igual o más barato,
  inclúyelo. Marca tipo "recomendacion".
- Si el modelo que pide no es conocido ni está en el catálogo, devuelve
  lista vacía y tipo "ninguno".
- Si lo que pide está en el catálogo pero dice SIN PRECIO, devuélvelo igual
  con tipo "sin_precio".
- Si solo saluda, pregunta por horario, ubicación, servicio técnico,
  reparación, pantallas o repuestos, o cualquier cosa que no sea elegir un
  celular, devuelve lista vacía y tipo "ninguno", aunque antes le
  interesara un modelo.
- Si el mensaje solo da nivel, línea aprobada o datos de pago, SIN que antes
  se estuviera hablando de un equipo concreto, devuelve lista vacía y tipo
  "ninguno". No elijas un equipo por tu cuenta basado en la línea.
- PERO si más abajo dice que en mensajes anteriores le interesaba un modelo, y
  ahora el cliente solo está dando su nivel o línea, devuelve ESE modelo con
  tipo "exacto". Está completando los datos para cotizar lo que ya pidió.
- Si nombra UN solo modelo, aunque sea a medias ("el tecno spark", "el logic"), de los
  que le interesaban antes, devuelve SOLO ese modelo con tipo
  "exacto", aunque en el mismo mensaje diga gracias, se despida o diga
  "cualquier cosa voy por ahí".
- Si pide ver el equipo ("¿tiene fotos?", "mándame la foto", "¿cómo se ve?")
  y antes le interesaba un modelo, NO es un criterio: devuelve ESE modelo
  con tipo "exacto". Solo es criterio si dice "para fotos" o "buena cámara".
- Si pide un criterio en vez de un modelo (fotos, juegos, batería, RAM,
  almacenamiento, presupuesto), elige hasta 3 que cumplan de TODO el
  catálogo, aunque antes le interesara otro modelo, y marca tipo "exacto".
- PERO si está comparando o eligiendo ENTRE los que ya le interesaban
  ("el que tenga más capacidad", "cuál de los dos tiene mejor batería",
  "el más barato", "el de 6 de RAM"), elige solo entre esos, con tipo
  "exacto". Devuelve UNO solo: si empatan en lo que pide, desempata por
  más RAM y luego por más almacenamiento.
- Si nombra DOS O MÁS modelos concretos en este mensaje (para compararlos o
  pedir que le recomienden uno), devuelve TODOS los que nombró (máximo 4) con
  tipo "exacto", aunque antes le interesaran otros. Esta regla manda sobre la
  de devolver UNO solo.
- Si pregunta si hay MÁS opciones ("¿solo esos?", "¿no tienes más?",
  "¿otras marcas?"), NO está eligiendo los que vio: lista vacía y tipo
  "mas_opciones". Si nombra una marca, es tipo "exacto".
CANAL DE PAGO: si en ESTE mensaje el cliente nombra Krece, Cashea o
CrediTienda, aunque lo escriba mal ("caschea", "kashea", "kreze",
"credi tieda"), pon ese canal en "canal": "krece", "cashea" o "creditienda".
Si no nombra ninguno de los tres, pon "canal": null.

En "nombrados" pon true solo cuando devuelves los modelos que el cliente nombró
(2 o más); en cualquier otro caso, false.

Responde SOLO con JSON, sin explicaciones ni markdown:
{{"claves": ["clave1", "clave2"], "tipo": "exacto", "canal": null, "nombrados": false}}"""

    # Parte variable: cambia en cada mensaje, va después de la caché.
    parte_variable = f'{historial_texto}\nMensaje del cliente por WhatsApp:\n"{mensaje}"'

    bloque_fijo = {"type": "text", "text": parte_fija}
    if catalogo_completo:
        # Solo el catálogo completo se guarda 1 hora en caché
        bloque_fijo["cache_control"] = {"type": "ephemeral", "ttl": "1h"}

    try:
        r = client.messages.create(
            model=MODELO_CELULARES,
            max_tokens=1500,
            extra_body={"output_config": {"effort": "low"}},
            messages=[{"role": "user", "content": [
                bloque_fijo,
                {"type": "text", "text": parte_variable},
            ]}],
        )
        texto = texto_respuesta(r)
        registrar_uso("celulares-interpretar", r)
        if not texto:
            print("La interpretación llegó vacía (se agotaron los tokens)")
            return [], "ninguno", None
        texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.MULTILINE).strip()
        datos = json.loads(texto)
        claves = datos.get("claves") or []
        tipo = datos.get("tipo") or "ninguno"
        canal_ia = datos.get("canal")
        nombrados = bool(datos.get("nombrados"))
        if canal_ia not in ("krece", "cashea", "creditienda"):
            canal_ia = None
        print(f"IA CRUDO -> claves={claves} tipo={tipo} canal={canal_ia}")
    except Exception as e:
        print(f"Error interpretando pedido: {e}")
        return [], "ninguno", None

    if tipo == "mas_opciones":
        return [], "mas_opciones", canal_ia
    if not claves:
        return [], "ninguno", canal_ia

    # Validación dura: solo claves que existan de verdad
    equipos, hay_sin_precio = [], False
    for clave in claves[:4]:
        eq = obtener_por_clave(clave)
        if eq:
            equipos.append(eq)
        elif existe_clave(clave):
            hay_sin_precio = True
            print(f"Clave marcada sin_precio pero el equipo debería tener precio: '{clave}'")
        else:
            print(f"IA devolvió una clave inexistente, descartada: '{clave}'")

    if not equipos:
        return [], ("sin_precio" if hay_sin_precio else "ninguno"), canal_ia

    if tipo not in ("exacto", "recomendacion", "mas_opciones"):
        tipo = "exacto"
    return ordenar_equipos(equipos, ocultar_demorados=not nombrados), tipo, canal_ia


# ── Flujo de celulares ────────────────────────────────────────────────────────

def atender_celulares(from_number, numero_limpio, body):
    """Atiende a un cliente de celulares de punta a punta."""
    perfil = cargar_perfil(numero_limpio)
    # Volvió a escribir después de ver equipos o precios: conversación real
    if perfil.get("modelo_interes") and not perfil.get("respondio"):
        guardar_perfil(numero_limpio, respondio=True)
    nombre_dicho = detectar_nombre(body)
    if nombre_dicho:
        perfil["nombre"] = nombre_dicho
        guardar_perfil(numero_limpio, nombre=nombre_dicho)
    cambios = {}
    if (numero_limpio in krece_supuesto and perfil.get("nivel_cliente") == "azul"
            and float(perfil.get("linea_krece") or 0) == 300):
        perfil["_krece_supuesto"] = True   # sigue con el Azul/$300 supuesto
    if numero_limpio in krece_linea_disponible:
        perfil["_linea_disponible"] = krece_linea_disponible[numero_limpio]

    # Canal de pago
    detectado = detectar_canal(body)
    # Si ya hay un canal activo (Krece, Cashea, CrediTienda), una mención
    # suelta de "dólares" o "divisas" como simple unidad de precio no debe
    # hacer saltar el canal a "contado". Solo se cambia si el cliente usa
    # una palabra explícita de pago de contado.
    PALABRAS_CONTADO_EXPLICITO = ("contado", "efectivo", "zelle", "usdt", "cash",
                                  "precio normal", "sin financiamiento",
                                  "sin financiar", "sin cuotas",
                                  "pago de una vez", "pagar de una vez",
                                  "pagarlo de una vez", "un solo pago", "pago completo")
    canal_previo = perfil.get("canal_pago")
    if (detectado == "contado" and canal_previo and canal_previo != "contado"
            and not any(p in body.lower() for p in PALABRAS_CONTADO_EXPLICITO)):
        detectado = None
        # "precio en divisa" sin hablar de inicial ni cuotas: quiere ver también
        # el precio de contado. Se le muestran los dos (su financiamiento y contado).
        if not re.search(r"inicial|cuota", body.lower()):
            perfil["canal_extra"] = "contado"
            cambios["canal_extra"] = "contado"
    canal = detectado or perfil.get("canal_pago")
    canal_anterior = perfil.get("canal_pago")
    if not canal and re.search(r"\bnivel\s*(1|uno)\b", body.lower()):
        perfil["_nivel_sin_canal"] = True   # solo en memoria
        nivel1_pendiente.add(numero_limpio)

    # Dos canales a la vez ("con krece y creditienda"): se guardan los dos
    nombrados = detectar_canales(body)
    if len(nombrados) >= 2:
        canal = nombrados[0]
        cambios["canal_extra"] = nombrados[1]
        perfil["canal_extra"] = nombrados[1]
    elif detectado and detectado == perfil.get("canal_extra"):
        # Pregunta por el segundo canal que ya estaba comparando: no se
        # cambia nada, se le siguen mostrando los dos
        canal = canal_anterior
    elif detectado and detectado != canal_anterior and perfil.get("canal_extra"):
        # Se fue a un tercer canal: deja de comparar
        cambios["canal_extra"] = None
        perfil["canal_extra"] = None

    # Comparando Krece y Cashea: si da su nivel de Cashea, ya eligió Cashea
    nivel_c = detectar_nivel_cashea_claro(body)
    if (perfil.get("canal_extra") == "cashea" and canal != "cashea"
            and len(nombrados) < 2 and nivel_c):
        canal = "cashea"
        cambios["canal_extra"] = None
        perfil["canal_extra"] = None
        perfil.pop("_krece_supuesto", None)
        krece_supuesto.discard(numero_limpio)

    if canal and canal != canal_anterior:
        cambios["canal_pago"] = canal
        perfil["canal_pago"] = canal
        # Solo si venía de OTRO canal: el nivel anterior no sirve para el nuevo
        if canal_anterior:
            cambios["nivel_cliente"] = None
            perfil["nivel_cliente"] = None
            if canal != "krece":
                cambios["linea_krece"] = None
                perfil["linea_krece"] = None

    # Llega por Krece sin nivel ni línea: 95% de los casos son Azul/$300.
    # Si pide iPhone no se asume (Azul no aplica): se le pregunta nivel y línea.
    pide_iphone = ("iphone" in body.lower()
                   or "iphone" in (perfil.get("modelo_interes") or "").lower())
    if (canal == "krece" and canal_anterior != "krece" and not pide_iphone
            and not perfil.get("nivel_cliente") and not perfil.get("linea_krece")):
        cambios["nivel_cliente"] = "azul"
        perfil["nivel_cliente"] = "azul"
        cambios["linea_krece"] = 300
        perfil["linea_krece"] = 300
        perfil["_krece_supuesto"] = True   # solo en memoria, no va a Supabase
        krece_supuesto.add(numero_limpio)

    # Nivel y línea, según el canal
    if canal == "krece":
        if numero_limpio in nivel1_pendiente:
            perfil["_nivel1_krece"] = True   # solo en memoria
        nivel1_pendiente.discard(numero_limpio)
        nivel = detectar_nivel_krece(body)
        linea = detectar_linea(body)
        if not linea:
            linea = detectar_linea_suelta(body)
        # Respaldo con IA: ya tiene nivel, espera la línea y Python no encontró el monto
        if (not linea and not pide_iphone and re.search(r"\d", body) and len(body) <= 80
                and (nivel or perfil.get("nivel_cliente")) and not perfil.get("linea_krece")):
            linea = linea_por_ia(numero_limpio, body)
        # Línea disponible (tope del iPhone): dicha con la palabra "disponible", o un
        # monto suelto cuando le interesa un iPhone y no habla de "celulares"
        disponible = detectar_linea_disponible(body)
        if disponible is None and pide_iphone and "celular" not in body.lower():
            # Para el iPhone, el monto que dé ("150", "plata 150", "mi línea es 40") es su Línea disponible
            m = re.fullmatch(r"\s*\$?\s*(\d{1,5})\s*(?:\$|d[oó]lares|usd)?\s*", body.lower())
            disponible = float(m.group(1)) if m else (detectar_linea_suelta(body) or linea)
        if disponible is not None and linea == disponible:
            linea = None
        if disponible is not None:
            krece_linea_disponible[numero_limpio] = disponible
            perfil["_linea_disponible"] = disponible
            perfil["_disponible_recien"] = True   # solo en memoria
        if linea_celulares_dudosa(numero_limpio, linea, body):
            # Monto chico: casi seguro es la "Línea disponible", no la de Celulares
            perfil["_linea_dudosa"] = linea   # solo en memoria
            linea = None
            cambios["linea_krece"] = None
            perfil["linea_krece"] = None
            perfil.pop("_krece_supuesto", None)
            krece_supuesto.discard(numero_limpio)
        if nivel:
            nivel_antes = perfil.get("nivel_cliente")
            if nivel_antes and nivel != nivel_antes and not linea:
                # Otro nivel sin línea: la anterior (o la supuesta) ya no sirve
                cambios["linea_krece"] = None
                perfil["linea_krece"] = None
            cambios["nivel_cliente"] = nivel
            perfil["nivel_cliente"] = nivel
            perfil["_nivel_recien"] = True   # lo dijo en este mensaje (solo en memoria)
        if linea:
            cambios["linea_krece"] = linea
            perfil["linea_krece"] = linea
        if nivel or linea or disponible is not None:
            # Ya dio un dato real: deja de ser supuesto
            perfil.pop("_krece_supuesto", None)
            krece_supuesto.discard(numero_limpio)
    elif canal == "cashea":
        nivel = detectar_nivel_cashea(body)
        if not nivel and numero_limpio in nivel1_pendiente:
            nivel = "1"
        nivel1_pendiente.discard(numero_limpio)
        if nivel:
            cambios["nivel_cliente"] = nivel
            perfil["nivel_cliente"] = nivel
    elif canal == "creditienda":
        t = body.lower()
        if re.search(r"\b(bs|bolivares|bolívares)\b", t):
            cambios["nivel_cliente"] = "bs"
            perfil["nivel_cliente"] = "bs"
        elif re.search(r"\b(divisas?|dolares|dólares|efectivo)\b", t):
            cambios["nivel_cliente"] = "divisas"
            perfil["nivel_cliente"] = "divisas"

    # Equipo: la IA interpreta lo que pidió
    contexto = ""
    if perfil.get("modelo_interes"):
        contexto = (f"\nEn mensajes anteriores le interesaba el "
                    f"{perfil['modelo_interes']}. Si ahora no menciona otro "
                    f"modelo, se refiere a ese o esos (también si los compara).\n")
    else:
        rango_ctx = listar_por_rango(**filtros_listas(perfil), con_promo=es_krece(perfil))
        if rango_ctx:
            etiquetas = ["económico", "intermedio", "gama alta"]
            desc = ", ".join(
                f"{etiquetas[i]}: {nombre_completo(eq)} (${int(eq['precio_paralelo'])})"
                for i, eq in enumerate(rango_ctx)
            )
            contexto = (f"\nSi el cliente no dio un modelo, puede referirse a "
                       f"alguna de estas 3 opciones que ya se le mostraron "
                       f"por rango de precio: {desc}. Si menciona un precio, "
                       f"una categoría (económico, intermedio, gama alta) o "
                       f"parte del nombre de una de ellas, elige ese equipo "
                       f"y marca tipo exacto.\n")
    # "No", "no tengo", "no estoy registrado en cashea": sigue con los mismos
    # equipos sin llamar a la IA (la IA lo tomaba como "no pide nada").
    # Igual si solo da el monto de su línea de Krece ("Ah 800"): la IA lo tomaba
    # como presupuesto y cambiaba de equipo.
    equipos = []
    if (canal in ("krece", "cashea") and perfil.get("modelo_interes")
            and len(body.split()) <= 6
            and (detectar_sin_cuenta(body) or es_no_corto(canal, perfil, body)
                 or (canal == "krece" and detectar_linea_suelta(body) is not None))):
        equipos = equipos_de_interes(perfil["modelo_interes"])
        tipo_resultado, canal_ia = "exacto", None
    # "Mándame las fotos de esos tres": pide ver varios de los que ya vio
    if (not equipos and perfil.get("modelo_interes") and pide_foto(body)
            and re.search(r"\b(fotos|imagenes|esos|esas|ambos|ambas|todos|todas|tres|dos)\b",
                          body.lower())):
        equipos = equipos_de_interes(perfil["modelo_interes"])
        if equipos:
            tipo_resultado, canal_ia = "exacto", None
    # Gracias, ok, saludo o "ubicados": no nombra equipo, no hace falta la IA
    sin_equipo = bool(perfil.get("modelo_interes") and es_mensaje_sin_equipo(body))
    if not equipos and sin_equipo:
        tipo_resultado, canal_ia = "ninguno", None
        print(f"Mensaje sin equipo ({numero_limpio}): no se llama a la IA")
    elif not equipos:
        equipos, tipo_resultado, canal_ia = interpretar_pedido(body, contexto)
        # Solo dio su nivel o línea: sigue con los equipos que ya le interesaban
        if (tipo_resultado == "ninguno" and perfil.get("modelo_interes")
                and (cambios.get("linea_krece") or cambios.get("nivel_cliente")
                     or detectado
                     or perfil.get("_disponible_recien")
                     or pide_ambas(body)
                     or pide_foto(body)
                     or body == "Aquí está mi captura de Krece")):
            interes = equipos_de_interes(perfil["modelo_interes"])
            if interes:
                equipos, tipo_resultado = interes, "exacto"
        # Dio su línea y la IA lo tomó por presupuesto: trajo equipos que no nombró
        # y que no le interesaban. Se sigue con los que ya le interesaban.
        elif (tipo_resultado in ("exacto", "recomendacion") and equipos
              and perfil.get("modelo_interes") and cambios.get("linea_krece")):
            interes = equipos_de_interes(perfil["modelo_interes"])
            nombres_interes = {nombre_completo(e) for e in interes}
            b = body.lower()
            nombro = any(p in b for e in equipos
                         for p in (str(e["marca"]).lower(), *str(e["modelo"]).lower().split())
                         if len(p) >= 2)
            if (interes and not nombro
                    and not all(nombre_completo(e) in nombres_interes for e in equipos)):
                equipos, tipo_resultado = interes, "exacto"

    # Si Python no reconoció el canal (mal escrito, como "caschea"),
    # se usa el que entendió la IA y se leen el nivel o la moneda
    if (not detectado and canal_ia and canal_ia != canal
            and canal_ia != perfil.get("canal_extra")):
        print(f"Canal detectado por IA: {canal_ia} (antes: {canal})")
        canal = canal_ia
        cambios["canal_pago"] = canal
        perfil["canal_pago"] = canal
        cambios["nivel_cliente"] = None
        perfil["nivel_cliente"] = None
        if perfil.get("canal_extra"):
            cambios["canal_extra"] = None
            perfil["canal_extra"] = None
        if canal != "krece":
            cambios["linea_krece"] = None
            perfil["linea_krece"] = None
        if canal == "krece":
            nivel = detectar_nivel_krece(body)
            linea = detectar_linea(body) or detectar_linea_suelta(body)
            if linea_celulares_dudosa(numero_limpio, linea, body):
                perfil["_linea_dudosa"] = linea   # solo en memoria
                linea = None
            if nivel:
                cambios["nivel_cliente"] = nivel
                perfil["nivel_cliente"] = nivel
            if linea:
                cambios["linea_krece"] = linea
                perfil["linea_krece"] = linea
        elif canal == "cashea":
            nivel = detectar_nivel_cashea(body)
            if nivel:
                cambios["nivel_cliente"] = nivel
                perfil["nivel_cliente"] = nivel

    # Cliente sin cuenta en Krece o Cashea: se cotiza con el nivel de entrada
    # (el registro se lo hacemos en la tienda con su cédula laminada)
    if canal in ("krece", "cashea") and (detectar_sin_cuenta(body)
                                         or es_no_corto(canal, perfil, body)):
        if canal == "krece":
            cambios.update(nivel_cliente="azul", linea_krece=300)
            perfil.update(nivel_cliente="azul", linea_krece=300)
            perfil.pop("_krece_supuesto", None)   # sin cuenta: es el nivel de entrada
            krece_supuesto.discard(numero_limpio)
        else:
            cambios["nivel_cliente"] = "1"
            perfil["nivel_cliente"] = "1"

    # Krece Azul (dado por él, no supuesto) y pide iPhone: por Krece no aplica,
    # se le ofrece Cashea (si no lo tiene, el registro se le hace en la tienda)
    if (canal == "krece" and perfil.get("nivel_cliente") == "azul"
            and not perfil.get("_krece_supuesto") and not perfil.get("canal_extra")
            and equipos and tipo_resultado in ("exacto", "recomendacion")
            and all(e["marca"].lower() == "iphone" for e in equipos)):
        canal = "cashea"
        cambios.update(canal_pago="cashea", nivel_cliente=None, linea_krece=None)
        perfil.update(canal_pago="cashea", nivel_cliente=None, linea_krece=None)
        perfil["_iphone_a_cashea"] = True   # solo para este mensaje

    if equipos:
        modelo = " / ".join(nombre_completo(e) for e in equipos)
        if modelo != perfil.get("modelo_interes"):
            cambios["modelo_interes"] = modelo
            perfil["modelo_interes"] = modelo

    if cambios:
        guardar_perfil(numero_limpio,
                       borrar=[k for k, v in cambios.items() if v is None],
                       **cambios)

    # Lo único que ve el modelo
    if tipo_resultado == "sin_precio":
        info = ("El equipo existe pero NO tiene precio verificado. "
                "No lo cotices: responde DERIVAR_PRECIO.")
    elif tipo_resultado == "recomendacion":
        info = ("El cliente pidió un modelo que NO tenemos. Dile con claridad "
                "que ese no lo manejas (NO respondas DERIVAR_PRECIO), y ofrécele "
                "estas alternativas parecidas:\n" + bloque_equipo(equipos, canal, perfil))
    elif tipo_resultado == "mas_opciones":
        info = ("El cliente pregunta si hay más opciones. SÍ hay más: mándale "
                "la LISTA CORTA tal cual y pregúntale si busca alguna marca o presupuesto.")
    elif tipo_resultado == "ninguno" and "anuncio de variedad" in body.lower():
        info = ("El cliente viene del anuncio de variedad de equipos: quiere ver "
                "qué tenemos disponible. Mándale las TRES OPCIONES tal cual (económico, "
                "intermedio y gama alta), una línea por equipo, y pregúntale cuál le "
                "llama la atención o qué presupuesto tiene. Si además "
                "pregunta otra cosa (ubicación, formas de pago), respóndela también.")
    elif tipo_resultado == "ninguno" and "anuncio de mi celular" in body.lower():
        info = ("El cliente viene del anuncio de celular dañado: su celular actual ya "
                "le falla (se traba o no aguanta la carga) y quiere cambiarlo. Dile en "
                "una línea que lo ayudamos a estrenar, mándale las TRES OPCIONES tal cual "
                "y pregúntale si lo prefiere de contado o con Cashea o Krece. Si además "
                "pregunta otra cosa, respóndela también.")
    elif tipo_resultado == "ninguno" and "anuncio de compra segura" in body.lower():
        info = ("El cliente viene del anuncio de compra segura: le preocupa que lo "
                "estafen comprando por internet. Dile en una línea que somos tienda "
                "física en Santa Teresa del Tuy y que puede venir a ver el equipo antes "
                "de llevárselo, mándale las TRES OPCIONES tal cual y pregúntale cuál le "
                "llama la atención. Si además pregunta otra cosa, respóndela también.")
    elif tipo_resultado == "ninguno":
        info = ("El cliente no está preguntando por un equipo concreto, o pidió "
                "algo que no tenemos ni se parece a nada del catálogo. "
                "No inventes modelos ni precios.")
        if perfil.get("modelo_interes"):
            info += (f" Antes le interesaba el {perfil['modelo_interes']}: lo que ya "
                     "le dijiste de ese equipo sigue siendo válido, NO lo corrijas "
                     "ni lo retires. Responde solo lo que pregunta ahora.")
            if sin_equipo:
                interes = equipos_de_interes(perfil["modelo_interes"])
                if interes:
                    info += ("\nDatos vigentes de ese equipo (lo que ya dijiste es "
                             "correcto):" + bloque_equipo(interes, canal, perfil))
    else:
        info = bloque_equipo(equipos, canal, perfil)

    # Si está comparando dos canales, se agregan los precios del segundo
    canal_extra = perfil.get("canal_extra")
    if canal_extra and equipos and tipo_resultado in ("exacto", "recomendacion"):
        perfil_extra = dict(perfil)
        perfil_extra["nivel_cliente"] = None   # el nivel guardado es del primer canal
        info += (f"\n\nLOS MISMOS EQUIPOS CON {canal_extra.upper()}:"
                 + bloque_equipo(equipos, canal_extra, perfil_extra))

    iphone_a_cashea = perfil.pop("_iphone_a_cashea", None)

    print(f"INFO AL MODELO [{numero_limpio}] -> " + info[:2000].replace("\n", " / "))

    # Si llegó otro mensaje mientras se interpretaba este, se responden juntos
    with buffer_lock:
        pendiente = buffer_mensajes.get(numero_limpio)
        if pendiente:
            pendiente["textos"].insert(0, body)
    if pendiente:
        print(f"⏭️ Llegó otro mensaje de {numero_limpio}: se responden juntos")
        return

    historial = cargar_historial(numero_limpio)
    # Segundo "ok / perfecto / listo" seguido: el bot ya respondió al anterior (sin
    # pregunta pendiente), no se contesta otra vez. "Gracias" y saludos sí se contestan.
    if (sin_equipo and len(historial) >= 2
            and historial[-1].get("role") == "assistant"
            and historial[-2].get("role") == "user"
            and es_mensaje_sin_equipo(str(historial[-2].get("content", "")))
            and not re.search(r"gracias|grax|hola|buen|saludos", body.lower())
            and "?" not in str(historial[-1].get("content", ""))):
        print(f"Segundo acuse seguido ({numero_limpio}): no se responde")
        return
    historial.append({"role": "user", "content": body})
    if len(historial) > 4:
        historial = historial[-4:]
    if len(historial) > 1 and debe_pedir_nombre(perfil):
        perfil["_pedir_nombre"] = True

    if iphone_a_cashea:
        # Respuesta fija, sin IA: Krece Azul no aplica para iPhone, se ofrece Cashea
        reply = texto_iphone_cashea(equipos, tipo_resultado)
    else:
        respuesta = client.messages.create(
            model=MODELO_CELULARES,
            max_tokens=4000,
            system=get_system_prompt_celulares(info, perfil, bloque_rangos(perfil),
                                               bloque_lista_corta(canal, perfil),
                                               bloque_promos(perfil, body)),
            messages=historial,
        )
        reply = texto_respuesta(respuesta)
        registrar_uso("celulares-respuesta", respuesta)
        if not reply:
            print("La respuesta al cliente llegó vacía")
            reply = "Dame un momento y te confirmo"
    reply = marcas_nombre(numero_limpio, reply)

    # ── Marcadores ────────────────────────────────────────────────────────────
    if "DERIVAR_TECNICO" in reply and tecnico_celulares.pop(numero_limpio, None):
        reply = "¡Claro! 🔧 Escríbeme el modelo de la pantalla o repuesto que buscas y te doy el precio."
    elif "DERIVAR_TECNICO" in reply:
        reply = ("Para servicio técnico y reparaciones escríbenos a este número: "
                 "0422-039-2375 📲")

    elif "DERIVAR_OTROS" in reply:
        reply = ("Para accesorios y otras consultas escríbenos a este número: "
                 "0412-609-3756 📲")

    elif "DERIVAR_PRECIO" in reply and equipos and dato_faltante(canal, perfil):
        # El equipo SÍ tiene precio: lo que falta es un dato del cliente.
        # Se le pide el dato y no se manda la alerta falsa.
        print("DERIVAR_PRECIO descartado: falta un dato del cliente")
        reply = dato_faltante(canal, perfil)
    elif "DERIVAR_PRECIO" in reply and tipo_resultado == "recomendacion" and equipos:
        # El modelo pedido no existe: no hay precio que confirmar
        print("DERIVAR_PRECIO descartado: era recomendación")
        nombres = "\n".join(f"• {nombre_completo(e)}" for e in equipos[:4])
        reply = ("Ese modelo no lo tenemos disponible 😕 Te puedo ofrecer estos parecidos:\n"
                 f"{nombres}\n¿Quieres que te cotice alguno?")
    elif "DERIVAR_PRECIO" in reply:
        notificar_precio_sin_verificar(from_number, body)
        reply = msg_asesor(
            "Déjame confirmarte el precio de ese modelo y te escribo en un momento",
            "Déjame confirmarte el precio de ese modelo, te escribo mañana a partir de las 6:00 am")

    elif "INTENCION_COMPRA" in reply:
        online = "INTENCION_COMPRA_ONLINE" in reply
        notificar_intencion_compra(from_number, perfil, equipos, online)
        hora_vzla = datetime.now(pytz.timezone("America/Caracas")).hour
        if online and 6 <= hora_vzla < 22:
            reply = ("¡Perfecto! 🙌 Ya le pasé tu solicitud de compra online a un asesor "
                     "de la tienda, él te da todos los detalles. Solo debes estar atento "
                     "a sus indicaciones al momento de hacer la compra.")
        elif online:
            reply = ("¡Perfecto! 🙌 Ya le pasé tu solicitud de compra online a un asesor "
                     "de la tienda y mañana a partir de las 6:00 am "
                     "te da todos los detalles. Solo debes estar atento a sus indicaciones "
                     "al momento de hacer la compra.")
        elif 6 <= hora_vzla < 22:
            reply = ("¡Perfecto! Ya le pasé tu solicitud a un asesor de la tienda, "
                     "en breve te contacta para coordinar 🙌")
        else:
            reply = ("¡Perfecto! Ya le pasé tu solicitud a un asesor de la tienda. "
                     "Te contactará mañana a partir de las 6:00 am para coordinar 🙌")

    enviar_ubicacion = "ENVIAR_UBICACION" in reply
    if enviar_ubicacion:
        reply = re.sub(r"\n\s*\n", "\n\n", reply.replace("ENVIAR_UBICACION", "")).strip()
        if not reply:
            reply = ("¿Te ayudo con algo más del equipo que viste? 😊" if perfil.get("modelo_interes")
                     else "¿Qué equipo estás buscando? Te ayudo a escoger 😊")

    quiere_foto = "[FOTO]" in reply
    if quiere_foto:
        reply = reply.replace("[FOTO]", "").strip()

    # Expresiones de otros países que a veces se escapan
    for malo, bueno in (("¿qué te late?", "¿qué te parece?"), ("te late", "te gusta"), ("Te late", "Te gusta")):
        reply = reply.replace(malo, bueno)
    # WhatsApp pone negrita con un solo asterisco; con dos se ven los asteriscos
    reply = reply.replace("**", "*")

    # Equipos que el bot nombró por su cuenta (tres opciones o LISTA CORTA) se
    # suman al modelo de interés, para entender "sí", "ese" o "el más barato"
    if reply:
        previo = perfil.get("modelo_interes") or ""
        filtros = filtros_listas(perfil)
        nuevos = []
        for eq in (listar_por_rango(**filtros, con_promo=es_krece(perfil))
                   + listar_mas_baratos(12, **filtros)):
            nombre = nombre_completo(eq)
            if (f"{eq['marca']} {eq['modelo']}".lower() in reply.lower()
                    and nombre.lower() not in previo.lower() and nombre not in nuevos):
                nuevos.append(nombre)
        if nuevos:
            todos = ([n for n in previo.split(" / ") if n] + nuevos)[-4:]
            guardar_perfil(numero_limpio, modelo_interes=" / ".join(todos))

    historial.append({"role": "assistant", "content": reply or "[enviado]"})
    guardar_historial(numero_limpio, historial)

    # Foto de la fachada + dirección + mapa primero; la pregunta queda de última
    if enviar_ubicacion:
        send_whapi_ubicacion(from_number)

    if reply:
        send_whapi_message(from_number, reply)

    if quiere_foto and equipos:
        # Hasta 3 fotos, una por modelo (sin repetir el mismo modelo con otra memoria)
        vistos, enviadas = set(), 0
        for eq in equipos:
            clave_foto = (eq["marca"].lower(), eq["modelo"].lower())
            if clave_foto in vistos or enviadas >= 3:
                continue
            vistos.add(clave_foto)
            url_foto = buscar_foto(eq["marca"], eq["modelo"], eq["almacenamiento"])
            if url_foto:
                send_whapi_image(from_number, url_foto, nombre_completo(eq))
                enviadas += 1
            else:
                print(f"Sin foto para {nombre_completo(eq)}")
        if not enviadas:
            send_whapi_message(from_number,
                "Por ahora no tengo la foto de ese modelo, pero puedes pasar "
                "por la tienda a verlo en persona 😊")


# ── Modo administrador: cotizaciones rápidas con cálculo exacto ───────────────
ADMIN_MEMORIA_SEG = 600          # recuerda la última consulta 10 minutos
consultas_admin = {}             # numero -> consulta pendiente

NOMBRES_CASHEA = {"1": "Semilla", "2": "Raíz", "3": "Hoja",
                  "4": "Tronco", "5": "Árbol", "6": "Araguaney"}
ORIGEN_TEXTO = {"tienda": "en tienda", "aliada": "aliada", "proveedor": "proveedor"}


def _admin_nivel_cashea(texto):
    """Nivel de Cashea sin confundirlo con números del modelo (Spark Go 3)."""
    t = texto.lower().strip()
    mapa = {"semilla": "1", "raiz": "2", "raíz": "2", "hoja": "3",
            "tronco": "4", "arbol": "5", "árbol": "5", "araguaney": "6"}
    for palabra, num in mapa.items():
        if palabra in t:
            return num
    m = (re.search(r"\bnivel\s*([1-6])\b", t)
         or re.fullmatch(r"(?:cashea\s*)?([1-6])", t))
    return m.group(1) if m else None


def _admin_nivel_krece(texto):
    t = texto.lower()
    for nivel in ("platino", "oro", "plata", "azul"):
        if nivel in t:
            return nivel
    return None


def _admin_linea(texto):
    """Línea de Krece: 'plata 220', 'linea 220', '$220' o solo '220'."""
    t = texto.lower().strip()
    m = re.search(r"(?:azul|plata|oro|platino)\s*(?:con\s*)?\$?\s*(\d{2,5})\b", t)
    if m:
        return float(m.group(1))
    linea = detectar_linea(texto)
    if linea:
        return linea
    m = re.fullmatch(r"(?:krece\s*)?\$?\s*(\d{2,5})", t)
    return float(m.group(1)) if m else None


def _admin_solo_datos(texto):
    """True si el mensaje solo trae canal, nivel o línea, sin modelo."""
    t = texto.lower()
    t = re.sub(r"krece|cashea|creditienda|contado|divisas?|nivel|linea|línea|"
               r"aprobad[oa]|azul|plata|oro|platino|semilla|ra[ií]z|hoja|tronco|"
               r"[aá]rbol|araguaney|con|de|el|la|en|y|\$|\d+", " ", t)
    return not t.strip(" .,:;!?\n")


def _admin_cotizar(eq, c):
    """Texto de precios de un equipo según el canal de la consulta."""
    p = eq["precio_paralelo"]
    origen = ORIGEN_TEXTO.get(eq["origen"], eq["origen"])
    entrega = "entrega inmediata" if eq["inmediato"] else "24-48h"
    lineas = [f"📱 *{nombre_completo(eq)}*",
              f"   {origen} ({eq.get('proveedor') or '-'}) · {entrega}"]
    canal = c.get("canal")

    if canal in (None, "contado"):
        lineas.append(f"   De contado: ${int(p)}")
        tasa = obtener_tasa_bcv()
        bcv = precios.precio_bcv(p)
        if tasa:
            bs = f"{precios.precio_bolivares(p, tasa):,}".replace(",", ".")
            lineas.append(f"   Tasa BCV: ${bcv} (Bs {bs})")
        else:
            lineas.append(f"   Tasa BCV: ${bcv} (tasa no disponible)")

    if canal in (None, "creditienda") and eq["marca"].lower() == "iphone":
        lineas.append("   CrediTienda: no aplica para iPhone")
    elif canal in (None, "creditienda"):
        for moneda, etiqueta in (("divisas", "divisas"), ("bs", "Bs")):
            ct = precios.creditienda(p, moneda)
            lineas.append(f"   CrediTienda {etiqueta}: inicial ${ct['inicial']} "
                          f"+ 4 x ${ct['monto_cuota']} (total ${ct['total']})")

    if canal == "cashea":
        n = c["nivel"]
        ch = precios.cashea(p, n)
        lineas.append(f"   Cashea nivel {n} {NOMBRES_CASHEA.get(n, '')}: "
                      f"inicial ${ch['inicial']} + 3 x ${ch['monto_cuota']} "
                      f"(total ${ch['total']})")

    if canal == "krece":
        nivel, linea = c["nivel"], c["linea"]
        if eq["marca"].lower() == "iphone" and nivel == "azul":
            lineas.append("   Krece: los iPhone requieren nivel Plata o superior")
            return "\n".join(lineas)
        # iPhone: la línea que dan es su Línea disponible (tipo "Otros" del portal)
        tipo = "otros" if eq["marca"].lower() == "iphone" else "celular"
        nombre_linea = "Línea disponible" if tipo == "otros" else "línea"
        lineas.append(f"   Krece {nivel.capitalize()}, {nombre_linea} ${int(linea)}:")
        hubo = False
        for plazo in precios.plazos_krece(nivel, tipo):
            k = precios.krece(p, nivel, plazo, linea=linea, tipo=tipo)
            if not k.get("aplica"):
                continue
            hubo = True
            extra = " ⚠️ inicial subida por la línea" if k["topado_por_linea"] else ""
            lineas.append(f"   {plazo} cuotas: inicial ${k['inicial']} "
                          f"+ {plazo} x ${k['monto_cuota']}{extra}")
        if not hubo:
            lineas.append("   No aplica: el equipo supera la línea aprobada")

    return "\n".join(lineas)


def atender_admin(from_number, numero_limpio, body):
    """
    Cotización rápida para los administradores. Devuelve True si la atendió;
    False si el mensaje no es una consulta de precios (sigue el flujo normal).
    """
    ahora = time.time()
    previa = consultas_admin.get(numero_limpio)
    if previa and ahora - previa["hora"] > ADMIN_MEMORIA_SEG:
        previa = None

    canal = detectar_canal(body)

    # ¿Trae un modelo nuevo o solo completa la consulta anterior?
    equipos, tipo = [], "ninguno"
    if not (previa and _admin_solo_datos(body)):
        equipos, tipo, _ = interpretar_pedido(body)

    aporta = (canal or _admin_nivel_cashea(body) or _admin_nivel_krece(body)
              or _admin_linea(body))

    if equipos:
        c = {"equipos": equipos, "tipo": tipo, "canal": canal,
             "nivel": None, "linea": None}
        # Venía de "¿De qué modelo?": conserva el canal y los datos ya dados
        if previa and not previa["equipos"] and not canal:
            c.update(canal=previa["canal"], nivel=previa["nivel"],
                     linea=previa["linea"])
    elif tipo == "sin_precio":
        send_whapi_message(from_number, "Ese equipo está en el catálogo pero "
                           "*sin precio verificado*. Actualízalo en la hoja.")
        return True
    elif previa and aporta:
        c = previa
        if canal and canal != c["canal"]:
            c["canal"], c["nivel"], c["linea"] = canal, None, None
    elif canal:
        send_whapi_message(from_number, "¿De qué modelo?")
        nivel = (_admin_nivel_cashea(body) if canal == "cashea"
                 else _admin_nivel_krece(body) if canal == "krece" else None)
        linea = _admin_linea(body) if canal == "krece" else None
        consultas_admin[numero_limpio] = {"equipos": [], "tipo": "exacto",
                                          "canal": canal, "nivel": nivel,
                                          "linea": linea, "hora": ahora}
        return True
    else:
        return False   # no es consulta de precios: sigue el flujo normal

    # Nivel y línea que trae este mensaje
    if c["canal"] == "cashea":
        c["nivel"] = _admin_nivel_cashea(body) or c["nivel"]
    elif c["canal"] == "krece":
        c["nivel"] = _admin_nivel_krece(body) or c["nivel"]
        c["linea"] = _admin_linea(body) or c["linea"]
    c["hora"] = ahora
    consultas_admin[numero_limpio] = c

    # Falta el modelo (solo ha dado canal, nivel o línea)
    if not c["equipos"]:
        send_whapi_message(from_number, "¿De qué modelo?")
        return True

    # Faltan datos del canal
    if c["canal"] == "cashea" and not c["nivel"]:
        send_whapi_message(from_number, "¿Qué nivel de Cashea tiene el cliente? "
                           "(1 Semilla a 6 Araguaney)")
        return True
    if c["canal"] == "krece" and not (c["nivel"] and c["linea"]):
        falta = ("nivel y línea aprobada" if not c["nivel"] and not c["linea"]
                 else "nivel" if not c["nivel"] else "línea aprobada")
        send_whapi_message(from_number, f"¿Qué {falta} tiene el cliente en Krece?")
        return True

    # Respuesta con los números
    partes = []
    if c["tipo"] == "recomendacion":
        partes.append("⚠️ Ese modelo no está en el catálogo. Los más parecidos:")
    partes += [_admin_cotizar(eq, c) for eq in c["equipos"]]
    if not c["canal"]:
        partes.append("Para Cashea o Krece escríbeme el canal y el nivel.")
    send_whapi_message(from_number, "\n\n".join(partes))
    return True


# ── Modo administrador: preguntas libres sobre el inventario (IA) ─────────────
ADMIN_IA_MEMORIA_SEG = 600       # recuerda la conversación 10 minutos
ADMIN_IA_MAX_MENSAJES = 6        # últimos mensajes que se reenvían a la IA
memoria_admin_ia = {}            # numero -> {"mensajes": [...], "hora": t}

PALABRAS_INVENTARIO = re.compile(
    r"\b(cost[oó]|costos|margen|ganancia|ganamos|ubicaci[oó]n|"
    r"d[oó]nde|proveedor|proveedores|aliada|inventario|cu[aá]ntos|cu[aá]ntas|"
    r"disponible|disponibles|lleg[oó]|llegaron|lista|p[eé]rdida|stock)\b",
    re.IGNORECASE)

PROMPT_ADMIN_IA = """Eres el asistente interno de Cell Center 4620 (tienda de celulares en \
Santa Teresa del Tuy, Venezuela). Hablas con un ADMINISTRADOR de la tienda, no con \
un cliente: puedes darle costos, márgenes, proveedores y ubicación.

Abajo está el inventario completo, una línea por modelo:
Modelo Almacenamiento/RAM | precio de venta en divisas | origen (proveedor): costo · disponible · fecha de lista · última vez visto

Significado del origen:
- tienda: el equipo está físicamente en la tienda (entrega inmediata)
- aliada: está en una tienda aliada (entrega inmediata)
- proveedor: hay que pedirlo al proveedor (24-48h)

Reglas:
- Responde SOLO con datos del inventario. Si algo no está, dilo; nunca inventes.
- "SIN VERIFICAR" o "SIN PRECIO" significa que el precio de venta no está confirmado: avísalo.
- Disponible NO = ya no está en la última lista.
- Margen = precio de venta - costo. Si falta el costo, di que no hay costo cargado.
- La hoja no tiene cantidad de unidades: si preguntan cuántas unidades, di dónde hay \
disponibilidad y que las unidades no están en la hoja.
- Di "precio en divisas" o "precio de venta"; nunca uses la palabra "paralelo".
- Para bolívares usa la tasa BCV que viene con la pregunta.
- Respuestas cortas para WhatsApp: sin tablas, una línea por equipo, *negritas* \
solo para el nombre del modelo.

INVENTARIO:
"""


def es_pregunta_inventario(texto):
    """True si el admin pregunta por costo, ubicación, proveedor, inventario..."""
    return bool(PALABRAS_INVENTARIO.search(texto))


def atender_admin_ia(from_number, numero_limpio, body):
    """Responde con IA cualquier pregunta del administrador sobre el inventario."""
    inventario = inventario_admin()
    if not inventario:
        send_whapi_message(from_number, "❌ No pude leer el inventario de la hoja.")
        return

    ahora = time.time()
    memoria = memoria_admin_ia.get(numero_limpio)
    if not memoria or ahora - memoria["hora"] > ADMIN_IA_MEMORIA_SEG:
        memoria = {"mensajes": [], "hora": ahora}

    tasa = obtener_tasa_bcv()
    pregunta = f"(Tasa BCV hoy: {tasa or 'no disponible'})\n{body}"
    mensajes = memoria["mensajes"] + [{"role": "user", "content": pregunta}]

    r = client.messages.create(
        model=MODELO_CELULARES,
        max_tokens=1200,
        # Prompt + inventario en caché 1h: solo cambia si cambia la hoja
        system=[{"type": "text", "text": PROMPT_ADMIN_IA + inventario,
                 "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        messages=mensajes,
    )
    registrar_uso("admin-ia", r)
    respuesta = texto_respuesta(r) or "No pude armar la respuesta, intenta de nuevo."
    send_whapi_message(from_number, respuesta)

    mensajes.append({"role": "assistant", "content": respuesta})
    memoria_admin_ia[numero_limpio] = {
        "mensajes": mensajes[-ADMIN_IA_MAX_MENSAJES:], "hora": ahora}


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True) or {}
        messages_list = data.get("messages", [])

        for msg in messages_list:
            texto_log = (msg.get('text', {}).get('body', '') or '')[:1500].replace('\n', ' / ')
            print(f"📨 MSG RECIBIDO | chat: {msg.get('chat_id','')} | from: {msg.get('from','')} | type: {msg.get('type','')} | body: {texto_log} | ts: {msg.get('timestamp',0)} | from_me: {msg.get('from_me',False)}")
            if msg.get("from_me", False):
                # Detectar si el asesor escribe ** para pausar el bot
                body_asesor = msg.get("text", {}).get("body", "").strip()
                if body_asesor == "**":
                    chat_id_pausa = msg.get("chat_id", "") or msg.get("chatId", "") or ""
                    numero_cliente = chat_id_pausa.replace("@s.whatsapp.net", "").replace("+", "")
                    pausas_activas[numero_cliente] = time.time() + 900  # 15 minutos
                    print(f"Bot pausado para {numero_cliente} por 15 minutos")
                elif body_asesor == "++":
                    chat_id_pausa = msg.get("chat_id", "") or msg.get("chatId", "") or ""
                    numero_cliente = chat_id_pausa.replace("@s.whatsapp.net", "").replace("+", "")
                    if numero_cliente in pausas_activas:
                        del pausas_activas[numero_cliente]
                        print(f"Bot reanudado para {numero_cliente}")
                continue

            chat_id = msg.get("chat_id", "") or msg.get("chatId", "") or ""

            # ── Capturar imágenes del grupo de pagos ──────────────────────────
            if (GRUPO_PAGOS_ID
                    and chat_id == GRUPO_PAGOS_ID
                    and msg.get("type") == "image"):
                print(f"📥 Imagen de pago recibida de {msg.get('from_name', msg.get('from', ''))}")
                procesar_imagen_pago(msg)
                continue

            # ── Capturar borrado de mensajes del grupo de pagos ───────────────
            if (GRUPO_PAGOS_ID
                     and chat_id == GRUPO_PAGOS_ID
                     and msg.get("type") in ("revoke", "action")):
                deleted_id = (
                     msg.get("action", {}).get("target") or
                     msg.get("revoked_msg_id") or
                     msg.get("id", "")
                )
                print(f"🗑️ Mensaje borrado en grupo de pagos: {deleted_id}")
                borrar_pago_por_msg_id(deleted_id)
                continue

            from_number = msg.get("from", "")
            if not from_number:
                continue

            if "@g.us" in from_number or "@g.us" in chat_id:
                print("Mensaje de grupo ignorado")
                continue
            if "broadcast" in from_number.lower() or "broadcast" in chat_id.lower():
                print("Mensaje broadcast ignorado")
                continue

            msg_type = msg.get("type", "")

            msg_timestamp = msg.get("timestamp", 0)
            ahora = time.time()
            antiguedad = int(ahora - msg_timestamp)

            if msg_timestamp < BOT_START_TIME or antiguedad > 3600:
                print("Mensaje ignorado - muy antiguo: " + str(antiguedad) + "s")
                continue

            numero_limpio = from_number.replace("@s.whatsapp.net", "").replace("+", "")

            # ── Verificar si el bot está en pausa manual para este número ──────
            #    (el comando reset de admins y números de prueba pasa igual)
            es_reset = (msg_type == "text"
                        and msg.get("text", {}).get("body", "").strip().lower()
                        in ("reset", "reset_historial")
                        and (numero_limpio in ADMINISTRADORES
                             or numero_limpio in NUMEROS_PRUEBA_CELULARES))
            if numero_limpio in pausas_activas and not es_reset:
                if time.time() < pausas_activas[numero_limpio]:
                    print(f"⏸️ Bot en pausa para {numero_limpio}, mensaje ignorado")
                    continue
                else:
                    del pausas_activas[numero_limpio]
                    print(f"▶️ Pausa expirada para {numero_limpio}, reanudando")

            
            # ── Mensajes que no son texto (fotos, audios, stickers...) ─────────
            if msg_type != "text":
                es_cel_temp = ((numero_limpio in NUMEROS_PRUEBA_CELULARES
                                or numero_limpio not in NUMEROS_AUTORIZADOS)
                               and numero_limpio not in ADMINISTRADORES
                               and numero_limpio not in modo_prueba_pantallas)

                # Se ignoran sin responder
                if msg_type in ("sticker", "contact", "contacts", "location", "reaction"):
                    continue

                # Técnicos autorizados: igual que antes
                if not es_cel_temp:
                    if msg_type in ("image", "audio", "voice", "video", "document"):
                        send_whapi_message(from_number, "Por los momentos solo puedo leer mensajes de texto. Por favor escribe el modelo que buscas. 📝")
                    continue

                # Foto de un cliente de Krece al que le falta nivel o línea: leer la captura
                perfil_img = cargar_perfil(numero_limpio) if msg_type == "image" else {}
                if (msg_type == "image" and perfil_img.get("canal_pago") == "krece"
                        and not (perfil_img.get("nivel_cliente") and perfil_img.get("linea_krece")
                                 and numero_limpio in krece_linea_disponible)):
                    try:
                        nivel, linea, es_krece, disponible = leer_captura_krece(msg.get("image", {}))
                    except Exception as e:
                        print(f"Error procesando captura Krece: {e}")
                        nivel, linea, es_krece, disponible = None, None, False, None
                    if disponible is not None:
                        krece_linea_disponible[numero_limpio] = float(disponible)
                    if nivel or linea or disponible is not None:
                        datos_krece = {"canal_pago": "krece"}
                        if nivel:
                            datos_krece["nivel_cliente"] = nivel
                        if linea:
                            datos_krece["linea_krece"] = linea
                        guardar_perfil(numero_limpio, **datos_krece)
                        atender_celulares(from_number, numero_limpio,
                                          "Aquí está mi captura de Krece")
                        continue
                    if es_krece:
                        send_whapi_message(from_number,
                            "No pude leer bien la captura. ¿Me confirmas por escrito tu nivel y el monto que te aparece en *Celulares*?")
                        continue
                    # No es una captura de Krece: sigue abajo como cualquier otra foto

                # Cualquier otra foto, video, documento o nota de voz
                if msg_type in ("image", "video", "document", "audio", "voice"):
                    send_whapi_message(from_number,
                        "Soy un asistente con inteligencia artificial y no puedo ver fotos, "
                        "videos ni documentos, ni escuchar notas de voz 🙏 Por favor "
                        "escríbeme tu consulta por aquí y con gusto te ayudo 📝")
                continue        

            # ── Obtener body aquí para que esté disponible en ambos flujos ──────
            body = msg.get("text", {}).get("body", "").strip()
            if not body:
                continue

            # ── Comando para limpiar historial (administradores y números de prueba)
            if (body.strip().lower() in ("reset", "reset_historial")
                    and (numero_limpio in ADMINISTRADORES
                         or numero_limpio in NUMEROS_PRUEBA_CELULARES)):
                try:
                    supabase.table("Clientes").delete().eq("numero", numero_limpio).execute()
                    with buffer_lock:
                        datos_buffer = buffer_mensajes.pop(numero_limpio, None)
                    if datos_buffer and datos_buffer.get("timer"):
                        datos_buffer["timer"].cancel()
                    pausas_activas.pop(numero_limpio, None)
                    consultas_admin.pop(numero_limpio, None)
                    memoria_admin_ia.pop(numero_limpio, None)
                    krece_supuesto.discard(numero_limpio)
                    krece_linea_disponible.pop(numero_limpio, None)
                    tecnico_celulares.pop(numero_limpio, None)
                    confirmar_celular.pop(numero_limpio, None)
                    send_whapi_message(from_number, "✅ Historial limpiado. Puedes empezar una conversación nueva.")
                except Exception as e:
                    send_whapi_message(from_number, f"❌ Error limpiando historial: {e}")
                continue

            # ── Números de prueba: cambiar entre flujo de pantallas y celulares ─
            if numero_limpio in NUMEROS_PRUEBA_CELULARES:
                if body.strip().lower() == "modo pantallas":
                    modo_prueba_pantallas.add(numero_limpio)
                    send_whapi_message(from_number, "🔧 Modo pantallas (técnico) activado. "
                                       "Escribe *modo celulares* para volver.")
                    continue
                if body.strip().lower() == "modo celulares":
                    modo_prueba_pantallas.discard(numero_limpio)
                    send_whapi_message(from_number, "📱 Modo celulares activado.")
                    continue

            # ── Aviso de IA la primera vez que escribe ─────────────────────────
            if numero_limpio not in ADMINISTRADORES:
                registrar_cliente(numero_limpio, msg.get("from_name", ""))

            # ── Administradores: solo modo administrador ──────────────────────
            # Inventario, costos, ubicación → IA con el inventario completo.
            # Cotizaciones → cálculo exacto en Python. Nunca van al flujo de clientes.
            if numero_limpio in ADMINISTRADORES:
                try:
                    if marcar_compra(from_number, body):
                        continue
                    if (es_pregunta_inventario(body)
                            or not atender_admin(from_number, numero_limpio, body)):
                        atender_admin_ia(from_number, numero_limpio, body)
                except Exception as e:
                    print(f"Error en modo administrador: {e}")
                    send_whapi_message(from_number, f"❌ Error en la consulta: {e}")
                continue

            # ── Técnicos: cambio entre pantallas y celulares (Python, sin IA) ──
            texto_min = body.lower()
            pendiente = confirmar_celular.pop(numero_limpio, None)
            if pendiente and time.time() - pendiente[1] < 600:
                if any(p in texto_min for p in PALABRAS_PANTALLA):
                    tecnico_celulares.pop(numero_limpio, None)
                    if len(body.split()) <= 3:
                        body = "pantalla " + pendiente[0]
                elif any(p in texto_min for p in PALABRAS_TELEFONO):
                    tecnico_celulares[numero_limpio] = time.time() + 1800
                    agregar_al_buffer(from_number, numero_limpio, pendiente[0] + "\n" + body)
                    continue
            elif any(p in texto_min for p in PALABRAS_PANTALLA):
                tecnico_celulares.pop(numero_limpio, None)

            # ── Determinar comportamiento según el número ──────────────────────
            es_cliente_celulares = (((numero_limpio in NUMEROS_PRUEBA_CELULARES
                                      or numero_limpio not in NUMEROS_AUTORIZADOS)
                                     and numero_limpio not in modo_prueba_pantallas)
                                    or tecnico_celulares.get(numero_limpio, 0) > time.time())

            # ── Flujo de celulares ────────────────────────────────────────────
            if es_cliente_celulares:
                print(f"Cliente celulares: {numero_limpio}")
                agregar_al_buffer(from_number, numero_limpio, body)
                continue

            # ── Flujo original para clientes de repuestos (sin tocar) ──────────
            if (numero_limpio not in NUMEROS_AUTORIZADOS
                    and numero_limpio not in modo_prueba_pantallas):
                print("Número no autorizado: " + numero_limpio)
                continue

            # Técnico que habla de un teléfono (sin mencionar pantalla): se decide en Python
            texto_tec = body.lower()
            if not any(p in texto_tec for p in PALABRAS_PANTALLA):
                pide_compra = any(p in texto_tec for p in PALABRAS_COMPRA)
                if pide_compra and any(p in texto_tec for p in ("celular", "telefono", "teléfono", "equipo")):
                    tecnico_celulares[numero_limpio] = time.time() + 1800
                    agregar_al_buffer(from_number, numero_limpio, body)
                    continue
                if any(p in texto_tec for p in ("celular", "telefono", "teléfono")):
                    confirmar_celular[numero_limpio] = (body, time.time())
                    send_whapi_message(from_number, "¿Buscas el *teléfono completo* para comprarlo 📱 o la *pantalla* 🔧?")
                    continue

            if from_number in stock_bajo_pendiente:
                if any(palabra in body.lower() for palabra in PALABRAS_SI):
                    info = stock_bajo_pendiente.pop(from_number)
                    notificar_stock_bajo(from_number, info["producto"], info["stock"])
                    send_whapi_message(from_number, "✅ Listo, avisamos al asesor para coordinar el apartado.")
                    continue
                else:
                    stock_bajo_pendiente.pop(from_number)               

            # Mensajes de cortesía ("ya voy", "gracias", "ok"): respuesta fija, sin IA ni Odoo
            respuesta_cortesia = respuesta_cortesia_repuestos(body)
            if respuesta_cortesia:
                print(f"Cortesía repuestos ({numero_limpio}): {body}")
                send_whapi_message(from_number, respuesta_cortesia)
                continue

            productos, compatibles, similares = consultar_odoo(body)
            stock_bajo_info = None

            if productos or compatibles:
                contexto_odoo = ""

                if productos:
                    contexto_odoo += "\n\nINFORMACIÓN DEL INVENTARIO:\n"
                    for p in productos:
                        precio_usd, precio_bs = calcular_precio_bs(p['list_price'])
                        stock = int(p['qty_available'])
                        nombre = p['name']
                        bs_str = f" Bs. {precio_bs:,}" if precio_bs is not None else ""
                        contexto_odoo += f"- {nombre}: ${precio_usd}{bs_str} | Stock: {stock} unidades\n"
                        if stock_bajo_info is None and 1 <= stock <= 2:
                            stock_bajo_info = {"producto": nombre, "stock": stock}

                if compatibles:
                    contexto_odoo += "\n\nPRODUCTOS COMPATIBLES:\n"
                    if isinstance(compatibles, list):
                        for comp in compatibles:
                            precio_usd, precio_bs = calcular_precio_bs(comp['list_price'])
                            stock = int(comp['qty_available'])
                            nombre = comp['name']
                            modelo_pedido = comp.get('_compatible_con', '')
                            ref = comp.get('_referencia', '')
                            bs_str = f" Bs. {precio_bs:,}" if precio_bs is not None else ""
                            contexto_odoo += f"- {nombre} (compatible con {ref or modelo_pedido}): ${precio_usd}{bs_str} | Stock: {stock} unidades\n"
                            if stock_bajo_info is None and 1 <= stock <= 2:
                                stock_bajo_info = {"producto": nombre, "stock": stock}
                    else:
                        precio_usd, precio_bs = calcular_precio_bs(compatibles['list_price'])
                        stock = int(compatibles['qty_available'])
                        nombre = compatibles['name']
                        modelo_pedido = compatibles.get('_compatible_con', '')
                        bs_str = f" Bs. {precio_bs:,}" if precio_bs is not None else ""
                        contexto_odoo += f"- {nombre} (compatible con {modelo_pedido}): ${precio_usd}{bs_str} | Stock: {stock} unidades\n"
                        if stock_bajo_info is None and 1 <= stock <= 2:
                            stock_bajo_info = {"producto": nombre, "stock": stock}
               

                if similares:
                    contexto_odoo += "\n\nMODELOS NO ENCONTRADOS:\n"
                    for ref, lista_sim in similares:
                        contexto_odoo += f"- {ref}: no encontrado exacto\n"

                if not obtener_tasa_bcv():
                    contexto_odoo += "\nNOTA: La tasa BCV no está disponible. Muestra los precios SOLO en USD, no inventes ni muestres bolívares.\n"

                historial = cargar_historial(from_number)
                historial.append({"role": "user", "content": body + contexto_odoo})
                if len(historial) > 4:
                    historial = historial[-4:]

                response = client.messages.create(
                    model="claude-haiku-5-5",
                    max_tokens=300,
                    system=get_system_prompt(),
                    messages=historial
                )
                reply = texto_respuesta(response)
                registrar_uso("repuestos", response)

                if "DERIVAR_CELULARES" in reply:
                    tecnico_celulares[numero_limpio] = time.time() + 1800
                    agregar_al_buffer(from_number, numero_limpio, body)
                    continue
                if "CONFIRMAR_CELULAR" in reply:
                    confirmar_celular[numero_limpio] = (body, time.time())
                    send_whapi_message(from_number, "¿Buscas el *teléfono completo* para comprarlo 📱 o la *pantalla* 🔧?")
                    continue
                if "DERIVAR_TECNICO" in reply:
                    notificar_asesor(ASESOR_TECNICO, "celulares o servicio técnico", from_number)
                    reply = "Un momento, un asesor te atenderá enseguida 👋"
                elif "DERIVAR_ACCESORIOS" in reply:
                    notificar_asesor(ASESOR_ACCESORIOS, "accesorios", from_number)
                    reply = "Un momento, un asesor te atenderá enseguida 👋"
                elif "DATOS_PAGO" in reply:
                    reply = "📱 *Datos de Pago Móvil*\n\n04149202844\nJ401188613\n0134\nServicio Técnico Cellcenter"

                if stock_bajo_info:
                    stock_bajo_pendiente[from_number] = stock_bajo_info

                historial.append({"role": "assistant", "content": reply})
                guardar_historial(from_number, historial)
                send_whapi_message(from_number, reply)

            elif similares:
                lineas = []
                if similares and isinstance(similares[0], tuple) and isinstance(similares[0][0], str):
                    for ref, lista_similares in similares:
                        lineas.append(f"*{ref}:*")
                        for _, nombre_producto, stock, es_compatible, modelo_compatible in lista_similares:
                            icono = "✅" if stock > 0 else "❌"
                            if es_compatible:
                                lineas.append(f"  {icono} {nombre_producto} (compatible con {modelo_compatible})")
                            else:
                                lineas.append(f"  {icono} {nombre_producto}")
                else:
                    for _, nombre_producto, stock, es_compatible, modelo_compatible in similares:
                        icono = "✅" if stock > 0 else "❌"
                        if es_compatible:
                            lineas.append(f"{icono} {nombre_producto} (compatible con {modelo_compatible})")
                        else:
                            lineas.append(f"{icono} {nombre_producto}")

                lista = "\n".join(lineas)
                reply = (
                    f"Soy un sistema automatizado 🤖. Para consultar disponibilidad, "
                    f"escribe la *marca y modelo exacto* sin errores de escritura.\n\n"
                    f"Los modelos más parecidos que tenemos son:\n{lista}\n\n"
                    f"Si no ves tu modelo aquí, es porque no lo tenemos disponible.\n\n"
                    f"✏️ *Vuelve a escribir tu modelo*"
                )
                registrar_producto_no_encontrado(numero_limpio, body)
                send_whapi_message(from_number, reply)

            else:
                # Registrar producto no encontrado
                registrar_producto_no_encontrado(numero_limpio, body)
                response = client.messages.create(
                    model="claude-haiku-5-5",
                    max_tokens=300,
                    system=get_system_prompt(),
                    messages=[{"role": "user", "content": body + "\n\nINFORMACIÓN DEL INVENTARIO:\nEste producto NO existe en el inventario. Stock: 0. No inventes productos ni precios."}]
                )
                reply = texto_respuesta(response)
                registrar_uso("repuestos-no-encontrado", response)

                if "DERIVAR_CELULARES" in reply:
                    tecnico_celulares[numero_limpio] = time.time() + 1800
                    agregar_al_buffer(from_number, numero_limpio, body)
                    continue
                if "CONFIRMAR_CELULAR" in reply:
                    confirmar_celular[numero_limpio] = (body, time.time())
                    send_whapi_message(from_number, "¿Buscas el *teléfono completo* para comprarlo 📱 o la *pantalla* 🔧?")
                    continue
                if "DERIVAR_TECNICO" in reply:
                    notificar_asesor(ASESOR_TECNICO, "celulares o servicio técnico", from_number)
                    reply = "Un momento, un asesor te atenderá enseguida 👋"
                elif "DERIVAR_ACCESORIOS" in reply:
                    notificar_asesor(ASESOR_ACCESORIOS, "accesorios", from_number)
                    reply = "Un momento, un asesor te atenderá enseguida 👋"
                elif "DATOS_PAGO" in reply:
                    reply = "📱 *Datos de Pago Móvil*\n\n04149202844\nJ401188613\n0134\nServicio Técnico Cellcenter"

                send_whapi_message(from_number, reply)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        import traceback
        print(f"Error en webhook: {e}")
        print(traceback.format_exc())
        return jsonify({"status": "error", "detail": str(e)}), 200

# ── Relación con clientes de celulares: nombre, bienvenida y contacto en Google ──
HORA_BIENVENIDA = 10
DIAS_PARA_REPREGUNTAR = 7
MAX_BIENVENIDAS_DIA = 20
SALUDOS_BIENVENIDA = [
    "{saludo} Gracias por escribirnos a *Cell Center 4620* 🙌 Te dejo nuestro contacto para que nos "
    "guardes: así verás en nuestros estados las promociones y los equipos nuevos que van llegando. "
    "¡Nosotros también te guardamos! 📲",
    "{saludo} Fue un gusto atenderte en *Cell Center 4620* 📱 Guarda nuestro número y no te pierdas "
    "las ofertas y novedades que publicamos en los estados. ¡Ya te tenemos agendado! ✅",
    "{saludo} Gracias por tu mensaje 🙏 Aquí tienes el contacto de *Cell Center 4620*: guárdalo y "
    "verás en nuestros estados los equipos que van llegando y las promociones del día 🔥",
    "{saludo} Te escribimos de *Cell Center 4620* 😊 Agrega este número a tus contactos para ver en "
    "los estados nuestras promociones y novedades. ¡Nosotros ya te guardamos! 📲",
]
# Seguimiento de venta: va en lugar de la bienvenida si el cliente preguntó por un
# equipo. A quien ya compró, Omar lo marca con "compró <número>" y no le llega nada.
SEGUIMIENTOS = [
    "{saludo} Te escribimos de *Cell Center 4620*. ¿Te animas con el *{equipo}*? Si quieres "
    "te lo apartamos hoy y pasas a buscarlo 📲 Cualquier duda con el pago, aquí estamos. "
    "¡Te dejo nuestro contacto para que nos guardes!",
    "{saludo} Soy de *Cell Center 4620* 📱 ¿Pudiste decidirte por el *{equipo}*? Todavía lo "
    "tenemos y te lo podemos apartar para hoy. Te dejo nuestro contacto para cualquier duda 🙌",
]
NO_ES_NOMBRE = {"mama", "mamá", "papa", "papá", "mi", "el", "la", "de", "del", "dios",
                "amor", "vida", "cell", "tienda", "celular", "telefono", "teléfono",
                "movil", "móvil", "shop", "store", "servicio", "repuestos", "cliente",
                # apodos y formas de cariño
                "negro", "negra", "negrito", "negrita", "gordo", "gorda", "gordito",
                "gordita", "flaco", "flaca", "flaquito", "flaquita", "bebe", "bebé",
                "beba", "baby", "bby", "princesa", "reina", "rey", "chamo", "chama",
                "pana", "mami", "papi", "nena", "nene", "bella", "bello", "linda",
                "lindo", "guapa", "guapo", "morena", "moreno", "catira", "catire",
                "chiquita", "chiquito", "tia", "tía", "tio", "tío", "abuela", "abuelo",
                "hermano", "hermana", "primo", "prima", "jefe", "jefa", "señor",
                "señora", "familia", "bendecido", "bendecida", "feliz", "hola", "yo",
                "soy", "usuario", "user", "whatsapp", "desconocido",
                # negocios
                "variedades", "inversiones", "multiservicios", "comercial",
                "comercializadora", "distribuidora", "bodega", "bodegon", "bodegón",
                "ventas", "accesorios", "importadora", "tech", "store", "market",
                "licoreria", "licorería", "ferreteria", "ferretería", "farmacia",
                "panaderia", "panadería", "peluqueria", "peluquería", "barberia",
                "barbería", "taller", "motos", "autos", "ca", "cristo", "jesucristo", "jehova", "jehová", "yahveh", "amen", "amén", "aleluya", "bendiciones"}
TITULOS_NOMBRE = {"dr", "dra", "doctor", "doctora", "lic", "licdo", "licda", "ing",
                  "sr", "sra", "srta", "prof", "profe", "don", "doña", "abg", "tsu"}
FRASE_RELIGIOSA = {"es", "te", "ama", "vive", "reina", "salva", "mi", "su", "nos", "guia", "guía", "cuida", "bendice", "protege"}


def limpiar_nombre(texto):
    """Primer nombre, solo si parece un nombre real."""
    # Emojis, símbolos y números pegados se vuelven espacios: "💕Ángeles💕" -> "Ángeles"
    limpio = re.sub(r"[^\w\s]|_|\d", " ", texto or "")
    palabras = [w for w in limpio.split() if len(w) >= 3]   # "J.Carlos" -> "Carlos"
    while palabras and palabras[0].lower() in TITULOS_NOMBRE:   # "Dra. María" -> "María"
        palabras = palabras[1:]
    if not palabras:
        return None
    if len(palabras) > 1 and palabras[0].lower() in ("jesus", "jesús") and palabras[1].lower() in FRASE_RELIGIOSA: return None
    p = palabras[0]
    if not p.isalpha() or len(p) > 14 or p.lower() in NO_ES_NOMBRE:
        return None
    if p.islower() and len(p) >= 10: return None   # "pedromarquez" parece usuario, no nombre
    if not re.search(r"[aeiouáéíóú]", p.lower()) or len(set(p.lower())) == 1: return None   # "Mrs", "Hhh" no son nombres
    return p.capitalize()


def detectar_nombre(texto):
    """'me llamo Juan' o 'mi nombre es Juan' -> 'Juan' (Python, sin IA)."""
    m = re.search(r"\b(?:me llamo|mi nombre es)\s+(.{1,40})", texto or "", re.IGNORECASE)
    return limpiar_nombre(m.group(1)) if m else None


def debe_pedir_nombre(perfil):
    """Sin nombre y sin haberlo preguntado en los últimos 7 días."""
    if perfil.get("nombre"):
        return False
    ultimo = perfil.get("nombre_preguntado")
    if not ultimo:
        return True
    try:
        fecha = datetime.fromisoformat(ultimo.replace("Z", "")[:19])
        return (datetime.utcnow() - fecha).days >= DIAS_PARA_REPREGUNTAR
    except Exception:
        return False


def marcas_nombre(numero_limpio, reply):
    """Quita PREGUNTE_NOMBRE y NOMBRE:<x> de la respuesta y los guarda."""
    if "PREGUNTE_NOMBRE" in reply or "con quién tengo el gusto" in reply.lower():
        guardar_perfil(numero_limpio, nombre_preguntado=datetime.utcnow().isoformat() + "Z")
        reply = reply.replace("PREGUNTE_NOMBRE", "")
    m = re.search(r"NOMBRE:\s*<?([^\s>]*)>?", reply)
    if m:
        nombre = limpiar_nombre(m.group(1))
        if nombre:
            guardar_perfil(numero_limpio, nombre=nombre)
        reply = reply.replace(m.group(0), "")
    return reply.strip()


def registrar_cliente(numero_limpio, from_name):
    """Clientes de celulares: guarda nombre de WhatsApp y fecha de primer contacto."""
    if numero_limpio in NUMEROS_AUTORIZADOS or numero_limpio in modo_prueba_pantallas:
        return   # técnicos: por ahora sin nombre
    try:
        r = supabase.table("Clientes").select("nombre").eq("numero", numero_limpio).execute()
        fila = r.data[0] if r.data else None
        if fila is None:   # cliente totalmente nuevo
            guardar_perfil(numero_limpio, nombre=limpiar_nombre(from_name),
                           primer_contacto=datetime.utcnow().isoformat() + "Z")
        elif not fila.get("nombre"):
            guardar_perfil(numero_limpio, nombre=limpiar_nombre(from_name))
    except Exception as e:
        print(f"Error registrando cliente: {e}")


def guardar_contacto_google(numero, nombre):
    """Guarda al cliente en Google Contacts. Sin credenciales en Render, no hace nada."""
    cid = os.environ.get("GOOGLE_CONTACTS_CLIENT_ID")
    sec = os.environ.get("GOOGLE_CONTACTS_CLIENT_SECRET")
    ref = os.environ.get("GOOGLE_CONTACTS_REFRESH_TOKEN")
    if not (cid and sec and ref):
        print("Google Contacts sin configurar: contacto no guardado")
        return
    try:
        t = requests.post("https://oauth2.googleapis.com/token", timeout=10, data={
            "client_id": cid, "client_secret": sec,
            "refresh_token": ref, "grant_type": "refresh_token"})
        t.raise_for_status()
        requests.post("https://people.googleapis.com/v1/people:createContact", timeout=10,
                      headers={"Authorization": "Bearer " + t.json()["access_token"]},
                      json={"names": [{"givenName": nombre or "Cliente", "familyName": "Cell Center"}],
                            "phoneNumbers": [{"value": "+" + numero}]}).raise_for_status()
    except Exception as e:
        print(f"Error guardando contacto en Google: {e}")


def send_whapi_contacto(to):
    """Envía la tarjeta de contacto de la tienda (un toque para guardarla)."""
    vcard = ("BEGIN:VCARD\nVERSION:3.0\nFN:Cell Center 4620\nORG:Cell Center 4620\n"
             "TEL;type=CELL;waid=584121889224:+58 412 1889224\nEND:VCARD")
    try:
        requests.post(f"{WHAPI_API_URL}/messages/contact",
                      json={"to": to, "name": "Cell Center 4620", "vcard": vcard},
                      headers={"Authorization": f"Bearer {WHAPI_TOKEN}"}, timeout=10).raise_for_status()
    except Exception as e:
        print(f"Error enviando contacto Whapi: {e}")


def tuvo_conversacion(historial_json):
    """True si el cliente escribió al menos 2 mensajes (no un simple 'hola')."""
    try:
        historial = json.loads(historial_json or "[]")
    except Exception:
        return False
    return sum(1 for m in historial if m.get("role") == "user") >= 2


def marcar_compra(from_number, body):
    """Admin: 'compró 0414-210-8150' -> ese cliente ya no recibe bienvenida ni seguimiento."""
    m = re.match(r"\s*compr[oó]\s+(.+)", body or "", re.IGNORECASE)
    if not m:
        return False
    digitos = re.sub(r"\D", "", m.group(1))
    if len(digitos) < 10:   # "compro un iPhone 13…" no es un número: sigue normal
        return False
    if digitos.startswith("0"):
        digitos = "58" + digitos[1:]
    elif len(digitos) == 10:
        digitos = "58" + digitos
    r = (supabase.table("Clientes").update({"bienvenida_enviada": True})
         .eq("numero", digitos).execute())
    if r.data:
        send_whapi_message(from_number, f"✅ Marcado como cliente que compró: +{digitos}. "
                                        f"No le llegará el seguimiento.")
    else:
        send_whapi_message(from_number, f"No encontré a +{digitos} entre los clientes del bot. "
                                        f"Revisa el número.")
    return True


def enviar_bienvenidas():
    """Bienvenida a los clientes que escribieron por primera vez el día anterior."""
    hoy0 = datetime.now(pytz.timezone("America/Caracas")).replace(hour=0, minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    desde = (hoy0 - timedelta(days=2)).astimezone(pytz.utc).strftime(fmt)
    hasta = hoy0.astimezone(pytz.utc).strftime(fmt)
    r = (supabase.table("Clientes").select("numero,nombre,respondio,modelo_interes")
         .gte("primer_contacto", desde).lt("primer_contacto", hasta)
         .is_("bienvenida_enviada", "null").execute())
    enviados = 0
    for c in r.data or []:
        numero, nombre = c["numero"], c.get("nombre")
        if enviados >= MAX_BIENVENIDAS_DIA:
            break   # tope diario: el resto queda para mañana
        if not c.get("respondio"):
            continue   # no volvió a escribir después de ver equipos o precios
        # Se marca primero: así nunca se envía dos veces, aunque Render reinicie
        marca = (supabase.table("Clientes").update({"bienvenida_enviada": True})
                 .eq("numero", numero).is_("bienvenida_enviada", "null").execute())
        if not marca.data:
            continue
        guardar_contacto_google(numero, nombre)
        saludo = f"¡Hola, {nombre}! 😊" if nombre else "¡Hola! 😊"
        equipo = (c.get("modelo_interes") or "").split(" / ")[-1].strip()
        if equipo:   # preguntó por un equipo: seguimiento de venta
            texto = random.choice(SEGUIMIENTOS).format(saludo=saludo, equipo=equipo)
        else:
            texto = random.choice(SALUDOS_BIENVENIDA).format(saludo=saludo)
        send_whapi_message(numero, texto)
        send_whapi_contacto(numero)
        time.sleep(random.uniform(8, 15))   # pausa entre envíos para cuidar el número
        enviados += 1


def _ciclo_bienvenidas():
    ultimo_dia = None
    while True:
        time.sleep(60)
        try:
            ahora = datetime.now(pytz.timezone("America/Caracas"))
            if HORA_BIENVENIDA <= ahora.hour < 20 and ultimo_dia != ahora.date():
                ultimo_dia = ahora.date()
                enviar_bienvenidas()
        except Exception as e:
            print(f"Error en el ciclo de bienvenidas: {e}")


threading.Thread(target=_ciclo_bienvenidas, daemon=True).start()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
