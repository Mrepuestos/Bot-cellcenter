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
from datetime import datetime
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
    inventario_admin,
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
                    "frente a La Asunción, a 30 mtrs")

# ── Modelo que atiende el flujo de celulares ──────────────────────────────────
MODELO_CELULARES = "claude-sonnet-5"

# ── Mensaje predefinido con el que llegan los clientes de Krece ───────────────
MENSAJE_KRECE = "quiero comprar con krece"

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
             "linea_krece": None, "modelo_interes": None}
    try:
        r = supabase.table("Clientes").select(
            "canal_pago,canal_extra,nivel_cliente,linea_krece,modelo_interes"
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
    m = re.search(r"\bnivel\s*([1-6])\b", t) or re.search(r"\b([1-6])\b", t)
    return m.group(1) if m else None


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
    m = re.search(r"(?:linea|l\u00ednea|aprobad[oa]|credito|cr\u00e9dito|limite|l\u00edmite)"
                  r"[^\d]{0,15}(\d{2,5})", texto.lower())
    if m:
        return float(m.group(1))
    m = re.search(r"\b(\d{2,5})\s*(?:\$|d[oó]lares|usd)?\s*(?:de\s+)?"
                  r"(?:linea|línea|cr[eé]dito|l[ií]mite)", texto.lower())
    if m:
        return float(m.group(1))
    m = re.search(r"\$\s*(\d{2,5})", texto)
    return float(m.group(1)) if m else None


RELLENO_LINEA = {"y", "mi", "es", "de", "la", "linea", "línea", "tengo", "son", "me",
                 "aprobaron", "aprobado", "aprobada", "limite", "límite", "dolares",
                 "dólares", "usd", "oro", "plata", "azul", "platino", "nivel", "ok",
                 "si", "sí", "con", "krece", "soy", "como"}


def detectar_linea_suelta(texto):
    """Número solo como línea de Krece: "400", "400 nivel oro", "$400".
    Solo si en ese renglón no hay nada más (ni modelo ni GB)."""
    for renglon in texto.lower().split("\n"):
        t = re.sub(r"nivel\s+\w+", " ", renglon)
        t = re.sub(r"[^\w\s]", " ", t)
        palabras = [p for p in t.split() if p not in RELLENO_LINEA]
        if len(palabras) == 1 and palabras[0].isdigit():
            n = int(palabras[0])
            if 50 <= n <= 5000 and n not in (64, 128, 256, 512):
                return float(n)
    return None


def dato_faltante(canal, perfil):
    """Pregunta para pedir el dato que falta para cotizar, o None."""
    if canal == "krece" and not perfil.get("nivel_cliente"):
        return ("Para cotizarte con Krece, ¿qué nivel tienes en la app? "
                "(Azul, Plata, Oro o Platino) 📲")
    if canal == "krece" and not perfil.get("linea_krece"):
        return ("Para darte las cuotas con Krece solo me falta tu línea aprobada. "
                "¿Cuánto te aparece en la app? 📲")
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
            if not nivel or not linea:
                if not nivel and not linea:
                    falta = "su nivel y su línea aprobada"
                elif not nivel:
                    falta = f"su nivel (ya sabes que su línea es ${float(linea):.0f})"
                else:
                    falta = (f"su línea aprobada (su nivel {nivel} ya está anotado: no se lo "
                             f"pidas ni digas que ya lo sabías; si te lo acaba de decir, "
                             f"confírmalo natural, ej. '¡Perfecto, nivel {nivel.capitalize()}!')")
                lineas.append(f"  El equipo SÍ tiene precio, pero para cotizar Krece falta "
                              f"{falta}. Pídeselo en una frase. NO respondas DERIVAR_PRECIO.")
                continue
            hubo = False
            try:
                plazos = precios.plazos_krece(nivel)
            except (ValueError, KeyError):
                lineas.append(f"  El nivel '{nivel}' de Krece no es válido. "
                              f"Pídele que confirme: Azul, Plata, Oro o Platino.")
                continue
            for plazo in plazos:
                k = precios.krece(p, nivel, plazo, linea=linea)
                if not k.get("aplica"):
                    continue
                hubo = True
                extra = " (inicial ajustada a su línea)" if k["topado_por_linea"] else ""
                lineas.append(f"  Krece {plazo} cuotas: inicial ${k['inicial']} "
                              f"+ {plazo} x ${k['monto_cuota']}{extra}")
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


def sin_iphone(perfil):
    """True si al cliente no se le muestran iPhone: Krece Azul o CrediTienda."""
    if perfil.get("canal_pago") == "creditienda":
        return True
    return (perfil.get("canal_pago") == "krece"
            and perfil.get("nivel_cliente") == "azul")


def inicial_corta(eq, canal, perfil):
    """Una línea corta con lo que paga de entrada. None si no aplica."""
    p = eq["precio_paralelo"]
    try:
        if canal == "krece":
            nivel, linea = perfil.get("nivel_cliente"), perfil.get("linea_krece")
            if not nivel or not linea:
                return f"de contado ${int(p)}"
            plazo = precios.plazos_krece(nivel)[-1]   # el plazo más largo = cuota más baja
            k = precios.krece(p, nivel, plazo, linea=linea)
            if not k.get("aplica"):
                return None
            return f"Krece: inicial ${k['inicial']} + {plazo} x ${k['monto_cuota']}"
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
    for eq in listar_mas_baratos(cantidad + 4, excluir_iphone=sin_iphone(perfil)):
        texto = inicial_corta(eq, canal, perfil)
        if texto:
            salida.append(f"  • {nombre_completo(eq)} — {texto}")
        if len(salida) >= cantidad:
            break
    return "\n".join(salida) or "  No hay equipos disponibles en este momento."


def bloque_rangos(perfil=None):
    """Tres equipos por rango de precio, para cuando no dice modelo."""
    equipos = listar_por_rango(excluir_iphone=sin_iphone(perfil or {}))
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
            model="claude-haiku-4-5-20251001",
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
    url = f"{WHAPI_API_URL}/messages/location"
    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "to": to,
        "latitude": TIENDA_LAT,
        "longitude": TIENDA_LNG,
        "name": TIENDA_NOMBRE,
        "address": TIENDA_DIRECCION,
    }
    try:
        requests.post(url, json=payload, headers=headers, timeout=10).raise_for_status()
    except Exception as e:
        print(f"Error enviando ubicación Whapi: {e}")
        send_whapi_message(to, f"📍 *{TIENDA_NOMBRE}*\n{TIENDA_DIRECCION}")


