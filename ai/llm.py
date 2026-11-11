"""Capa delgada para hablar con un modelo de lenguaje: Claude (Anthropic) o Gemini (Google).

Se elige con la variable LLM_PROVIDER ("anthropic" o "gemini"). Si no se define,
se usa el proveedor cuya clave esté configurada. Las claves se leen del entorno
(archivo .env en local, o Secrets en Streamlit Community Cloud) y nunca van en el código.
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

MODELO_ANTHROPIC = "claude-haiku-5-5"   # rápido y barato; cambia con LLM_MODEL
MODELO_GEMINI = "gemini-2.5-flash"      # tiene capa gratuita; cambia con LLM_MODEL


class SinModelo(RuntimeError):
    """No hay clave de API configurada."""


@dataclass
class Herramienta:
    """Una función de la app que el modelo puede llamar."""
    nombre: str
    descripcion: str
    parametros: dict            # JSON Schema de un objeto
    funcion: Callable[..., Any]


def proveedor() -> str | None:
    p = os.getenv("LLM_PROVIDER", "").strip().lower()
    if p in ("anthropic", "gemini"):
        return p
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "gemini"
    return None


def modelo() -> str:
    return os.getenv("LLM_MODEL") or (MODELO_ANTHROPIC if proveedor() == "anthropic" else MODELO_GEMINI)


def disponible() -> bool:
    return proveedor() is not None


def descripcion() -> str:
    p = proveedor()
    return f"{'Claude' if p == 'anthropic' else 'Gemini'} ({modelo()})" if p else "sin modelo configurado"


def _json_tolerante(texto: str) -> dict:
    """Extrae el primer objeto JSON de la respuesta, aunque venga entre ``` o con texto alrededor."""
    t = texto.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1)
    ini, fin = t.find("{"), t.rfind("}")
    if ini == -1 or fin == -1:
        raise ValueError("El modelo no devolvió JSON.")
    return json.loads(t[ini:fin + 1])


# ---------- JSON estructurado (extracción) ----------

def pedir_json(instrucciones: str, texto: str = "", pdf: bytes | None = None, max_tokens: int = 2000) -> dict:
    """Pide al modelo un objeto JSON. Si `pdf` viene, el modelo lee el PDF directamente."""
    p = proveedor()
    if p is None:
        raise SinModelo("Configura ANTHROPIC_API_KEY o GEMINI_API_KEY en el archivo .env")

    if p == "anthropic":
        import anthropic
        cliente = anthropic.Anthropic()
        contenido: list[dict] = []
        if pdf:
            contenido.append({"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf).decode()}})
        contenido.append({"type": "text", "text": instrucciones + ("\n\n" + texto if texto else "")})
        r = cliente.messages.create(model=modelo(), max_tokens=max_tokens,
                                    messages=[{"role": "user", "content": contenido}])
        return _json_tolerante("".join(b.text for b in r.content if b.type == "text"))

    from google import genai
    from google.genai import types
    cliente = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    partes: list[Any] = []
    if pdf:
        partes.append(types.Part.from_bytes(data=pdf, mime_type="application/pdf"))
    partes.append(instrucciones + ("\n\n" + texto if texto else ""))
    r = cliente.models.generate_content(
        model=modelo(), contents=partes,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0))
    return _json_tolerante(r.text or "")


# ---------- conversación con herramientas (asistente) ----------

def conversar(sistema: str, historial: list[dict], herramientas: list[Herramienta],
              max_rondas: int = 6) -> str:
    """Conversación con uso de herramientas. `historial` = [{"role": "user"|"assistant", "content": str}].

    El modelo decide qué herramientas llamar; aquí se ejecutan y se le devuelven los
    resultados hasta que responde con texto.
    """
    p = proveedor()
    if p is None:
        raise SinModelo("Configura ANTHROPIC_API_KEY o GEMINI_API_KEY en el archivo .env")
    por_nombre = {h.nombre: h for h in herramientas}

    def ejecutar(nombre: str, args: dict) -> str:
        try:
            return json.dumps(por_nombre[nombre].funcion(**(args or {})), ensure_ascii=False, default=str)
        except Exception as e:  # el error vuelve al modelo para que se corrija
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    if p == "anthropic":
        import anthropic
        cliente = anthropic.Anthropic()
        tools = [{"name": h.nombre, "description": h.descripcion, "input_schema": h.parametros}
                 for h in herramientas]
        mensajes: list[dict] = [{"role": m["role"], "content": m["content"]} for m in historial]
        for _ in range(max_rondas):
            r = cliente.messages.create(model=modelo(), max_tokens=1200, system=sistema,
                                        tools=tools, messages=mensajes)
            if r.stop_reason != "tool_use":
                return "".join(b.text for b in r.content if b.type == "text").strip()
            mensajes.append({"role": "assistant", "content": [b.model_dump() for b in r.content]})
            mensajes.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": ejecutar(b.name, b.input)}
                for b in r.content if b.type == "tool_use"]})
        return "No pude completar la respuesta. Intenta con una pregunta más específica."

    from google import genai
    from google.genai import types
    cliente = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    decl = [types.FunctionDeclaration(name=h.nombre, description=h.descripcion, parameters_json_schema=h.parametros)
            for h in herramientas]
    config = types.GenerateContentConfig(
        system_instruction=sistema, tools=[types.Tool(function_declarations=decl)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    contenidos: list[Any] = [
        types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part(text=m["content"])])
        for m in historial]
    for _ in range(max_rondas):
        r = cliente.models.generate_content(model=modelo(), contents=contenidos, config=config)
        llamadas = r.function_calls or []
        if not llamadas:
            return (r.text or "").strip()
        contenidos.append(r.candidates[0].content)
        contenidos.append(types.Content(role="user", parts=[
            types.Part.from_function_response(name=c.name, response={"resultado": ejecutar(c.name, dict(c.args or {}))})
            for c in llamadas]))
    return "No pude completar la respuesta. Intenta con una pregunta más específica."
