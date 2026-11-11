"""Pruebas del motor de plazos. Ejecutar: python -m pytest -q"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import plazos as P  # noqa: E402


def test_salta_fin_de_semana():
    # viernes 6 nov 2026 + 1 día hábil = lunes 9 nov 2026
    assert P.sumar_habiles(date(2026, 11, 6), 1) == date(2026, 11, 9)


def test_salta_feriados():
    # miércoles 1 abr 2026 + 1 día hábil: jueves y viernes santo son feriados -> lunes 6 abr
    assert P.sumar_habiles(date(2026, 4, 1), 1) == date(2026, 4, 6)
    # 7 dic 2026 + 1: el 8 y 9 de diciembre son feriados -> 10 dic
    assert P.sumar_habiles(date(2026, 12, 7), 1) == date(2026, 12, 10)


def test_habiles_entre_simetrico():
    a, b = date(2026, 11, 2), date(2026, 11, 16)
    assert P.habiles_entre(a, b) == 10
    assert P.habiles_entre(b, a) == -10
    assert P.habiles_entre(a, a) == 0


def test_cronograma_encadena_etapas():
    cr = P.cronograma(date(2026, 11, 6))
    assert cr[0].limite == date(2026, 11, 6)
    for prev, act in zip(cr, cr[1:]):
        assert P.habiles_entre(prev.limite, act.limite) == act.dias_habiles


def test_ampliacion_mueve_todo_lo_siguiente():
    pub = date(2026, 11, 6)
    base = P.cronograma(pub)
    amp = P.cronograma(pub, [P.Evento("ampliacion", "info", 15)])
    assert amp[0].limite == base[0].limite
    assert P.habiles_entre(base[1].limite, amp[1].limite) == 15
    assert P.habiles_entre(base[-1].limite, amp[-1].limite) == 15


def test_semaforo():
    pub = date(2026, 11, 6)
    limite_info = P.cronograma(pub)[1].limite
    assert P.estado(pub, [], date(2026, 11, 9)).clase == "verde"
    assert P.estado(pub, [], P.sumar_habiles(limite_info, 0)).clase == "ambar"
    vencido = P.estado(pub, [], date(2027, 1, 15))
    assert vencido.clase == "rojo" and vencido.dias_restantes < 0


def test_etapa_cumplida_avanza():
    pub = date(2026, 11, 6)
    est = P.estado(pub, [P.Evento("etapa_cumplida", "info", fecha=date(2026, 11, 20))], date(2026, 11, 23))
    assert est.etapas[est.actual].id == "campo"


def test_sin_procedimiento():
    assert P.estado(None, [], date(2026, 11, 10)).clase == "sin"
