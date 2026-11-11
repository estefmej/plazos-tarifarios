"""Almacenamiento en SQLite de EPS, procedimientos y eventos.

La primera vez se llena con datos de demostración SINTÉTICOS (data/*_demo.csv).
Las resoluciones reales que se suben en la app se guardan con fuente = "pdf".
"""
from __future__ import annotations

import csv
import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from backend import periodos as R
from backend import plazos as P

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"
DB = Path(os.getenv("PLAZOS_DB", DATA / "plazos.db"))

ESQUEMA = """
CREATE TABLE IF NOT EXISTS eps (
  id TEXT PRIMARY KEY, nombre TEXT, ciudad TEXT, departamento TEXT,
  lat REAL, lon REAL, conexiones INTEGER
);
CREATE TABLE IF NOT EXISTS procedimientos (
  id TEXT PRIMARY KEY, eps_id TEXT REFERENCES eps(id), resolucion TEXT,
  fecha_emision TEXT, fecha_publicacion TEXT, responsable TEXT, subgerencia TEXT,
  fuente TEXT, archivo TEXT, creado TEXT
);
CREATE TABLE IF NOT EXISTS eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, procedimiento_id TEXT REFERENCES procedimientos(id),
  tipo TEXT, etapa_id TEXT, dias INTEGER, fecha TEXT, resolucion TEXT, nota TEXT,
  fuente TEXT DEFAULT 'demo', creado TEXT
);
CREATE TABLE IF NOT EXISTS periodos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, eps_id TEXT REFERENCES eps(id), resolucion TEXT,
  fecha_publicacion TEXT, inicio TEXT, anios INTEGER, fuente TEXT, archivo TEXT, nota TEXT, creado TEXT
);
"""


# EPS con periodo real recién iniciado: se les quita el procedimiento de demostración.
SIN_PROCEDIMIENTO_DEMO = {"maranon", "emapisco", "sanmartin", "juliaca", "pasco"}
# EPS que se registran EN VIVO en el demo (pegando su enlace de El Peruano): empiezan sin periodo ni procedimiento.
DEMO_EN_VIVO = {"epsel", "sedacaj"}


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower().strip()


@contextmanager
def conexion():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def iniciar(forzar_demo: bool = False) -> None:
    """Crea la base y la llena con los datos de demostración si está vacía."""
    with conexion() as con:
        con.executescript(ESQUEMA)
        vacia = con.execute("SELECT COUNT(*) FROM eps").fetchone()[0] == 0 \
            or con.execute("SELECT COUNT(*) FROM periodos").fetchone()[0] == 0 \
            or con.execute("SELECT COUNT(*) FROM periodos WHERE fuente='el_peruano'").fetchone()[0] == 0
        if not (vacia or forzar_demo):
            return
        con.executescript("DELETE FROM eventos; DELETE FROM procedimientos; DELETE FROM periodos; DELETE FROM eps;")
        with open(DATA / "eps.csv", encoding="utf-8") as f:
            con.executemany(
                "INSERT INTO eps VALUES (:id,:nombre,:ciudad,:departamento,:lat,:lon,:conexiones)",
                list(csv.DictReader(f)))
        with open(DATA / "procedimientos_demo.csv", encoding="utf-8") as f:
            con.executemany(
                "INSERT INTO procedimientos VALUES (:id,:eps_id,:resolucion,:fecha_emision,"
                ":fecha_publicacion,:responsable,:subgerencia,:fuente,NULL,NULL)",
                list(csv.DictReader(f)))
        with open(DATA / "eventos_demo.csv", encoding="utf-8") as f:
            con.executemany(
                "INSERT INTO eventos (procedimiento_id,tipo,etapa_id,dias,fecha,resolucion,nota) "
                "VALUES (:procedimiento_id,:tipo,:etapa_id,:dias,:fecha,:resolucion,:nota)",
                list(csv.DictReader(f)))
        with open(DATA / "periodos_demo.csv", encoding="utf-8") as f:
            con.executemany(
                "INSERT INTO periodos (eps_id,resolucion,fecha_publicacion,inicio,anios,fuente) "
                "VALUES (:eps_id,:resolucion,:fecha_publicacion,:inicio,:anios,:fuente)",
                list(csv.DictReader(f)))
        for eps_id in DEMO_EN_VIVO:
            con.execute("DELETE FROM periodos WHERE eps_id=?", (eps_id,))
            for pr in con.execute("SELECT id FROM procedimientos WHERE eps_id=?", (eps_id,)).fetchall():
                con.execute("DELETE FROM eventos WHERE procedimiento_id=?", (pr["id"],))
                con.execute("DELETE FROM procedimientos WHERE id=?", (pr["id"],))
        # Periodos REALES tomados de resoluciones publicadas en El Peruano: reemplazan a los de demostración.
        with open(DATA / "periodos_el_peruano.csv", encoding="utf-8") as f:
            reales = list(csv.DictReader(f))
        for r in reales:
            con.execute("DELETE FROM periodos WHERE eps_id=?", (r["eps_id"],))
            con.execute(
                "INSERT INTO periodos (eps_id,resolucion,fecha_publicacion,inicio,anios,fuente,archivo,nota) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (r["eps_id"], r["resolucion"], r["fecha_publicacion"], r["inicio"], int(r["anios"]),
                 "el_peruano", r["url"], r["nota"]))
            if r["eps_id"] in SIN_PROCEDIMIENTO_DEMO:
                # Su tarifa se aprobó hace poco: un procedimiento de demostración en curso sería contradictorio.
                for pr in con.execute("SELECT id FROM procedimientos WHERE eps_id=? AND fuente='demo'",
                                      (r["eps_id"],)).fetchall():
                    con.execute("DELETE FROM eventos WHERE procedimiento_id=?", (pr["id"],))
                    con.execute("DELETE FROM procedimientos WHERE id=?", (pr["id"],))


