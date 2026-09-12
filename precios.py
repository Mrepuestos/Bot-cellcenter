"""
PRECIOS — Motor de cálculo de todos los canales de pago
Cell Center 4620

Parte del PRECIO PARALELO (precio en divisas) que está en el catálogo.
Todos los demás canales salen de ahí.

Este módulo no depende de nada del bot: se puede probar solo.
"""

from decimal import Decimal, ROUND_HALF_UP

# ══════════════════════════════════════════════════════════════════════════
#  PARÁMETROS — lo único que se cambia cuando cambian las condiciones
# ══════════════════════════════════════════════════════════════════════════

# Cuánto más caro es el precio BCV respecto al paralelo
RECARGO_BCV = Decimal("0.20")          # paralelo × 1.20 = BCV

# CrediTienda: recargo escalonado según el precio
CREDITIENDA_UMBRAL = Decimal("280")    # hasta 280 aplica el recargo alto
CREDITIENDA_RECARGO_BAJO = Decimal("0.20")   # precio <= umbral
CREDITIENDA_RECARGO_ALTO = Decimal("0.15")   # precio  > umbral
CREDITIENDA_INICIAL = Decimal("0.40")
CREDITIENDA_CUOTAS = 4

# Cashea
CASHEA_RECARGO = Decimal("0.10")
CASHEA_CUOTAS = 3
CASHEA_NIVELES = {
    "1": Decimal("0.60"), "semilla":   Decimal("0.60"),
    "2": Decimal("0.50"), "raiz":      Decimal("0.50"),
    "3": Decimal("0.40"), "hoja":      Decimal("0.40"),
    "4": Decimal("0.40"), "tronco":    Decimal("0.40"),
    "5": Decimal("0.40"), "arbol":     Decimal("0.40"),
    "6": Decimal("0.40"), "araguaney": Decimal("0.40"),
}

# Krece — verificado contra 416 simulaciones del portal de aliado
KRECE_INICIAL = {
    "azul":    Decimal("0.30"),
    "plata":   Decimal("0.25"),
    "oro":     Decimal("0.20"),
    "platino": Decimal("0.15"),
}
KRECE_FACTORES = {
    "azul":    {3: "1.20", 4: "1.35", 6: "1.50"},
    "plata":   {3: "1.20", 4: "1.35", 6: "1.50", 8: "1.55"},
    "oro":     {3: "1.20", 4: "1.30", 6: "1.42", 8: "1.50"},
    "platino": {3: "1.18", 4: "1.26", 6: "1.34", 8: "1.40", 10: "1.44"},
}
KRECE_LINEA_DEFECTO = {
    "azul": Decimal("180"), "plata": Decimal("220"),
    "oro": Decimal("260"), "platino": Decimal("310"),
}
KRECE_INICIAL_MAXIMA = Decimal("0.50")   # tope: nunca se pide más del 50%


# ══════════════════════════════════════════════════════════════════════════
#  Utilidades
# ══════════════════════════════════════════════════════════════════════════

def _d(valor):
    """Convierte a Decimal sin errores de coma flotante."""
    return Decimal(str(valor))


def _redondear(valor):
    """Redondea al dólar, media hacia arriba (como el simulador de Krece)."""
    return int(_d(valor).quantize(Decimal(0), rounding=ROUND_HALF_UP))


def _normalizar(texto):
    """'Plata' -> 'plata'; '1 Semilla' -> 'semilla'; quita acentos."""
    t = str(texto).strip().lower()
    for a, b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u")):
        t = t.replace(a, b)
    # '1 semilla' -> intenta primero la palabra, luego el número
    partes = t.split()
    return partes[-1] if len(partes) > 1 else t


# ══════════════════════════════════════════════════════════════════════════
#  Canales de contado
# ══════════════════════════════════════════════════════════════════════════

def precio_bcv(paralelo):
    """Precio para quien paga en bolívares a tasa BCV."""
    return _redondear(_d(paralelo) * (1 + RECARGO_BCV))


def precio_bolivares(paralelo, tasa_bcv):
    """Monto en bolívares. tasa_bcv viene de la API."""
    return _redondear(_d(precio_bcv(paralelo)) * _d(tasa_bcv))


# ══════════════════════════════════════════════════════════════════════════
#  CrediTienda
# ══════════════════════════════════════════════════════════════════════════

