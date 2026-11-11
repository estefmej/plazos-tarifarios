"""Pruebas de la línea de comandos (`python -m backend ...`).

Todo lo que la app hace con clics tiene que poder hacerse desde un cron o un script:
si el comando se rompe, el reporte semanal se rompe en silencio.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from backend import cli
from backend import datos as D


def json_de(capsys, argv: list[str]) -> dict | list:
    assert cli.main(argv) == 0
    return json.loads(capsys.readouterr().out)


def test_cronograma_json_trae_las_etapas(capsys):
    datos = json_de(capsys, ["cronograma", "--publicacion", "2026-11-06", "--hoy", "2026-11-11", "--json"])
    assert datos["publicacion"] == "2026-11-06" and datos["clase"] == "verde"
    assert [e["id"] for e in datos["etapas"]][0] == "inicio"
    assert len(datos["etapas"]) == 7
    assert datos["etapas"][0]["limite"] == "2026-11-06"
    assert datos["advertencias"] == []


def test_cronograma_texto_muestra_la_tabla(capsys):
    assert cli.main(["cronograma", "--publicacion", "2026-11-06", "--hoy", "2026-11-11"]) == 0
    salida = capsys.readouterr().out
    assert "ETAPA" in salida and "LÍMITE" in salida and "info" in salida


def test_ampliacion_por_linea_de_comandos_mueve_el_limite(capsys):
    base = json_de(capsys, ["cronograma", "--publicacion", "2026-11-06", "--hoy", "2026-11-11", "--json"])
    con = json_de(capsys, ["cronograma", "--publicacion", "2026-11-06", "--hoy", "2026-11-11",
                           "--evento", "info=15", "--json"])
    assert base["etapas"][1]["limite"] != con["etapas"][1]["limite"]
    assert con["etapas"][1]["ampliacion"] == 15


def test_evento_con_etapa_inexistente_avisa_por_stderr(capsys):
    assert cli.main(["cronograma", "--publicacion", "2026-11-06", "--hoy", "2026-11-11",
                     "--evento", "informacion=5"]) == 0
    assert "informacion" in capsys.readouterr().err


def test_fecha_invalida_sale_con_error_de_uso():
    with pytest.raises(SystemExit) as exc:
        cli.main(["cronograma", "--publicacion", "06/11/2026"])
    assert exc.value.code == 2


def test_evento_mal_escrito_sale_con_error_de_uso():
    with pytest.raises(SystemExit) as exc:
        cli.main(["cronograma", "--publicacion", "2026-11-06", "--evento", "info"])
    assert exc.value.code == 2


def test_periodo_json_calcula_los_hitos(capsys):
    datos = json_de(capsys, ["periodo", "--inicio", "2026-01-01", "--anios", "5", "--hoy", "2030-06-01", "--json"])
    assert datos["termino"] == "2030-12-31"
    hitos = {h["id"]: h["fecha"] for h in datos["hitos"]}
    assert hitos == {"comunicacion_sunass": "2029-05-31", "solicitud_eps": "2030-02-28"}
    assert datos["clase"] == "rojo"


def test_periodo_con_solicitud_presentada_cierra_el_semaforo(capsys):
    datos = json_de(capsys, ["periodo", "--inicio", "2026-01-01", "--anios", "5", "--hoy", "2030-06-01",
                             "--solicitud", "--json"])
    assert datos["clase"] == "fin" and datos["texto"] == "En revisión periódica"


def test_periodo_rechaza_duraciones_fuera_de_la_ley():
    with pytest.raises(SystemExit) as exc:
        cli.main(["periodo", "--inicio", "2026-01-01", "--anios", "2"])
    assert exc.value.code == 2


def test_tablero_sin_base_de_datos_explica_que_hacer(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(D, "DB", tmp_path / "no-existe.db")
    assert cli.main(["tablero"]) == 2
    assert "no hay base de datos" in capsys.readouterr().err.lower()


def test_tablero_lee_la_base_y_filtra(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(D, "DB", tmp_path / "prueba.db")
    D.iniciar(forzar_demo=True)
    filas = json_de(capsys, ["tablero", "--hoy", "2026-11-11", "--json"])
    assert len(filas) == len(D.lista_eps())
    assert all(f["clase"] in ("rojo", "ambar", "verde", "fin", "sin") for f in filas)

    rojas = json_de(capsys, ["tablero", "--hoy", "2026-11-11", "--clase", "rojo", "--json"])
    assert rojas and all(f["clase"] == "rojo" for f in rojas)

    una = json_de(capsys, ["tablero", "--hoy", "2026-11-11", "--eps", "sedapal", "--json"])
    assert len(una) == 1 and una[0]["eps"] == "sedapal"


def test_tablero_con_eps_inexistente_falla(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(D, "DB", tmp_path / "prueba.db")
    D.iniciar(forzar_demo=True)
    assert cli.main(["tablero", "--eps", "no-existe"]) == 2
    assert "no-existe" in capsys.readouterr().err


def test_ics_escribe_el_archivo(tmp_path, capsys):
    destino = tmp_path / "plazos.ics"
    assert cli.main(["ics", "--publicacion", "2026-11-06", "--eps", "SEDAPAL",
                     "--salida", str(destino)]) == 0
    texto = destino.read_text(encoding="utf-8")
    assert texto.startswith("BEGIN:VCALENDAR") and texto.count("BEGIN:VEVENT") == 7
    assert "SEDAPAL" in texto
    assert "7 citas" in capsys.readouterr().out


def test_ics_por_salida_estandar(capsys):
    assert cli.main(["ics", "--inicio", "2026-01-01", "--anios", "5"]) == 0
    assert "BEGIN:VCALENDAR" in capsys.readouterr().out


def test_ics_sin_origen_sale_con_error(capsys):
    assert cli.main(["ics"]) == 2
    assert "publicacion" in capsys.readouterr().err


def test_ics_del_periodo_incluye_hitos(capsys):
    assert cli.main(["ics", "--inicio", "2026-01-01", "--anios", "5", "--eps", "SEDAPAL"]) == 0
    salida = capsys.readouterr().out
    assert salida.count("BEGIN:VEVENT") == 4        # inicio, 2 hitos, término
    assert "DTSTART;VALUE=DATE:20300228" in salida


def test_fichero_de_salida_en_directorio_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError):
        cli.main(["ics", "--publicacion", "2026-11-06", "--salida", str(tmp_path / "no" / "hay" / "x.ics")])


def test_fecha_por_defecto_es_hoy(capsys):
    datos = json_de(capsys, ["cronograma", "--publicacion", "2026-11-06", "--json"])
    assert datos["hoy"] == date.today().isoformat()


def test_los_comandos_no_tocan_la_base_para_el_cronograma(tmp_path, monkeypatch):
    """`cronograma` y `periodo` son puro cálculo: no deben abrir ni crear la base."""
    monkeypatch.setattr(D, "DB", tmp_path / "intacta.db")
    assert cli.main(["cronograma", "--publicacion", "2026-11-06"]) == 0
    assert cli.main(["periodo", "--inicio", "2026-01-01", "--anios", "5"]) == 0
    assert not Path(D.DB).exists()
