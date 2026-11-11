"""Lee una resolución en PDF y devuelve sus datos estructurados.

Flujo: PDF -> texto por página (pypdf) -> modelo de lenguaje con salida JSON ->
validación de fechas y búsqueda de la EPS en el catálogo.
Si el PDF es una imagen escaneada (sin texto), se envía el PDF completo al modelo.
Si no hay clave de API, se usa un lector básico con expresiones regulares,
claramente marcado como "sin IA" para que la persona revise todo.
"""
from __future__ import annotations

import difflib
import io
import re
from html import unescape
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from ai import llm
from backend import periodos as R
from backend.datos import lista_eps, normalizar

PROMPT = (Path(__file__).parent / "prompts" / "extraccion.md").read_text(encoding="utf-8")
MESES = {m: i + 1 for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"])}
MESES["setiembre"] = 9
ETAPAS_VALIDAS = {"info", "campo", "proyecto", "audiencia", "comentarios", "final"}

# Nombres largos con los que a veces aparecen las EPS en las resoluciones.
ALIAS = {
    "servicio de agua potable y alcantarillado de lima": "sedapal",
    "servicio de agua potable y alcantarillado de arequipa": "sedapar",
    "servicio de agua potable y alcantarillado de la libertad": "sedalib",
    "entidad prestadora de servicios de saneamiento de lambayeque": "epsel",
}


@dataclass
class Extraccion:
    datos: dict
    eps_id: str | None
    eps_confianza: float
    modo: str                       # "ia" | "basico"
    paginas: int
    texto: str
    avisos: list[str] = field(default_factory=list)


# ---------- lectura del PDF ----------

def texto_pdf(pdf: bytes, max_paginas: int = 12) -> tuple[str, int]:
    lector = PdfReader(io.BytesIO(pdf))
    partes = []
    for i, pag in enumerate(lector.pages[:max_paginas], start=1):
        partes.append(f"[Página {i}]\n{(pag.extract_text() or '').strip()}")
    return "\n\n".join(partes), len(lector.pages)


# ---------- normalización ----------

def a_fecha(valor) -> date | None:
    if not valor:
        return None
    s = str(valor).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"(\d{1,2})\s+de\s+([a-záéíóú]+)\s+(?:de|del)\s+(\d{4})", normalizar(s))
    if m and m.group(2) in MESES:
        return date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))
    return None


def emparejar_eps(nombre: str | None, texto: str = "") -> tuple[str | None, float]:
    """Busca la EPS en el catálogo. Devuelve (id, confianza entre 0 y 1)."""
    catalogo = lista_eps()
    candidatos = [normalizar(nombre or "")] if nombre else []
    for alias, eps_id in ALIAS.items():
        if alias in normalizar(nombre or "") or (not nombre and alias in normalizar(texto)):
            return eps_id, 0.95
    if not candidatos and texto:   # modo básico: buscar nombres del catálogo en el texto
        t = normalizar(texto)
        hallados = [e for e in catalogo if re.search(rf"\b{re.escape(normalizar(e['nombre']))}\b", t)]
        if hallados:
            mejor = max(hallados, key=lambda e: len(e["nombre"]))
            return mejor["id"], 0.8
        return None, 0.0
    mejor, puntaje = None, 0.0
    for e in catalogo:
        n = normalizar(e["nombre"])
        c = candidatos[0]
        p = 1.0 if (n in c or c in n) else difflib.SequenceMatcher(None, n, c).ratio()
        if p > puntaje:
            mejor, puntaje = e["id"], p
    return (mejor, round(puntaje, 2)) if puntaje >= 0.55 else (None, round(puntaje, 2))