def asesor_disponible():
    hora = datetime.now(pytz.timezone("America/Caracas")).hour
    return 6 <= hora < 22


def msg_asesor(de_dia, de_noche):
    return de_dia if asesor_disponible() else de_noche


def notificar_intencion_compra(numero_cliente, perfil, equipos, online=False):
    numero = "+" + numero_cliente.replace("@s.whatsapp.net", "")
    modelo = perfil.get("modelo_interes") or (
        nombre_completo(equipos[0]) if equipos else "sin especificar")
    partes = ["🟢 *INTENCIÓN DE COMPRA*", f"Cliente: {numero}", f"Equipo: {modelo}"]
    if perfil.get("canal_pago"):
        partes.append(f"Paga con: {perfil['canal_pago']}")
    if perfil.get("nivel_cliente"):
        partes.append(f"Nivel: {perfil['nivel_cliente']}")
    if perfil.get("linea_krece"):
        partes.append(f"Línea aprobada: ${float(perfil['linea_krece']):.0f}")
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
        return None, None, True

    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    mime = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
    b64 = base64.standard_b64encode(resp.content).decode("utf-8")

    prompt = """Si esta imagen es una captura de la app de Krece, extrae el NIVEL del
cliente (Azul, Plata, Oro o Platino) y la LÍNEA APROBADA o límite de crédito
en dólares. Responde SOLO con JSON: {"es_krece": true, "nivel": "plata", "linea": 220}
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
        return datos.get("nivel"), datos.get("linea"), bool(datos.get("es_krece"))
    except Exception as e:
        print(f"Error leyendo captura Krece: {e}")
        return None, None, True

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

REGLA PRINCIPAL: Cuando el inventario muestre productos con stock mayor a 0, SIEMPRE da el precio. NUNCA digas que no está disponible si hay stock. NUNCA preguntes si es para pantalla o celular, asume que siempre es para pantalla.

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

2. CELULARES (comprar celular completo): responde exactamente: "DERIVAR_TECNICO"
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

def get_system_prompt_celulares(info_equipo, perfil, rangos, lista_corta):
    """Prompt del vendedor de celulares. Recibe solo el equipo consultado,
    nunca el catálogo completo."""
    tz = pytz.timezone("America/Caracas")
    ahora = datetime.now(tz)
    es_domingo = ahora.weekday() == 6
    horario_hoy = "9:00am a 2:00pm" if es_domingo else "8:30am a 5:30pm"
    dia_hoy = "domingo" if es_domingo else "lunes a sábado"
    estado_tienda = "ABIERTA" if esta_abierto() else "CERRADA"

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
            datos.append(f"nivel {perfil['nivel_cliente']}")
    if perfil.get("linea_krece"):
        datos.append(f"línea aprobada ${float(perfil['linea_krece']):.0f}")
    if perfil.get("modelo_interes"):
        datos.append(f"le interesa el {perfil['modelo_interes']}")
    conocidos = ("Ya sabes de él: " + ", ".join(datos) +
                 ". No se lo vuelvas a preguntar.") if datos else ""
    if perfil.get("_krece_supuesto"):
        conocidos += (" OJO: él NO te dio nivel ni línea; tú supusiste Azul con $300. "
                      "Cotiza con los montos de EQUIPO CONSULTADO sin pedirle esos datos. "
                      "Nunca digas 'tu nivel' ni 'tu línea': di que es calculado con "
                      "Azul y $300 por ser lo más común.")

    fijo = """Eres el asistente de ventas de Cell Center 4620, ...

