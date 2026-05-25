# ── REPERTORIO DE CORRECCIONES ────────────────────────────────────────────────
# Archivo separado para gestionar correcciones de escritura, abreviaciones
# y palabras a ignorar. Edita este archivo para agregar nuevos casos
# sin tocar app.py

# ── Correcciones de marcas ────────────────────────────────────────────────────
CORRECCIONES_MARCAS = {
    # Samsung
    "samsug": "samsung",
    "samsum": "samsung",
    "samsun": "samsung",
    "sansung": "samsung",
    "samsin": "samsung",
    "samsumg": "samsung",

    # Redmi / Xiaomi
    "remi": "redmi",
    "redme": "redmi",
    "reedmi": "redmi",
    "xiaome": "xiaomi",
    "xioami": "xiaomi",
    "xiomi": "xiaomi",

    # Infinix
    "infnix": "infinix",
    "infinik": "infinix",
    "ifninx": "infinix",
    "infinity": "infinix",
    "infiniti": "infinix",
    "infinixx": "infinix",
    "infix": "infinix",
    "infinx": "infinix",

    # iPhone
    "iph": "iphone",
    "aifon": "iphone",
    "aiphone": "iphone",
    "ifon": "iphone",
    "iphon": "iphone",
    "ifone": "iphone",
    "aifone": "iphone",

    # Huawei
    "huawe": "huawei",
    "huawey": "huawei",
    "huawai": "huawei",
    "hauwei": "huawei",
    "guawei": "huawei",
    "uawei": "huawei",

    # Tecno
    "tecnho": "tecno",
    "tekno": "tecno",
    "teckno": "tecno",
    "tecko": "tecno",
    "tenco": "tecno",

    # Motorola
    "motoral": "motorola",
    "motarola": "motorola",
    "motorol": "motorola",
    "motorla": "motorola",
    "motolora": "motorola",
    "motoriola": "motorola",

    # Alcatel
    "alkatel": "alcatel",
    "alcater": "alcatel",
    "alcatal": "alcatel",
    "alcatell": "alcatel",

    # Honor
    "onor": "honor",
    "onour": "honor",
    "honur": "honor",
    "honnor": "honor",

    # Realme
    "relame": "realme",
    "reame": "realme",
    "realmi": "realme",
    "relme": "realme",
}

# ── Modelos abreviados ────────────────────────────────────────────────────────
# Solo se expanden cuando son la única palabra clave del mensaje
MODELOS_ABREVIADOS = {
    "2023": "Tecno Spark Go 2023",
    "2024": "Tecno Spark Go 2024",
    "go 2023": "Tecno Spark Go 2023",
    "go 2024": "Tecno Spark Go 2024",
    "11 play": "Infinix Hot 11 Play",
    "12 play": "Infinix Hot 12 Play",
    "30 play": "Infinix Hot 30 Play",
    "30 i": "Infinix Hot 30i",

    "go24": "Tecno Spark Go 2024",
    "go23": "Tecno Spark Go 2023",
    "10Pro": "Tecno Spark 10 Pro",
}

# ── Palabras a ignorar en la búsqueda ─────────────────────────────────────────
PALABRAS_IGNORAR = {
    # Artículos y preposiciones
    "de","el","la","los","las","un","una","para","del","con","por","que",
    "y","o","a","en","al","lo","le","se","su","sus","es","son",
    # Verbos comunes
    "tienes","tienen","hay","tengo","tiene","dame","dime","quiero","quieres",
    "puedes","puede","necesito","tendrás","podria","podría","teneis","tenes",
    "tene","tienen",
    # Productos
    "pantalla","3/4","precio","cuanto","cuánto","stock","disponibles",
    "disponible","cuales",
    # Saludos y cortesías
    "hola","buenas","buen","buenos","dias","día","dia","tardes","noches",
    "saludos","favor","porfavor","porfa","gracias","please","xfa","salu",
    # Tratamientos
    "mano","hermano","brother","bro","amigo","amiga","chamo","chama","pana",
    "jefe","jefa","señor","señora","maestro","profe","socio","vale","papi",
    "mami","primo","prima","causa","camara",
    # Pronombres
    "me","mi","mis","tu","tus","nos","ese","esa","esto","esta","aqui",
    "cuando","como","donde","quien",
    # Adverbios y otros
    "mas","más","muy","bien","mal","solo","también","tampoco","d","q","x",
    "k","ahí","ahi","acá","aca",
    # Monedas
    "bolivares","bolívares","divisa","divisas","dolar","dólares","dolares",
    "dólar","bs","usd",
}
