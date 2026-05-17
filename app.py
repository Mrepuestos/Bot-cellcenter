from flask import Flask, request, jsonify
import requests
import anthropic
import os
import time
import xmlrpc.client
import random

app = Flask(__name__)

BOT_START_TIME = time.time()

NUMEROS_AUTORIZADOS = [
    "584149202844",
    "584123680624"
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

TABLA_PRECIOS = {
    11: 15,
    12: 16,
    13: 17,
    14: 19,
    15: 20,
    17: 23,
    19: 26,
    21: 28,
    24: 32
}

tasa_bcv_cache = {"tasa": 515.0, "fecha": ""}
stock_bajo_pendiente = {}

FRASES_STOCK_BAJO = [
    "¡Ojo! Solo nos quedan {stock} unidad(es) de esta pantalla. ¿La apartas?",
    "Quedan pocas, solo {stock} en inventario. ¿Te interesa asegurarla?",
    "Stock limitado, únicamente {stock} disponible(s). ¿La reservamos?",
    "Casi agotada, solo {stock} unidad(es). ¿Quieres que te la guardemos?",
    "Últimas {stock} unidad(es) disponibles. ¿La separamos para ti?"
]

PALABRAS_SI = ["si", "sí", "yes", "claro", "dale", "ok", "okay", "quiero", "aparta", "reserva", "separa", "confirmado", "afirmativo"]


def obtener_tasa_bcv():
    try:
        fecha_hoy = time.strftime("%Y-%m-%d")
        if tasa_bcv_cache["fecha"] == fecha_hoy:
            return tasa_bcv_cache["tasa"]
        r = requests.get("https://pydolarve.org/api/v1/dollar?page=bcv", timeout=5)
        data = r.json()
        tasa = float(data["monitors"]["usd"]["price"])
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
    tasa = obtener_tasa_bcv()
    precio_bs = round(precio_tabla * tasa)
    return precio_tabla, precio_bs


CORRECCIONES = {
    "samsug": "samsung", "samsum": "samsung", "samsun": "samsung",
    "remi": "redmi", "xiaomi": "redmi",
    "infnix": "infinix", "infinik": "infinix", "ifninx": "infinix",
    "iph": "iphone", "aifon": "iphone", "aiphone": "iphone",
    "huawe": "huawei", "huawey": "huawei", "huawai": "huawei",
    "tecnho": "tecno", "tekno": "tecno",
    "motoral": "motorola", "motarola": "motorola",
    "nte": "note", "notte": "note",
    "alkatel": "alcatel", "alcater": "alcatel",
    "onor": "honor", "onour": "honor"
}


def corregir_texto(texto):
    texto_lower = texto.lower()
    for error, correcto in CORRECCIONES.items():
        texto_lower = texto_lower.replace(error, correcto)
    return texto_lower


def consultar_odoo(mensaje):
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
        print(f"Odoo UID: {uid}")
        if not uid:
            print("ERROR: No se pudo autenticar en Odoo")
            return None

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
        todos = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            'product.product', 'search_read',
            [[['name', 'like', 'Repuesto']]],
            {'fields': ['name', 'list_price', 'qty_available'], 'limit': 500}
        )
        print(f"Total productos en Odoo: {len(todos)}")
        if todos:
            print(f"Ejemplo producto: {todos[0]['name']}")

        mensaje_corregido = corregir_texto(mensaje)
        palabras = [p for p in mensaje_corregido.split() if len(p) > 1]
        print(f"Palabras buscadas: {palabras}")

        encontrados = []
        for producto in todos:
            nombre_lower = producto['name'].lower()
            coincidencias = sum(1 for p in palabras if p in nombre_lower)
            if coincidencias > 0:
                producto['_score'] = coincidencias
                encontrados.append(producto)

        print(f"Productos encontrados: {len(encontrados)}")
        encontrados.sort(key=lambda x: x['_score'], reverse=True)
        return encontrados[:5]

    except Exception as e:
        print(f"Error consultando Odoo: {e}")
        return None


