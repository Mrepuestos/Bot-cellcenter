from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import anthropic
import requests

app = Flask(__name__)

CATALOG = [{"referencia":"Alcatel1B","marca":"Alcatel","precio":"13"},{"referencia":"Alcatel1","marca":"Alcatel","precio":"12"},{"referencia":"HONORX6S","marca":"Honor","precio":"12"},{"referencia":"Honor x6a","marca":"Honor","precio":"12"},{"referencia":"Honor x8","marca":"Honor","precio":"14"},{"referencia":"play10","marca":"Honor","precio":"12"},{"referencia":"x8a","marca":"Honor","precio":"14"},{"referencia":"Y9prime2019","marca":"Huawei","precio":"12"},{"referencia":"p30lite","marca":"Huawei","precio":"13"},{"referencia":"Y92019","marca":"Huawei","precio":"12"},{"referencia":"PSMART2019","marca":"Huawei","precio":"12"},{"referencia":"P20lite","marca":"Huawei","precio":"12"},{"referencia":"Hot112022","marca":"Infinix","precio":"13"},{"referencia":"SMART6PLUS","marca":"Infinix","precio":"12"},{"referencia":"NOTE12","marca":"Infinix","precio":"15"},{"referencia":"Note 8","marca":"Infinix","precio":"15"},{"referencia":"HOT20I","marca":"Infinix","precio":"12"},{"referencia":"60i","marca":"Infinix","precio":"12"},{"referencia":"Smart6HD","marca":"Infinix","precio":"12"},{"referencia":"HOT11S","marca":"Infinix","precio":"15"},{"referencia":"SMART9","marca":"Infinix","precio":"12"},{"referencia":"Smart6","marca":"Infinix","precio":"12"},{"referencia":"Hot10LITE","marca":"Infinix","precio":"12"},{"referencia":"HOT30I","marca":"Infinix","precio":"12"},{"referencia":"HOT30PLAY","marca":"Infinix","precio":"13"},{"referencia":"SMART10","marca":"Infinix","precio":"12"},{"referencia":"hot10play","marca":"Infinix","precio":"13"},{"referencia":"Hot12play","marca":"Infinix","precio":"13"},{"referencia":"Note11S","marca":"Infinix","precio":"15"},{"referencia":"HOT12","marca":"Infinix","precio":"13"},{"referencia":"Hot11PLAY","marca":"Infinix","precio":"13"},{"referencia":"Hot9","marca":"Infinix","precio":"12"},{"referencia":"HOT10I","marca":"Infinix","precio":"12"},{"referencia":"HOT50PRO","marca":"Infinix","precio":"13"},{"referencia":"IPH8PLUS","marca":"Iphone","precio":"12"},{"referencia":"Iphxr","marca":"Iphone","precio":"17"},{"referencia":"IPH8","marca":"Iphone","precio":"11"},{"referencia":"IPH7PLUS","marca":"Iphone","precio":"12"},{"referencia":"IPHXS","marca":"Iphone","precio":"19"},{"referencia":"IPHX","marca":"Iphone","precio":"19"},{"referencia":"IPH11","marca":"Iphone","precio":"17"},{"referencia":"MOTOG30","marca":"Motorola","precio":"12"},{"referencia":"G9POWER","marca":"Motorola","precio":"14"},{"referencia":"E20","marca":"Motorola","precio":"12"},{"referencia":"Moto g8play","marca":"Motorola","precio":"12"},{"referencia":"C112020","marca":"Realme","precio":"12"},{"referencia":"C112021","marca":"Realme","precio":"12"},{"referencia":"Note8","marca":"Redmi","precio":"12"},{"referencia":"Note13","marca":"Redmi","precio":"15"},{"referencia":"RMNOTE11","marca":"Redmi","precio":"14"},{"referencia":"Rm note12 con marco","marca":"Redmi","precio":"17"},{"referencia":"RM14C","marca":"Redmi","precio":"12"},{"referencia":"Note 8pro","marca":"Redmi","precio":"12"},{"referencia":"POCOM3","marca":"Redmi","precio":"14"},{"referencia":"Note9","marca":"Redmi","precio":"12"},{"referencia":"RM7A","marca":"Redmi","precio":"11"},{"referencia":"Rm12","marca":"Redmi","precio":"14"},{"referencia":"RM9","marca":"Redmi","precio":"12"},{"referencia":"RMNOTE12","marca":"Redmi","precio":"15"},{"referencia":"x3","marca":"Redmi","precio":"14"},{"referencia":"RMNOTE11OLED","marca":"Redmi","precio":"24"},{"referencia":"a5","marca":"Redmi","precio":"12"},{"referencia":"NOTE8T","marca":"Redmi","precio":"12"},{"referencia":"NOTE10PRO","marca":"Redmi","precio":"15"},{"referencia":"9A","marca":"Redmi","precio":"12"},{"referencia":"RMA3","marca":"Redmi","precio":"12"},{"referencia":"RM10C","marca":"Redmi","precio":"12"},{"referencia":"RM8A","marca":"Redmi","precio":"12"},{"referencia":"15c","marca":"Redmi","precio":"12"},{"referencia":"RM10","marca":"Redmi","precio":"14"},{"referencia":"A1","marca":"Redmi","precio":"11"},{"referencia":"NOTE9S","marca":"Redmi","precio":"12"},{"referencia":"Note7","marca":"Redmi","precio":"12"},{"referencia":"RMNOTE10","marca":"Redmi","precio":"12"},{"referencia":"A20CONMARCO","marca":"Samsung","precio":"14"},{"referencia":"J2core","marca":"Samsung","precio":"11"},{"referencia":"A20SCONMARCO","marca":"Samsung","precio":"13"},{"referencia":"A71","marca":"Samsung","precio":"14"},{"referencia":"A06","marca":"Samsung","precio":"12"},{"referencia":"a05s","marca":"Samsung","precio":"14"},{"referencia":"a07","marca":"Samsung","precio":"12"},{"referencia":"A23","marca":"Samsung","precio":"12"},{"referencia":"A30SCONMARCO","marca":"Samsung","precio":"14"},{"referencia":"A04S","marca":"Samsung","precio":"12"},{"referencia":"A10s","marca":"Samsung","precio":"12"},{"referencia":"A02S","marca":"Samsung","precio":"12"},{"referencia":"A05","marca":"Samsung","precio":"13"},{"referencia":"A51","marca":"Samsung","precio":"14"},{"referencia":"A22CONMARCO","marca":"Samsung","precio":"14"},{"referencia":"A01core","marca":"Samsung","precio":"12"},{"referencia":"a17","marca":"Samsung","precio":"12"},{"referencia":"J6PLUS","marca":"Samsung","precio":"12"},{"referencia":"A13","marca":"Samsung","precio":"12"},{"referencia":"A16","marca":"Samsung","precio":"15"},{"referencia":"A32CONMARCO","marca":"Samsung","precio":"15"},{"referencia":"A20S","marca":"Samsung","precio":"12"},{"referencia":"A02","marca":"Samsung","precio":"12"},{"referencia":"A11","marca":"Samsung","precio":"13"},{"referencia":"A21s","marca":"Samsung","precio":"13"},{"referencia":"A12","marca":"Samsung","precio":"12"},{"referencia":"A30 c/m oled","marca":"Samsung","precio":"21"},{"referencia":"A31 con marco","marca":"Samsung","precio":"15"},{"referencia":"A03 core","marca":"Samsung","precio":"12"},{"referencia":"POP5LITE","marca":"Tecno","precio":"12"},{"referencia":"Spark8T","marca":"Tecno","precio":"12"},{"referencia":"SPARK6GO","marca":"Tecno","precio":"12"},{"referencia":"SPARK6","marca":"Tecno","precio":"12"},{"referencia":"sparkgo2024","marca":"Tecno","precio":"12"},{"referencia":"Camon 20","marca":"Tecno","precio":"14"},{"referencia":"spark10PRO","marca":"Tecno","precio":"14"},{"referencia":"camon17pro","marca":"Tecno","precio":"13"},{"referencia":"POP6PRO","marca":"Tecno","precio":"12"},{"referencia":"SPARK7","marca":"Tecno","precio":"12"},{"referencia":"8C","marca":"Tecno","precio":"12"},{"referencia":"sparkgo2023","marca":"Tecno","precio":"12"},{"referencia":"Pova neo2","marca":"Tecno","precio":"12"},{"referencia":"pova3","marca":"Tecno","precio":"14"},{"referencia":"SPARK30C","marca":"Tecno","precio":"12"}]

