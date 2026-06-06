from flask import Flask, request, jsonify
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

# ── Módulo catálogo celulares ──────────────────────────────────────────────────
from sheets_celulares import obtener_catalogo_celulares

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
    "584126093756"
]

ASESOR_TECNICO = "584241564298"
ASESOR_ACCESORIOS = "584126093756"
ASESOR_STOCK = "584126093756"

# ── Asesor para clientes de celulares ─────────────────────────────────────────
ASESOR_CELULARES = "584241346346"

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

tasa_bcv_cache = {"tasa": 515.0, "fecha": ""}
stock_bajo_pendiente = {}

PALABRAS_SI = ["si","sí","yes","claro","dale","ok","okay","quiero","aparta","reserva","separa","confirmado","afirmativo","me interesa","la quiero"]

# ── Marcas conocidas para búsqueda sin marca ──────────────────────────────────
MARCAS_CONOCIDAS = {
    "samsung", "redmi", "xiaomi", "infinix", "iphone", "huawei",
    "tecno", "motorola", "alcatel", "honor", "realme"
}


# ── Utilidades generales ──────────────────────────────────────────────────────

def normalizar_texto(texto):
    texto = texto.lower().strip()
    texto = re.sub(r'([a-zA-Z]{3,})(\d)', r'\1 \2', texto)
    texto = re.sub(r'(\d)([a-zA-Z]{3,})', r'\1 \2', texto)
    for error, correcto in CORRECCIONES_MARCAS.items():
        texto = re.sub(r'\b' + re.escape(error) + r'\b', correcto, texto)
    return texto


def limpiar_html(texto):
    if not texto:
        return ""
    texto_limpio = re.sub(r'<[^>]+>', ' ', str(texto))
    texto_limpio = texto_limpio.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').replace('&nbsp;',' ')
    return re.sub(r'\s+', ' ', texto_limpio).strip()


def obtener_tasa_bcv():
    try:
        fecha_hoy = time.strftime("%Y-%m-%d")
        if tasa_bcv_cache["fecha"] == fecha_hoy:
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


def calcular_precio_bs(precio_usd_odoo):
    precio_int = int(precio_usd_odoo)
    precio_tabla = TABLA_PRECIOS.get(precio_int, round(precio_usd_odoo * 1.35))
    precio_bs = round(precio_tabla * obtener_tasa_bcv())
    return precio_usd_odoo, precio_tabla, precio_bs


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


# ── Extracción de palabras clave ──────────────────────────────────────────────

def extraer_palabras_clave(mensaje):
    normalizado = normalizar_texto(mensaje)
    palabras = [p for p in normalizado.split() if p not in PALABRAS_IGNORAR and (len(p) > 1 or p.isdigit())]
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
    for i in range(len(palabras_temp) - 1):
        combinacion = palabras_temp[i] + palabras_temp[i+1]
        if combinacion in MODELOS_ABREVIADOS:
            expandido = MODELOS_ABREVIADOS[combinacion]
            print(f"Abreviación expandida: '{mensaje}' → '{expandido}'")
            return expandido

    # Buscar palabra sola
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


def buscar_compatible_exacto(todos, palabras_clave):
    if not palabras_clave:
        return None

    compatibles_con_stock = []
    compatibles_sin_stock = []

    for producto in todos:
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
                if (all(p in palabras_modelo for p in palabras_clave) and palabras_extra <= 1) or clave_junta == modelo_junto:
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
            {'fields': ['name', 'list_price', 'qty_available', 'description'], 'limit': 500}
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


def notificar_asesor(asesor: str, tema: str, numero_cliente: str):
    numero_formateado = "+" + numero_cliente.replace("@s.whatsapp.net", "")
    send_whapi_message(asesor, f"🔔 *Mensaje pendiente*\nUn cliente está esperando respuesta sobre *{tema}*.\nNúmero: {numero_formateado}")