# ---------- lectura ----------

@dataclass
class Fila:
    """Una EPS con su procedimiento y su periodo regulatorio vigentes, y su estado a una fecha."""
    eps: dict
    proc: dict | None
    eventos: list[P.Evento]
    estado: P.Estado
    periodo: dict | None = None
    estado_periodo: R.EstadoPeriodo | None = None


def _a_fecha(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def lista_eps() -> list[dict]:
    with conexion() as con:
        return [dict(r) for r in con.execute("SELECT * FROM eps ORDER BY nombre")]


def procedimiento_vigente(con, eps_id: str) -> dict | None:
    r = con.execute(
        "SELECT * FROM procedimientos WHERE eps_id=? ORDER BY fecha_publicacion DESC LIMIT 1",
        (eps_id,)).fetchone()
    return dict(r) if r else None


def periodo_vigente(con, eps_id: str, hoy: date | None = None) -> dict | None:
    """El periodo más reciente que ya empezó (o el más próximo, si ninguno empezó)."""
    filas = [dict(r) for r in con.execute("SELECT * FROM periodos WHERE eps_id=? ORDER BY inicio DESC", (eps_id,))]
    if not filas:
        return None
    if hoy:
        iniciados = [f for f in filas if f["inicio"] <= hoy.isoformat()]
        if iniciados:
            return iniciados[0]
    return filas[-1]


def eventos_de(con, proc_id: str) -> list[P.Evento]:
    filas = con.execute("SELECT * FROM eventos WHERE procedimiento_id=? ORDER BY id", (proc_id,))
    return [P.Evento(r["tipo"], r["etapa_id"], int(r["dias"] or 0), _a_fecha(r["fecha"]), r["resolucion"] or "")
            for r in filas]


def tablero(hoy: date) -> list[Fila]:
    """Todas las EPS con su estado a la fecha `hoy`."""
    salida = []
    with conexion() as con:
        for e in con.execute("SELECT * FROM eps"):
            e = dict(e)
            proc = procedimiento_vigente(con, e["id"])
            evs = eventos_de(con, proc["id"]) if proc else []
            est = P.estado(_a_fecha(proc["fecha_publicacion"]) if proc else None, evs, hoy)
            per = periodo_vigente(con, e["id"], hoy)
            ini = _a_fecha(per["inicio"]) if per else None
            # La EPS ya solicitó la revisión si tiene un procedimiento iniciado durante este periodo,
            # esté en curso o ya concluido. Antes se exigía además que el procedimiento siguiera abierto
            # (`est.actual is not None`): al marcar la última etapa como cumplida, el periodo volvía a
            # «Solicitud no registrada» y el tablero acusaba a la EPS de no haber presentado nada.
            solicito = bool(proc and per and proc["fecha_publicacion"] >= per["inicio"])
            est_per = R.estado(ini, per["anios"] if per else None, hoy, solicito)
            salida.append(Fila(e, proc, evs, est, per, est_per))
    return salida


def buscar_eps(texto: str) -> list[dict]:
    """Coincidencia por nombre, ciudad o id, sin importar tildes ni mayúsculas."""
    t = normalizar(texto)
    if not t:
        return []
    salida = []
    for e in lista_eps():
        n = normalizar(e["nombre"])
        if t in n or n in t or t in normalizar(e["ciudad"]) or t == e["id"]:
            salida.append(e)
    return salida


# ---------- escritura ----------

def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def registrar_inicio(eps_id: str, resolucion: str, fecha_emision: date | None, fecha_publicacion: date,
                     responsable: str = "", subgerencia: str = "", archivo: str = "") -> str:
    """Registra un procedimiento real. Si la EPS tenía uno de demostración, lo reemplaza."""
    with conexion() as con:
        demo = con.execute("SELECT id FROM procedimientos WHERE eps_id=? AND fuente='demo'", (eps_id,)).fetchall()
        for r in demo:
            con.execute("DELETE FROM eventos WHERE procedimiento_id=?", (r["id"],))
            con.execute("DELETE FROM procedimientos WHERE id=?", (r["id"],))
        n = con.execute("SELECT COUNT(*) FROM procedimientos").fetchone()[0]
        pid = f"R{n + 1:03d}-{eps_id}"
        con.execute(
            "INSERT INTO procedimientos VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, eps_id, resolucion, fecha_emision.isoformat() if fecha_emision else "",
             fecha_publicacion.isoformat(), responsable, subgerencia, "pdf", archivo, _ahora()))
    return pid


