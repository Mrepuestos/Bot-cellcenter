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
    "tienes","tienen","hay","tendrás","tengo","tiene","das","dar","dame","dime","sabe","saben","quiero","quieres","puedes","puede","necesito",
    "pantalla","precio","cuanto","cuánto","stock","disponibles","disponible","cuales","hola","buenas","buen","buenos","dias","día","dia","tardes","noches","saludos",
    "favor","porfavor","porfa","gracias","please","podria","podría",
    "mano","hermano","brother","bro","amigo","amiga","chamo","chama","pana","compañero","compañera","jefe","jefa","señor","señora","estimado","estimada","maestro","profe","socio","vale","papi","mami",
    "me","mi","mis","tu","tus","nos","nuestro","nuestra","alguna","alguno","algún","otro","otra",
    "ese","esa","esto","esta","aqui","acá","allá","cuando","como","donde","quien","qué","mas","más","muy","bien","mal","solo","también","tampoco",
    "d","q","x","k"
}

SEPARADORES_MODELOS = {"y", "and", "tambien", "también", "ademas", "además"}

CORRECCIONES = {
    "samsug":"samsung","samsum":"samsung","samsun":"samsung",
    "remi":"redmi","xiaomi":"redmi",
    "infnix":"infinix","infinik":"infinix","ifninx":"infinix",
    "iph":"iphone","aifon":"iphone","aiphone":"iphone",
    "huawe":"huawei","huawey":"huawei","huawai":"huawei",
    "tecnho":"tecno","tekno":"tecno",
    "motoral":"motorola","motarola":"motorola",
    "nte":"note","notte":"note",
    "alkatel":"alcatel","alcater":"alcatel",
    "onor":"honor","onour":"honor"
}

MARCAS = {"samsung","redmi","infinix","iphone","huawei","tecno","motorola","alcatel","honor","realme","xiaomi"}


def limpiar_html(texto):
    if not texto:
        return ""
    texto_limpio = re.sub(r'<[^>]+>', ' ', str(texto))
    texto_limpio = texto_limpio.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').replace('&nbsp;',' ')
    return re.sub(r'\s+', ' ', texto_limpio).strip()


def limpiar_puntuacion(texto):
    return re.sub(r'[^\w\s]', ' ', texto)


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


def corregir_texto(texto):
    texto_lower = limpiar_puntuacion(texto.lower())
    for error, correcto in CORRECCIONES.items():
        texto_lower = texto_lower.replace(error, correcto)
    return texto_lower


def es_coincidencia_exacta_palabra(palabra, nombre_lower):
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(palabra) + r'(?![a-z0-9])', nombre_lower))


def calcular_score(palabras, nombre_lower, mensaje_sin_espacios):
    nombre_sin_espacios = nombre_lower.replace(" ", "")

    if len(palabras) > 1:
        if not all(es_coincidencia_exacta_palabra(p, nombre_lower) for p in palabras):
            return 0

    coincidencias = sum(1 for p in palabras if es_coincidencia_exacta_palabra(p, nombre_lower))

    if mensaje_sin_espacios and len(mensaje_sin_espacios) > 2:
        if nombre_sin_espacios == mensaje_sin_espacios:
            coincidencias += 5
        elif nombre_sin_espacios.startswith(mensaje_sin_espacios) or nombre_sin_espacios.endswith(mensaje_sin_espacios):
            coincidencias += 3
        elif mensaje_sin_espacios in nombre_sin_espacios:
            coincidencias += 2
        elif nombre_sin_espacios in mensaje_sin_espacios:
            coincidencias += 2

    if palabras and all(es_coincidencia_exacta_palabra(p, nombre_lower) for p in palabras):
        coincidencias += 2

    return coincidencias


def dividir_en_referencias(mensaje):
    mensaje_corregido = corregir_texto(mensaje)
    partes = re.split(r'\n', mensaje_corregido)
    referencias = []

    for parte in partes:
        parte = parte.strip()
        if not parte:
            continue

        palabras = parte.split()
        grupo_actual = []

        for i, palabra in enumerate(palabras):
            if palabra in SEPARADORES_MODELOS:
                siguiente_es_marca = (i + 1 < len(palabras) and palabras[i+1].lower() in MARCAS)
                if siguiente_es_marca and grupo_actual:
                    ref = " ".join(grupo_actual).strip()
                    if ref:
                        referencias.append(ref)
                    grupo_actual = []
                else:
                    grupo_actual.append(palabra)
            else:
                grupo_actual.append(palabra)

        if grupo_actual:
            ref = " ".join(grupo_actual).strip()
            if ref:
                referencias.append(ref)

    return referencias if referencias else [mensaje_corregido]


