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

app = Flask(__name__)

BOT_START_TIME = time.time()

NUMEROS_AUTORIZADOS = [
    "584149202844",
    "584241564298",
    "584126093756"
]

ASESOR_TECNICO = "584241564298"
ASESOR_ACCESORIOS = "584126093756"
ASESOR_STOCK = "584126093756"

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
    11: 15, 12: 16, 13: 17, 14: 19, 15: 20,
    17: 23, 19: 26, 21: 28, 24: 32
}

tasa_bcv_cache = {"tasa": 515.0, "fecha": ""}
stock_bajo_pendiente = {}

PALABRAS_SI = ["si","sí","yes","claro","dale","ok","okay","quiero","aparta","reserva","separa","confirmado","afirmativo","me interesa","la quiero"]

PALABRAS_IGNORAR = {
    "de","el","la","los","las","un","una","para","del","con","por","que","y","o","a","en","al","lo","le","se","su","sus","es","son",
    "tienes","tienen","hay","tengo","tiene","dame","dime","quiero","quieres","puedes","puede","necesito",
    "pantalla","3/4","precio","cuanto","cuánto","stock","disponibles","disponible","cuales","hola","buenas","buenos",
    "dias","día","dia","tardes","noches","saludos","favor","porfavor","porfa","gracias","please",
    "mano","hermano","brother","bro","amigo","chamo","pana","jefe","señor","señora","maestro","socio","papi","mami",
    "me","mi","mis","tu","tus","nos","ese","esa","esto","esta","aqui","cuando","como","donde",
    "mas","más","muy","bien","mal","solo","también","tampoco","d","q","x","k","tendrás","podria","podría",
    "teneis","tenes","tene","tiene","hay","tienen",
    "bolivares","bolívares","divisa","divisas","dolar","dólares","dolares","dólar"
}

