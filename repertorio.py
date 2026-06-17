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
    "motorolag": "motorola g",

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
    "go2023": "Tecno Spark Go 2023",
    "go2024": "Tecno Spark Go 2024",
    "11play": "Infinix Hot 11 Play",
    "12play": "Infinix Hot 12 Play",
    "30play": "Infinix Hot 30 Play",
    "30i": "Infinix Hot 30i",

    "go24": "Tecno Spark Go 2024",
    "go23": "Tecno Spark Go 2023",
    "10pro": "Tecno Spark 10 Pro",

    "note10": "Redmi Note 10",
    "note10pro": "Redmi Note 10 Pro",
    "note11": "Redmi Note 11",
    "note11pro": "Redmi Note 11 Pro",
    "note12": "Redmi Note 12",
    "note12pro": "Redmi Note 12 Pro",
    "note13": "Redmi Note 13",
    "note13pro": "Redmi Note 13 Pro",
    "note14": "Redmi Note 14",
    "note14pro": "Redmi Note 14 Pro",

    # Huawei P (la P se pierde por ser letra sola)
    "psmart": "Huawei P SMART 2019",
    "psmart2019": "Huawei P SMART 2019",
    "p20lite": "Huawei P20 lite",
    "p30lite": "Huawei P30 lite",

    # Redmi Note variantes (complementan los que ya tienes)
    "note10pro": "Redmi Note 10 Pro",
    "note8pro": "Redmi Note 8pro",
    "note9s": "Redmi NOTE 9S",
    "note11oled": "Redmi NOTE 11 OLED",
    "note8t": "Redmi NOTE 8T",

    # Motorola abreviados
    "g8play": "Motorola G8 Play",
    "g9power": "Motorola G9 POWER",

    # Tecno abreviados
    "camon17pro": "Tecno camon 17 pro",
    "pop5lite": "Tecno POP 5 LITE",
    "pop6pro": "Tecno POP 6 PRO",
    "pova3": "Tecno pova 3",
    "povaneo2": "Tecno Pova neo 2",
    "spark6go": "Tecno SPARK 6 GO",
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
    "hola","buenas","buen","buenos","dias","día","días","dia","tardes","noches",
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