def notificar_stock_bajo(numero_cliente: str, producto: str, stock: int):
    numero_formateado = "+" + numero_cliente.replace("@s.whatsapp.net", "")
    send_whapi_message(ASESOR_STOCK, f"⚠️ *Stock bajo - Cliente interesado*\nProducto: *{producto}*\nStock: {stock} unidad(es)\nCliente: {numero_formateado}\n\nEl cliente confirmó que quiere apartar esta pantalla.")


# ── System prompt ─────────────────────────────────────────────────────────────

def get_system_prompt():
    estado_tienda = "ABIERTA" if esta_abierto() else "CERRADA"
    return f"""Eres un vendedor directo de Cell Center 4620, tienda de celulares en Venezuela. Solo vendemos PANTALLAS y repuestos de celulares.
La tienda está actualmente: {estado_tienda}

REGLA PRINCIPAL: Cuando el inventario muestre productos con stock mayor a 0, SIEMPRE da el precio. NUNCA digas que no está disponible si hay stock. NUNCA preguntes si es para pantalla o celular, asume que siempre es para pantalla.

1. PANTALLAS: Si el inventario muestra productos disponibles, responde con precio en USD y bolívares. Formato EXACTO:
✅ *Nombre producto*: $XX USD / ($YY) Bs. XX,XXX

Donde $XX es el precio Odoo, $YY es el precio equivalente y Bs. XX,XXX es el precio en bolívares.

MÚLTIPLES PRODUCTOS: Si el inventario muestra varios productos, responde en lista:
✅ *Modelo*: $12 USD / ($15) Bs. 8,243
✅ *Modelo*: $13 USD / ($17) Bs. 8,856

COMPATIBILIDADES: Si el inventario dice "PRODUCTOS COMPATIBLES", responde EXACTAMENTE así:
"Tenemos una pantalla compatible para ese modelo 👍

✅ *[nombre exacto del producto]*: $XX USD / ($YY) Bs. XX,XXX"

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

def get_system_prompt_celulares():
    """System prompt vendedor persuasivo de celulares."""
    estado_tienda = "ABIERTA" if esta_abierto() else "CERRADA"
    catalogo = obtener_catalogo_celulares()
    return f"""Eres un vendedor experto y persuasivo de una tienda de celulares en Venezuela.
Tu objetivo principal es CERRAR LA VENTA. La tienda está: {estado_tienda}

PERSONALIDAD:
- Cálido, entusiasta y enfocado en ayudar al cliente a tomar la mejor decisión
- Usas emojis con moderación para dar energía a la conversación
- Nunca eres agresivo, pero siempre empujas suavemente hacia el cierre
- Hablas como un venezolano natural, no como un robot

FLUJO DE VENTA:
1. SALUDO: Saluda calurosamente y pregunta para qué usará el celular
   (redes sociales, fotos, trabajo, juegos, regalo, etc.)

2. RECOMENDACIÓN: Basándote en su respuesta, recomienda máximo 2 equipos
   del catálogo que mejor se adapten a su necesidad. Explica brevemente
   por qué esos y no otros.

3. PRECIO: Muestra el precio con este formato EXACTO:
✅ *Marca Modelo*
📦 Almac · RAM · Cámara · Batería
💵 En divisas: $X
💰 BCV: $X (Bs X)
💛 Cashea: $X inicial + 3 cuotas de $X
💙 Krece: $X inicial + 4 cuotas de $X
💜 CrediTienda: $X inicial + 4 cuotas de $X

4. CIERRE: Siempre termina con una pregunta de cierre. Varía las frases:
   "¿Con cuál forma de pago lo cerramos?"
   "¿Lo apartamos para ti?"
   "¿Te lo reservo hoy?"
   "¿Cuál se adapta más a tu presupuesto?"

TÉCNICAS DE VENTA:
- ESCASEZ: Siempre menciona que los equipos tienen alta rotación.
  Varía las frases:
  "Este modelo está volando 🔥"
  "Es uno de los más pedidos esta semana"
  "Los equipos buenos no duran mucho aquí"
  "Varios clientes me han preguntado por este hoy"