El estado de la tienda (abierta o cerrada) y el horario de HOY están en DATOS DE ESTA CONVERSACIÓN, al final.
Horario general: lunes a sábado 8:30am-5:30pm · domingos y feriados 9:00am-2:00pm

CÓMO HABLAS
Eres venezolano, cálido y directo. Hablas como un vendedor de confianza, no como un robot.
Emojis con moderación, uno o dos por mensaje.
Máximo 4 o 5 líneas por respuesta. La gente lee WhatsApp con el pulgar.
UNA sola pregunta por mensaje. Nunca dos seguidas.
NUNCA uses "hermano", "hermana", "amigo", "pana" ni tratamientos parecidos.
Habla como se habla en Venezuela. NUNCA uses expresiones de otros países como
"te late", "¿qué te late?", "chido", "padre" (por bueno), "órale", "platicar",
"vale" (a la española) o "bacano". En vez de "¿te late?" di "¿te gusta?",
"¿qué te parece?" o "¿te interesa?".
No repitas lo que ya dijiste en el mensaje anterior.
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
Sin esos datos no das ningún número, ni aproximado.
SIEMPRE hay cuota inicial, sin excepción. La línea aprobada es solo un techo:
si el equipo la supera, la inicial sube — nunca la elimina. Usa EXACTAMENTE
los montos de inicial y cuota que aparecen en EQUIPO CONSULTADO. NUNCA digas
que "no hace falta inicial" ni que el cliente "no necesita poner inicial".
NUNCA digas cuántas cuotas son antes de tener el cálculo: varía entre 3 y 10.
Si el cliente te da nivel y línea pero todavía no dijo qué equipo quiere,
pregúntaselo: "¿Qué modelo tienes en mente?"
Los iPhone con Krece solo aplican de nivel Plata en adelante. Si el cliente es
nivel Azul y pregunta por un iPhone, dile que ese equipo requiere Plata o
superior, y pregúntale si quiere ver otra opción o subir de nivel.
Si el cliente llega hablando de Krece sin darte nivel ni línea (con el mensaje
predefinido o con otras palabras), NO le preguntes nivel y línea (salvo que pida
un iPhone: ahí sí pregúntaselos, porque el iPhone requiere Plata o superior): asume que es
Azul con $300 de línea (es el caso del 95% de los que llegan así) y muéstrale
de una vez tres opciones —gama baja, media y alta— cotizadas con esos datos.
Al final, deja abierta la corrección: "Si tu nivel o línea es distinto,
dímelo y te recalculo."
Nunca ofrezcas ni sugieras un iPhone a un cliente Azul, ni siquiera como
opción a mostrar. Si él mismo lo pide, ahí sí explícale la restricción.
Si el cliente dice que no tiene Krece o nunca lo ha usado, NO es un obstáculo:
dile que en la tienda le hacemos el registro en el momento, y que debe ser
mayor de edad y traer su cédula laminada. Dale los montos de EQUIPO CONSULTADO
diciendo SIEMPRE que son aproximados: el monto real lo da Krece cuando quede
registrado.
Si ese cliente sin cuenta pide un iPhone, NO le digas que "tiene" nivel Azul
ni que "suba de nivel": dile que al registrarse empieza en nivel Azul y que el
iPhone se habilita desde nivel Plata, y pregúntale si quiere ver otra opción.

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
Si pregunta dónde quedan, responde exactamente: ENVIAR_UBICACION
Cuando detectes intención de compra (dice que lo quiere, pregunta cómo apartarlo,
o confirma que va a ir), responde exactamente: INTENCION_COMPRA
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
"Lo voy a pensar" -> sin presionar, menciona que los equipos rotan rápido.
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
- Usar la palabra "paralelo"