def registrar_evento(proc_id: str, tipo: str, etapa_id: str, dias: int = 0, fecha: date | None = None,
                     resolucion: str = "", nota: str = "", fuente: str = "pdf") -> None:
    with conexion() as con:
        con.execute(
            "INSERT INTO eventos (procedimiento_id,tipo,etapa_id,dias,fecha,resolucion,nota,fuente,creado) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (proc_id, tipo, etapa_id, int(dias or 0), fecha.isoformat() if fecha else None,
             resolucion, nota, fuente, _ahora()))


def procedimiento_de_eps(eps_id: str) -> dict | None:
    with conexion() as con:
        return procedimiento_vigente(con, eps_id)


def eventos_de_proc(proc_id: str) -> list[P.Evento]:
    with conexion() as con:
        return eventos_de(con, proc_id)


def registrar_periodo(eps_id: str, resolucion: str, fecha_publicacion: date | None, inicio: date, anios: int,
                      archivo: str = "", nota: str = "") -> None:
    if not 3 <= int(anios) <= 5:
        raise ValueError("El periodo regulatorio solo puede durar 3, 4 o 5 años.")
    """Registra el periodo aprobado por una resolución real. Reemplaza el periodo de demostración de esa EPS."""
    with conexion() as con:
        con.execute("DELETE FROM periodos WHERE eps_id=? AND fuente='demo'", (eps_id,))
        con.execute(
            "INSERT INTO periodos (eps_id,resolucion,fecha_publicacion,inicio,anios,fuente,archivo,nota,creado) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (eps_id, resolucion, fecha_publicacion.isoformat() if fecha_publicacion else "", inicio.isoformat(),
             int(anios), "pdf", archivo, nota, _ahora()))
