"""Asistente de plazos: un agente que responde preguntas usando herramientas.

El modelo NO calcula fechas: llama a estas funciones, que consultan la base de datos
y el motor de plazos, y redacta la respuesta con lo que devuelven.
"""
from __future__ import annotations

from datetime import date

from ai import llm
from backend import datos as D
from backend import plazos as P

SISTEMA = """Eres el asistente de plazos de una gerencia de regulación tarifaria del sector saneamiento en el Perú.
Respondes en español, en texto breve (2 a 6 líneas), claro y sin Markdown pesado.
Reglas:
- Nunca calcules fechas ni días hábiles por tu cuenta: usa siempre las herramientas.
- Si preguntan por una EPS, búscala con buscar_eps y luego usa plazos_eps con su id.
  La app muestra una tabla con el cronograma de cada EPS que consultes con plazos_eps,
  así que no repitas toda la tabla: resume estado, etapa actual, fecha límite y días hábiles.
- Si dicen "esta EPS", "la seleccionada" o no nombran ninguna, usa la EPS seleccionada.
- Para lo que vence pronto usa vencimientos; para un departamento usa eps_por_departamento
  (un departamento puede tener varias EPS); para la carga del equipo usa carga_equipo.
- Para el periodo regulatorio (inicio, término y los hitos de 19 y 10 meses antes del término) usa periodo_eps;
  para saber qué EPS tienen hitos próximos o no presentaron su solicitud usa hitos_periodo.
  Base: según los artículos del Reglamento General de Tarifas, se debe comunicar a la EPS el siguiente periodo como
  máximo 19 meses antes del término, y la EPS presenta su solicitud a más tardar 10 meses antes.
- No menciones el nombre de ninguna institución reguladora ni números de artículos; di "el regulador" o
  "los artículos del Reglamento General de Tarifas".
- Indica si un dato viene de datos de demostración (fuente "demo") o de una resolución real (fuente "pdf").
- Si una herramienta no devuelve algo, dilo; no inventes."""


def _fila_a_dict(f: D.Fila) -> dict:
    est = f.estado
    r = {"id": f.eps["id"], "nombre": f.eps["nombre"], "departamento": f.eps["departamento"],
         "estado": est.texto}
    if f.proc:
        r.update(resolucion=f.proc["resolucion"], publicada_en_el_peruano=f.proc["fecha_publicacion"],
                 fuente=f.proc["fuente"], responsable=f.proc["responsable"], subgerencia=f.proc["subgerencia"])
    if est.actual is not None:
        e = est.etapas[est.actual]
        r.update(etapa_actual=e.nombre, fecha_limite=e.limite.isoformat(), dias_habiles_restantes=est.dias_restantes)
    return r


