"""Pruebas del calendario de feriados y de la cobertura del motor.

El motor cuenta días hábiles con `data/reglas/feriados.csv`. Cuando ese archivo no
tiene el año, el motor no puede notarlo: sigue contando y devuelve una fecha que
parece legal. Estas pruebas fijan las fechas conocidas y vigilan el archivo.
"""
import csv
from datetime import date, timedelta

from backend import plazos as P


def pascua(anio: int) -> date:
    """Domingo de Pascua por el algoritmo gregoriano (Computus)."""
    a, b, c = anio % 19, anio // 100, anio % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    lun = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lun) // 451
    mes, dia = (h + lun - 7 * m + 114) // 31, ((h + lun - 7 * m + 114) % 31) + 1
    return date(anio, mes, dia)


def test_cobertura_incluye_los_anios_de_las_resoluciones_del_repo():
    primero, ultimo = P.cobertura()
    assert primero == date(2023, 1, 1)
    assert ultimo >= date(2028, 12, 25)
    assert set(range(2023, 2029)) <= set(P.anios_con_calendario())


def test_semana_santa_del_csv_coincide_con_el_computus():
    """Si alguien edita el CSV a mano, los dos feriados móviles tienen que seguir ahí."""
    with open(P.REGLAS / "feriados.csv", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    for anio in P.anios_con_calendario():
        domingo = pascua(anio)
        del_anio = {date.fromisoformat(r["fecha"]) for r in filas if r["fecha"].startswith(str(anio))}
        assert domingo - timedelta(days=3) in del_anio, f"falta Jueves Santo {anio}"
        assert domingo - timedelta(days=2) in del_anio, f"falta Viernes Santo {anio}"
        assert len(del_anio) == 16, f"{anio} tiene {len(del_anio)} feriados, se esperan 16"


def test_avisa_cuando_no_hay_calendario_para_el_anio():
    avisos = P.advertencias_calendario(date(2030, 3, 1), date(2030, 4, 1))
    assert len(avisos) == 1
    assert "2030" in avisos[0] and "feriados.csv" in avisos[0]


def test_no_avisa_dentro_de_la_cobertura():
    assert P.advertencias_calendario(date(2026, 1, 2), date(2027, 12, 20)) == []


def test_avisa_los_anios_que_faltan_en_medio():
    avisos = P.advertencias_calendario(date(2028, 11, 1), date(2030, 2, 1))
    assert len(avisos) == 1 and "2029, 2030" in avisos[0]


def test_cronograma_de_2024_descuenta_fiestas_patrias():
    """RCD 00027-2024 (publicada el 05/06/2024): antes de cargar 2024 el último plazo
    salía 2025-01-23, trece días antes del legal, sin ningún aviso."""
    cr = {e.id: e.limite for e in P.cronograma(date(2024, 6, 5))}
    assert cr["info"] == date(2024, 7, 4)          # salta 07/06 (Batalla de Arica)
    assert cr["campo"] == date(2024, 8, 20)        # 23/07, 28/07, 29/07 y 06/08
    assert cr["final"] == date(2025, 2, 5)


def test_cronograma_que_cruza_2028_descuenta_semana_santa():
    cr = {e.id: e.limite for e in P.cronograma(date(2027, 12, 1))}
    assert cr["final"] == date(2028, 7, 31)
    assert cr["final"].weekday() < 5


def test_estado_incluye_las_advertencias_del_calendario():
    est = P.estado(date(2029, 3, 1), [], date(2029, 3, 5))
    assert est.advertencias and "2029" in est.advertencias[0]
    assert P.estado(date(2026, 3, 1), [], date(2026, 3, 5)).advertencias == []


def test_sumar_habiles_rechaza_dias_negativos():
    import pytest

    with pytest.raises(ValueError, match="No se pueden contar"):
        P.sumar_habiles(date(2026, 11, 6), -5)
