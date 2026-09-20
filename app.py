from flask import Flask, request, jsonify
import base64
import requests
import anthropic
import os
import time
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
    ordenar_equipos, listar_por_rango, buscar_foto, nombre_completo,
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
    "584241369824",
    "584126093756",
    "584241464083",
    "584241255279"
]

ASESOR_TECNICO = "584149202844"
ASESOR_ACCESORIOS = "584149202844"
ASESOR_STOCK = "584149202844"

# ── Asesores del flujo de celulares ───────────────────────────────────────────
ASESOR_CELULARES   = "584149202844"   # intención de compra y precios sin verificar
ASESOR_CEL_TECNICO = "584220392375"   # servicio técnico y reparaciones
ASESOR_CEL_OTROS   = "584126093756"   # accesorios y todo lo demás

# ── Números que van al flujo de celulares aunque estén autorizados ────────────
# Vaciar la lista ( = [] ) cuando termines de probar.
NUMEROS_PRUEBA_CELULARES = ["584149202844"]

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
    """canal_pago, nivel_cliente, linea_krece y modelo_interes."""
    vacio = {"canal_pago": None, "nivel_cliente": None,
             "linea_krece": None, "modelo_interes": None}
    try:
        r = supabase.table("Clientes").select(
            "canal_pago,nivel_cliente,linea_krece,modelo_interes"
        ).eq("numero", numero).execute()
        if r.data:
            return {k: r.data[0].get(k) for k in vacio}
        return vacio
    except Exception as e:
        print(f"Error cargando perfil: {e}")
        return vacio


def guardar_perfil(numero, **campos):
    """Guarda solo los campos que vengan con valor."""
    datos = {k: v for k, v in campos.items() if v is not None}
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


# ── Detección de canal, nivel y línea en lo que escribe el cliente ────────────

def detectar_canal(texto):
    t = texto.lower()
    if MENSAJE_KRECE in t or "krece" in t or "krese" in t or "crece" in t:
        return "krece"
    if "cashea" in t or "cashe" in t or "cachea" in t:
        return "cashea"
    if "creditienda" in t or "credi tienda" in t:
        return "creditienda"
    if any(p in t for p in ("contado", "efectivo", "divisa", "dolar", "d\u00f3lar",
                            "zelle", "usdt", "cash")):
        return "contado"
    return None


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
    m = re.search(r"\bnivel\s*([1-6])\b", t) or re.search(r"\b([1-6])\b", t)
    return m.group(1) if m else None


def detectar_linea(texto):
    """Busca un monto que parezca la línea aprobada de Krece."""
    m = re.search(r"(?:linea|l\u00ednea|aprobad[oa]|credito|cr\u00e9dito|limite|l\u00edmite)"
                  r"[^\d]{0,15}(\d{2,5})", texto.lower())
    if m:
        return float(m.group(1))
    m = re.search(r"\$\s*(\d{2,5})", texto)
    return float(m.group(1)) if m else None


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
                lineas.append("  Faltan datos para cotizar Krece: nivel y línea aprobada.")
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
                lineas.append("  Falta el nivel de Cashea del cliente.")
                continue
            try:
                c = precios.cashea(p, nivel)
                lineas.append(f"  Cashea: inicial ${c['inicial']} + 3 x ${c['monto_cuota']}")
            except ValueError:
                lineas.append(f"  El nivel '{nivel}' no es válido para Cashea. "
                              f"Pídele que confirme: 1 Semilla, 2 Raíz, 3 Hoja, "
                              f"4 Tronco, 5 Árbol o 6 Araguaney.")

        elif canal == "creditienda":
            d = precios.creditienda(p, "divisas")
            b = precios.creditienda(p, "bs")
            lineas.append(f"  CrediTienda en divisas: inicial ${d['inicial']} "
                          f"+ 4 x ${d['monto_cuota']}")
            lineas.append(f"  CrediTienda en bolívares: inicial ${b['inicial']} "
                          f"+ 4 x ${b['monto_cuota']}")

        else:  # contado o canal sin definir
            tasa = obtener_tasa_bcv()
            bcv = precios.precio_bcv(p)
            lineas.append(f"  En divisas (Zelle, USDT, efectivo): ${int(p)}")
            if tasa:
                bs = precios.precio_bolivares(p, tasa)
                lineas.append(f"  En bolívares: ${bcv} (Bs {bs:,})")
            else:
                lineas.append(f"  En bolívares: ${bcv} "
                              f"(no menciones el monto en Bs, la tasa no está disponible)")

        if eq.get("camara") or eq.get("bateria"):
            lineas.append(f"  [solo si las pide] Cámara {eq.get('camara','-')} · "
                          f"Batería {eq.get('bateria','-')} · RAM {eq.get('ram','-')}GB")

    return "\n".join(lineas)


