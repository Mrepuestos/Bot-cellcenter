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

PALABRAS_SI = ["si", "sí", "yes", "claro", "dale", "ok", "okay", "quiero", "aparta", "reserva", "separa", "confirmado", "afirmativo", "me interesa", "la quiero"]

PALABRAS_IGNORAR = {"pantalla", "de", "el", "la", "los", "las", "un", "una", "para", "del", "con", "por", "que", "precio", "cuanto", "tienes", "tienen", "hay", "stock", "y", "tendrás", "cuales", "son", "disponibles"}

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


def limpiar_html(texto):
    if not texto:
        return ""
    texto_limpio = re.sub(r'<[^>]+>', ' ', str(texto))
    texto_limpio = texto_limpio.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
    texto_limpio = re.sub(r'\s+', ' ', texto_limpio)
    return texto_limpio.strip()


def obtener_tasa_bcv():
    try:
        fecha_hoy = time.strftime("%Y-%m-%d")
        if tasa_bcv_cache["fecha"] == fecha_hoy:
            return tasa_bcv_cache["tasa"]
        r = requests.get("https://ve.dolarapi.com/v1/dolares/oficial", timeout=5)
        data = r.json()
        tasa = float(data["promedio"])
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


def esta_abierto():
    tz = pytz.timezone("America/Caracas")
    ahora = datetime.now(tz)
    dia = ahora.weekday()
    hora = ahora.hour + ahora.minute / 60
    if dia == 6:
        return 9.0 <= hora < 14.0
    else:
        return 8.5 <= hora < 17.5


def cargar_historial(numero):
    try:
        resultado = supabase.table("Clientes").select("historial").eq("numero", numero).execute()
        if resultado.data:
            historial_str = resultado.data[0].get("historial", "")
            if historial_str:
                return json.loads(historial_str)
        return []
    except Exception as e:
        print(f"Error cargando historial: {e}")
        return []


def guardar_historial(numero, historial):
    try:
        historial_str = json.dumps(historial[-20:])
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


def corregir_texto(texto):
    texto_lower = texto.lower()
    for error, correcto in CORRECCIONES.items():
        texto_lower = texto_lower.replace(error, correcto)
    return texto_lower


def es_coincidencia_exacta_palabra(palabra, nombre_lower):
    patron = r'(?<![a-z0-9])' + re.escape(palabra) + r'(?![a-z0-9])'
    return bool(re.search(patron, nombre_lower))


def hay_coincidencia_exacta_con_stock(resultado_final, palabras, mensaje_sin_espacios):
    """Verifica si algún producto tiene coincidencia exacta Y tiene stock"""
    for p in resultado_final:
        if int(p['qty_available']) <= 0:
            continue
        nombre_lower = p['name'].lower()
        nombre_sin_espacios = nombre_lower.replace(" ", "")
        # Coincidencia exacta por nombre sin espacios
        if nombre_sin_espacios == mensaje_sin_espacios:
            return True
        # Todas las palabras coinciden y el nombre es similar en longitud
        if palabras and all(es_coincidencia_exacta_palabra(p2, nombre_lower) for p2 in palabras):
            palabras_nombre = [w for w in nombre_lower.split() if w not in PALABRAS_IGNORAR]
            if abs(len(palabras_nombre) - len(palabras)) <= 1:
                return True
    return False


def buscar_compatibles(todos, mensaje_sin_espacios, palabras):
    """Busca productos que tengan compatibilidad con el modelo buscado"""
    compatibles = []
    for producto in todos:
        if int(producto['qty_available']) <= 0:
            continue

        notas_raw = producto.get('description') or ""
        notas = limpiar_html(notas_raw)

        if 'COMPATIBLE:' not in notas.upper():
            continue

        for linea in notas.split('\n'):
            if 'COMPATIBLE:' not in linea.upper():
                continue

            modelos_str = linea.upper().replace('COMPATIBLE:', '').strip()
            modelos_lista = [m.strip().lower() for m in modelos_str.split(',')]
            print(f"Revisando compatibles de {producto['name']}: {modelos_lista}")

            for modelo in modelos_lista:
                modelo = modelo.strip()
                if not modelo:
                    continue
                modelo_sin_espacios = modelo.replace(" ", "")

                coincide = False
                if mensaje_sin_espacios and modelo_sin_espacios:
                    if mensaje_sin_espacios == modelo_sin_espacios:
                        coincide = True
                    elif mensaje_sin_espacios in modelo_sin_espacios or modelo_sin_espacios in mensaje_sin_espacios:
                        coincide = True
                if not coincide and palabras and len(palabras) >= 1:
                    if all(p in modelo for p in palabras):
                        coincide = True

                if coincide:
                    producto['_compatible_con'] = modelo
                    compatibles.append(producto)
                    print(f"Compatible encontrado: {producto['name']} es compatible con {modelo}")
                    break

    return compatibles


