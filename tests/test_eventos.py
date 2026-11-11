"""Pruebas de los eventos que modifican el cronograma.

Una ampliación otorgada por resolución no puede desaparecer en silencio: si el evento
trae una etapa que el motor no conoce (o cero días), el plazo se calcula más corto de
lo que la resolución concede y el tablero muestra una fecha equivocada sin avisar.
"""
from datetime import date

import pytest

from backend import plazos as P

PUB = date(2026, 11, 6)


def limites(eventos=None) -> dict[str, date]:
    return {e.id: e.limite for e in P.cronograma(PUB, eventos)}


def test_ampliacion_valida_corre_todo_lo_siguiente():
    sin, con = limites(), limites([P.Evento("ampliacion", "info", 15)])
    assert P.habiles_entre(sin["info"], con["info"]) == 15
    assert P.habiles_entre(sin["final"], con["final"]) == 15
    assert con["inicio"] == sin["inicio"]


def test_suspension_suma_igual_que_una_ampliacion():
    assert limites([P.Evento("ampliacion", "campo", 7)]) == limites([P.Evento("suspension", "campo", 7)])


def test_etapa_inexistente_no_se_aplica_y_se_avisa():
    raro = P.Evento("ampliacion", "informacion", 20)
    assert P.cronograma(PUB, [raro]) == P.cronograma(PUB)
    avisos = P.estado(PUB, [raro], PUB).advertencias
    assert len(avisos) == 1 and "informacion" in avisos[0]


def test_ampliacion_sin_dias_se_ignora_y_se_avisa():
    est = P.estado(PUB, [P.Evento("ampliacion", "info", 0)], PUB)
    assert est.etapas[1].dias_extra == 0
    assert any("mayores que cero" in a for a in est.advertencias)


def test_tipo_de_evento_desconocido_se_ignora_y_se_avisa():
    est = P.estado(PUB, [P.Evento("prorroga", "info", 10)], PUB)
    assert est.etapas[1].dias_extra == 0
    assert any("prorroga" in a for a in est.advertencias)


def test_varias_ampliaciones_de_la_misma_etapa_se_suman():
    cr = P.cronograma(PUB, [P.Evento("ampliacion", "info", 5), P.Evento("ampliacion", "info", 7)])
    assert cr[1].dias_extra == 12
    assert P.habiles_entre(limites()["info"], cr[1].limite) == 12


def test_etapa_cumplida_avanza_la_etapa_actual():
    ev = P.Evento("etapa_cumplida", "info", fecha=date(2026, 12, 1))
    est = P.estado(PUB, [ev], date(2026, 12, 2))
    assert est.etapas[est.actual].id == "campo"
    assert est.etapas[1].cumplida and est.etapas[1].fecha_cumplida == date(2026, 12, 1)


def test_eventos_aplicables_separa_validos_de_ignorados():
    validos = [P.Evento("ampliacion", "info", 5), P.Evento("etapa_cumplida", "campo")]
    invalidos = [P.Evento("ampliacion", "no-existe", 5), P.Evento("ampliacion", "info", -3)]
    aplicables, avisos = P.eventos_aplicables(validos + invalidos)
    assert aplicables == validos
    assert len(avisos) == 2
    assert "no-existe" in avisos[0] and "-3" in avisos[1]


def test_sin_eventos_no_hay_avisos():
    assert P.eventos_aplicables(None) == ([], [])
    assert P.estado(PUB, [], PUB).advertencias == []


def test_dias_restantes_no_cambia_por_un_evento_ignorado():
    a = P.estado(PUB, [], date(2026, 11, 9))
    b = P.estado(PUB, [P.Evento("ampliacion", "informacion", 30)], date(2026, 11, 9))
    assert (a.clase, a.actual, a.dias_restantes) == (b.clase, b.actual, b.dias_restantes)


@pytest.mark.parametrize("etapa", [e.id for e in P.etapas()])
def test_toda_etapa_del_yaml_admite_ampliacion(etapa):
    assert P.eventos_aplicables([P.Evento("ampliacion", etapa, 1)])[1] == []
    fila = next(f for f in P.cronograma(PUB, [P.Evento("ampliacion", etapa, 1)]) if f.id == etapa)
    assert fila.dias_extra == 1
