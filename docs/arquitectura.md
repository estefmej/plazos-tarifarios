# Arquitectura

```mermaid
flowchart TD
    A[Resolución en PDF<br/>publicada en El Peruano] --> B[Lectura del PDF<br/>pypdf, texto por página]
    B --> C[Modelo de lenguaje<br/>Claude o Gemini<br/>salida JSON + evidencia]
    C --> D[Revisión humana<br/>la persona confirma los datos]
    D --> E[Motor de plazos<br/>reglas fijas: días hábiles y feriados]
    E --> F[(SQLite<br/>EPS, procedimientos, eventos)]
    F --> G[Mapa y vencimientos]
    F --> H[Línea de tiempo]
    F --> I[Asistente<br/>agente con herramientas]
    I -- llama a --> E
```

## Decisiones de diseño

**La IA lee, las reglas calculan.** El modelo de lenguaje solo extrae datos del documento (EPS, número, fechas, días de ampliación). Las fechas límite las calcula `backend/plazos.py` con reglas fijas y el calendario de feriados. Un plazo legal no puede depender de lo que "adivine" un modelo, y así cada fecha se puede verificar a mano.

**Siempre hay revisión humana.** La extracción se muestra con su evidencia (cita literal y página) y la persona confirma antes de guardar.

**El asistente no inventa fechas.** Es un agente con herramientas (`ai/asistente.py`): busca la EPS, consulta el motor de plazos y redacta la respuesta con lo que devuelve.

## Modelos y por qué

| Uso | Modelo por defecto | Motivo |
|---|---|---|
| Extracción de resoluciones | `claude-haiku-5-5` o `gemini-2.5-flash` | Documentos cortos y tarea estructurada: un modelo rápido y barato basta. Gemini tiene capa gratuita. |
| Asistente | El mismo | Respuestas breves con uso de herramientas. |

Se cambia con `LLM_PROVIDER` y `LLM_MODEL` en `.env`. Las resoluciones son públicas, así que no hay problema de confidencialidad al enviarlas a una API.

## Archivos clave

| Archivo | Qué hace |
|---|---|
| `app/app.py` | Interfaz en Streamlit: tablero, línea de tiempo, registro de resoluciones, asistente y equipo |
| `ai/extraer_resolucion.py` | PDF → texto → JSON validado → EPS del catálogo |
| `ai/prompts/extraccion.md` | Instrucciones de extracción |
| `ai/asistente.py` | Agente con herramientas sobre el motor de plazos |
| `ai/llm.py` | Capa común para Claude y Gemini |
| `backend/plazos.py` | Motor de plazos en días hábiles |
| `backend/periodos.py` | Periodo regulatorio: inicio, término e hitos de 19 y 10 meses |
| `backend/datos.py` | Base de datos SQLite |
| `data/reglas/` | Etapas, plazos, feriados y reglas del periodo regulatorio (editables sin tocar código) |
| `tests/` | Pruebas del motor, la extracción y el agente |