def send_whapi_message(to: str, text: str):
    url = f"{WHAPI_API_URL}/messages/text"
    headers = {
        "Authorization": f"Bearer {WHAPI_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"to": to, "body": text}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"Error enviando mensaje Whapi: {e}")


def notificar_asesor(asesor: str, tema: str, numero_cliente: str):
    numero_formateado = "+" + numero_cliente.replace("@s.whatsapp.net", "")
    mensaje = f"🔔 *Mensaje pendiente*\nUn cliente está esperando respuesta sobre *{tema}*.\nNúmero: {numero_formateado}"
    send_whapi_message(asesor, mensaje)


def notificar_stock_bajo(numero_cliente: str, producto: str, stock: int):
    numero_formateado = "+" + numero_cliente.replace("@s.whatsapp.net", "")
    mensaje = f"⚠️ *Stock bajo - Cliente interesado*\nProducto: *{producto}*\nStock: {stock} unidad(es)\nCliente: {numero_formateado}\n\nEl cliente confirmó que quiere apartar esta pantalla."
    send_whapi_message(ASESOR_STOCK, mensaje)


SYSTEM = """Eres un vendedor directo de Cell Center 4620, tienda de celulares en Venezuela.

Detecta automáticamente qué necesita el cliente y responde según el tema:

1. PANTALLAS: Si pregunta por pantallas o repuestos, consulta el inventario que se te proporcionará y responde con precio en USD y bolívares y stock disponible. No inventes precios.

2. CELULARES: Si pregunta por comprar un celular responde exactamente: "DERIVAR_TECNICO"

3. SERVICIO TÉCNICO: Si pregunta por reparaciones, servicio técnico o diagnóstico responde exactamente: "DERIVAR_TECNICO"

4. ACCESORIOS: Si pregunta por accesorios, fundas, vidrios templados, cargadores, etc. responde exactamente: "DERIVAR_ACCESORIOS"

5. OTROS TEMAS: Si pregunta algo que no tiene que ver con la tienda, responde amablemente que solo manejas productos y servicios de Cell Center 4620.

Responde siempre corto y directo, máximo 2-3 líneas.
Si el inventario dice stock 0, dilo claramente.
Si no encuentras el producto en inventario, dilo y ofrece contactar a un asesor.
Ignora la parte "Repuesto/Marca/" del nombre, muestra solo Marca + Modelo al cliente."""

conversations = {}
client = anthropic.Anthropic()


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

            productos = consultar_odoo(body)
            contexto_odoo = ""
            stock_bajo_info = None

            if productos:
                contexto_odoo = "\n\nINFORMACIÓN DEL INVENTARIO:\n"
                for p in productos:
                    precio_usd = p['list_price']
                    precio_tabla, precio_bs = calcular_precio_bs(precio_usd)
                    stock = int(p['qty_available'])
                    partes = p['name'].split('/')
                    nombre = partes[-1] if len(partes) > 0 else p['name']
                    marca = partes[1] if len(partes) > 1 else ''
                    contexto_odoo += f"- {marca} {nombre}: ${precio_tabla} USD / Bs. {precio_bs:,} | Stock: {stock} unidades\n"
                    if stock_bajo_info is None and 1 <= stock <= 2:
                        stock_bajo_info = {
                            "producto": f"{marca} {nombre}",
                            "stock": stock
                        }
            else:
                contexto_odoo = "\n\nNo se encontró el producto en el inventario de pantallas."

            if from_number not in conversations:
                conversations[from_number] = []

            mensaje_con_contexto = body + contexto_odoo
            conversations[from_number].append({"role": "user", "content": mensaje_con_contexto})

            if len(conversations[from_number]) > 20:
                conversations[from_number] = conversations[from_number][-20:]

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                system=SYSTEM,
                messages=conversations[from_number]
            )

            reply = response.content[0].text

            if "DERIVAR_TECNICO" in reply:
                notificar_asesor(ASESOR_TECNICO, "celulares o servicio técnico", from_number)
                reply = "Un momento, un asesor te atenderá enseguida 👋"
            elif "DERIVAR_ACCESORIOS" in reply:
                notificar_asesor(ASESOR_ACCESORIOS, "accesorios", from_number)
                reply = "Un momento, un asesor te atenderá enseguida 👋"
            elif stock_bajo_info:
                frase = random.choice(FRASES_STOCK_BAJO).format(stock=stock_bajo_info["stock"])
                reply = reply + "\n\n" + frase
                stock_bajo_pendiente[from_number] = stock_bajo_info

            conversations[from_number].append({"role": "assistant", "content": reply})
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
