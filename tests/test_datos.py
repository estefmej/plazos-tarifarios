"""Pruebas de la capa de datos: el estado del periodo que ve el tablero.

La columna «Periodo regulatorio» es la que usa la gerencia para reclamar a una EPS
que no presentó su solicitud. Tiene que salir del hecho de haber presentado, no de
que el procedimiento siga abierto.
"""
from datetime import date

import pytest

from backend import datos as D
from backend import periodos as R
from backend import plazos as P


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DB", tmp_path / "prueba.db")
    D.iniciar(forzar_demo=True)
    return D


def fila_de(eps_id: str, hoy: date) -> D.Fila:
    return next(f for f in D.tablero(hoy) if f.eps["id"] == eps_id)


def test_procedimiento_concluido_no_borra_la_solicitud(db):
    """Repro del defecto: con todas las etapas cumplidas el periodo volvía a
    «Solicitud no registrada» y el tablero acusaba a la EPS de no haber presentado nada."""
    eps = D.lista_eps()[0]
    D.registrar_periodo(eps["id"], "RCD N.° 999-2026-SUNASS-CD", date(2025, 12, 1), date(2026, 1, 1), 5)
    pid = D.registrar_inicio(eps["id"], "RCD N.° 999-2026-SUNASS-CD", date(2026, 2, 20), date(2026, 3, 1))

    en_curso = fila_de(eps["id"], date(2030, 6, 1)).estado_periodo
    assert en_curso.texto == "En revisión periódica"

    for etapa in [e.id for e in P.etapas()][1:]:
        D.registrar_evento(pid, "etapa_cumplida", etapa, fecha=date(2026, 9, 30))

    concluido = fila_de(eps["id"], date(2030, 6, 1))
    assert concluido.estado.actual is None            # el procedimiento ya cerró
    assert concluido.estado_periodo.clase != "rojo"
    assert concluido.estado_periodo.texto == "En revisión periódica"


def test_sin_procedimiento_el_periodo_si_marca_la_solicitud(db):
    eps = next(e for e in D.lista_eps() if e["id"] == "abancay")
    f = fila_de("abancay", date(2026, 11, 11))
    assert f.proc is None
    assert f.estado_periodo.clase == "rojo" and f.estado_periodo.texto == "Solicitud no registrada"
    assert eps["nombre"]


def test_procedimiento_anterior_al_periodo_no_cuenta_como_solicitud(db):
    """Un procedimiento del periodo anterior no es la solicitud de este."""
    eps = D.lista_eps()[0]
    D.registrar_periodo(eps["id"], "RCD N.° 998-2026-SUNASS-CD", date(2025, 12, 1), date(2026, 1, 1), 5)
    D.registrar_inicio(eps["id"], "RCD N.° 001-2024", date(2023, 12, 20), date(2024, 1, 2))
    f = fila_de(eps["id"], date(2030, 6, 1))
    assert f.estado_periodo.texto == "Solicitud no registrada"


def test_tablero_trae_todas_las_eps_con_estado(db):
    filas = D.tablero(date(2026, 11, 11))
    assert len(filas) == len(D.lista_eps())
    assert all(f.estado.clase in ("rojo", "ambar", "verde", "fin", "sin") for f in filas)
    assert all(f.estado_periodo is not None for f in filas)


def test_tablero_propaga_las_advertencias_del_calendario(db):
    """Un cronograma que se mete en un año sin feriados cargados tiene que decirlo en el tablero."""
    eps = D.lista_eps()[0]
    D.registrar_inicio(eps["id"], "RCD N.° 997-2028-SUNASS-CD", date(2028, 5, 20), date(2028, 6, 1))
    f = fila_de(eps["id"], date(2028, 6, 5))
    assert any("2029" in a for a in f.estado.advertencias)


def test_buscar_eps_ignora_tildes_y_mayusculas(db):
    assert [e["id"] for e in D.buscar_eps("SEDAPAL")] == ["sedapal"]
    assert D.buscar_eps("") == []
    assert any(e["id"] == "maranon" for e in D.buscar_eps("marañon"))


def test_periodo_vigente_toma_el_que_ya_empezo(db):
    with D.conexion() as con:
        per = D.periodo_vigente(con, "sedapal", date(2026, 11, 11))
    assert per["inicio"] <= "2026-11-11"
    assert per["fuente"] == "el_peruano"


def test_registrar_periodo_rechaza_duraciones_fuera_de_la_ley(db):
    with pytest.raises(ValueError, match="3, 4 o 5 años"):
        D.registrar_periodo("sedapal", "RCD X", date(2026, 1, 1), date(2026, 1, 1), 2)


def test_hitos_del_periodo_usan_el_termino(db):
    per = R.termino(date(2026, 1, 1), 5)
    fechas = {h.id: h.fecha for h in R.hitos(per)}
    assert per == date(2030, 12, 31)
    assert fechas["comunicacion_sunass"] == date(2029, 5, 31)
    assert fechas["solicitud_eps"] == date(2030, 2, 28)