def consultar_odoo(mensaje):
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
        print(f"Odoo UID: {uid}")
        if not uid:
            print("ERROR: No se pudo autenticar en Odoo")
            return None, None

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
        todos = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            'product.product', 'search_read',
            [[]],
            {'fields': ['name', 'list_price', 'qty_available', 'description'], 'limit': 500}
        )
        print(f"Total productos en Odoo: {len(todos)}")

        mensaje_corregido = corregir_texto(mensaje)
        palabras = [p for p in mensaje_corregido.split() if len(p) >= 1 and p not in PALABRAS_IGNORAR]
        print(f"Palabras buscadas: {palabras}")

        mensaje_sin_espacios = mensaje_corregido.replace(" ", "").lower()

        encontrados = []
        for producto in todos:
            nombre_lower = producto['name'].lower()
            nombre_sin_espacios = nombre_lower.replace(" ", "")

            coincidencias = sum(1 for p in palabras if es_coincidencia_exacta_palabra(p, nombre_lower))

            if mensaje_sin_espacios and len(mensaje_sin_espacios) > 2:
                if nombre_sin_espacios == mensaje_sin_espacios:
                    coincidencias += 5
                elif nombre_sin_espacios.startswith(mensaje_sin_espacios) or nombre_sin_espacios.endswith(mensaje_sin_espacios):
                    coincidencias += 2

            if palabras and all(es_coincidencia_exacta_palabra(p, nombre_lower) for p in palabras):
                coincidencias += 2

            if coincidencias > 0:
                producto['_score'] = coincidencias
                encontrados.append(producto)

        encontrados.sort(key=lambda x: x['_score'], reverse=True)
        limite = 5 if len(encontrados) <= 5 else 3
        resultado_final = encontrados[:limite]

        for p in resultado_final:
            print(f"Producto: {p['name']} | Stock: {p['qty_available']} | Score: {p['_score']}")

        # Verificar si hay coincidencia exacta con stock
        exacto_con_stock = hay_coincidencia_exacta_con_stock(resultado_final, palabras, mensaje_sin_espacios)
        sin_resultados = not resultado_final
        todos_agotados = resultado_final and all(int(p['qty_available']) == 0 for p in resultado_final)

        print(f"Exacto con stock: {exacto_con_stock} | Sin resultados: {sin_resultados} | Todos agotados: {todos_agotados}")

        # Buscar compatibles si no hay exacto con stock
        compatibles = []
        if not exacto_con_stock:
            print("No hay coincidencia exacta con stock, buscando compatibilidades...")
            compatibles = buscar_compatibles(todos, mensaje_sin_espacios, palabras)

        # Decidir qué retornar
        if exacto_con_stock:
            return resultado_final, None
        elif compatibles:
            return None, compatibles
        elif sin_resultados:
            return None, None
        else:
            return resultado_final, None

    except Exception as e:
        print(f"Error consultando Odoo: {e}")
        return None, None


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


