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
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from ai import llm
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

def texto_pdf(pdf: bytes, max_paginas: int = 30) -> tuple[str, int]:
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
    if not d.get("fecha_publicacion_el_peruano"):
        avisos.append("No se encontró la fecha de publicación en El Peruano: ingrésala a mano antes de guardar.")
    if d.get("fecha_emision") and d.get("fecha_publicacion_el_peruano") \
            and d["fecha_publicacion_el_peruano"] < d["fecha_emision"]:
        avisos.append("La fecha de publicación es anterior a la de emisión: revísalas.")
    d.setdefault("evidencia", [])
    return d, avisos


# ---------- lector básico (sin IA) ----------

def extraer_basico(texto: str) -> dict:
    t = normalizar(texto)
    num = re.search(r"n\.?\s*[°º]?\s*(\d{2,4}-\d{4}-[a-z\-]+)", t)
    fechas = re.findall(r"\d{1,2}\s+de\s+[a-z]+\s+de(?:l)?\s+\d{4}", t)
    pub = re.search(r"publica\w*\s+(?:en\s+)?(?:el\s+)?(?:diario\s+oficial\s+)?el\s+peruano\D{0,40}?"
                    r"(\d{1,2}\s+de\s+[a-z]+\s+de(?:l)?\s+\d{4})", t)
    tipo = "otro"
    if re.search(r"(inici\w+|da inicio).{0,120}(revision|procedimiento)", t):
        tipo = "inicio_revision_periodica"
    elif "amplia" in t and "plazo" in t:
        tipo = "ampliacion_plazo"
    elif "suspend" in t and "plazo" in t:
        tipo = "suspension_plazo"
    dias = re.search(r"(\d{1,3})\s+dias\s+habiles", t) if tipo != "inicio_revision_periodica" else None
    return {
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

def extraer(pdf: bytes) -> Extraccion:
    texto, paginas = texto_pdf(pdf)
    escaneado = len(re.sub(r"\[Página \d+\]|\s", "", texto)) < 300
    avisos: list[str] = []

    if llm.disponible():
        datos = llm.pedir_json(PROMPT, pdf=pdf) if escaneado else llm.pedir_json(PROMPT, texto=texto[:120_000])
        modo = "ia"
        if escaneado:
            avisos.append("El PDF parece escaneado: el modelo lo leyó como imagen.")
    else:
        datos = extraer_basico(texto)
        modo = "basico"
        avisos.append("No hay modelo de IA configurado: se usó un lector básico. Revisa todos los campos.")
        if escaneado:
            avisos.append("El PDF parece escaneado y el lector básico no puede leer imágenes.")

    datos, avisos_limpieza = _limpiar(datos)
    eps_id, confianza = emparejar_eps(datos.get("eps"), texto)
    if eps_id is None:
        avisos.append("No se reconoció la EPS en el catálogo: elígela a mano.")
    return Extraccion(datos, eps_id, confianza, modo, paginas, texto, avisos + avisos_limpieza)