class Asistente:
    def __init__(self, hoy: date, eps_seleccionada: str | None = None):
        self.hoy = hoy
        self.eps_seleccionada = eps_seleccionada
        self.consultadas: list[str] = []      # EPS cuya tabla se mostrará en la app

    # ----- herramientas -----
    def buscar_eps(self, texto: str) -> list[dict]:
        return [{"id": e["id"], "nombre": e["nombre"], "ciudad": e["ciudad"], "departamento": e["departamento"]}
                for e in D.buscar_eps(texto)[:10]]

    def plazos_eps(self, eps_id: str) -> dict:
        f = next((x for x in D.tablero(self.hoy) if x.eps["id"] == str(eps_id)), None)
        if f is None:
            raise ValueError(f"No existe la EPS con id '{eps_id}'. Usa buscar_eps primero.")
        if eps_id not in self.consultadas:
            self.consultadas.append(str(eps_id))
        r = _fila_a_dict(f)
        r["etapas"] = [{"etapa": e.nombre, "dias_habiles": e.dias_habiles + e.dias_extra,
                        "ampliacion": e.dias_extra or None, "fecha_limite": e.limite.isoformat(),
                        "situacion": e.situacion} for e in f.estado.etapas]
        return r

    def vencimientos(self, dias_habiles: int = 10, departamento: str | None = None) -> list[dict]:
        filas = [f for f in D.tablero(self.hoy) if f.estado.actual is not None
                 and f.estado.dias_restantes <= int(dias_habiles)
                 and (not departamento or D.normalizar(f.eps["departamento"]) == D.normalizar(departamento))]
        filas.sort(key=lambda f: f.estado.dias_restantes)
        return [_fila_a_dict(f) for f in filas]

    def eps_por_departamento(self, departamento: str) -> list[dict]:
        k = D.normalizar(departamento)
        return [_fila_a_dict(f) for f in D.tablero(self.hoy) if D.normalizar(f.eps["departamento"]) == k]

    def carga_equipo(self, dias_habiles: int = 20) -> list[dict]:
        carga: dict[tuple, dict] = {}
        for f in D.tablero(self.hoy):
            if f.estado.actual is None or not f.proc:
                continue
            k = (f.proc["subgerencia"] or "Sin asignar", f.proc["responsable"] or "Sin asignar")
            c = carga.setdefault(k, {"subgerencia": k[0], "responsable": k[1], "vencidos": 0,
                                     "por_vencer": 0, "en_curso": 0})
            c["en_curso"] += 1
            if f.estado.dias_restantes < 0:
                c["vencidos"] += 1
            elif f.estado.dias_restantes <= int(dias_habiles):
                c["por_vencer"] += 1
        return sorted(carga.values(), key=lambda c: (-c["vencidos"], -c["por_vencer"]))

    def periodo_eps(self, eps_id: str) -> dict:
        f = next((x for x in D.tablero(self.hoy) if x.eps["id"] == str(eps_id)), None)
        if f is None:
            raise ValueError(f"No existe la EPS con id '{eps_id}'. Usa buscar_eps primero.")
        ep = f.estado_periodo
        if not ep or not ep.inicio:
            return {"nombre": f.eps["nombre"], "estado": "Sin periodo registrado"}
        return {"nombre": f.eps["nombre"], "departamento": f.eps["departamento"], "estado": ep.texto,
                "resolucion": f.periodo["resolucion"], "fuente": f.periodo["fuente"], "anios": f.periodo["anios"],
                "inicio": ep.inicio.isoformat(), "termino": ep.termino.isoformat(),
                "hitos": [{"hito": h.nombre, "fecha": h.fecha.isoformat(), "base": h.base, "cumplido": h.cumplido}
                          for h in ep.hitos]}

    def hitos_periodo(self, dias: int = 120) -> list[dict]:
        salida = []
        for f in D.tablero(self.hoy):
            ep = f.estado_periodo
            if ep and ep.clase in ("rojo", "ambar") or (ep and ep.dias is not None and 0 <= ep.dias <= int(dias)):
                salida.append({"nombre": f.eps["nombre"], "departamento": f.eps["departamento"], "estado": ep.texto,
                               "hito": ep.proximo.nombre if ep.proximo else None,
                               "fecha": ep.proximo.fecha.isoformat() if ep.proximo else None,
                               "dias_calendario": ep.dias, "termino_periodo": ep.termino.isoformat()})
        return sorted(salida, key=lambda x: x["dias_calendario"] if x["dias_calendario"] is not None else 0)

    def herramientas(self) -> list[llm.Herramienta]:
        obj = lambda props, req: {"type": "object", "properties": props, "required": req}
        return [
            llm.Herramienta("buscar_eps", "Busca EPS por nombre, ciudad o parte del nombre. Devuelve id, nombre, "
                            "ciudad y departamento. Úsala antes de plazos_eps.",
                            obj({"texto": {"type": "string"}}, ["texto"]), self.buscar_eps),
            llm.Herramienta("plazos_eps", "Cronograma calculado de una EPS: cada etapa con días hábiles, fecha límite "
                            "y situación, más la etapa actual y los días hábiles restantes (negativo = atraso).",
                            obj({"eps_id": {"type": "string"}}, ["eps_id"]), self.plazos_eps),
            llm.Herramienta("vencimientos", "EPS cuya etapa actual vence dentro de N días hábiles o ya venció, "
                            "ordenadas por urgencia. Filtro opcional por departamento.",
                            obj({"dias_habiles": {"type": "integer"}, "departamento": {"type": "string"}},
                                ["dias_habiles"]), self.vencimientos),
            llm.Herramienta("eps_por_departamento", "Todas las EPS de un departamento con su estado y etapa actual.",
                            obj({"departamento": {"type": "string"}}, ["departamento"]), self.eps_por_departamento),
            llm.Herramienta("periodo_eps", "Periodo regulatorio vigente de una EPS: resolución, inicio, término y los "
                            "hitos de 19 y 10 meses antes del término, con su base legal.",
                            obj({"eps_id": {"type": "string"}}, ["eps_id"]), self.periodo_eps),
            llm.Herramienta("hitos_periodo", "EPS que no presentaron su solicitud a tiempo o que tienen un hito del "
                            "periodo regulatorio (19 o 10 meses antes del término) dentro de N días calendario.",
                            obj({"dias": {"type": "integer"}}, []), self.hitos_periodo),
            llm.Herramienta("carga_equipo", "Plazos en curso, por vencer y vencidos por subgerencia y responsable.",
                            obj({"dias_habiles": {"type": "integer"}}, []), self.carga_equipo),
        ]

    # ----- conversación -----
    def responder(self, historial: list[dict]) -> str:
        self.consultadas = []
        sel = next((e for e in D.lista_eps() if e["id"] == self.eps_seleccionada), None)
        contexto = (f"\nFecha de consulta: {self.hoy.isoformat()}."
                    f"\nEPS seleccionada en la app: {sel['nombre']} (id {sel['id']})." if sel
                    else f"\nFecha de consulta: {self.hoy.isoformat()}.\nEPS seleccionada en la app: ninguna.")
        if llm.disponible():
            return llm.conversar(SISTEMA + contexto, historial[-10:], self.herramientas())
        return self.responder_basico(historial[-1]["content"])

    def responder_basico(self, pregunta: str) -> str:
        """Respaldo sin IA: reconoce nombres de EPS y la palabra 'vence'."""
        t = D.normalizar(pregunta)
        hallados = [e for e in D.lista_eps() if D.normalizar(e["nombre"]) in t]
        if not hallados and self.eps_seleccionada and any(w in t for w in ("esta", "seleccion", "sus plazos")):
            hallados = [e for e in D.lista_eps() if e["id"] == self.eps_seleccionada]
        if hallados:
            lineas = []
            for e in hallados[:3]:
                r = self.plazos_eps(e["id"])
                if "etapa_actual" in r:
                    d = r["dias_habiles_restantes"]
                    lineas.append(f"{r['nombre']}: {r['estado'].lower()}. Etapa actual: {r['etapa_actual']}, "
                                  f"límite {r['fecha_limite']} "
                                  f"({'%d días hábiles de atraso' % -d if d < 0 else 'faltan %d días hábiles' % d}).")
                else:
                    lineas.append(f"{r['nombre']}: {r['estado'].lower()}.")
            return "\n".join(lineas) + "\n\n(Modo básico sin IA: configura una clave de API para preguntas libres.)"
        if "venc" in t:
            xs = self.vencimientos(10)
            return "Vencidos o por vencer en 10 días hábiles:\n" + "\n".join(
                f"- {x['nombre']}: {x['etapa_actual']} ({x['fecha_limite']})" for x in xs) + \
                "\n\n(Modo básico sin IA.)"
        return ("En modo básico solo entiendo preguntas que nombran una EPS o que piden lo que vence. "
                "Configura ANTHROPIC_API_KEY o GEMINI_API_KEY para hacer preguntas libres.")
