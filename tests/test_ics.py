"""Pruebas del calendario .ics (RFC 5545).

Un archivo mal formado no da error: el cliente de correo lo importa a medias o lo
ignora. Por eso las pruebas miran el archivo crudo (CRLF, 75 octetos, escapes) y no
solo el objeto que lo genera.
"""
from datetime import date, datetime, timezone

from backend import ics
from backend import periodos as R
from backend import plazos as P

AHORA = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def desplegar(texto: str) -> list[str]:
    """Deshace el plegado de líneas (CRLF + espacio)."""
    return texto.replace("\r\n ", "").split("\r\n")


def calendario_con(citas) -> str:
    cal = ics.Calendario(nombre="Plazos tarifarios · SEDAPAL")
    cal.citas += citas
    return ics.generar(cal, ahora=AHORA)


def test_estructura_minima_del_archivo():
    texto = calendario_con(ics.citas_de_procedimiento("SEDAPAL", date(2026, 11, 6), P.cronograma(date(2026, 11, 6))))
    lineas = desplegar(texto)
    assert lineas[0] == "BEGIN:VCALENDAR"
    assert lineas[-2] == "END:VCALENDAR" and texto.endswith("\r\n")
    assert "VERSION:2.0" in lineas
    assert any(x.startswith("PRODID:") for x in lineas)
    assert lineas.count("BEGIN:VEVENT") == lineas.count("END:VEVENT") == 7


def test_solo_crlf_y_lineas_de_75_octetos():
    titulo = "SEDAPAL · Publicación del proyecto de estudio tarifario de agua potable y alcantarillado"
    texto = calendario_con([ics.Cita(date(2026, 5, 31), titulo, "Descripción con áes y óes " * 12)])
    assert texto.count("\n") == texto.count("\r\n")          # ningún LF suelto
    for linea in texto.split("\r\n"):
        assert len(linea.encode("utf-8")) <= 75, linea


def test_el_plegado_no_parte_caracteres_ni_escapes():
    titulo = "Educación, saneamiento; comisión \\ dirección " * 4
    texto = calendario_con([ics.Cita(date(2026, 5, 31), titulo)])
    plano = desplegar(texto)
    resumen = next(x for x in plano if x.startswith("SUMMARY:"))
    esperado = titulo.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    assert resumen == f"SUMMARY:{esperado}"


def test_escapa_comas_punto_y_coma_barras_y_saltos():
    cita = ics.Cita(date(2026, 5, 31), "Plazo, final; urgente", "Línea 1\nLínea 2")
    plano = desplegar(calendario_con([cita]))
    assert "SUMMARY:Plazo\\, final\\; urgente" in plano
    assert "DESCRIPTION:Línea 1\\nLínea 2" in plano


def test_fechas_de_dia_completo_y_recordatorio():
    plano = desplegar(calendario_con([ics.Cita(date(2026, 5, 31), "Hito")]))
    assert "DTSTART;VALUE=DATE:20260531" in plano
    assert "DTEND;VALUE=DATE:20260601" in plano
    assert "TRIGGER:-P3D" in plano
    assert f"DTSTAMP:{AHORA:%Y%m%dT%H%M%SZ}" in plano


def test_sin_recordatorio_si_aviso_cero():
    cal = ics.Calendario()
    cal.agregar(ics.Cita(date(2026, 5, 31), "Hito"))
    assert "VALARM" not in ics.generar(cal, ahora=AHORA, aviso_dias=0)


def test_uid_estable_para_reimportar_sin_duplicar():
    citas = [ics.Cita(date(2026, 5, 31), "Hito"), ics.Cita(date(2026, 6, 30), "Otro hito")]
    uno = [x for x in desplegar(calendario_con(citas)) if x.startswith("UID:")]
    dos = [x for x in desplegar(calendario_con(list(citas))) if x.startswith("UID:")]
    assert uno == dos and len(set(uno)) == 2


def test_citas_del_cronograma_una_por_etapa():
    etapas = P.cronograma(date(2026, 11, 6))
    citas = ics.citas_de_procedimiento("SEDAPAL", date(2026, 11, 6), etapas, "RCD N.° 079-2021")
    assert len(citas) == len(etapas)
    assert [c.fecha for c in citas] == [e.limite for e in etapas]
    assert all("SEDAPAL" in c.titulo for c in citas)
    assert "RCD N.° 079-2021" in citas[0].descripcion


def test_citas_del_periodo_incluyen_inicio_hitos_y_termino():
    ep = R.estado(date(2022, 1, 1), 5, date(2026, 6, 1))
    citas = ics.citas_de_periodo("SEDAPAL", ep, "RCD N.° 079-2021")
    assert len(citas) == len(ep.hitos) + 2
    fechas = {c.fecha for c in citas}
    assert {date(2022, 1, 1), date(2025, 5, 31), date(2026, 2, 28), date(2026, 12, 31)} <= fechas


def test_ordenado_por_fecha():
    citas = [ics.Cita(date(2026, 6, 30), "B"), ics.Cita(date(2026, 5, 31), "A")]
    plano = desplegar(calendario_con(citas))
    assert plano.index("SUMMARY:A") < plano.index("SUMMARY:B")