def _limpiar(d: dict) -> tuple[dict, list[str]]:
    avisos = []
    for campo in ("fecha_emision", "fecha_publicacion_el_peruano"):
        valor = d.get(campo)
        f = a_fecha(valor)
        if valor and not f:
            avisos.append(f"No se pudo leer la fecha '{valor}' en {campo}.")
        d[campo] = f.isoformat() if f else None
    if d.get("etapa_afectada") not in ETAPAS_VALIDAS:
        d["etapa_afectada"] = None
    try:
        d["dias_habiles_adicionales"] = int(d.get("dias_habiles_adicionales") or 0) or None
    except (TypeError, ValueError):
        d["dias_habiles_adicionales"] = None
    for campo in ("periodo_anio_inicio", "periodo_anio_fin", "duracion_anios"):
        try:
            d[campo] = int(d.get(campo)) if d.get(campo) not in (None, "") else None
        except (TypeError, ValueError):
            d[campo] = None
    if d.get("duracion_anios") not in (None, 3, 4, 5):
        avisos.append(f"Duración leída de {d['duracion_anios']} años: el periodo solo puede ser de 3, 4 o 5 años.")
        d["duracion_anios"] = None
    if d.get("regla_inicio_periodo") not in ("anio_fiscal_siguiente", "mes_siguiente"):
        d["regla_inicio_periodo"] = None
    if not d.get("duracion_anios") and d.get("periodo_anio_inicio") and d.get("periodo_anio_fin"):
        inicio = None
        pub = a_fecha(d.get("fecha_publicacion_el_peruano"))
        if pub:
            if d.get("regla_inicio_periodo") == "anio_fiscal_siguiente":
                inicio = date(pub.year + 1, 1, 1)
            elif d.get("regla_inicio_periodo") == "mes_siguiente":
                inicio = R.sumar_meses(pub.replace(day=1), 1)
            else:
                inicio = R.inicio_por_regla(pub)[0]
        d["duracion_anios"] = R.duracion_desde_anios(d["periodo_anio_inicio"], d["periodo_anio_fin"], inicio)
    if not d.get("fecha_publicacion_el_peruano"):
        avisos.append("No se encontró la fecha de publicación en El Peruano: ingrésala a mano antes de guardar.")
    if d.get("fecha_emision") and d.get("fecha_publicacion_el_peruano") \
            and d["fecha_publicacion_el_peruano"] < d["fecha_emision"]:
        avisos.append("La fecha de publicación es anterior a la de emisión: revísalas.")
    d.setdefault("evidencia", [])
    return d, avisos


# ---------- lector básico (sin IA) ----------

def extraer_basico(texto: str) -> dict:
    t = re.sub(r"\s+", " ", normalizar(texto))   # los PDF cortan las frases en varias líneas
    num = re.search(r"n\.?\s*[°º]?\s*(\d{2,4}-\d{4}-[a-z\-]+)", t)
    fechas = re.findall(r"\d{1,2}\s+de\s+[a-z]+\s+de(?:l)?\s+\d{4}", t)
    pub = re.search(r"publica\w*\s+(?:en\s+)?(?:el\s+)?(?:diario\s+oficial\s+)?el\s+peruano\D{0,40}?"
                    r"(\d{1,2}\s+de\s+[a-z]+\s+de(?:l)?\s+\d{4})", t)
    tipo = "otro"
    if re.search(r"aprob\w+\s+(?:la\s+)?formula tarifaria", t):
        tipo = "aprobacion_final"
    elif re.search(r"(inici\w+|da inicio).{0,120}(revision|procedimiento)", t):
        tipo = "inicio_revision_periodica"
    elif "amplia" in t and "plazo" in t:
        tipo = "ampliacion_plazo"
    elif "suspend" in t and "plazo" in t:
        tipo = "suspension_plazo"
    dias = re.search(r"(\d{1,3})\s+dias\s+habiles", t) if tipo in ("ampliacion_plazo", "suspension_plazo") else None
    per = re.search(r"periodo regulatorio\s+(\d{4})\s*[-–]\s*(\d{4})", t)
    regla = ("anio_fiscal_siguiente" if "primer dia del ano fiscal siguiente" in t else
             "mes_siguiente" if "primer dia del mes calendario siguiente" in t else None)
    dur = re.search(r"periodo regulatorio de (\d|tres|cuatro|cinco) anos", t)
    palabras = {"tres": 3, "cuatro": 4, "cinco": 5}
    return {
        "duracion_anios": (palabras.get(dur.group(1)) or int(dur.group(1))) if dur else None,
        "periodo_anio_inicio": int(per.group(1)) if per else None,
        "periodo_anio_fin": int(per.group(2)) if per else None,
        "regla_inicio_periodo": regla,
        "es_resolucion": "resolucion" in t,
        "tipo": tipo,
        "numero_resolucion": num.group(1).upper() if num else None,
        "eps": None,
        "fecha_emision": fechas[0] if fechas else None,
        "fecha_publicacion_el_peruano": pub.group(1) if pub else None,
        "dias_habiles_adicionales": int(dias.group(1)) if dias else None,
        "etapa_afectada": None,
        "resumen": "Lectura básica sin IA: revisa todos los campos.",
        "evidencia": [],
    }


