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


# ---------- periodo regulatorio ----------
from backend import periodos as R  # noqa: E402


def test_ejemplo_rcd_079_2021():
    # Publicada el 26/12/2021 (regla anterior: mes siguiente) -> periodo 2022-2026
    ini, _ = R.inicio_por_regla(date(2021, 12, 26))
    assert ini == date(2022, 1, 1)
    fin = R.termino(ini, 5)
    assert fin == date(2026, 12, 31)
    h = {x.id: x.fecha for x in R.hitos(fin)}
    assert h["comunicacion_sunass"] == date(2025, 5, 31)   # 19 meses antes del término
    assert h["solicitud_eps"] == date(2026, 2, 28)         # 10 meses antes del término


def test_regla_anio_fiscal():
    ini, _ = R.inicio_por_regla(date(2024, 12, 18))
    assert ini == date(2025, 1, 1)


def test_regla_mes_siguiente():
    ini, _ = R.inicio_por_regla(date(2023, 8, 15))
    assert ini == date(2023, 9, 1)
    assert R.termino(ini, 5) == date(2028, 8, 31)


def test_solicitud_no_presentada():
    ep = R.estado(date(2022, 1, 1), 5, date(2026, 6, 1), solicitud_presentada=False)
    assert ep.clase == "rojo"
    ep2 = R.estado(date(2022, 1, 1), 5, date(2026, 6, 1), solicitud_presentada=True)
    assert ep2.texto == "En revisión periódica"
