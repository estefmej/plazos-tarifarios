"""Pruebas de la extracción (sin llamar a ninguna API real)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["PLAZOS_DB"] = str(Path(__file__).parent / "_prueba.db")
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "LLM_PROVIDER"):
    os.environ.pop(k, None)

from ai import extraer_resolucion as X  # noqa: E402
from ai import llm  # noqa: E402
from backend import datos as D  # noqa: E402

D.iniciar(forzar_demo=True)
FIX = Path(__file__).parent / "fixtures"


def test_lector_basico_sin_ia():
    x = X.extraer((FIX / "resolucion_ficticia_inicio.pdf").read_bytes())
    assert x.modo == "basico"
    assert x.datos["tipo"] == "inicio_revision_periodica"
    assert x.datos["fecha_emision"] == "2026-11-04"
    assert x.datos["fecha_publicacion_el_peruano"] == "2026-11-06"
    assert x.eps_id == "ilo"


def test_con_ia_simulada(monkeypatch):
    respuesta = {"es_resolucion": True, "tipo": "ampliacion_plazo", "numero_resolucion": "N.° 100-2026",
                 "eps": "Sedapal S.A.", "fecha_emision": "09/11/2026",
                 "fecha_publicacion_el_peruano": "2026-11-10", "dias_habiles_adicionales": "15",
                 "etapa_afectada": "info", "resumen": "Amplía el plazo.", "evidencia": []}
    monkeypatch.setattr(llm, "disponible", lambda: True)
    monkeypatch.setattr(llm, "pedir_json", lambda *a, **k: dict(respuesta))
    x = X.extraer((FIX / "resolucion_ficticia_ampliacion.pdf").read_bytes())
    assert x.modo == "ia"
    assert x.eps_id == "sedapal"
    assert x.datos["fecha_emision"] == "2026-11-09"
    assert x.datos["dias_habiles_adicionales"] == 15


def test_json_tolerante():
    assert llm._json_tolerante('Aquí va:\n```json\n{"a": 1}\n```') == {"a": 1}