CORRECCIONES_MARCAS = {
    "remi": "redmi",
    "samsug": "samsung",
    "samsum": "samsung",
    "samsun": "samsung",
    "infinity": "infinix",
    "infiniti": "infinix",
    "infnix": "infinix",
    "infinik": "infinix",
    "ifninx": "infinix",
    "iph": "iphone",
    "aifon": "iphone",
    "aiphone": "iphone",
    "huawe": "huawei",
    "huawey": "huawei",
    "huawai": "huawei",
    "tecnho": "tecno",
    "tekno": "tecno",
    "motoral": "motorola",
    "motarola": "motorola",
    "alkatel": "alcatel",
    "alcater": "alcatel",
    "onor": "honor",
    "onour": "honor",
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


# ── Extracción de palabras clave ──────────────────────────────────────────────

def extraer_palabras_clave(mensaje):
    normalizado = normalizar_texto(mensaje)
    palabras = [p for p in normalizado.split() if p not in PALABRAS_IGNORAR and (len(p) > 1 or p.isdigit())]
    print(f"Mensaje normalizado: '{normalizado}' | Palabras clave: {palabras}")
    return palabras, normalizado


def dividir_mensaje(mensaje):
    separadores = r'\by también\b|\by\b|,'
    partes = re.split(separadores, mensaje, flags=re.IGNORECASE)
    partes = [p.strip() for p in partes if p.strip()]
    print(f"Referencias divididas: {partes}")
    return partes if len(partes) > 1 else None


# ── Búsqueda exacta ───────────────────────────────────────────────────────────

def buscar_exacto(todos, palabras_clave):
    if not palabras_clave:
        return []

    encontrados = []
    for producto in todos:
        nombre_norm = normalizar_texto(producto['name'])
        palabras_nombre = nombre_norm.split()

        if not all(p in palabras_nombre for p in palabras_clave):
            continue

        # Coincidencia 100% exacta — sin palabras extra
        palabras_extra = len(palabras_nombre) - len(palabras_clave)
        if palabras_extra > 0:
            continue

        encontrados.append(producto)
        print(f"Match exacto: {producto['name']}")

    return encontrados


# ── Búsqueda de compatible exacto ─────────────────────────────────────────────

def buscar_compatible_exacto(todos, palabras_clave):
    if not palabras_clave:
        return None

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
                palabras_modelo = modelo_norm.split()

                if not palabras_modelo:
                    continue

                palabras_extra = len(palabras_modelo) - len(palabras_clave)
                if all(p in palabras_modelo for p in palabras_clave) and palabras_extra == 0:
                    print(f"Compatible exacto: {producto['name']} | modelo='{modelo_odoo.strip()}'")
                    producto_copia = dict(producto)
                    producto_copia['_compatible_con'] = modelo_odoo.strip()
                    return producto_copia

    return None


# ── Búsqueda de similares ─────────────────────────────────────────────────────

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
                similares.append((
                    coincidencias,
                    producto['name'],
                    int(producto['qty_available']),
                    False,
                    ""
                ))

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
                        similares.append((
                            coincidencias,
                            producto['name'],
                            int(producto['qty_available']),
                            True,
                            modelo_odoo.strip()
                        ))

    similares.sort(key=lambda x: x[0], reverse=True)
    return similares[:max_resultados]


# ── Búsqueda por referencia individual ───────────────────────────────────────

def buscar_referencia(todos, ref):
    palabras_clave, _ = extraer_palabras_clave(ref)
    if not palabras_clave:
        return None, None, None

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

    compatible = buscar_compatible_exacto(todos, palabras_clave)
    if compatible:
        return None, compatible, None

    similares = buscar_similares(todos, palabras_clave)
    return None, None, similares


# ── Consulta principal ────────────────────────────────────────────────────────

def consultar_odoo(mensaje):
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
        print(f"Odoo UID: {uid}")
        if not uid:
            return None, None, None

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
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

        palabras_clave, _ = extraer_palabras_clave(mensaje)
        if not palabras_clave:
            return None, None, None

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

        compatible = buscar_compatible_exacto(todos, palabras_clave)
        if compatible:
            return None, compatible, None

        print("Sin match exacto, buscando similares...")
        similares = buscar_similares(todos, palabras_clave)
        print(f"Similares: {similares}")
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

1. PANTALLAS: Si el inventario muestra productos disponibles, responde con precio en USD y bolívares. No menciones cantidad de stock.

MÚLTIPLES PRODUCTOS: Si el inventario muestra varios productos, responde en lista:
✅ *Modelo*: $12 USD / Bs. 8,243
✅ *Modelo*: $13 USD / Bs. 8,856

COMPATIBILIDADES: Si el inventario dice "PRODUCTO COMPATIBLE", responde:
"No tenemos la pantalla para [modelo pedido], pero tenemos una compatible: *[nombre producto]*: $XX USD / Bs. XX,XXX"

STOCK 1 o 2: da el precio y avisa que queda muy poco. Varía las frases:
"Por cierto, este modelo está casi agotado. ¿Lo reservamos?"
"Nos queda muy poco de este modelo. ¿Lo apartamos?"
"Existencia muy limitada. ¿Lo separamos para ti?"
"Está por agotarse. ¿Lo guardamos?"

STOCK 3 o más: solo da el precio sin comentarios.
STOCK 0: solo di que no está disponible.

2. CELULARES (comprar celular completo): responde exactamente: "DERIVAR_TECNICO"
3. SERVICIO TÉCNICO o reparaciones: responde exactamente: "DERIVAR_TECNICO"
4. ACCESORIOS: responde exactamente: "DERIVAR_ACCESORIOS"

5. HORARIO o si estamos abiertos:
- ABIERTA: confirmamos que sí estamos. Horario: lunes a sábado 8:30am-5:30pm, domingos y feriados 9:00am-2:00pm.
- CERRADA: avisa que estamos cerrados pero puedes responder preguntas. Varía las frases:
  "En este momento estamos cerrados, pero aquí estoy para ayudarte. Horario: lunes a sábado 8:30am-5:30pm, domingos y feriados 9:00am-2:00pm."
  "La tienda está cerrada, aunque puedo ayudarte con precios. Abrimos lunes a sábado 8:30am-5:30pm, domingos y feriados 9:00am-2:00pm."

6. OTROS TEMAS: responde amablemente que solo manejas productos y servicios de Cell Center 4620.

Responde siempre corto y directo. Muestra el nombre exacto del producto como aparece en el inventario."""


client = anthropic.Anthropic()


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True) or {}
        messages_list = data.get("messages", [])

        for msg in messages_list:
            if msg.get("from_me", False):
                continue
            if msg.get("type", "") != "text":
                continue

            msg_timestamp = msg.get("timestamp", 0)
            ahora = time.time()
            antiguedad = int(ahora - msg_timestamp)

            if msg_timestamp < BOT_START_TIME or antiguedad > 3600:
                print("Mensaje ignorado - muy antiguo: " + str(antiguedad) + "s")
                continue

            from_number = msg.get("from", "")
            if not from_number:
                continue

            chat_id = msg.get("chat_id", "") or msg.get("chatId", "") or ""
            if "@g.us" in from_number or "@g.us" in chat_id:
                print("Mensaje de grupo ignorado")
                continue
            if "broadcast" in from_number.lower() or "broadcast" in chat_id.lower():
                print("Mensaje broadcast ignorado")
                continue

            numero_limpio = from_number.replace("@s.whatsapp.net", "").replace("+", "")
            if numero_limpio not in NUMEROS_AUTORIZADOS:
                print("Número no autorizado: " + numero_limpio)
                continue

            body = msg.get("text", {}).get("body", "").strip()
            if not body:
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
                        precio_usd, precio_bs = calcular_precio_bs(p['list_price'])
                        stock = int(p['qty_available'])
                        nombre = p['name']
                        contexto_odoo += f"- {nombre}: ${precio_usd} USD / Bs. {precio_bs:,} | Stock: {stock} unidades\n"
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
                            contexto_odoo += f"- {nombre} (compatible con {ref or modelo_pedido}): ${precio_usd} USD / Bs. {precio_bs:,} | Stock: {stock} unidades\n"
                            if stock_bajo_info is None and 1 <= stock <= 2:
                                stock_bajo_info = {"producto": nombre, "stock": stock}
                    else:
                        precio_usd, precio_bs = calcular_precio_bs(compatibles['list_price'])
                        stock = int(compatibles['qty_available'])
                        nombre = compatibles['name']
                        modelo_pedido = compatibles.get('_compatible_con', '')
                        contexto_odoo += f"- {nombre} (compatible con {modelo_pedido}): ${precio_usd} USD / Bs. {precio_bs:,} | Stock: {stock} unidades\n"
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
                    f"Si no ves tu modelo aquí, es porque no lo tenemos disponible."
                )
                send_whapi_message(from_number, reply)

            else:
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