def get_tasa_bcv():
    try:
        response = requests.get("https://pydolarve.org/api/v1/dollar?page=bcv", timeout=5)
        data = response.json()
        tasa = data["monitors"]["usd"]["price"]
        return float(tasa)
    except:
        return None

def build_system():
    tasa = get_tasa_bcv()
    if tasa:
        catalog_text = "\n".join([
            f"- {p['marca']} {p['referencia']}: ${p['precio']} USD / Bs. {int(float(p['precio']) * tasa):,}"
            for p in CATALOG
        ])
        currency_note = "Muestra siempre ambos precios: USD y bolívares."
    else:
        catalog_text = "\n".join([
            f"- {p['marca']} {p['referencia']}: ${p['precio']} USD"
            for p in CATALOG
        ])
        currency_note = "Muestra el precio en USD."

    return f"""Eres un vendedor directo de Cell Center, tienda de celulares en Venezuela.
Solo manejas pantallas por ahora.
Responde corto y directo, máximo 2 líneas.
{currency_note}
Si no está en catálogo, dilo y ofrece contactar a un asesor.
No inventes precios.

CATÁLOGO:
{catalog_text}"""

conversations = {}
client = anthropic.Anthropic()

@app.route('/webhook', methods=['POST'])
def webhook():
    from_number = request.form.get('From', '')
    body = request.form.get('Body', '').strip()

    if from_number not in conversations:
        conversations[from_number] = []

    conversations[from_number].append({"role": "user", "content": body})

    if len(conversations[from_number]) > 20:
        conversations[from_number] = conversations[from_number][-20:]

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system=build_system(),
        messages=conversations[from_number]
    )

    reply = response.content[0].text
    conversations[from_number].append({"role": "assistant", "content": reply})

    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