def bloque_rangos():
    """Tres equipos por rango de precio, para cuando no dice modelo."""
    equipos = listar_por_rango()
    if not equipos:
        return "  No hay equipos disponibles en este momento."
    etiquetas = ["Económico", "Intermedio", "Gama alta"]
    salida = []
    for i, eq in enumerate(equipos):
        etiqueta = etiquetas[i] if i < len(etiquetas) else ""
        salida.append(f"  {etiqueta}: {nombre_completo(eq)} — "
                      f"desde ${int(eq['precio_paralelo'])} en divisas")
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
    """Nombres de productos de la categoría REPUESTOS, para anclar la IA."""
    nombres = []
    for p in todos:
        categ = p.get('categ_id')
        categ_nombre = categ[1] if isinstance(categ, (list, tuple)) and len(categ) > 1 else ""
        if "REPUESTOS" in str(categ_nombre).upper():
            nombre = p.get('name', '').strip()
            if nombre and nombre not in nombres:
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


def send_whapi_image(to: str, url_imagen: str, caption: str = ""):
    url = f"{WHAPI_API_URL}/messages/image"
    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}", "Content-Type": "application/json"}
    payload = {"to": to, "media": url_imagen}
    if caption:
        payload["caption"] = caption
    try:
        requests.post(url, json=payload, headers=headers, timeout=15).raise_for_status()
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


def notificar_intencion_compra(numero_cliente, perfil, equipos):
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
        return None, None

    headers = {"Authorization": f"Bearer {WHAPI_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    mime = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
    b64 = base64.standard_b64encode(resp.content).decode("utf-8")

    prompt = """Esta es una captura de la app de Krece. Extrae el NIVEL del
cliente (Azul, Plata, Oro o Platino) y la LÍNEA APROBADA o límite de crédito
en dólares. Responde SOLO con JSON: {"nivel": "plata", "linea": 220}
Si no puedes leer alguno de los dos con certeza, pon null en ese campo."""

    r = client.messages.create(
        model=MODELO_CELULARES, max_tokens=200,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": prompt},
        ]}],
    )
    try:
        datos = json.loads(texto_respuesta(r))
        return datos.get("nivel"), datos.get("linea")
    except Exception as e:
        print(f"Error leyendo captura Krece: {e}")
        return None, None

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
✅ *Nombre producto*: $XX USD / (€) Bs. XX,XXX

Donde $XX es el precio en USD y Bs. XX,XXX es el precio en bolívares calculado con tasa euro. NO hay precio intermedio.

MÚLTIPLES PRODUCTOS: Si el inventario muestra varios productos, responde en lista:
✅ *Modelo*: $12 USD / (€) Bs. 8,243
✅ *Modelo*: $13 USD / (€) Bs. 8,856

COMPATIBILIDADES: Si el inventario dice "PRODUCTOS COMPATIBLES":
- Si el stock es mayor a 0, responde:
"Tenemos una pantalla compatible para ese modelo 👍
✅ *[nombre exacto del producto]*: $XX USD / (€) Bs. XX,XXX"
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