Si dice "la aplicación" o "la app" sin nombrarla, sigue con el canal que ya
tiene. Si todavía no tiene canal, pregúntale cuál aplicación usa.

"""

    variable = f"""DATOS DE ESTA CONVERSACIÓN

TIENDA
La tienda está ahora: {estado_tienda}
Hoy es {dia_hoy}. El horario de HOY es {horario_hoy}.

CLIENTE
{recordatorio}
{conocidos}

TRES OPCIONES (para cuando no sabe qué quiere)
{rangos}

LISTA CORTA
{lista_corta}

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

Tu tarea: entender QUÉ QUIERE el cliente y elegir hasta 3 equipos del catálogo.

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
- Si pide un criterio en vez de un modelo (fotos, juegos, batería, RAM,
  almacenamiento, presupuesto), elige hasta 3 que cumplan de TODO el
  catálogo, aunque antes le interesara otro modelo, y marca tipo "exacto".
- Si pregunta si hay MÁS opciones ("¿solo esos?", "¿no tienes más?",
  "¿otras marcas?"), NO está eligiendo los que vio: lista vacía y tipo
  "mas_opciones". Si nombra una marca, es tipo "exacto".
CANAL DE PAGO: si en ESTE mensaje el cliente nombra Krece, Cashea o
CrediTienda, aunque lo escriba mal ("caschea", "kashea", "kreze",
"credi tieda"), pon ese canal en "canal": "krece", "cashea" o "creditienda".
Si no nombra ninguno de los tres, pon "canal": null.

