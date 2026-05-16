from flask import Flask, request, jsonify
import requests
import anthropic
import os
import time
import xmlrpc.client

app = Flask(__name__)

BOT_START_TIME = time.time()

NUMEROS_AUTORIZADOS = [
    "584149202844",
    "584126093756"
]

TASA_BCV = float(os.environ.get("TASA_BCV", "36.5"))
WHAPI_TOKEN = os.environ.get("WHAPI_TOKEN", "")
WHAPI_API_URL = os.environ.get("WHAPI_API_URL", "https://gate.whapi.cloud")
ODOO_URL = os.environ.get("ODOO_URL", "")
ODOO_DB = os.environ.get("ODOO_DB", "")
ODOO_USER = os.environ.get("ODOO_USER", "")
ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")

# Correcciones ortográficas comunes
CORRECCIONES = {
    "samsug": "samsung", "samsum": "samsung", "samsun": "samsung",
    "redmi": "redmi", "remi": "redmi", "xiaomi": "redmi",
    "infnix": "infinix", "infinik": "infinix", "ifninx": "infinix",
    "iph": "iphone", "aifon": "iphone", "aiphone": "iphone",
    "huawe": "huawei", "huawey": "huawei", "huawai": "huawei",
    "tecnho": "tecno", "tekno": "tecno", "tecno": "tecno",
    "motorola": "motorola", "motoral": "motorola", "motarola": "motorola",
    "nte": "note", "notte": "note", "not": "note",
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
            return None

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

        # Primero obtener todos los productos
        todos = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            'product.product', 'search_read',
            [[['name', 'like', 'Repuesto']]],
            {'fields': ['name', 'list_price', 'qty_available'], 'limit': 500}
        )

        # Corregir el mensaje del cliente
        mensaje_corregido = corregir_texto(mensaje)

        # Dividir en palabras clave
        palabras = [p for p in mensaje_corregido.split() if len(p) > 1]

        print(f"Buscando palabras: {palabras}")

        # Filtrar productos que contengan alguna palabra clave
        encontrados = []
        for producto in todos:
            nombre_lower = producto['name'].lower()
            coincidencias = sum(1 for p in palabras if p in nombre_lower)
            if coincidencias > 0:
                producto['_score'] = coincidencias
                encontrados.append(producto)

        # Ordenar por mayor coincidencia
        encontrados.sort(key=lambda x: x['_score'], reverse=True)

        print(f"Productos encontrados: {encontrados[:5]}")
        return encontrados[:5]

    except Exception as e:
        print(f"Error consultando Odoo: {e}")
        return None


SYSTEM = f"""Eres un vendedor directo de Cell Center, tienda de celulares en Venezuela.
Solo manejas pantallas por ahora.
Responde corto y directo, máximo 2 líneas.
Muestra siempre ambos precios: USD y bolívares (tasa BCV: {TASA_BCV}).
No inventes precios.

El sistema consultará el inventario de Odoo automáticamente y te dará el precio y stock real.
Usa SOLO esa información para responder. No inventes precios ni stock.

Si el inventario dice stock 0, dilo claramente.
Si no se encuentra el producto, dilo y ofrece contactar a un asesor.

Ignora la parte "Repuesto/Marca/" del nombre, solo muestra el modelo al cliente.
Ejemplo: "Repuesto/Samsung/A12" → muestra como "Samsung A12"."""

conversations = {}
client = anthropic.Anthropic()


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

            # Consultar Odoo
            productos = consultar_odoo(body)
            contexto_odoo = ""
            if productos:
                contexto_odoo = "\n\nINFORMACIÓN DEL INVENTARIO:\n"
                for p in productos:
                    precio_usd = p['list_price']
                    precio_bs = int(precio_usd * TASA_BCV)
                    stock = int(p['qty_available'])
                    nombre = p['name'].split('/')[-1]
                    marca = p['name'].split('/')[1] if '/' in p['name'] else ''
                    contexto_odoo += f"- {marca} {nombre}: ${precio_usd} USD / Bs. {precio_bs:,} | Stock: {stock} unidades\n"
            else:
                contexto_odoo = "\n\nNo se encontró el producto en el inventario."

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
