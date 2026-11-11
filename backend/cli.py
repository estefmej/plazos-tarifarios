"""Línea de comandos del motor de plazos: `python -m backend <comando>`.

La app de Streamlit es para el tablero del día; esto sirve para todo lo demás:
un cron que arma el reporte semanal, un script que exporta el calendario, una
revisión puntual sin abrir el navegador. Todos los comandos leen las reglas de
`data/reglas/`, igual que la app, y no llaman a ningún modelo.

    python -m backend cronograma --publicacion 2026-11-06
    python -m backend periodo --inicio 2026-01-01 --anios 5 --hoy 2030-06-01
    python -m backend tablero --hoy 2026-11-11 --clase rojo
    python -m backend ics --publicacion 2026-11-06 --eps SEDAPAL --salida plazos.ics
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date

from backend import periodos as R
from backend import plazos as P


def _fecha(txt: str) -> date:
    try:
        return date.fromisoformat(txt)
    except ValueError:
        raise argparse.ArgumentTypeError(f"fecha inválida: «{txt}» (usa AAAA-MM-DD)") from None


def _evento(txt: str) -> P.Evento:
    """`etapa=N` (o `etapa=-N`) en la línea de comandos."""
    etapa, _, dias = txt.partition("=")
    if not etapa or not dias:
        raise argparse.ArgumentTypeError(f"evento inválido: «{txt}» (usa etapa=N, por ejemplo info=15)")
    try:
        n = int(dias)
    except ValueError:
        raise argparse.ArgumentTypeError(f"días inválidos en «{txt}»: «{dias}» no es un número") from None
    return P.Evento("ampliacion", etapa.strip(), n)


def _json(datos) -> None:
    print(json.dumps(datos, ensure_ascii=False, indent=2, default=str))


def _tabla(encabezados: list[str], filas: list[list[str]]) -> str:
    anchos = [max(len(str(e)), *(len(str(f[i])) for f in filas)) if filas else len(str(e))
              for i, e in enumerate(encabezados)]
    linea = "  ".join(str(e).ljust(a) for e, a in zip(encabezados, anchos))
    salida = [linea, "  ".join("-" * a for a in anchos)]
    salida += ["  ".join(str(c).ljust(a) for c, a in zip(f, anchos)) for f in filas]
    return "\n".join(salida)


# ---------- comandos ----------

def cmd_cronograma(ns) -> int:
    eventos = ns.evento or []
    est = P.estado(ns.publicacion, eventos, ns.hoy)
    if ns.json:
        _json({"publicacion": ns.publicacion.isoformat(), "hoy": ns.hoy.isoformat(),
               "clase": est.clase, "texto": est.texto, "dias_restantes": est.dias_restantes,
               "etapas": [{"id": e.id, "nombre": e.nombre, "dias_habiles": e.dias_habiles,
                           "ampliacion": e.dias_extra, "desde": e.desde.isoformat(),
                           "limite": e.limite.isoformat(), "situacion": e.situacion,
                           "cumplida": e.cumplida,
                           "fecha_cumplida": e.fecha_cumplida.isoformat() if e.fecha_cumplida else None}
                          for e in est.etapas],
               "advertencias": est.advertencias})
        return 0
    print(f"Publicación en El Peruano: {ns.publicacion.isoformat()} · estado al {ns.hoy.isoformat()}: "
          f"{est.texto}")
    filas = [[e.id, str(e.dias_habiles + e.dias_extra), f"+{e.dias_extra}" if e.dias_extra else "",
              e.limite.isoformat(), e.situacion] for e in est.etapas]
    print(_tabla(["ETAPA", "DÍAS", "AMPL.", "LÍMITE", "SITUACIÓN"], filas))
    for aviso in est.advertencias:
        print(f"\nAVISO: {aviso}", file=sys.stderr)
    return 0


def cmd_periodo(ns) -> int:
    ep = R.estado(ns.inicio, ns.anios, ns.hoy, ns.solicitud)
    if ep.inicio is None or ep.termino is None:
        print("Sin inicio o duración no se puede calcular el periodo.", file=sys.stderr)
        return 2
    if ns.json:
        _json({"inicio": ep.inicio.isoformat(), "termino": ep.termino.isoformat(),
               "anios": ns.anios, "hoy": ns.hoy.isoformat(), "clase": ep.clase, "texto": ep.texto,
               "dias_al_proximo_hito": ep.dias,
               "hitos": [{"id": h.id, "nombre": h.nombre, "fecha": h.fecha.isoformat(),
                          "base": h.base, "cumplido": h.cumplido} for h in ep.hitos]})
        return 0
    print(f"Periodo regulatorio: {ep.inicio.isoformat()} – {ep.termino.isoformat()} ({ns.anios} años) · "
          f"estado al {ns.hoy.isoformat()}: {ep.texto}")
    print(_tabla(["HITO", "FECHA", "SITUACIÓN", "BASE"],
                 [[h.id, h.fecha.isoformat(), "cumplido" if h.cumplido else "pendiente", h.base]
                  for h in ep.hitos]))
    return 0


def cmd_tablero(ns) -> int:
    from backend import datos as D

    if ns.sembrar:
        D.iniciar(forzar_demo=True)
    if not D.DB.exists():
        print(f"No hay base de datos en {D.DB}. Abre la app una vez o usa --sembrar.", file=sys.stderr)
        return 2
    try:
        filas = D.tablero(ns.hoy)
    except sqlite3.OperationalError as exc:
        print(f"No se pudo leer la base {D.DB}: {exc}. Abre la app una vez o usa --sembrar.",
              file=sys.stderr)
        return 2
    if ns.eps:
        filas = [f for f in filas if f.eps["id"] == ns.eps]
        if not filas:
            print(f"No hay una EPS con id «{ns.eps}» en la base.", file=sys.stderr)
            return 2
    if ns.clase:
        filas = [f for f in filas if f.estado.clase == ns.clase]
    if ns.json:
        _json([{"eps": f.eps["id"], "nombre": f.eps["nombre"], "departamento": f.eps["departamento"],
                "procedimiento": f.proc["resolucion"] if f.proc else None,
                "publicacion": f.proc["fecha_publicacion"] if f.proc else None,
                "clase": f.estado.clase, "texto": f.estado.texto,
                "etapa": f.estado.etapas[f.estado.actual].id if f.estado.actual is not None else None,
                "limite": (f.estado.etapas[f.estado.actual].limite.isoformat()
                           if f.estado.actual is not None else None),
                "dias_restantes": f.estado.dias_restantes,
                "periodo": f.estado_periodo.texto if f.estado_periodo else None,
                "advertencias": f.estado.advertencias} for f in filas])
        return 0
    print(f"Estado al {ns.hoy.isoformat()} · {len(filas)} EPS")
    print(_tabla(["EPS", "CLASE", "ETAPA", "LÍMITE", "DÍAS", "PERIODO"],
                 [[f.eps["nombre"][:28], f.estado.clase,
                   f.estado.etapas[f.estado.actual].id if f.estado.actual is not None else "—",
                   f.estado.etapas[f.estado.actual].limite.isoformat()
                   if f.estado.actual is not None else "—",
                   str(f.estado.dias_restantes) if f.estado.dias_restantes is not None else "—",
                   f.estado_periodo.texto if f.estado_periodo else "—"] for f in filas]))
    return 0


def cmd_ics(ns) -> int:
    from backend import ics

    if not ns.publicacion and not (ns.inicio and ns.anios):
        print("Indica --publicacion (cronograma) o --inicio con --anios (periodo regulatorio).",
              file=sys.stderr)
        return 2
    cal = ics.Calendario(nombre=f"Plazos tarifarios · {ns.eps}")
    if ns.publicacion:
        etapas = P.cronograma(ns.publicacion, ns.evento or [])
        cal.citas += ics.citas_de_procedimiento(ns.eps, ns.publicacion, etapas, ns.resolucion)
    if ns.inicio and ns.anios:
        ep = R.estado(ns.inicio, ns.anios, ns.hoy, ns.solicitud)
        if ep.inicio is None or ep.termino is None:
            print("Sin inicio o duración no se puede exportar el periodo.", file=sys.stderr)
            return 2
        cal.citas += ics.citas_de_periodo(ns.eps, ep, ns.resolucion)
    contenido = ics.generar(cal, aviso_dias=ns.dias_aviso)
    if ns.salida == "-":
        sys.stdout.write(contenido)
    else:
        with open(ns.salida, "w", encoding="utf-8", newline="") as f:
            f.write(contenido)
        print(f"{len(cal.citas)} citas escritas en {ns.salida}")
    return 0


# ---------- armado ----------

def construir() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m backend",
                                description="Motor de plazos tarifarios sin la interfaz web.")
    sub = p.add_subparsers(dest="comando", required=True)

    hoy_por_defecto = date.today().isoformat()

    c = sub.add_parser("cronograma", help="Cronograma de un procedimiento en días hábiles.")
    c.add_argument("--publicacion", type=_fecha, required=True,
                   help="Fecha de publicación de la resolución de inicio en El Peruano.")
    c.add_argument("--evento", type=_evento, action="append", metavar="ETAPA=N",
                   help="Ampliación o suspensión de días hábiles sobre una etapa (repetible).")
    c.add_argument("--hoy", type=_fecha, default=date.fromisoformat(hoy_por_defecto))
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_cronograma)

    per = sub.add_parser("periodo", help="Periodo regulatorio e hitos de 19 y 10 meses.")
    per.add_argument("--inicio", type=_fecha, required=True)
    per.add_argument("--anios", type=int, choices=(3, 4, 5), required=True)
    per.add_argument("--hoy", type=_fecha, default=date.fromisoformat(hoy_por_defecto))
    per.add_argument("--solicitud", action="store_true",
                     help="La EPS ya presentó su solicitud de revisión periódica.")
    per.add_argument("--json", action="store_true")
    per.set_defaults(func=cmd_periodo)

    t = sub.add_parser("tablero", help="Todas las EPS con su estado (lee la base SQLite).")
    t.add_argument("--hoy", type=_fecha, default=date.fromisoformat(hoy_por_defecto))
    t.add_argument("--eps", help="Filtra por id de EPS.")
    t.add_argument("--clase", choices=("rojo", "ambar", "verde", "fin", "sin"),
                   help="Filtra por color del semáforo.")
    t.add_argument("--sembrar", action="store_true",
                   help="Carga los datos de demostración si la base no existe.")
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=cmd_tablero)

    i = sub.add_parser("ics", help="Exporta los vencimientos a un calendario .ics.")
    i.add_argument("--publicacion", type=_fecha, help="Publicación de la resolución de inicio.")
    i.add_argument("--inicio", type=_fecha, help="Inicio del periodo regulatorio.")
    i.add_argument("--anios", type=int, choices=(3, 4, 5))
    i.add_argument("--eps", default="EPS", help="Nombre de la EPS para los títulos.")
    i.add_argument("--resolucion", default="", help="Número de resolución para la descripción.")
    i.add_argument("--evento", type=_evento, action="append", metavar="ETAPA=N")
    i.add_argument("--hoy", type=_fecha, default=date.fromisoformat(hoy_por_defecto))
    i.add_argument("--solicitud", action="store_true")
    i.add_argument("--dias-aviso", type=int, default=3, dest="dias_aviso",
                   help="Días de anticipación del recordatorio (0 = sin recordatorio).")
    i.add_argument("--salida", default="-", help="Archivo de salida; «-» es la salida estándar.")
    i.set_defaults(func=cmd_ics)
    return p


def main(argv: list[str] | None = None) -> int:
    ns = construir().parse_args(argv)
    try:
        return ns.func(ns)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