def get_system_prompt():
    abierto = esta_abierto()
    estado_tienda = "ABIERTA" if abierto else "CERRADA"

    return f"""Eres un vendedor directo de Cell Center 4620, tienda de celulares en Venezuela.
La tienda está actualmente: {estado_tienda}

Detecta automáticamente qué necesita el cliente y responde según el tema:

1. PANTALLAS: Si pregunta por pantallas o repuestos, consulta el inventario que se te proporcionará y responde con precio en USD y bolívares. No inventes precios. No menciones la cantidad de stock al cliente.

MÚLTIPLES REFERENCIAS: Si el cliente manda varios modelos en un mensaje, responde cada uno en formato lista:
✅ *Modelo*: $12 USD / Bs. 8,243
❌ *Modelo*: No disponible

COMPATIBILIDADES: Si el inventario incluye una sección "PRODUCTOS COMPATIBLES", significa que el modelo exacto no está disponible pero hay uno compatible. Responde así:
"No tenemos la pantalla para [modelo pedido], pero tenemos una compatible: *[modelo compatible]*: $XX USD / Bs. XX,XXX"

REGLAS IMPORTANTES:
- Nunca preguntes "¿Te interesa?" ni frases similares después de dar un precio. Solo da el precio y punto.
- Si el stock es 0 o no existe: solo di que no está disponible. NUNCA sugieras otros modelos ni similares.
- Si el stock es 1 o 2: da el precio y agrega una frase corta avisando que queda muy poco sin decir la cantidad. Varía las frases:
  "Por cierto, este modelo está casi agotado. ¿Lo reservamos?"
  "Nos queda muy poco de este modelo. ¿Lo apartamos?"
  "Existencia muy limitada. ¿Lo separamos para ti?"
  "Está por agotarse. ¿Lo guardamos?"
- Si el stock es 3 o más: solo da el precio. Sin comentarios adicionales.

2. CELULARES: responde exactamente: "DERIVAR_TECNICO"

3. SERVICIO TÉCNICO: responde exactamente: "DERIVAR_TECNICO"

4. ACCESORIOS: responde exactamente: "DERIVAR_ACCESORIOS"

5. HORARIO O SI ESTAMOS ABIERTOS: Si preguntan si estamos trabajando, abiertos, en tienda o por el horario:
- Si la tienda está ABIERTA: responde que sí están en tienda y el horario es lunes a sábado 8:30am-5:30pm, domingos y feriados 9:00am-2:00pm.
- Si la tienda está CERRADA: avisa que estamos fuera de horario pero que puedes ayudar con preguntas. Varía las frases:
  "En este momento estamos cerrados, pero aquí estoy para responder tus preguntas. Nuestro horario es lunes a sábado 8:30am-5:30pm, domingos y feriados 9:00am-2:00pm."
  "La tienda está cerrada por ahora, aunque puedo ayudarte con lo que necesites. Abrimos lunes a sábado 8:30am-5:30pm, domingos y feriados 9:00am-2:00pm."
  "Estamos fuera de horario, pero no te preocupes, estoy aquí para atenderte. Nuestro horario es lunes a sábado 8:30am-5:30pm, domingos y feriados 9:00am-2:00pm."

6. OTROS TEMAS: responde amablemente que solo manejas productos y servicios de Cell Center 4620.

Responde siempre corto y directo. Muestra el nombre del producto tal como aparece en el inventario."""


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

            productos, compatibles = consultar_odoo(body)
            contexto_odoo = ""
            stock_bajo_info = None

            if productos:
                contexto_odoo = "\n\nINFORMACIÓN DEL INVENTARIO:\n"
                for p in productos:
                    precio_usd = p['list_price']
                    precio_tabla, precio_bs = calcular_precio_bs(precio_usd)
                    stock = int(p['qty_available'])
                    nombre = p['name']
                    contexto_odoo += f"- {nombre}: ${int(precio_usd)} USD / Bs. {precio_bs:,} | Stock: {stock} unidades\n"
                    if stock_bajo_info is None and 1 <= stock <= 2:
                        stock_bajo_info = {
                            "producto": nombre,
                            "stock": stock
                        }
            elif compatibles:
                contexto_odoo = "\n\nPRODUCTOS COMPATIBLES (el modelo exacto no está en inventario):\n"
                for p in compatibles:
                    precio_usd = p['list_price']
                    precio_tabla, precio_bs = calcular_precio_bs(precio_usd)
                    stock = int(p['qty_available'])
                    nombre = p['name']
                    compatible_con = p.get('_compatible_con', '')
                    contexto_odoo += f"- {nombre} (compatible con {compatible_con}): ${int(precio_usd)} USD / Bs. {precio_bs:,} | Stock: {stock} unidades\n"
                    if stock_bajo_info is None and 1 <= stock <= 2:
                        stock_bajo_info = {
                            "producto": nombre,
                            "stock": stock
                        }
            else:
                contexto_odoo = "\n\nNo se encontró el producto en el inventario."

            historial = cargar_historial(from_number)
            mensaje_con_contexto = body + contexto_odoo
            historial.append({"role": "user", "content": mensaje_con_contexto})

            if len(historial) > 20:
                historial = historial[-20:]

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

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"Error en webhook: {e}")
        return jsonify({"status": "error", "detail": str(e)}), 200


@app.route('/', methods=['GET'])
def health():
    return "Cell Center Bot activo ✅", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