- DIVISAS: Siempre menciona el descuento cuando el cliente muestre
  interés en cerrar:
  "Si pagas en Zelle o USDT te sale mejor todavía 💵"
  "Con divisas te hacemos un precio especial"
  "Recibimos Zelle y USDT"

- OBJECIONES: Si el cliente dice que está caro o que va a pensarlo:
  "Entiendo, pero con Cashea/Krece/CrediTienda te lo llevas
   hoy mismo con solo $X de inicial"
  "Mientras más esperas más sube el dólar, hoy es el mejor momento"

- COMPARACIÓN: Si el cliente compara con otra tienda:
  "Aquí tienes garantía, soporte directo y las mejores modalidades
   de financiamiento del mercado"

REGLAS IMPORTANTES:
- Solo ofreces equipos del catálogo disponible
- Si preguntan por un modelo que no está, dilo claramente
  y ofrece una alternativa similar del catálogo
- Máximo 4-5 líneas por respuesta para no abrumar
- Si el cliente ya eligió el equipo, enfócate en cerrar el método de pago
- Si el cliente confirma que quiere comprar, indícale que un asesor
  lo contactará para coordinar el pago y la entrega
- NUNCA uses la palabra "paralelo"

DERIVACIONES:
- Servicio técnico o reparación → responde exactamente: DERIVAR_ASESOR
- Accesorios → responde exactamente: DERIVAR_ASESOR

HORARIO — La tienda está: {estado_tienda}
- Lunes a sábado 8:30am-5:30pm
- Domingos y feriados 9:00am-2:00pm
- Si está CERRADA: avisa pero sigue atendiendo y tomando pedidos