# ---------- punto de entrada ----------

def _fecha_pagina_el_peruano(texto: str) -> str | None:
    """La página de El Peruano trae «Fecha de publicación: dd/mm/aaaa»: es el dato más confiable."""
    m = re.search(r"Fecha de publicaci[oó]n:\s*(\d{1,2}/\d{1,2}/\d{4})", texto)
    f = a_fecha(m.group(1)) if m else None
    return f.isoformat() if f else None


def extraer_texto(texto: str, paginas: int = 1, pdf: bytes | None = None) -> Extraccion:
    escaneado = pdf is not None and len(re.sub(r"\[Página \d+\]|\s", "", texto)) < 300
    avisos: list[str] = []

    if llm.disponible():
        datos = llm.pedir_json(PROMPT, pdf=pdf) if escaneado else llm.pedir_json(PROMPT, texto=texto[:30_000])
        modo = "ia"
        if escaneado:
            avisos.append("El PDF parece escaneado: el modelo lo leyó como imagen.")
    else:
        datos = extraer_basico(texto)
        modo = "basico"
        avisos.append("No hay modelo de IA configurado: se usó un lector básico. Revisa todos los campos.")
        if escaneado:
            avisos.append("El PDF parece escaneado y el lector básico no puede leer imágenes.")

    fecha_pagina = _fecha_pagina_el_peruano(texto)
    if fecha_pagina:
        if datos.get("fecha_publicacion_el_peruano") and a_fecha(datos["fecha_publicacion_el_peruano"]) \
                and a_fecha(datos["fecha_publicacion_el_peruano"]).isoformat() != fecha_pagina:
            avisos.append(f"La IA leyó otra fecha de publicación; se usa la de la página de El Peruano ({fecha_pagina}).")
        datos["fecha_publicacion_el_peruano"] = fecha_pagina

    datos, avisos_limpieza = _limpiar(datos)
    eps_id, confianza = emparejar_eps(datos.get("eps"), texto)
    if eps_id is None:
        avisos.append("No se reconoció la EPS en el catálogo: elígela a mano.")
    return Extraccion(datos, eps_id, confianza, modo, paginas, texto, avisos + avisos_limpieza)


def extraer(pdf: bytes) -> Extraccion:
    texto, paginas = texto_pdf(pdf)
    return extraer_texto(texto, paginas, pdf)


def html_a_texto(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr|table|section)>", "\n", html)
    texto = re.sub(r"<[^>]+>", " ", html)
    texto = unescape(texto)
    texto = re.sub(r"[ \t\r\f\v]+", " ", texto)
    return re.sub(r"\n\s*\n+", "\n", texto).strip()


DOMINIOS_PERMITIDOS = ("elperuano.pe", "gob.pe")


def extraer_url(url: str) -> Extraccion:
    """Lee una norma desde un enlace público: la página de El Peruano (HTML) o un PDF de El Peruano o gob.pe."""
    import requests
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower()
    if not any(host == d or host.endswith("." + d) for d in DOMINIOS_PERMITIDOS):
        raise ValueError("Pega un enlace de busquedas.elperuano.pe o de gob.pe.")
    r = requests.get(url, timeout=40, headers={"User-Agent": "Mozilla/5.0 (Plazia; proyecto academico)"})
    r.raise_for_status()
    tipo = r.headers.get("Content-Type", "").lower()
    if "pdf" in tipo or r.content[:5] == b"%PDF-" or urlparse(url).path.lower().endswith(".pdf"):
        return extraer(r.content)      # el enlace es un PDF: se lee igual que uno subido
    texto = html_a_texto(r.text)
    if len(texto) < 500:
        raise ValueError("La página no trajo el texto de la norma. Descarga el PDF y súbelo.")
    return extraer_texto(texto, 1)