def buscar_compatible_individual(todos, mensaje_sin_espacios, palabras):
    """Busca compatible con coincidencia más flexible"""
    for producto in todos:
        if int(producto['qty_available']) <= 0:
            continue
        notas = limpiar_html(producto.get('description') or "")
        if 'COMPATIBLE:' not in notas.upper():
            continue
        for linea in notas.split('\n'):
            if 'COMPATIBLE:' not in linea.upper():
                continue
            modelos_str = linea.upper().replace('COMPATIBLE:', '').strip()
            for modelo in modelos_str.split(','):
                modelo = limpiar_puntuacion(modelo.strip().lower())
                if not modelo:
                    continue
                modelo_sin_espacios = modelo.replace(" ", "")
                palabras_modelo = [p for p in modelo.split() if p not in PALABRAS_IGNORAR and len(p) > 1]

                coincide = False

                # Coincidencia sin espacios
                if mensaje_sin_espacios and modelo_sin_espacios:
                    if mensaje_sin_espacios == modelo_sin_espacios:
                        coincide = True
                    elif mensaje_sin_espacios in modelo_sin_espacios or modelo_sin_espacios in mensaje_sin_espacios:
                        coincide = True

                # Coincidencia por palabras — todas las palabras buscadas en el modelo
                if not coincide and palabras:
                    palabras_filtradas = [p for p in palabras if len(p) > 1]
                    if palabras_filtradas and all(p in modelo for p in palabras_filtradas):
                        coincide = True

                # Coincidencia parcial — al menos la mitad de palabras coinciden
                if not coincide and palabras and palabras_modelo:
                    palabras_filtradas = [p for p in palabras if len(p) > 1]
                    if palabras_filtradas:
                        coincidencias = sum(1 for p in palabras_filtradas if p in modelo)
                        if coincidencias >= len(palabras_filtradas) * 0.7:
                            coincide = True

                if coincide:
                    producto_copia = dict(producto)
                    producto_copia['_compatible_con'] = modelo
                    print(f"Compatible encontrado: {producto['name']} es compatible con {modelo}")
                    return producto_copia
    return None


def buscar_en_odoo(todos, referencia):
    mensaje_corregido = corregir_texto(referencia)
    palabras = [p for p in mensaje_corregido.split() if len(p) >= 1 and p not in PALABRAS_IGNORAR]
    mensaje_sin_espacios = mensaje_corregido.replace(" ", "").lower()

    if not palabras:
        return None, None

    encontrados = []
    for producto in todos:
        nombre_lower = producto['name'].lower()
        score = calcular_score(palabras, nombre_lower, mensaje_sin_espacios)
        if score > 0:
            producto_copia = dict(producto)
            producto_copia['_score'] = score
            encontrados.append(producto_copia)

    encontrados.sort(key=lambda x: x['_score'], reverse=True)
    resultado = encontrados[:3] if encontrados else []
    con_stock = [p for p in resultado if int(p['qty_available']) > 0]

    if con_stock:
        return con_stock[0], None

    compatible = buscar_compatible_individual(todos, mensaje_sin_espacios, palabras)
    if compatible:
        return None, compatible

    return resultado[0] if resultado else None, None

def consultar_odoo(mensaje):
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
        print(f"Odoo UID: {uid}")
        if not uid:
            return None, None

        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
        todos = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            'product.product', 'search_read',
            [[]],
            {'fields': ['name', 'list_price', 'qty_available', 'description'], 'limit': 500}
        )
        print(f"Total productos en Odoo: {len(todos)}")

        referencias = dividir_en_referencias(mensaje)
        print(f"Referencias detectadas: {referencias}")

        if len(referencias) == 1:
            mensaje_corregido = corregir_texto(mensaje)
            palabras = [p for p in mensaje_corregido.split() if len(p) >= 1 and p not in PALABRAS_IGNORAR]
            print(f"Palabras buscadas: {palabras}")
            mensaje_sin_espacios = mensaje_corregido.replace(" ", "").lower()

            encontrados = []
            for producto in todos:
                nombre_lower = producto['name'].lower()
                score = calcular_score(palabras, nombre_lower, mensaje_sin_espacios)
                if score > 0:
                    producto['_score'] = score
                    encontrados.append(producto)

            encontrados.sort(key=lambda x: x['_score'], reverse=True)
            resultado_final = encontrados[:5] if len(encontrados) <= 5 else encontrados[:3]

            for p in resultado_final:
                print(f"Producto: {p['name']} | Stock: {p['qty_available']} | Score: {p['_score']}")

            con_stock = [p for p in resultado_final if int(p['qty_available']) > 0]
            sin_stock = [p for p in resultado_final if int(p['qty_available']) <= 0]
            sin_resultados = not resultado_final

            print(f"Con stock: {len(con_stock)} | Sin stock: {len(sin_stock)} | Sin resultados: {sin_resultados}")

            if con_stock:
                return con_stock, None

            print("Buscando compatibilidades...")
            compatible = buscar_compatible_individual(todos, mensaje_sin_espacios, palabras)
            if compatible:
                return None, [compatible]
            elif sin_resultados:
                return None, None
            else:
                return sin_stock, None

        resultados_multiples = []
        compatibles_multiples = []

        for ref in referencias:
            print(f"Buscando referencia: {ref}")
            producto, compatible = buscar_en_odoo(todos, ref)

            if producto and int(producto['qty_available']) > 0:
                producto['_referencia'] = ref
                resultados_multiples.append(producto)
            elif compatible:
                compatible['_referencia'] = ref
                compatibles_multiples.append(compatible)
            elif producto:
                producto['_referencia'] = ref
                resultados_multiples.append(producto)

        if resultados_multiples or compatibles_multiples:
            return resultados_multiples if resultados_multiples else None, compatibles_multiples if compatibles_multiples else None

        return None, None

    except Exception as e:
        print(f"Error consultando Odoo: {e}")
        return None, None


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


