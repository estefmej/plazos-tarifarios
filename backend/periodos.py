"""Periodo regulatorio: inicio, término y los hitos que se cuentan en MESES antes del término.

Igual que el motor de días hábiles, esto son reglas fijas, sin IA.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

REGLAS = Path(__file__).resolve().parent.parent / "data" / "reglas" / "periodo.yaml"


@lru_cache
def reglas() -> dict:
    with open(REGLAS, encoding="utf-8") as f:
        return yaml.safe_load(f)


def sumar_meses(d: date, meses: int) -> date:
    """Suma (o resta) meses calendario. Si el día no existe en el mes destino, usa el último día del mes."""
    total = d.year * 12 + (d.month - 1) + meses
    anio, mes = divmod(total, 12)
    mes += 1
    return date(anio, mes, min(d.day, calendar.monthrange(anio, mes)[1]))


def meses_antes_del_termino(termino: date, meses: int) -> date:
    """N meses antes del término. Si el término es fin de mes, el resultado también es fin de mes."""
    previo = sumar_meses(termino, -meses)
    if termino.day == calendar.monthrange(termino.year, termino.month)[1]:
        return previo.replace(day=calendar.monthrange(previo.year, previo.month)[1])
    return previo


def regla_inicio(fecha_publicacion: date) -> str:
    r = reglas()["inicio_periodo"]
    corte = r["fecha_de_cambio"]
    corte = corte if isinstance(corte, date) else date.fromisoformat(str(corte))
    return r["despues"] if fecha_publicacion >= corte else r["antes"]


def inicio_por_regla(fecha_publicacion: date) -> tuple[date, str]:
    """Inicio del periodo según la regla vigente a la fecha de publicación. Devuelve (fecha, explicación)."""
    if regla_inicio(fecha_publicacion) == "anio_fiscal_siguiente":
        return date(fecha_publicacion.year + 1, 1, 1), "primer día del año fiscal siguiente a la publicación"
    siguiente = sumar_meses(fecha_publicacion.replace(day=1), 1)
    return siguiente, "primer día del mes calendario siguiente a la publicación"


def termino(inicio: date, anios: int) -> date:
    return sumar_meses(inicio, 12 * int(anios)) - timedelta(days=1)


@dataclass
class Hito:
    id: str
    nombre: str
    fecha: date
    base: str
    cumplido: bool = False


@dataclass
class EstadoPeriodo:
    clase: str              # rojo | ambar | verde | fin | sin
    texto: str
    inicio: date | None = None
    termino: date | None = None
    hitos: list[Hito] = field(default_factory=list)
    proximo: Hito | None = None
    dias: int | None = None  # días calendario al próximo hito (negativo = pasado)


def hitos(fin: date) -> list[Hito]:
    return [Hito(h["id"], h["nombre"], meses_antes_del_termino(fin, int(h["meses_antes_del_termino"])), h["base"])
            for h in reglas()["hitos"]]


def estado(inicio: date | None, anios: int | None, hoy: date, solicitud_presentada: bool = False) -> EstadoPeriodo:
    """Estado del periodo a la fecha `hoy`.

    `solicitud_presentada`: la EPS ya tiene un procedimiento de revisión periódica iniciado
    después del inicio de este periodo (entonces el hito de solicitud está cumplido).
    """
    if not inicio or not anios:
        return EstadoPeriodo("sin", "Sin periodo registrado")
    fin = termino(inicio, anios)
    hs = hitos(fin)
    for h in hs:
        h.cumplido = h.fecha < hoy or (h.id == "solicitud_eps" and solicitud_presentada)
    sol = next((h for h in hs if h.id == "solicitud_eps"), None)
    if hoy > fin:
        return EstadoPeriodo("fin", "Periodo terminado", inicio, fin, hs)
    if sol and sol.fecha < hoy and not solicitud_presentada:
        return EstadoPeriodo("rojo", "Solicitud no presentada", inicio, fin, hs, sol, (sol.fecha - hoy).days)
    if solicitud_presentada:
        return EstadoPeriodo("fin", "En revisión periódica", inicio, fin, hs)
    prox = next((h for h in hs if h.fecha >= hoy), None)
    if prox is None:
        return EstadoPeriodo("verde", "Último año del periodo", inicio, fin, hs)
    dias = (prox.fecha - hoy).days
    umbral = int(reglas().get("semaforo", {}).get("por_vencer_dias", 90))
    if dias <= umbral:
        return EstadoPeriodo("ambar", "Hito próximo", inicio, fin, hs, prox, dias)
    return EstadoPeriodo("verde", "En curso", inicio, fin, hs, prox, dias)
