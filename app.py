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


def consultar_odoo(producto_nombre):
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
        if not uid:
            return None

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
        productos = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            'product.product', 'search_read',
            [[['name', 'ilike', producto_nombre]]],
            {'fields': ['name', 'list_price', 'qty_available'], 'limit': 5}
        )
        return productos
    except Exception as e:
        print(f"Error consultando Odoo: {e}")
        return None


SYSTEM = f"""Eres un vendedor directo de Cell Center, tienda de celulares en Venezuela.
Solo manejas pantallas por ahora.
Responde corto y directo, máximo 2 líneas.
Muestra siempre ambos precios: USD y bolívares (tasa BCV: {TASA_BCV}).
No inventes precios.

Cuando el cliente pregunte por una pantalla, el sistema consultará el inventario de Odoo automáticamente y te dará el precio y stock real. Usa esa información para responder.

Si el producto no está en inventario, dilo y ofrece contactar a un asesor.

MANEJO DE ERRORES ORTOGRÁFICOS:
- Interpreta errores de escritura y abreviaciones con flexibilidad.
- "samsug", "samsum" → Samsung
- "redmi", "remi", "xiaomi" → Redmi
- "infnix", "infinik" → Infinix
- "iph", "aifon" → Iphone
- "huawe", "huawey" → Huawei
- "tecnho", "tekno" → Tecno
- Si hay varias opciones parecidas muéstralas todas"""

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

            # Consultar Odoo con lo que escribió el cliente
            productos = consultar_odoo(body)
            contexto_odoo = ""
            if productos:
                contexto_odoo = "\n\nINFORMACIÓN DEL INVENTARIO ODOO:\n"
                for p in productos:
                    precio_usd = p['list_price']
                    precio_bs = int(precio_usd * TASA_BCV)
                    stock = int(p['qty_available'])
                    contexto_odoo += f"- {p['name']}: ${precio_usd} USD / Bs. {precio_bs:,} | Stock: {stock} unidades\n"
            else:
                contexto_odoo = "\n\nEl producto consultado no está en el inventario de Odoo."

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