Responde SOLO con JSON, sin explicaciones ni markdown:
{{"claves": ["clave1", "clave2"], "tipo": "exacto", "canal": null}}"""

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
    for clave in claves[:3]:
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
    return ordenar_equipos(equipos), tipo, canal_ia


# ── Flujo de celulares ────────────────────────────────────────────────────────

def atender_celulares(from_number, numero_limpio, body):
    """Atiende a un cliente de celulares de punta a punta."""
    perfil = cargar_perfil(numero_limpio)
    cambios = {}
    if (numero_limpio in krece_supuesto and perfil.get("nivel_cliente") == "azul"
            and float(perfil.get("linea_krece") or 0) == 300):
        perfil["_krece_supuesto"] = True   # sigue con el Azul/$300 supuesto

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
    canal = detectado or perfil.get("canal_pago")
    canal_anterior = perfil.get("canal_pago")

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
        nivel = detectar_nivel_krece(body)
        linea = detectar_linea(body)
        if not linea:
            linea = detectar_linea_suelta(body)
        if nivel:
            nivel_antes = perfil.get("nivel_cliente")
            if nivel_antes and nivel != nivel_antes and not linea:
                # Otro nivel sin línea: la anterior (o la supuesta) ya no sirve
                cambios["linea_krece"] = None
                perfil["linea_krece"] = None
            cambios["nivel_cliente"] = nivel
            perfil["nivel_cliente"] = nivel
        if linea:
            cambios["linea_krece"] = linea
            perfil["linea_krece"] = linea
        if nivel or linea:
            # Ya dio un dato real: deja de ser supuesto
            perfil.pop("_krece_supuesto", None)
            krece_supuesto.discard(numero_limpio)
    elif canal == "cashea":
        nivel = detectar_nivel_cashea(body)
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
                    f"modelo ni pide una característica, se refiere a ese o esos.\n")
    else:
        rango_ctx = listar_por_rango(excluir_iphone=sin_iphone(perfil))
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
    equipos, tipo_resultado, canal_ia = interpretar_pedido(body, contexto)

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
    if canal in ("krece", "cashea") and detectar_sin_cuenta(body):
        if canal == "krece":
            cambios.update(nivel_cliente="azul", linea_krece=300)
            perfil.update(nivel_cliente="azul", linea_krece=300)
            perfil.pop("_krece_supuesto", None)   # sin cuenta: es el nivel de entrada
            krece_supuesto.discard(numero_limpio)
        else:
            cambios["nivel_cliente"] = "1"
            perfil["nivel_cliente"] = "1"

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
    elif tipo_resultado == "ninguno":
        info = ("El cliente no está preguntando por un equipo concreto, o pidió "
                "algo que no tenemos ni se parece a nada del catálogo. "
                "No inventes modelos ni precios.")
    else:
        info = bloque_equipo(equipos, canal, perfil)

    # Si está comparando dos canales, se agregan los precios del segundo
    canal_extra = perfil.get("canal_extra")
    if canal_extra and equipos and tipo_resultado in ("exacto", "recomendacion"):
        perfil_extra = dict(perfil)
        perfil_extra["nivel_cliente"] = None   # el nivel guardado es del primer canal
        info += (f"\n\nLOS MISMOS EQUIPOS CON {canal_extra.upper()}:"
                 + bloque_equipo(equipos, canal_extra, perfil_extra))

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
    historial.append({"role": "user", "content": body})
    if len(historial) > 4:
        historial = historial[-4:]

    respuesta = client.messages.create(
        model=MODELO_CELULARES,
        max_tokens=4000,
        system=get_system_prompt_celulares(info, perfil, bloque_rangos(perfil),
                                           bloque_lista_corta(canal, perfil)),
        messages=historial,
    )
    reply = texto_respuesta(respuesta)
    registrar_uso("celulares-respuesta", respuesta)
    if not reply:
        print("La respuesta al cliente llegó vacía")
        reply = "Dame un momento y te confirmo"

    # ── Marcadores ────────────────────────────────────────────────────────────
    if "DERIVAR_TECNICO" in reply:
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
        reply = reply.replace("ENVIAR_UBICACION", "").strip()
        if not reply:
            reply = "Aquí te dejo la ubicación. Te esperamos, un vendedor te atenderá en la tienda"

    quiere_foto = "[FOTO]" in reply
    if quiere_foto:
        reply = reply.replace("[FOTO]", "").strip()

    historial.append({"role": "assistant", "content": reply or "[enviado]"})
    guardar_historial(numero_limpio, historial)

    if reply:
        send_whapi_message(from_number, reply)

    if quiere_foto and equipos:
        url_foto = buscar_foto(equipos[0]["marca"], equipos[0]["modelo"],
                               equipos[0]["almacenamiento"])
        if url_foto:
            send_whapi_image(from_number, url_foto, nombre_completo(equipos[0]))
        else:
            print(f"Sin foto para {nombre_completo(equipos[0])}")
            send_whapi_message(from_number,
                "Por ahora no tengo la foto de ese modelo, pero puedes pasar "
                "por la tienda a verlo en persona 😊")

    if enviar_ubicacion:
        send_whapi_ubicacion(from_number)


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
        lineas.append(f"   Krece {nivel.capitalize()}, línea ${int(linea)}:")
        hubo = False
        for plazo in precios.plazos_krece(nivel):
            k = precios.krece(p, nivel, plazo, linea=linea)
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
                        and not (perfil_img.get("nivel_cliente") and perfil_img.get("linea_krece"))):
                    try:
                        nivel, linea, es_krece = leer_captura_krece(msg.get("image", {}))
                    except Exception as e:
                        print(f"Error procesando captura Krece: {e}")
                        nivel, linea, es_krece = None, None, False
                    if nivel or linea:
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
                            "No pude leer bien la captura. ¿Me confirmas tu nivel y línea aprobada por escrito?")
                        continue
                    # No es una captura de Krece: sigue abajo como cualquier otra foto

                # Cualquier otra foto, video, documento o nota de voz
                if msg_type in ("image", "video", "document", "audio", "voice"):
                    notificar_asesor(ASESOR_CELULARES,
                        "un archivo que el bot no puede ver (foto, video, documento o audio)",
                        from_number)
                    send_whapi_message(from_number, msg_asesor(
                        "Soy un asistente con inteligencia artificial y no puedo ver fotos, "
                        "videos ni documentos, ni escuchar notas de voz 🙏 Ya le avisé a un "
                        "asesor para que lo revise. Si tu consulta se puede escribir, "
                        "cuéntamela por aquí y te ayudo.",
                        "Soy un asistente con inteligencia artificial y no puedo ver fotos, "
                        "videos ni documentos, ni escuchar notas de voz 🙏 Un asesor lo "
                        "revisará mañana a partir de las 6:00 am. Si tu consulta se puede "
                        "escribir, cuéntamela por aquí y te ayudo."))
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
                enviar_aviso_ia(from_number, numero_limpio)

            # ── Administradores: solo modo administrador ──────────────────────
            # Inventario, costos, ubicación → IA con el inventario completo.
            # Cotizaciones → cálculo exacto en Python. Nunca van al flujo de clientes.
            if numero_limpio in ADMINISTRADORES:
                try:
                    if (es_pregunta_inventario(body)
                            or not atender_admin(from_number, numero_limpio, body)):
                        atender_admin_ia(from_number, numero_limpio, body)
                except Exception as e:
                    print(f"Error en modo administrador: {e}")
                    send_whapi_message(from_number, f"❌ Error en la consulta: {e}")
                continue

            # ── Determinar comportamiento según el número ──────────────────────
            es_cliente_celulares = ((numero_limpio in NUMEROS_PRUEBA_CELULARES
                                     or numero_limpio not in NUMEROS_AUTORIZADOS)
                                    and numero_limpio not in modo_prueba_pantallas)

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

            if from_number in stock_bajo_pendiente:
                if any(palabra in body.lower() for palabra in PALABRAS_SI):
                    info = stock_bajo_pendiente.pop(from_number)
                    notificar_stock_bajo(from_number, info["producto"], info["stock"])
                    send_whapi_message(from_number, "✅ Listo, avisamos al asesor para coordinar el apartado.")
                    continue
                else:
                    stock_bajo_pendiente.pop(from_number)               

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
                    model="claude-haiku-4-5-20251001",
                    max_tokens=300,
                    system=get_system_prompt(),
                    messages=historial
                )
                reply = texto_respuesta(response)
                registrar_uso("repuestos", response)

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
                    model="claude-haiku-4-5-20251001",
                    max_tokens=300,
                    system=get_system_prompt(),
                    messages=[{"role": "user", "content": body + "\n\nINFORMACIÓN DEL INVENTARIO:\nEste producto NO existe en el inventario. Stock: 0. No inventes productos ni precios."}]
                )
                reply = texto_respuesta(response)
                registrar_uso("repuestos-no-encontrado", response)

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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
