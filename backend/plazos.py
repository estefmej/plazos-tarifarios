"""Motor de plazos: calcula el cronograma de un procedimiento en días hábiles.

Esta parte NO usa IA a propósito: un plazo legal debe salir de una regla
verificable, no de lo que "adivine" un modelo. La IA solo lee los documentos;
este módulo hace las cuentas.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
REGLAS = RAIZ / "data" / "reglas"


@dataclass(frozen=True)
class Etapa:
    id: str
    nombre: str
    dias_habiles: int


@dataclass
class Evento:
    """Algo que cambia el cronograma: una ampliación, una suspensión o una etapa cumplida."""
    tipo: str                    # "ampliacion" | "suspension" | "etapa_cumplida"
    etapa_id: str
    dias: int = 0                # días hábiles añadidos (ampliación o suspensión)
    fecha: date | None = None    # fecha de cumplimiento (etapa_cumplida)
    resolucion: str = ""


@dataclass
class EtapaCalculada:
    id: str
    nombre: str
    dias_habiles: int
    dias_extra: int
    desde: date
    limite: date
    cumplida: bool = False
    fecha_cumplida: date | None = None
    situacion: str = "pendiente"   # cumplida | en curso | vencida | pendiente


@dataclass
class Estado:
    clase: str                   # rojo | ambar | verde | fin | sin
    texto: str
    etapas: list[EtapaCalculada] = field(default_factory=list)
    actual: int | None = None
    dias_restantes: int | None = None   # negativo = días hábiles de atraso
    advertencias: list[str] = field(default_factory=list)


# ---------- reglas ----------

@lru_cache
def cargar_reglas() -> dict:
    with open(REGLAS / "etapas.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def etapas() -> list[Etapa]:
    return [Etapa(e["id"], e["nombre"], int(e["dias_habiles"])) for e in cargar_reglas()["etapas"]]


def nombre_etapa(etapa_id: str) -> str:
    return next((e.nombre for e in etapas() if e.id == etapa_id), etapa_id)


def umbral_por_vencer() -> int:
    return int(cargar_reglas().get("semaforo", {}).get("por_vencer_dias_habiles", 10))


@lru_cache
def feriados() -> frozenset[date]:
    with open(REGLAS / "feriados.csv", encoding="utf-8") as f:
        return frozenset(date.fromisoformat(r["fecha"]) for r in csv.DictReader(f))


# ---------- cobertura del calendario ----------

def anios_con_calendario() -> list[int]:
    """Años que tienen feriados cargados en `feriados.csv`."""
    return sorted({d.year for d in feriados()})


def cobertura() -> tuple[date, date]:
    """Primer y último día con calendario de feriados cargado."""
    f = feriados()
    return min(f), max(f)


def advertencias_calendario(desde: date, hasta: date) -> list[str]:
    """Avisa de los años del rango que no tienen feriados cargados.

    El motor no puede distinguir un año sin feriados de un año que nadie cargó:
    sin este aviso un plazo que cae en 2029 se calcula como si en 2029 no hubiera
    fiestas patrias, y la fecha sale antes de la legal sin que nada lo diga.
    """
    if hasta < desde:
        desde, hasta = hasta, desde
    cubiertos = set(anios_con_calendario())
    faltan = [a for a in range(desde.year, hasta.year + 1) if a not in cubiertos]
    if not faltan:
        return []
    lista = ", ".join(str(a) for a in faltan)
    return [f"Sin calendario de feriados para {lista}: los plazos que caen en esos años se cuentan "
            f"como si no hubiera feriados. Agrega esas fechas en data/reglas/feriados.csv."]


# ---------- días hábiles ----------

def es_habil(d: date) -> bool:
    return d.weekday() < 5 and d not in feriados()


def sumar_habiles(desde: date, n: int) -> date:
    """Cuenta n días hábiles a partir del día siguiente a `desde`.

    Un plazo legal se amplía o se suspende, nunca se acorta: `n` negativo es un
    error de programación, no un plazo de cero días (antes devolvía `desde` y la
    etapa quedaba con límite igual al de la anterior, sin ningún aviso).
    """
    if int(n) < 0:
        raise ValueError(f"No se pueden contar {n} días hábiles: usa un número igual o mayor que cero.")
    d, c = desde, 0
    while c < n:
        d += timedelta(days=1)
        if es_habil(d):
            c += 1
    return d


def habiles_entre(a: date, b: date) -> int:
    """Días hábiles desde a (excluido) hasta b (incluido). Negativo si b es anterior a a."""
    if a == b:
        return 0
    signo = 1
    if b < a:
        a, b, signo = b, a, -1
    d, c = a, 0
    while d < b:
        d += timedelta(days=1)
        if es_habil(d):
            c += 1
    return signo * c


# ---------- cronograma ----------

TIPOS_CON_DIAS = ("ampliacion", "suspension")
TIPOS_EVENTO = ("ampliacion", "suspension", "etapa_cumplida")


def eventos_aplicables(eventos: list[Evento] | None) -> tuple[list[Evento], list[str]]:
    """Separa los eventos que el motor entiende de los que no puede aplicar.

    Una ampliación que nombra una etapa que no existe en `etapas.yaml` (un id con
    tilde, un plural, una etapa retirada) o con cero días se ignoraba en silencio:
    el cronograma salía sin la ampliación y nada lo decía, así que el plazo se veía
    más corto de lo que la resolución concede. Lo mismo con un tipo desconocido.
    """
    ids = {e.id for e in etapas()}
    aplicables, avisos = [], []
    for ev in eventos or []:
        if ev.tipo not in TIPOS_EVENTO:
            avisos.append(f"Evento de tipo «{ev.tipo}» ignorado: el motor solo aplica "
                          f"{', '.join(TIPOS_EVENTO)}.")
        elif ev.etapa_id not in ids:
            avisos.append(f"Evento «{ev.tipo}» ignorado: la etapa «{ev.etapa_id}» no existe "
                          f"en data/reglas/etapas.yaml.")
        elif ev.tipo in TIPOS_CON_DIAS and int(ev.dias) <= 0:
            avisos.append(f"Evento «{ev.tipo}» de la etapa «{ev.etapa_id}» ignorado: "
                          f"los días deben ser mayores que cero (recibido: {ev.dias}).")
        else:
            aplicables.append(ev)
    return aplicables, avisos


def cronograma(fecha_publicacion: date, eventos: list[Evento] | None = None) -> list[EtapaCalculada]:
    """Calcula la fecha límite de cada etapa, aplicando ampliaciones y suspensiones."""
    eventos, _ = eventos_aplicables(eventos)
    extra: dict[str, int] = {}
    cumplidas: dict[str, date | None] = {}
    for ev in eventos:
        if ev.tipo in TIPOS_CON_DIAS:
            extra[ev.etapa_id] = extra.get(ev.etapa_id, 0) + int(ev.dias)
        elif ev.tipo == "etapa_cumplida":
            cumplidas[ev.etapa_id] = ev.fecha

    filas, previo = [], fecha_publicacion
    for i, e in enumerate(etapas()):
        dx = extra.get(e.id, 0)
        limite = fecha_publicacion if i == 0 else sumar_habiles(previo, e.dias_habiles + dx)
        fila = EtapaCalculada(e.id, e.nombre, e.dias_habiles, dx, previo, limite)
        if i == 0:
            fila.cumplida, fila.fecha_cumplida = True, fecha_publicacion
        elif e.id in cumplidas:
            fila.cumplida, fila.fecha_cumplida = True, cumplidas[e.id]
        filas.append(fila)
        previo = limite
    return filas


def estado(fecha_publicacion: date | None, eventos: list[Evento] | None, hoy: date) -> Estado:
    """Estado del procedimiento a una fecha: etapa actual, días restantes y semáforo."""
    if fecha_publicacion is None:
        return Estado("sin", "Sin procedimiento")
    _, avisos = eventos_aplicables(eventos)
    filas = cronograma(fecha_publicacion, eventos)
    avisos = advertencias_calendario(min(fecha_publicacion, filas[-1].limite), filas[-1].limite) + avisos
    if hoy < fecha_publicacion:
        return Estado("sin", "Por iniciar", filas, advertencias=avisos)

    actual = next((i for i, f in enumerate(filas) if not f.cumplida), None)
    for i, f in enumerate(filas):
        if f.cumplida:
            f.situacion = "cumplida"
        elif i == actual:
            f.situacion = "vencida" if f.limite < hoy else "en curso"
        else:
            f.situacion = "pendiente"

    if actual is None:
        return Estado("fin", "Concluido", filas, advertencias=avisos)
    resto = habiles_entre(hoy, filas[actual].limite)
    if resto < 0:
        clase, texto = "rojo", "Plazo vencido"
    elif resto <= umbral_por_vencer():
        clase, texto = "ambar", "Por vencer"
    else:
        clase, texto = "verde", "A tiempo"
    return Estado(clase, texto, filas, actual, resto, avisos)
