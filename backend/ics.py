"""Calendario `.ics` (RFC 5545) con los vencimientos de un procedimiento y los hitos del periodo.

La app avisa en pantalla, pero un plazo legal se pierde si nadie mira la pantalla:
el analista vive en Outlook o en el calendario del teléfono. Esto genera un archivo
que se importa una vez y queda con recordatorio propio.

Solo se emiten eventos de día completo (`DTSTART;VALUE=DATE`), que es lo que
corresponde a un plazo «hasta el 31 de mayo»: no hay hora legal que mostrar.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

PRODID = "-//Plazos tarifarios//Motor de plazos//ES"
MAX_OCTETOS = 75            # RFC 5545 §3.1: 75 octetos por línea, sin contar el fin de línea


@dataclass(frozen=True)
class Cita:
    """Un evento de día completo del calendario."""
    fecha: date
    titulo: str
    descripcion: str = ""
    categoria: str = ""
    uid: str = ""


@dataclass
class Calendario:
    nombre: str = "Plazos tarifarios"
    citas: list[Cita] = field(default_factory=list)

    def agregar(self, cita: Cita) -> None:
        self.citas.append(cita)


def _escapar(texto: str) -> str:
    """Escapa el texto de una propiedad (RFC 5545 §3.3.11)."""
    return (str(texto).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def _plegar(linea: str) -> str:
    """Divide una línea en tramos de 75 octetos sin partir caracteres ni escapes.

    Contar caracteres no sirve: «Educación» gasta dos octetos por vocal acentuada y
    una línea de 75 caracteres puede pasar de 100 octetos, que es justo lo que la
    norma prohíbe. Tampoco se puede cortar entre una barra invertida y el carácter
    que escapa, porque el trozo siguiente empieza con un espacio de continuación.
    Las líneas de continuación cuentan ese espacio: llevan 74 octetos de contenido.
    """
    trozos, actual, octetos, escape = [], [], 0, False
    limite = MAX_OCTETOS
    for ch in linea:
        n = len(ch.encode("utf-8"))
        if octetos + n > limite and actual and not escape:
            trozos.append("".join(actual))
            actual, octetos, limite = [], 0, MAX_OCTETOS - 1
        actual.append(ch)
        octetos += n
        escape = (not escape) and ch == "\\"
    trozos.append("".join(actual))
    return "\r\n ".join(trozos)


def _uid(citas: list[Cita], calendario: str, i: int) -> str:
    """UID estable: reimportar el archivo actualiza la cita en vez de duplicarla."""
    base = "|".join([calendario, citas[i].titulo, citas[i].fecha.isoformat()])
    return f"{hashlib.sha1(base.encode('utf-8')).hexdigest()[:24]}@plazos-tarifarios"


def _bloque(cita: Cita, uid: str, ahora: datetime, aviso_dias: int) -> list[str]:
    lineas = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{ahora:%Y%m%dT%H%M%SZ}",
        f"DTSTART;VALUE=DATE:{cita.fecha:%Y%m%d}",
        f"DTEND;VALUE=DATE:{cita.fecha + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_escapar(cita.titulo)}",
        "TRANSP:TRANSPARENT",
    ]
    if cita.descripcion:
        lineas.append(f"DESCRIPTION:{_escapar(cita.descripcion)}")
    if cita.categoria:
        lineas.append(f"CATEGORIES:{_escapar(cita.categoria)}")
    if aviso_dias > 0:
        lineas += [
            "BEGIN:VALARM",
            f"TRIGGER:-P{int(aviso_dias)}D",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escapar(cita.titulo)}",
            "END:VALARM",
        ]
    lineas.append("END:VEVENT")
    return lineas


def generar(calendario: Calendario, ahora: datetime | None = None, aviso_dias: int = 3) -> str:
    """Devuelve el calendario completo, con líneas CRLF como exige la norma."""
    ahora = ahora or datetime.now(timezone.utc)
    if ahora.tzinfo is None:                     # DTSTAMP es UTC obligatorio
        ahora = ahora.replace(tzinfo=timezone.utc)
    ahora = ahora.astimezone(timezone.utc)
    lineas = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escapar(calendario.nombre)}",
    ]
    # Las citas se ordenan por fecha: un calendario desordenado se ve roto al importarlo.
    ordenadas = sorted(calendario.citas, key=lambda c: (c.fecha, c.titulo))
    for i, cita in enumerate(ordenadas):
        uid = cita.uid or _uid(ordenadas, calendario.nombre, i)
        lineas += _bloque(cita, uid, ahora, aviso_dias)
    lineas.append("END:VCALENDAR")
    return "\r\n".join(_plegar(x) for x in lineas) + "\r\n"


def citas_de_procedimiento(eps_nombre: str, fecha_publicacion: date, etapas, resolucion: str = "") -> list[Cita]:
    """Una cita por etapa, con el límite en días hábiles ya calculado por `plazos`."""
    citas = []
    for e in etapas:
        detalle = [f"EPS: {eps_nombre}"]
        if resolucion:
            detalle.append(f"Resolución: {resolucion}")
        detalle += [f"Publicación de inicio: {fecha_publicacion.isoformat()}",
                    f"Plazo: {e.dias_habiles + e.dias_extra} días hábiles",
                    f"Cuenta desde: {e.desde.isoformat()}",
                    f"Situación: {'cumplida' if e.cumplida else 'pendiente'}"]
        citas.append(Cita(e.limite, f"{eps_nombre} · {e.nombre}", "\n".join(detalle),
                          "revision-periodica"))
    return citas


def citas_de_periodo(eps_nombre: str, estado_periodo, resolucion: str = "") -> list[Cita]:
    """Citas del periodo regulatorio: inicio, los hitos de 19 y 10 meses y el término."""
    citas = []
    detalles = [f"EPS: {eps_nombre}"] + ([f"Resolución: {resolucion}"] if resolucion else [])
    base = "\n".join(detalles + [f"Periodo: {estado_periodo.inicio.isoformat()} – "
                                 f"{estado_periodo.termino.isoformat()}"])
    citas.append(Cita(estado_periodo.inicio, f"{eps_nombre} · inicio del periodo regulatorio",
                      base, "periodo-regulatorio"))
    for h in estado_periodo.hitos:
        citas.append(Cita(h.fecha, f"{eps_nombre} · {h.nombre}",
                          "\n".join(detalles + [f"Base: {h.base}"]), "periodo-regulatorio"))
    citas.append(Cita(estado_periodo.termino, f"{eps_nombre} · término del periodo regulatorio",
                      base, "periodo-regulatorio"))
    return citas