def get_system_prompt():
    estado_tienda = "ABIERTA" if esta_abierto() else "CERRADA"
    return f"""Eres un vendedor directo de Cell Center 4620, tienda de celulares en Venezuela. Solo vendemos PANTALLAS y repuestos de celulares.
La tienda está actualmente: {estado_tienda}

REGLA PRINCIPAL: Cuando el inventario muestre productos con stock mayor a 0, SIEMPRE da el precio. NUNCA digas que no está disponible si hay stock. NUNCA preguntes si es para pantalla o celular, asume que siempre es para pantalla.

1. PANTALLAS: Si el inventario muestra productos disponibles, responde con precio en USD y bolívares. No menciones cantidad de stock.

MÚLTIPLES REFERENCIAS en un mensaje, responde en lista:
✅ *Modelo*: $12 USD / Bs. 8,243
❌ *Modelo*: No disponible
✅ *Modelo compatible con X*: $12 USD / Bs. 8,243

COMPATIBILIDADES: Si el inventario dice "PRODUCTOS COMPATIBLES", responde SOLO con ese producto compatible:
"No tenemos la pantalla para [modelo pedido], pero tenemos una compatible: *[nombre producto]*: $XX USD / Bs. XX,XXX"

STOCK 1 o 2: da el precio y avisa que queda muy poco. Varía las frases:
"Por cierto, este modelo está casi agotado. ¿Lo reservamos?"
"Nos queda muy poco de este modelo. ¿Lo apartamos?"
"Existencia muy limitada. ¿Lo separamos para ti?"
"Está por agotarse. ¿Lo guardamos?"

STOCK 3 o más: solo da el precio sin comentarios.
STOCK 0: solo di que no está disponible. NUNCA sugieras alternativas.

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

            productos, compatibles = consultar_odoo(body)
            contexto_odoo = ""
            stock_bajo_info = None

            if productos:
                contexto_odoo = "\n\nINFORMACIÓN DEL INVENTARIO:\n"
                for p in productos:
                    precio_usd, precio_bs = calcular_precio_bs(p['list_price'])
                    stock = int(p['qty_available'])
                    nombre = p['name']
                    contexto_odoo += f"- {nombre}: ${precio_usd} USD / Bs. {precio_bs:,} | Stock: {stock} unidades\n"
                    if stock_bajo_info is None and 1 <= stock <= 2:
                        stock_bajo_info = {"producto": nombre, "stock": stock}

            if compatibles:
                contexto_odoo += "\n\nPRODUCTOS COMPATIBLES (modelo exacto no disponible):\n"
                for p in compatibles:
                    precio_usd, precio_bs = calcular_precio_bs(p['list_price'])
                    stock = int(p['qty_available'])
                    nombre = p['name']
                    compatible_con = p.get('_compatible_con', '')
                    contexto_odoo += f"- {nombre} (compatible con {compatible_con}): ${precio_usd} USD / Bs. {precio_bs:,} | Stock: {stock} unidades\n"
                    if stock_bajo_info is None and 1 <= stock <= 2:
                        stock_bajo_info = {"producto": nombre, "stock": stock}

            if not productos and not compatibles:
                contexto_odoo = "\n\nNo se encontró el producto en el inventario."

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

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"Error en webhook: {e}")
        return jsonify({"status": "error", "detail": str(e)}), 200


@app.route('/', methods=['GET'])
def health():
    return "Cell Center Bot activo ✅", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