def creditienda(paralelo, moneda="divisas"):
    """
    moneda 'divisas' -> se calcula sobre el precio paralelo
    moneda 'bs'      -> se calcula sobre el precio BCV
    El umbral de 280 se mide sobre la base de cada variante.
    """
    base = _d(paralelo) if moneda == "divisas" else _d(precio_bcv(paralelo))
    recargo = (CREDITIENDA_RECARGO_BAJO if base <= CREDITIENDA_UMBRAL
               else CREDITIENDA_RECARGO_ALTO)
    total = base * (1 + recargo)
    inicial = total * CREDITIENDA_INICIAL
    cuota = (total - inicial) / CREDITIENDA_CUOTAS
    return {
        "canal": "CrediTienda",
        "moneda": moneda,
        "total": _redondear(total),
        "inicial": _redondear(inicial),
        "cuotas": CREDITIENDA_CUOTAS,
        "monto_cuota": _redondear(cuota),
    }


# ══════════════════════════════════════════════════════════════════════════
#  Cashea
# ══════════════════════════════════════════════════════════════════════════

def cashea(paralelo, nivel):
    """Necesita el nivel del cliente. Siempre 3 cuotas."""
    clave = _normalizar(nivel)
    if clave not in CASHEA_NIVELES:
        raise ValueError(f"Nivel de Cashea no reconocido: {nivel}")

    total = _d(precio_bcv(paralelo)) * (1 + CASHEA_RECARGO)
    inicial = total * CASHEA_NIVELES[clave]
    cuota = (total - inicial) / CASHEA_CUOTAS
    return {
        "canal": "Cashea",
        "nivel": clave,
        "total": _redondear(total),
        "inicial": _redondear(inicial),
        "cuotas": CASHEA_CUOTAS,
        "monto_cuota": _redondear(cuota),
    }


# ══════════════════════════════════════════════════════════════════════════
#  Krece
# ══════════════════════════════════════════════════════════════════════════

def krece(paralelo, nivel, plazo=6, linea=None, inicial_pct=None):
    """
    Fórmula verificada contra 416 simulaciones del portal:
        financiado = precio × (1 - %inicial)      <- SIN redondear
        cuota      = redondear(financiado × factor / n_cuotas)
        inicial    = redondear(precio × %inicial) <- solo para mostrar

    La línea aprobada es un techo: si el financiado la supera,
    Krece sube la inicial hasta que quepa.
    """
    clave = _normalizar(nivel)
    if clave not in KRECE_INICIAL:
        raise ValueError(f"Nivel de Krece no reconocido: {nivel}")
    if plazo not in KRECE_FACTORES[clave]:
        disponibles = sorted(KRECE_FACTORES[clave])
        raise ValueError(
            f"Nivel {clave} no maneja {plazo} cuotas. Disponibles: {disponibles}")

    precio = _d(precio_bcv(paralelo))
    pct = _d(inicial_pct) if inicial_pct is not None else KRECE_INICIAL[clave]
    linea = _d(linea) if linea is not None else KRECE_LINEA_DEFECTO[clave]

    # La línea topa el monto financiado: si no cabe, sube la inicial
    financiado = precio * (1 - pct)
    tope = False
    if financiado > linea:
        pct = 1 - (linea / precio)
        financiado = precio * (1 - pct)
        tope = True

    if pct > KRECE_INICIAL_MAXIMA:
        return {
            "canal": "Krece", "nivel": clave, "aplica": False,
            "motivo": "El equipo excede la línea aprobada del cliente",
        }

    factor = _d(KRECE_FACTORES[clave][plazo])
    cuota = _redondear(financiado * factor / plazo)
    inicial = _redondear(precio * pct)
    return {
        "canal": "Krece",
        "nivel": clave,
        "aplica": True,
        "inicial": inicial,
        "inicial_pct": round(float(pct) * 100, 1),
        "cuotas": plazo,
        "monto_cuota": cuota,
        "total": inicial + cuota * plazo,
        "topado_por_linea": tope,
    }


def plazos_krece(nivel):
    """Plazos que maneja un nivel."""
    return sorted(KRECE_FACTORES[_normalizar(nivel)])


# ══════════════════════════════════════════════════════════════════════════
#  Resumen para el bot
# ══════════════════════════════════════════════════════════════════════════

def resumen(paralelo, tasa_bcv=None):
    """Canales que no necesitan datos del cliente."""
    r = {
        "divisas": _redondear(paralelo),
        "bcv": precio_bcv(paralelo),
        "creditienda_divisas": creditienda(paralelo, "divisas"),
        "creditienda_bs": creditienda(paralelo, "bs"),
    }
    if tasa_bcv:
        r["bolivares"] = precio_bolivares(paralelo, tasa_bcv)
    return r
