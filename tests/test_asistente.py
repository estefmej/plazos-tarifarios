"""Prueba el ciclo de herramientas del asistente con un cliente de Claude simulado."""
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["PLAZOS_DB"] = str(Path(__file__).parent / "_prueba.db")

import anthropic  # noqa: E402

from ai.asistente import Asistente  # noqa: E402
from backend import datos as D  # noqa: E402

D.iniciar(forzar_demo=True)


class Bloque(SimpleNamespace):
    def model_dump(self):
        return dict(self.__dict__)


class ClienteFalso:
    def __init__(self, *a, **k):
        self.llamadas = 0
        self.messages = self

    def create(self, **kw):
        self.llamadas += 1
        if self.llamadas == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[
                Bloque(type="tool_use", id="t1", name="plazos_eps", input={"eps_id": "sedapal"})])
        ultimo = kw["messages"][-1]["content"][0]
        assert ultimo["type"] == "tool_result" and "SEDAPAL" in ultimo["content"]
        return SimpleNamespace(stop_reason="end_turn", content=[Bloque(type="text", text="SEDAPAL va por vencer.")])


def test_ciclo_de_herramientas(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "falsa")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(anthropic, "Anthropic", ClienteFalso)
    a = Asistente(date(2026, 11, 10))
    r = a.responder([{"role": "user", "content": "¿Plazos de SEDAPAL?"}])
    assert r == "SEDAPAL va por vencer."
    assert a.consultadas == ["sedapal"]


def test_modo_basico(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    a = Asistente(date(2026, 11, 10))
    r = a.responder([{"role": "user", "content": "plazos de EPS Grau"}])
    assert "EPS Grau" in r and "atraso" in r