def get_system_prompt_celulares(info_equipo, perfil, rangos):
    """Prompt del vendedor de celulares. Recibe solo el equipo consultado,
    nunca el catálogo completo."""
    tz = pytz.timezone("America/Caracas")
    ahora = datetime.now(tz)
    es_domingo = ahora.weekday() == 6
    horario_hoy = "9:00am a 2:00pm" if es_domingo else "8:30am a 5:30pm"
    dia_hoy = "domingo" if es_domingo else "lunes a sábado"
    estado_tienda = "ABIERTA" if esta_abierto() else "CERRADA"

    canal = perfil.get("canal_pago")
    if canal:
        recordatorio = (f"El cliente viene por *{canal.upper()}*. "
                        f"Háblale SOLO de ese medio, salvo que él pida otro.")
    else:
        recordatorio = "Todavía no sabes por qué medio quiere pagar."

    datos = []
    if perfil.get("nivel_cliente"):
        datos.append(f"nivel {perfil['nivel_cliente']}")
    if perfil.get("linea_krece"):
        datos.append(f"línea aprobada ${float(perfil['linea_krece']):.0f}")
    if perfil.get("modelo_interes"):
        datos.append(f"le interesa el {perfil['modelo_interes']}")
    conocidos = ("Ya sabes de él: " + ", ".join(datos) +
                 ". No se lo vuelvas a preguntar.") if datos else ""

    return f"""Eres el asistente de ventas de Cell Center 4620, tienda de celulares en Santa Teresa del Tuy. Atiendes por WhatsApp.

La tienda está ahora: {estado_tienda}
Hoy es {dia_hoy}. El horario de HOY es {horario_hoy}.
Horario general: lunes a sábado 8:30am-5:30pm · domingos y feriados 9:00am-2:00pm

CÓMO HABLAS
Eres venezolano, cálido y directo. Hablas como un vendedor de confianza, no como un robot.
Emojis con moderación, uno o dos por mensaje.
Máximo 4 o 5 líneas por respuesta. La gente lee WhatsApp con el pulgar.
UNA sola pregunta por mensaje. Nunca dos seguidas.
NUNCA uses "hermano", "hermana", "amigo", "pana" ni tratamientos parecidos.
No repitas lo que ya dijiste en el mensaje anterior.
No menciones el horario ni si estamos abiertos o cerrados a menos que el cliente
lo pregunte, o que quiera pasar hoy y ya esté cerrado. No lo digas al saludar.

LO PRIMERO: ENTENDER QUÉ QUIERE
- Compra de celular -> lo atiendes tú
- Servicio técnico o reparación -> responde exactamente: DERIVAR_TECNICO
- Accesorios, repuestos o cualquier otra cosa -> responde exactamente: DERIVAR_OTROS
Si ya dijo lo que quiere en su primer mensaje, no se lo preguntes de nuevo.

RESPETA EL CANAL QUE ELIGIÓ — regla más importante
{recordatorio}
Si viene por Krece, le hablas SOLO de Krece: no menciones Cashea, CrediTienda ni contado.
Lo mismo al revés. Solo cambias de canal si él lo pide.
{conocidos}

PRECIOS
NUNCA inventes un precio. Solo usas los montos que aparecen abajo en EQUIPO CONSULTADO.
Si no hay precio ahí, no lo estimes ni lo deduzcas de otro modelo: responde exactamente DERIVAR_PRECIO.
Nunca uses la palabra "paralelo". Di "en divisas", "en efectivo" o "en dólares".

KRECE
No cotizas sin dos datos: su NIVEL (Azul, Plata, Oro o Platino) y su LÍNEA APROBADA.
Pídeselos juntos en una sola frase, y ofrécele que te los escriba o te mande captura de la app.
Sin esos datos no das ningún número, ni aproximado.
NUNCA digas cuántas cuotas son antes de tener el cálculo: varía entre 3 y 10.
Si el cliente te da nivel y línea pero todavía no dijo qué equipo quiere,
pregúntaselo: "¿Qué modelo tienes en mente?"
Los iPhone con Krece solo aplican de nivel Plata en adelante. Si el cliente es
nivel Azul y pregunta por un iPhone, dile que ese equipo requiere Plata o
superior, y pregúntale si quiere ver otra opción o subir de nivel.
Si el cliente llegó con el mensaje predefinido de Krece ("Hola! Quiero comprar
con Krece. Como funciona?"), NO le preguntes nivel y línea: asume que es
Azul con $300 de línea (es el caso del 95% de los que llegan así) y muéstrale
de una vez tres opciones —gama baja, media y alta— cotizadas con esos datos.
Al final, deja abierta la corrección: "Si tu nivel o línea es distinto,
dímelo y te recalculo."
Nunca ofrezcas ni sugieras un iPhone a un cliente Azul, ni siquiera como
opción a mostrar. Si él mismo lo pide, ahí sí explícale la restricción.

CASHEA
Pregunta primero el NIVEL del cliente (1 Semilla al 6 Araguaney). Sin nivel no hay precio. Son 3 cuotas.

CREDITIENDA
No necesita nivel. Pregunta si paga en divisas o en bolívares, porque el precio cambia.

CUANDO NO SABES CÓMO VA A PAGAR
Si el cliente pregunta por un equipo y no ha dicho su medio de pago, confirma
la disponibilidad y pregúntale suavemente en qué modalidad le interesa verlo.
Nunca lo presiones ni le pidas que decida ya.
Ejemplo: "Sí, ese lo tenemos. ¿Te lo muestro de contado o prefieres verlo con
financiamiento?"

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
Si pide ver el equipo, incluye el marcador [FOTO] acompañado de una frase. Nunca lo mandes solo.

SI NO SABE QUÉ QUIERE
No mandes el catálogo completo. Muéstrale estas tres opciones y deja que se ubique:
{rangos}
Después pregúntale para qué lo va a usar.

CERRAR
El objetivo NO es cerrar la venta por chat: la decisión es del cliente y se
toma en la tienda, viendo el equipo.
No invites a pasar en el primer mensaje. Espera a que muestre interés real
(pregunta precio de un modelo concreto, pide fotos, compara opciones).
Recién ahí: "Si quieres pasar a verlo".
Si pregunta dónde quedan, responde exactamente: ENVIAR_UBICACION
Cuando detectes intención de compra (dice que lo quiere, pregunta cómo apartarlo,
o confirma que va a ir), responde exactamente: INTENCION_COMPRA

OBJECIONES
"Está caro" -> recuérdale que con el financiamiento se lo lleva hoy con la inicial.
"Lo voy a pensar" -> sin presionar, menciona que los equipos rotan rápido.
"En otra tienda está más barato" -> garantía, soporte directo y financiamiento.
Nunca hables mal de la competencia.

LO QUE NUNCA HACES
- Inventar precios, modelos o disponibilidad
- Dar montos de Krece sin nivel y línea
- Dar montos de Cashea sin nivel
- Prometer entrega inmediata de algo que viene de proveedor
- Mandar la lista completa de equipos
- Usar la palabra "paralelo"

EQUIPO CONSULTADO
{info_equipo}
"""


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
    catalogo = catalogo_para_ia()
    if not catalogo:
        return [], "ninguno"

    prompt = f"""Un cliente de una tienda de celulares en Venezuela escribió esto por WhatsApp:
"{mensaje}"
{historial_texto}
Este es el catálogo real de la tienda. Cada línea es:
clave | nombre | precio | cámara | entrega

{catalogo}

Tu tarea: entender QUÉ QUIERE y elegir hasta 3 equipos del catálogo.

Interpreta libremente. El cliente escribe rápido, con errores, abreviado o
sin saber el nombre exacto. Ejemplos de lo que debes entender:
- "el redmi 17 de 256" -> el Redmi 17 256GB
- "sansung a 07" -> el Samsung A07
- "algo bueno para fotos" -> equipos de gama media con buena cámara
- "el mas barato que tengas" -> el de menor precio
- "uno que no pase de 200" -> los que cuesten hasta $200
- "quiero un iphone" -> los iPhone que haya

REGLAS:
- Solo puedes devolver claves que estén EXACTAMENTE en la lista de arriba.
- Si pide un modelo conocido que NO está en el catálogo (por ejemplo un
  iPhone 13, un Samsung S24), elige 2 o 3 parecidos en precio y gama, y
  marca tipo "recomendacion".
- Si el modelo que pide no es conocido ni está en el catálogo, devuelve
  lista vacía y tipo "ninguno".
- Si lo que pide está en el catálogo pero dice SIN PRECIO, devuélvelo igual
  con tipo "sin_precio".
- Si solo saluda, pregunta por horario, ubicación, servicio técnico o
  cualquier cosa que no sea elegir un celular, devuelve lista vacía y
  tipo "ninguno".
- Si el mensaje solo da nivel, línea aprobada o datos de pago, SIN que antes
  se estuviera hablando de un equipo concreto, devuelve lista vacía y tipo
  "ninguno". No elijas un equipo por tu cuenta basado en la línea.
- PERO si arriba dice que en mensajes anteriores le interesaba un modelo, y
  ahora el cliente solo está dando su nivel o línea, devuelve ESE modelo con
  tipo "exacto". Está completando los datos para cotizar lo que ya pidió.
- Si pide un criterio en vez de un modelo (fotos, juegos, batería,
  presupuesto), elige hasta 3 que cumplan y marca tipo "exacto".

Responde SOLO con JSON, sin explicaciones ni markdown:
{{"claves": ["clave1", "clave2"], "tipo": "exacto"}}"""

    try:
        r = client.messages.create(
            model=MODELO_CELULARES,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        texto = texto_respuesta(r)
        if not texto:
            print("La interpretación llegó vacía (se agotaron los tokens)")
            return [], "ninguno"
        texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.MULTILINE).strip()
        datos = json.loads(texto)
        claves = datos.get("claves") or []
        tipo = datos.get("tipo") or "ninguno"
        print(f"IA CRUDO -> claves={claves} tipo={tipo}")
    except Exception as e:
        print(f"Error interpretando pedido: {e}")
        return [], "ninguno"

    if not claves:
        return [], "ninguno"

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
        return [], ("sin_precio" if hay_sin_precio else "ninguno")

    if tipo not in ("exacto", "recomendacion"):
        tipo = "exacto"
    return ordenar_equipos(equipos), tipo