CATÁLOGO ACTUALIZADO:
{catalogo}
"""

client = anthropic.Anthropic()

# ── Inicializar Google Sheets al arrancar ─────────────────────────────────────
inicializar_db()
inicializar_hoja_no_encontrados()

# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True) or {}
        messages_list = data.get("messages", [])

        for msg in messages_list:
            if msg.get("from_me", False):
                # Detectar si el asesor escribe ** para pausar el bot
                body_asesor = msg.get("text", {}).get("body", "").strip()
                if body_asesor == "**":
                    chat_id_pausa = msg.get("chat_id", "") or msg.get("chatId", "") or ""
                    numero_cliente = chat_id_pausa.replace("@s.whatsapp.net", "").replace("+", "")
                    pausas_activas[numero_cliente] = time.time() + 900  # 15 minutos
                    print(f"Bot pausado para {numero_cliente} por 15 minutos")
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
                if msg_type in ["image", "audio", "voice", "video", "document", "location", "sticker", "contact"]:
                    send_whapi_message(from_number, "Por los momentos solo puedo leer mensajes de texto. Por favor escribe el modelo que buscas. 📝")
                continue

            msg_timestamp = msg.get("timestamp", 0)
            ahora = time.time()
            antiguedad = int(ahora - msg_timestamp)

            if msg_timestamp < BOT_START_TIME or antiguedad > 3600:
                print("Mensaje ignorado - muy antiguo: " + str(antiguedad) + "s")
                continue

            numero_limpio = from_number.replace("@s.whatsapp.net", "").replace("+", "")

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
            es_cliente_celulares = numero_limpio not in NUMEROS_AUTORIZADOS

            if es_cliente_celulares:
                # ── Flujo para clientes de celulares (Google Sheets) ───────────
                print(f"Cliente celulares: {numero_limpio}")

                historial = cargar_historial(numero_limpio)
                historial.append({"role": "user", "content": body})
                if len(historial) > 4:
                    historial = historial[-4:]

                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=400,
                    system=get_system_prompt_celulares(),
                    messages=historial
                )
                reply = response.content[0].text

                if "DERIVAR_ASESOR" in reply:
                    notificar_asesor(ASESOR_CELULARES, "celular o accesorio", from_number)
                    reply = "Un momento, un asesor te atenderá enseguida 👋"

                historial.append({"role": "assistant", "content": reply})
                guardar_historial(numero_limpio, historial)
                send_whapi_message(from_number, reply)
                continue  # ← no cae al flujo de repuestos

            # ── Flujo original para clientes de repuestos (sin tocar) ──────────
            if numero_limpio not in NUMEROS_AUTORIZADOS:
                print("Número no autorizado: " + numero_limpio)
                continue

            if from_number in stock_bajo_pendiente:
                if any(palabra in body.lower() for palabra in PALABRAS_SI):
                    info = stock_bajo_pendiente.pop(from_number)
                    notificar_stock_bajo(from_number, info["producto"], info["stock"])
                else:
                    stock_bajo_pendiente.pop(from_number)

            productos, compatibles, similares = consultar_odoo(body)
            stock_bajo_info = None

            if productos or compatibles:
                contexto_odoo = ""

                if productos:
                    contexto_odoo += "\n\nINFORMACIÓN DEL INVENTARIO:\n"
                    for p in productos:
                        precio_usd, precio_tabla, precio_bs = calcular_precio_bs(p['list_price'])
                        stock = int(p['qty_available'])
                        nombre = p['name']
                        contexto_odoo += f"- {nombre}: ${precio_usd} USD / (${precio_tabla}) Bs. {precio_bs:,} | Stock: {stock} unidades\n"
                        if stock_bajo_info is None and 1 <= stock <= 2:
                            stock_bajo_info = {"producto": nombre, "stock": stock}

                if compatibles:
                    contexto_odoo += "\n\nPRODUCTOS COMPATIBLES:\n"
                    if isinstance(compatibles, list):
                        for comp in compatibles:
                            precio_usd, precio_tabla, precio_bs = calcular_precio_bs(comp['list_price'])
                            stock = int(comp['qty_available'])
                            nombre = comp['name']
                            modelo_pedido = comp.get('_compatible_con', '')
                            ref = comp.get('_referencia', '')
                            contexto_odoo += f"- {nombre} (compatible con {ref or modelo_pedido}): ${precio_usd} USD / (${precio_tabla}) Bs. {precio_bs:,} | Stock: {stock} unidades\n"
                            if stock_bajo_info is None and 1 <= stock <= 2:
                                stock_bajo_info = {"producto": nombre, "stock": stock}
                    else:
                        precio_usd, precio_tabla, precio_bs = calcular_precio_bs(compatibles['list_price'])
                        stock = int(compatibles['qty_available'])
                        nombre = compatibles['name']
                        modelo_pedido = compatibles.get('_compatible_con', '')
                        contexto_odoo += f"- {nombre} (compatible con {modelo_pedido}): ${precio_usd} USD / (${precio_tabla}) Bs. {precio_bs:,} | Stock: {stock} unidades\n"
                        if stock_bajo_info is None and 1 <= stock <= 2:
                            stock_bajo_info = {"producto": nombre, "stock": stock}

                if similares:
                    contexto_odoo += "\n\nMODELOS NO ENCONTRADOS:\n"
                    for ref, lista_sim in similares:
                        contexto_odoo += f"- {ref}: no encontrado exacto\n"

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
                reply = response.content[0].text

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
                send_whapi_message(from_number, reply)

            else:     
                    # Registrar producto no encontrado
                    registrar_producto_no_encontrado(numero_limpio, body)
                    response = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=300,
                        system=get_system_prompt(),
                        messages=[{"role": "user", "content": body}]
                   )
                   reply = response.content[0].text

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
        print(f"Error en webhook: {e}")
        return jsonify({"status": "error", "detail": str(e)}), 200


@app.route('/', methods=['GET'])
def health():
    return "Cell Center Bot activo ✅", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