# ── Flujo de celulares ────────────────────────────────────────────────────────

def atender_celulares(from_number, numero_limpio, body):
    """Atiende a un cliente de celulares de punta a punta."""
    perfil = cargar_perfil(numero_limpio)
    cambios = {}

    # Canal de pago
    canal = detectar_canal(body) or perfil.get("canal_pago")
    canal_anterior = perfil.get("canal_pago")

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

    # El mensaje predefinido de Krece: 95% de los casos son Azul/$300
    if canal == "krece" and MENSAJE_KRECE in body.lower() and not perfil.get("nivel_cliente"):
        cambios["nivel_cliente"] = "azul"
        perfil["nivel_cliente"] = "azul"
        cambios["linea_krece"] = 300
        perfil["linea_krece"] = 300

    # Nivel y línea, según el canal
    if canal == "krece":
        nivel = detectar_nivel_krece(body)
        if nivel:
            cambios["nivel_cliente"] = nivel
            perfil["nivel_cliente"] = nivel
        linea = detectar_linea(body)
        if linea:
            cambios["linea_krece"] = linea
            perfil["linea_krece"] = linea
    elif canal == "cashea":
        nivel = detectar_nivel_cashea(body)
        if nivel:
            cambios["nivel_cliente"] = nivel
            perfil["nivel_cliente"] = nivel

    # Equipo: la IA interpreta lo que pidió
    contexto = ""
    if perfil.get("modelo_interes"):
        contexto = (f"\nEn mensajes anteriores le interesaba el "
                    f"{perfil['modelo_interes']}. Si ahora no menciona otro "
                    f"modelo, se refiere a ese.\n")
    equipos, tipo_resultado = interpretar_pedido(body, contexto)

    if equipos:
        modelo = nombre_completo(equipos[0])
        if modelo != perfil.get("modelo_interes"):
            cambios["modelo_interes"] = modelo
            perfil["modelo_interes"] = modelo

    if cambios:
        guardar_perfil(numero_limpio, **cambios)

    # Lo único que ve el modelo
    if tipo_resultado == "sin_precio":
        info = ("El equipo existe pero NO tiene precio verificado. "
                "No lo cotices: responde DERIVAR_PRECIO.")
    elif tipo_resultado == "recomendacion":
        info = ("El cliente pidió un modelo que NO tenemos. Dile con claridad "
                "que ese no lo manejas, y ofrécele estas alternativas "
                "parecidas:\n" + bloque_equipo(equipos, canal, perfil))
    elif tipo_resultado == "ninguno":
        info = ("El cliente no está preguntando por un equipo concreto, o pidió "
                "algo que no tenemos ni se parece a nada del catálogo. "
                "No inventes modelos ni precios.")
    else:
        info = bloque_equipo(equipos, canal, perfil)

    print(f"INFO AL MODELO -> {info[:300]}")

    historial = cargar_historial(numero_limpio)
    historial.append({"role": "user", "content": body})
    if len(historial) > 4:
        historial = historial[-4:]

    respuesta = client.messages.create(
        model=MODELO_CELULARES,
        max_tokens=1500,
        system=get_system_prompt_celulares(info, perfil, bloque_rangos()),
        messages=historial,
    )
    reply = texto_respuesta(respuesta)
    if not reply:
        print("La respuesta al cliente llegó vacía")
        reply = "Dame un momento y te confirmo"

    # ── Marcadores ────────────────────────────────────────────────────────────
    if "DERIVAR_TECNICO" in reply:
        notificar_asesor(ASESOR_CEL_TECNICO, "servicio técnico", from_number)
        reply = "Un momento, te comunico con el asesor de servicio técnico"

    elif "DERIVAR_OTROS" in reply:
        notificar_asesor(ASESOR_CEL_OTROS, "accesorios u otra consulta", from_number)
        reply = "Un momento, un asesor te atiende enseguida"

    elif "DERIVAR_PRECIO" in reply:
        notificar_precio_sin_verificar(from_number, body)
        reply = "Déjame confirmarte el precio de ese modelo y te escribo en un momento"

    elif "INTENCION_COMPRA" in reply:
        notificar_intencion_compra(from_number, perfil, equipos)
        reply = "¡Perfecto! Te esperamos en la tienda para cerrar. Pregunta por Omar"

    enviar_ubicacion = "ENVIAR_UBICACION" in reply
    if enviar_ubicacion:
        reply = reply.replace("ENVIAR_UBICACION", "").strip()
        if not reply:
            reply = "Aquí te dejo la ubicación. Te esperamos, pregunta por Omar"

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

    if enviar_ubicacion:
        send_whapi_ubicacion(from_number)


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True) or {}
        messages_list = data.get("messages", [])

        for msg in messages_list:
            print(f"📨 MSG RECIBIDO | from: {msg.get('from','')} | type: {msg.get('type','')} | body: {msg.get('text',{}).get('body','')[:50]} | ts: {msg.get('timestamp',0)} | from_me: {msg.get('from_me',False)}")
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
            if msg_type != "text":
                numero_temp = from_number.replace("@s.whatsapp.net", "").replace("+", "")
                es_cel_temp = (numero_temp in NUMEROS_PRUEBA_CELULARES
                              or numero_temp not in NUMEROS_AUTORIZADOS)
                if msg_type == "image" and es_cel_temp:
                    try:
                        nivel, linea = leer_captura_krece(msg.get("image", {}))
                        if nivel or linea:
                            guardar_perfil(numero_temp, canal_pago="krece",
                                          nivel_cliente=nivel, linea_krece=linea)
                            atender_celulares(from_number, numero_temp,
                                             "Aquí está mi captura de Krece")
                        else:
                            send_whapi_message(from_number,
                                "No pude leer bien la captura. ¿Me confirmas tu nivel y línea aprobada por escrito?")
                    except Exception as e:
                        print(f"Error procesando captura Krece: {e}")
                        send_whapi_message(from_number,
                            "No pude leer la imagen. ¿Me dices tu nivel y línea aprobada?")
                elif msg_type in ["image", "audio", "voice", "video", "document", "location", "sticker", "contact"]:
                    send_whapi_message(from_number, "Por los momentos solo puedo leer mensajes de texto. Por favor escribe el modelo que buscas. 📝")
                continue

            msg_timestamp = msg.get("timestamp", 0)
            ahora = time.time()
            antiguedad = int(ahora - msg_timestamp)

            if msg_timestamp < BOT_START_TIME or antiguedad > 3600:
                print("Mensaje ignorado - muy antiguo: " + str(antiguedad) + "s")
                continue

            numero_limpio = from_number.replace("@s.whatsapp.net", "").replace("+", "")

            # ── Verificar si el bot está en pausa manual para este número ──────
            if numero_limpio in pausas_activas:
                if time.time() < pausas_activas[numero_limpio]:
                    print(f"⏸️ Bot en pausa para {numero_limpio}, mensaje ignorado")
                    continue
                else:
                    del pausas_activas[numero_limpio]
                    print(f"▶️ Pausa expirada para {numero_limpio}, reanudando")

            # ── Obtener body aquí para que esté disponible en ambos flujos ──────
            body = msg.get("text", {}).get("body", "").strip()
            if not body:
                continue

            # ── Comando secreto para limpiar historial (funciona en ambos flujos)
            if body.strip().lower() == "reset_historial":
                try:
                    supabase.table("Clientes").delete().eq("numero", numero_limpio).execute()
                    send_whapi_message(from_number, "✅ Historial limpiado. Puedes empezar una conversación nueva.")
                except Exception as e:
                    send_whapi_message(from_number, f"❌ Error limpiando historial: {e}")
                continue

            # ── Determinar comportamiento según el número ──────────────────────
            es_cliente_celulares = (numero_limpio in NUMEROS_PRUEBA_CELULARES
                                    or numero_limpio not in NUMEROS_AUTORIZADOS)

            # ── Flujo de celulares ────────────────────────────────────────────
            if es_cliente_celulares:
                print(f"Cliente celulares: {numero_limpio}")
                try:
                    atender_celulares(from_number, numero_limpio, body)
                except Exception as e:
                    print(f"Error en flujo de celulares: {e}")
                    notificar_asesor(ASESOR_CELULARES, "error del bot", from_number)
                    send_whapi_message(from_number,
                        "Dame un momento, un asesor te atiende enseguida")
                continue

            # ── Flujo original para clientes de repuestos (sin tocar) ──────────
            if numero_limpio not in NUMEROS_AUTORIZADOS:
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
                        bs_str = f" (€) Bs. {precio_bs:,}" if precio_bs is not None else ""
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
                            bs_str = f" (€) Bs. {precio_bs:,}" if precio_bs is not None else ""
                            contexto_odoo += f"- {nombre} (compatible con {ref or modelo_pedido}): ${precio_usd}{bs_str} | Stock: {stock} unidades\n"
                            if stock_bajo_info is None and 1 <= stock <= 2:
                                stock_bajo_info = {"producto": nombre, "stock": stock}
                    else:
                        precio_usd, precio_bs = calcular_precio_bs(compatibles['list_price'])
                        stock = int(compatibles['qty_available'])
                        nombre = compatibles['name']
                        modelo_pedido = compatibles.get('_compatible_con', '')
                        bs_str = f" (€) Bs. {precio_bs:,}" if precio_bs is not None else ""
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
