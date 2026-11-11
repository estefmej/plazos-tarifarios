"""Plazos tarifarios: control de los plazos de los procedimientos tarifarios de las EPS.

Ejecutar desde la raíz del repo:  streamlit run app/app.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Plazos tarifarios", page_icon="💧", layout="wide")

# Claves: .env en local, Secrets en Streamlit Cloud. Nunca en el código.
try:
    from dotenv import load_dotenv
    load_dotenv(RAIZ / ".env")
except ImportError:
    pass
try:
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL"):
        if k in st.secrets and not os.getenv(k):
            os.environ[k] = str(st.secrets[k])
except Exception:
    pass

from ai import llm  # noqa: E402
from ai.asistente import Asistente  # noqa: E402
from ai.extraer_resolucion import extraer, extraer_url  # noqa: E402
from backend import datos as D  # noqa: E402
from backend import periodos as R  # noqa: E402
from backend import plazos as P  # noqa: E402

COLORES = {"rojo": "#B8432F", "ambar": "#C98A12", "verde": "#2F8A5B", "fin": "#5E7F94", "sin": "#AEBBC1"}
ETIQUETAS = {"rojo": "Vencido", "ambar": "Por vencer", "verde": "A tiempo", "fin": "Concluido", "sin": "Sin procedimiento"}
ETIQUETAS_PER = {"rojo": "Solicitud no registrada", "ambar": "Hito próximo", "verde": "Estudio tarifario vigente",
                 "fin": "En revisión o terminado", "sin": "Sin periodo"}
PESO = {"rojo": 0, "ambar": 1, "verde": 2, "fin": 3, "sin": 4}
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"]
TIPOS = {"inicio_revision_periodica": "Inicio de revisión periódica", "ampliacion_plazo": "Ampliación de plazo",
         "suspension_plazo": "Suspensión de plazo", "aprobacion_proyecto": "Aprobación del proyecto",
         "aprobacion_final": "Aprobación del estudio tarifario (periodo regulatorio)", "otro": "Otro"}

D.iniciar()
st.session_state.setdefault("sel", None)
st.session_state.setdefault("chat", [])


def fmt(d: date | None) -> str:
    return f"{d.day} {MESES[d.month - 1]} {d.year}" if d else "—"


def dias_txt(n: int | None) -> str:
    if n is None:
        return ""
    if n < 0:
        return f"{-n} días hábiles de atraso"
    return "vence hoy" if n == 0 else f"faltan {n} días hábiles"


# ---------------- barra lateral ----------------
with st.sidebar:
    st.header("Filtros")
    hoy = st.date_input("Ver el estado al", value=date.today(), format="DD/MM/YYYY")
    filas = D.tablero(hoy)
    deps = sorted({f.eps["departamento"] for f in filas})
    dep = st.selectbox("Departamento", ["Todo el Perú"] + deps)
    clases = st.multiselect("Estado", list(ETIQUETAS), format_func=ETIQUETAS.get, placeholder="Todos")
    st.divider()
    st.caption(f"Modelo de IA: **{llm.descripcion()}**")
    if not llm.disponible():
        st.warning("Sin clave de API: la lectura de PDF y el asistente funcionan en modo básico. "
                   "Configura `ANTHROPIC_API_KEY` o `GEMINI_API_KEY` en `.env`.")
    st.caption("Los procedimientos marcados como **demo** son sintéticos. "
               "Los registrados desde un PDF real aparecen como **pdf**.")
    if st.button("Restablecer datos de demostración"):
        D.iniciar(forzar_demo=True)
        st.session_state.sel = None
        st.rerun()

vis = [f for f in filas if (dep == "Todo el Perú" or f.eps["departamento"] == dep)
       and (not clases or f.estado.clase in clases)]

# ---------------- encabezado ----------------
st.title("Plazos tarifarios")
st.caption("Procedimientos de revisión periódica de las EPS, con plazos calculados en días hábiles desde "
           "la publicación en El Peruano. Etapas y plazos ilustrativos: ajústalos en `data/reglas/etapas.yaml`.")
cols = st.columns(5)
base = [f for f in filas if dep == "Todo el Perú" or f.eps["departamento"] == dep]
for col, (c, t) in zip(cols, ETIQUETAS.items()):
    col.metric(t, sum(f.estado.clase == c for f in base))

tab_tab, tab_per, tab_linea, tab_pdf, tab_chat, tab_equipo = st.tabs(
    ["Tablero", "Periodo regulatorio", "Línea de tiempo", "Registrar resolución", "Asistente", "Equipo"])


# ---------------- utilidades de vista ----------------
def tabla_cronograma(est: P.Estado) -> pd.DataFrame:
    return pd.DataFrame([{
        "Etapa": e.nombre,
        "Días hábiles": e.dias_habiles + e.dias_extra,
        "Ampliación": f"+{e.dias_extra}" if e.dias_extra else "",
        "Fecha límite": fmt(e.limite),
        "Situación": {"cumplida": "Cumplida", "en curso": "En curso", "vencida": "Vencida",
                      "pendiente": "Pendiente"}[e.situacion],
        "Cumplida el": fmt(e.fecha_cumplida) if e.cumplida else "",
    } for e in est.etapas])


def mostrar_eps(f: D.Fila, clave: str) -> None:
    est = f.estado
    st.subheader(f.eps["nombre"])
    st.caption(f"{f.eps['ciudad']}, {f.eps['departamento']}")
    color = COLORES[est.clase]
    linea = ""
    if est.actual is not None:
        e = est.etapas[est.actual]
        linea = f"<br><b>{e.nombre}</b> · límite {fmt(e.limite)} · {dias_txt(est.dias_restantes)}"
    st.markdown(f"<div style='border-left:6px solid {color};padding:8px 12px;background:{color}18'>"
                f"<b style='color:{color}'>{est.texto}</b>{linea}</div>", unsafe_allow_html=True)
    mostrar_periodo(f)
    if not f.proc:
        st.info("Esta EPS no tiene procedimiento registrado. Súbelo en la pestaña «Registrar resolución».")
        return
    st.write(f"Resolución de inicio: **{f.proc['resolucion']}** · publicada en El Peruano el "
             f"**{fmt(date.fromisoformat(f.proc['fecha_publicacion']))}** · fuente: `{f.proc['fuente']}` · "
             f"responsable: {f.proc['responsable'] or '—'} ({f.proc['subgerencia'] or '—'})")
    st.dataframe(tabla_cronograma(est), hide_index=True, use_container_width=True)
    if est.actual is not None:
        e = est.etapas[est.actual]
        with st.form(f"cumplir-{clave}-{f.eps['id']}"):
            st.write(f"¿Ya se cumplió la etapa **{e.nombre}**?")
            fc = st.date_input("Fecha de cumplimiento", value=min(hoy, date.today()), format="DD/MM/YYYY")
            nota = st.text_input("Documento que lo acredita (opcional)", placeholder="Ej.: Oficio N.° 123-2026-EPS")
            if st.form_submit_button("Marcar como cumplida"):
                D.registrar_evento(f.proc["id"], "etapa_cumplida", e.id, fecha=fc, nota=nota, fuente="manual")
                st.success("Etapa marcada como cumplida.")
                st.rerun()


def tabla_hitos(ep: R.EstadoPeriodo) -> pd.DataFrame:
    filas_h = [{"Hito": "Inicio del periodo regulatorio", "Fecha": fmt(ep.inicio), "Base": "Resolución de aprobación",
                "Situación": "Cumplido"}]
    for h in ep.hitos:
        filas_h.append({"Hito": h.nombre, "Fecha": fmt(h.fecha), "Base": h.base,
                        "Situación": "Cumplido" if h.cumplido else ("Próximo" if ep.proximo and h.id == ep.proximo.id
                                                                     else "Pendiente")})
    filas_h.append({"Hito": "Término del periodo regulatorio", "Fecha": fmt(ep.termino), "Base": "inicio + duración",
                    "Situación": "Cumplido" if ep.texto == "Periodo terminado" else "Pendiente"})
    return pd.DataFrame(filas_h)


def mostrar_periodo(f: D.Fila) -> None:
    ep, per = f.estado_periodo, f.periodo
    if not per or not ep or not ep.inicio:
        return
    with st.expander(f"Periodo regulatorio: {fmt(ep.inicio)} – {fmt(ep.termino)} · {ep.texto}", expanded=False):
        st.write(f"Aprobado por **{per['resolucion']}** · {per['anios']} años · fuente: `{per['fuente']}`")
        if per.get("archivo", "").startswith("http"):
            st.markdown(f"[Ver la norma en El Peruano]({per['archivo']})")
        if per.get("nota"):
            st.caption(per["nota"])
        st.dataframe(tabla_hitos(ep), hide_index=True, use_container_width=True)


# ---------------- pestaña: tablero ----------------
@st.cache_data
def geojson() -> dict:
    g = json.loads((RAIZ / "data" / "geo" / "peru_departamentos.geojson").read_text(encoding="utf-8"))
    for ft in g["features"]:
        ft["id"] = D.normalizar(ft["properties"]["NOMBDEP"])
    return g


def tip_mapa(f: D.Fila, modo: str) -> str:
    base = f"<b>{f.eps['nombre']}</b> · {f.eps['ciudad']}<br>"
    if modo == "per":
        ep = f.estado_periodo
        s = base + ep.texto
        if ep.inicio:
            s += f"<br>Periodo {fmt(ep.inicio)} – {fmt(ep.termino)}"
            if f.periodo and f.periodo.get("fuente") == "el_peruano":
                s += f"<br>{f.periodo['resolucion']} (El Peruano)"
        if ep.proximo:
            s += f"<br>{ep.proximo.nombre}: {fmt(ep.proximo.fecha)}"
        return s
    s = base + f.estado.texto
    if f.estado.actual is not None:
        s += f"<br>{f.estado.etapas[f.estado.actual].nombre}<br>{dias_txt(f.estado.dias_restantes)}"
    return s


def mapa(filas_mapa: list[D.Fila], todas: list[D.Fila], modo: str = "proc") -> go.Figure:
    """Mapa del Perú con Plotly (sin mosaicos externos, funciona sin conexión)."""
    g = geojson()
    fig = go.Figure()
    todos = [ft["id"] for ft in g["features"]]
    fig.add_trace(go.Choropleth(geojson=g, locations=todos, z=[0] * len(todos), showscale=False,
                                colorscale=[[0, "#EEF2F3"], [1, "#EEF2F3"]], marker_line_color="white",
                                marker_line_width=0.8, hoverinfo="skip", showlegend=False))
    clase = (lambda f: f.estado_periodo.clase) if modo == "per" else (lambda f: f.estado.clase)
    etiquetas = ETIQUETAS_PER if modo == "per" else ETIQUETAS
    peor: dict[str, str] = {}
    for f in todas:
        k = D.normalizar(f.eps["departamento"])
        if k not in peor or PESO[clase(f)] < PESO[peor[k]]:
            peor[k] = clase(f)
    for c in ("rojo", "ambar", "verde", "fin"):
        ids = [k for k, v in peor.items() if v == c]
        if ids:
            fig.add_trace(go.Choropleth(
                geojson=g, locations=ids, z=[1] * len(ids), colorscale=[[0, COLORES[c]], [1, COLORES[c]]],
                showscale=False, marker_opacity=0.25, marker_line_color="white", marker_line_width=0.8,
                hoverinfo="skip", showlegend=False))
    for c in ("sin", "fin", "verde", "ambar", "rojo"):
        xs = [f for f in filas_mapa if clase(f) == c]
        if not xs:
            continue
        fig.add_trace(go.Scattergeo(
            lat=[f.eps["lat"] for f in xs], lon=[f.eps["lon"] for f in xs], mode="markers",
            marker=dict(size=[max(8, min(26, (f.eps["conexiones"] or 1) ** 0.5 / 45)) for f in xs],
                        color=COLORES[c], line=dict(color="white", width=1)),
            customdata=[f.eps["id"] for f in xs], name=etiquetas[c],
            text=[tip_mapa(f, modo) for f in xs],
            hovertemplate="%{text}<extra></extra>"))
    fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator")
    fig.update_layout(height=680, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)",
                      geo_bgcolor="rgba(0,0,0,0)", legend=dict(orientation="h", y=1.02, x=0))
    return fig


with tab_tab:
    izq, der = st.columns([1.25, 1])
    with izq:
        modo_mapa = st.radio("Colorear el mapa por", ["Procedimiento en curso", "Periodo regulatorio"],
                             horizontal=True, label_visibility="collapsed")
        modo = "per" if modo_mapa == "Periodo regulatorio" else "proc"
        ev = st.plotly_chart(mapa(vis, filas, modo), use_container_width=True, on_select="rerun",
                             selection_mode="points", key="mapa")
        puntos = (ev or {}).get("selection", {}).get("points", []) if isinstance(ev, dict) else \
            getattr(getattr(ev, "selection", None), "points", [])
        if puntos and puntos[0].get("customdata"):
            st.session_state.sel = puntos[0]["customdata"]
        st.caption("Cada departamento toma el color de su EPS más urgente. Haz clic en un punto para ver su detalle.")
    with der:
        st.subheader("Próximos vencimientos")
        prox = sorted([f for f in vis if f.estado.actual is not None],
                      key=lambda f: (PESO[f.estado.clase], f.estado.dias_restantes))
        df = pd.DataFrame([{
            "": "🔴" if f.estado.clase == "rojo" else "🟠" if f.estado.clase == "ambar" else "🟢",
            "EPS": f.eps["nombre"], "Departamento": f.eps["departamento"],
            "Etapa": f.estado.etapas[f.estado.actual].nombre,
            "Límite": fmt(f.estado.etapas[f.estado.actual].limite),
            "Días hábiles": f.estado.dias_restantes, "Fuente": f.proc["fuente"] if f.proc else "",
        } for f in prox])
        if df.empty:
            st.info("No hay plazos en curso con estos filtros.")
        else:
            st.dataframe(df, hide_index=True, use_container_width=True, height=560)

    st.divider()
    nombres = {f.eps["id"]: f.eps["nombre"] for f in sorted(filas, key=lambda f: f.eps["nombre"])}
    ids = list(nombres)
    sel = st.selectbox("Ver detalle de una EPS", ids, format_func=nombres.get, placeholder="Elige una EPS",
                       index=ids.index(st.session_state.sel) if st.session_state.sel in ids else None)
    if sel:
        st.session_state.sel = sel
        mostrar_eps(next(f for f in filas if f.eps["id"] == sel), "tablero")


# ---------------- pestaña: periodo regulatorio ----------------
with tab_per:
    st.caption("Fuente «El Peruano (real)»: periodos tomados de resoluciones publicadas; «demo»: datos sintéticos.")
    st.caption("Plazos que se cuentan hacia atrás desde el término del periodo regulatorio: la Sunass comunica el "
               "siguiente periodo como máximo 19 meses antes (art. 12.1 y 38.2 del Reglamento General de Tarifas) y "
               "la EPS presenta su solicitud a más tardar 10 meses antes (art. 38.1). Reglas en `data/reglas/periodo.yaml`.")
    cols_p = st.columns(5)
    for col, (c, txt) in zip(cols_p, ETIQUETAS_PER.items()):
        col.metric(txt, sum(f.estado_periodo.clase == c for f in base))
    xs = sorted([f for f in vis if f.estado_periodo and f.estado_periodo.inicio],
                key=lambda f: (PESO[f.estado_periodo.clase], f.estado_periodo.dias if f.estado_periodo.dias is not None
                               else 99999))
    if not xs:
        st.info("No hay periodos con estos filtros.")
    else:
        def hito(f, hid):
            h = next((h for h in f.estado_periodo.hitos if h.id == hid), None)
            return fmt(h.fecha) if h else ""
        dfp = pd.DataFrame([{
            "": {"rojo": "🔴", "ambar": "🟠", "verde": "🟢", "fin": "🔵", "sin": "⚪"}[f.estado_periodo.clase],
            "EPS": f.eps["nombre"], "Departamento": f.eps["departamento"],
            "Periodo": f"{f.estado_periodo.inicio.year}–{f.estado_periodo.termino.year}",
            "Estado": f.estado_periodo.texto,
            "Sunass comunica (19 m)": hito(f, "comunicacion_sunass"),
            "EPS solicita (10 m)": hito(f, "solicitud_eps"),
            "Término": fmt(f.estado_periodo.termino),
            "Resolución": f.periodo["resolucion"],
            "Fuente": "El Peruano (real)" if f.periodo["fuente"] == "el_peruano" else
                      "PDF subido" if f.periodo["fuente"] == "pdf" else "demo",
        } for f in xs])
        st.dataframe(dfp, hide_index=True, use_container_width=True, height=420)

        gp = go.Figure()
        nombres_p = [f"{f.eps['nombre']} · {f.eps['departamento']}" for f in xs]
        gp.add_trace(go.Bar(
            y=nombres_p, x=[(f.estado_periodo.termino - f.estado_periodo.inicio).days * 86400000 for f in xs],
            base=[f.estado_periodo.inicio.isoformat() for f in xs], orientation="h",
            marker_color=[COLORES[f.estado_periodo.clase] for f in xs], opacity=0.35, name="Periodo regulatorio",
            hovertext=[f"{fmt(f.estado_periodo.inicio)} – {fmt(f.estado_periodo.termino)}" for f in xs],
            hoverinfo="text+y"))
        for hid, simbolo, nombre in (("comunicacion_sunass", "diamond", "Sunass comunica (19 m)"),
                                     ("solicitud_eps", "triangle-up", "EPS solicita (10 m)")):
            pts = [(n, next(h for h in f.estado_periodo.hitos if h.id == hid)) for n, f in zip(nombres_p, xs)]
            gp.add_trace(go.Scatter(y=[n for n, _ in pts], x=[h.fecha for _, h in pts], mode="markers", name=nombre,
                                    marker=dict(symbol=simbolo, size=11, color="#172A33"),
                                    hovertext=[f"{nombre}: {fmt(h.fecha)}" for _, h in pts], hoverinfo="text"))
        gp.add_shape(type="line", x0=hoy, x1=hoy, y0=0, y1=1, yref="paper", line=dict(color="#B8432F", dash="dash"))
        gp.add_annotation(x=hoy, y=1.02, yref="paper", text="Hoy", showarrow=False, font=dict(color="#B8432F"))
        gp.update_layout(barmode="overlay", xaxis_type="date", height=max(420, 26 * len(xs) + 120),
                         margin=dict(l=0, r=0, t=30, b=0), legend=dict(orientation="h", y=-0.08))
        gp.update_yaxes(autorange="reversed")
        st.plotly_chart(gp, use_container_width=True)


# ---------------- pestaña: línea de tiempo ----------------
with tab_linea:
    xs = [f for f in vis if f.estado.etapas]
    if not xs:
        st.info("No hay procedimientos con estos filtros.")
    else:
        orden = sorted(xs, key=lambda f: (f.eps["departamento"], PESO[f.estado.clase]))
        filas_g = []
        for f in orden:
            for i, e in enumerate(f.estado.etapas[1:], start=1):
                sit = e.situacion
                color = ("Cumplida" if sit == "cumplida" else
                         ETIQUETAS[f.estado.clase] if i == f.estado.actual else "Pendiente")
                filas_g.append({"EPS": f"{f.eps['nombre']} · {f.eps['departamento']}", "Etapa": e.nombre,
                                "Inicio": e.desde, "Fin": e.limite + timedelta(days=1), "Situación": color,
                                "Límite": fmt(e.limite)})
        dfg = pd.DataFrame(filas_g)
        mapa_color = {"Cumplida": "#B9C6CD", "Pendiente": "#E8EDEF", **{ETIQUETAS[c]: COLORES[c] for c in COLORES}}
        fig = px.timeline(dfg, x_start="Inicio", x_end="Fin", y="EPS", color="Situación",
                          color_discrete_map=mapa_color, hover_data={"Etapa": True, "Límite": True,
                                                                     "Inicio": False, "Fin": False})
        fig.update_yaxes(autorange="reversed", title=None)
        fig.update_traces(marker_line_color="white", marker_line_width=1)
        fig.add_shape(type="line", x0=hoy, x1=hoy, y0=0, y1=1, yref="paper", line=dict(color="#B8432F", dash="dash"))
        fig.add_annotation(x=hoy, y=1.02, yref="paper", text="Hoy", showarrow=False, font=dict(color="#B8432F"))
        fig.update_layout(height=max(420, 26 * len(xs) + 120), margin=dict(l=0, r=0, t=30, b=0),
                          legend=dict(orientation="h", y=-0.05), xaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)


# ---------------- pestaña: registrar resolución ----------------
with tab_pdf:
    st.write("Sube una resolución publicada en El Peruano. La IA extrae la EPS, el número y las fechas; "
             "tú revisas los datos y el motor de reglas calcula los plazos.")
    ejemplos = sorted((RAIZ / "data" / "resoluciones").glob("*.pdf"))
    c1, c2 = st.columns([1.2, 1])
    archivo = c1.file_uploader("Resolución en PDF", type=["pdf"])
    ejemplo = c2.selectbox("…o usa una resolución del repositorio", ["—"] + [p.name for p in ejemplos])
    pdf_bytes, nombre_pdf = None, ""
    if archivo is not None:
        pdf_bytes, nombre_pdf = archivo.getvalue(), archivo.name
    elif ejemplo != "—":
        pdf_bytes, nombre_pdf = (RAIZ / "data" / "resoluciones" / ejemplo).read_bytes(), ejemplo

    if pdf_bytes and st.button("Leer con IA" if llm.disponible() else "Leer resolución (modo básico)", type="primary"):
        with st.spinner("Leyendo la resolución…"):
            try:
                st.session_state.extraccion = (nombre_pdf, extraer(pdf_bytes))
            except Exception as e:
                st.error(f"No se pudo leer el documento: {e}")

    st.write("**…o pega el enlace de la norma en El Peruano** (busquedas.elperuano.pe/dispositivo/NL/…)")
    u1, u2 = st.columns([3, 1])
    url_norma = u1.text_input("Enlace de El Peruano", label_visibility="collapsed",
                              placeholder="https://busquedas.elperuano.pe/dispositivo/NL/2332109-1")
    if u2.button("Leer enlace", use_container_width=True, disabled=not url_norma):
        with st.spinner("Descargando la norma de El Peruano y leyéndola…"):
            try:
                st.session_state.extraccion = (url_norma.strip(), extraer_url(url_norma.strip()))
            except Exception as e:
                st.error(f"No se pudo leer el enlace: {e}")

    if "extraccion" in st.session_state:
        nombre_pdf, x = st.session_state.extraccion
        d = x.datos
        st.success(f"{'Leído con ' + llm.descripcion() if x.modo == 'ia' else 'Lectura básica sin IA'} · "
                   f"{x.paginas} página(s) · {nombre_pdf}")
        for a in x.avisos:
            st.warning(a)
        if d.get("resumen"):
            st.info(d["resumen"])
        if d.get("evidencia"):
            with st.expander("Evidencia: dónde lo encontró la IA"):
                for ev_ in d["evidencia"]:
                    st.markdown(f"- **{ev_.get('campo')}** (pág. {ev_.get('pagina', '?')}): “{ev_.get('cita')}”")
        with st.expander("Texto extraído del PDF"):
            st.text(x.texto[:6000])

        st.subheader("Revisa y confirma")
        catalogo = D.lista_eps()
        ids_eps = [e["id"] for e in catalogo]
        nombres_eps = {e["id"]: f"{e['nombre']} ({e['departamento']})" for e in catalogo}
        a, b = st.columns(2)
        tipo = a.selectbox("Tipo de resolución", list(TIPOS), format_func=TIPOS.get,
                           index=list(TIPOS).index(d.get("tipo")) if d.get("tipo") in TIPOS else 0)
        eps_id = b.selectbox("EPS", ids_eps, format_func=nombres_eps.get,
                             index=ids_eps.index(x.eps_id) if x.eps_id in ids_eps else 0)
        if x.eps_id:
            b.caption(f"EPS leída: «{d.get('eps') or '—'}» · coincidencia {int(x.eps_confianza * 100)} %")
        numero = a.text_input("Número de resolución", value=d.get("numero_resolucion") or "")
        f_emi = b.date_input("Fecha de emisión", value=date.fromisoformat(d["fecha_emision"])
                             if d.get("fecha_emision") else None, format="DD/MM/YYYY")
        f_pub = a.date_input("Fecha de publicación en El Peruano", value=date.fromisoformat(
            d["fecha_publicacion_el_peruano"]) if d.get("fecha_publicacion_el_peruano") else None, format="DD/MM/YYYY")
        b.caption("Los plazos se cuentan desde la publicación, no desde la emisión.")

        proc = D.procedimiento_de_eps(eps_id)
        etapas_ids = [e.id for e in P.etapas()][1:]
        if tipo == "inicio_revision_periodica":
            r1, r2 = st.columns(2)
            resp = r1.text_input("Responsable", value="")
            subg = r2.text_input("Subgerencia", value="")
            if proc and proc["fuente"] == "demo":
                st.caption("Esta EPS tiene un procedimiento de demostración: se reemplazará por esta resolución real.")
            if f_pub:
                st.write("**Cronograma que se calculará:**")
                st.dataframe(tabla_cronograma(P.estado(f_pub, [], hoy)), hide_index=True, use_container_width=True)
            if st.button("Guardar y calcular plazos", type="primary", disabled=f_pub is None):
                D.registrar_inicio(eps_id, numero, f_emi, f_pub, resp, subg, nombre_pdf)
                st.session_state.sel = eps_id
                del st.session_state.extraccion
                st.success("Procedimiento registrado. Revísalo en el tablero.")
                st.rerun()
        elif tipo in ("ampliacion_plazo", "suspension_plazo"):
            if not proc:
                st.error("Esta EPS no tiene un procedimiento registrado al cual aplicar la ampliación.")
            else:
                r1, r2 = st.columns(2)
                dias = r1.number_input("Días hábiles adicionales", min_value=1, max_value=365,
                                       value=int(d.get("dias_habiles_adicionales") or 15))
                etapa = r2.selectbox("Etapa afectada", etapas_ids, format_func=P.nombre_etapa,
                                     index=etapas_ids.index(d["etapa_afectada"]) if d.get("etapa_afectada")
                                     in etapas_ids else 0)
                evs = D.eventos_de_proc(proc["id"])
                antes = P.estado(date.fromisoformat(proc["fecha_publicacion"]), evs, hoy)
                nuevo = P.Evento("ampliacion" if tipo == "ampliacion_plazo" else "suspension", etapa, int(dias))
                despues = P.estado(date.fromisoformat(proc["fecha_publicacion"]), evs + [nuevo], hoy)
                comp = pd.DataFrame([{"Etapa": e0.nombre, "Límite actual": fmt(e0.limite),
                                      "Nuevo límite": fmt(e1.limite),
                                      "Cambio (días calendario)": (e1.limite - e0.limite).days or ""}
                                     for e0, e1 in zip(antes.etapas, despues.etapas)])
                st.write("**Así cambia el cronograma:**")
                st.dataframe(comp, hide_index=True, use_container_width=True)
                if st.button("Aplicar al cronograma", type="primary"):
                    D.registrar_evento(proc["id"], nuevo.tipo, etapa, dias=int(dias), resolucion=numero,
                                       nota=nombre_pdf, fuente="pdf")
                    st.session_state.sel = eps_id
                    del st.session_state.extraccion
                    st.success("Cronograma actualizado.")
                    st.rerun()
        elif tipo == "aprobacion_final":
            if not f_pub:
                st.error("Ingresa la fecha de publicación en El Peruano para calcular el periodo.")
            else:
                regla_doc = d.get("regla_inicio_periodo")
                regla = regla_doc or R.regla_inicio(f_pub)
                if regla == "anio_fiscal_siguiente":
                    ini_calc, expl = date(f_pub.year + 1, 1, 1), "primer día del año fiscal siguiente a la publicación"
                else:
                    ini_calc, expl = R.sumar_meses(f_pub.replace(day=1), 1), \
                        "primer día del mes calendario siguiente a la publicación"
                origen = "lo dice la propia resolución" if regla_doc else "regla del reglamento según la fecha de publicación"
                r1, r2 = st.columns(2)
                inicio_p = r1.date_input("Inicio del periodo regulatorio", value=ini_calc, format="DD/MM/YYYY")
                r1.caption(f"Calculado como {expl} ({origen}).")
                sugerida = d.get("duracion_anios") or R.duracion_desde_anios(
                    d.get("periodo_anio_inicio"), d.get("periodo_anio_fin"), ini_calc) or 5
                anios_p = r2.selectbox("Duración (años)", [3, 4, 5], index=[3, 4, 5].index(int(sugerida)),
                                       help="La Ley del Servicio Universal fija periodos de 3 a 5 años.")
                if d.get("periodo_anio_inicio"):
                    r2.caption(f"La resolución menciona el periodo {d['periodo_anio_inicio']}–"
                               f"{d.get('periodo_anio_fin') or '?'}.")
                    fin_prev = R.termino(inicio_p, int(anios_p))
                    if d.get("periodo_anio_fin") and fin_prev.year != d["periodo_anio_fin"]:
                        st.warning(f"Con {anios_p} años el periodo terminaría en {fin_prev.year}, pero la resolución "
                                   f"dice {d['periodo_anio_fin']}. Revisa la duración.")
                    if d["periodo_anio_inicio"] != inicio_p.year:
                        st.warning(f"El año de inicio que dice la resolución ({d['periodo_anio_inicio']}) no coincide "
                                   f"con el calculado ({inicio_p.year}). Revísalo antes de guardar.")
                ep_prev = R.estado(inicio_p, int(anios_p), hoy)
                st.write(f"**Periodo regulatorio:** {fmt(ep_prev.inicio)} – {fmt(ep_prev.termino)}")
                st.dataframe(tabla_hitos(ep_prev), hide_index=True, use_container_width=True)
                if st.button("Guardar periodo regulatorio", type="primary"):
                    D.registrar_periodo(eps_id, numero, f_pub, inicio_p, int(anios_p), nombre_pdf, expl)
                    st.session_state.sel = eps_id
                    del st.session_state.extraccion
                    st.success("Periodo registrado. Revísalo en la pestaña «Periodo regulatorio».")
                    st.rerun()
        else:
            st.info("Este tipo de resolución todavía no cambia el cronograma automáticamente. "
                    "Puedes marcar la etapa como cumplida desde el detalle de la EPS.")


# ---------------- pestaña: asistente ----------------
with tab_chat:
    sel_nombre = next((f.eps["nombre"] for f in filas if f.eps["id"] == st.session_state.sel), None)
    st.caption(f"Responde consultando el motor de plazos. EPS seleccionada: **{sel_nombre or 'ninguna'}**.")
    sugerencias = [f"¿Cuáles son los plazos de {sel_nombre}?" if sel_nombre else "¿Qué plazos tiene SEDAPAL?",
                   "¿Qué vence en los próximos 10 días hábiles?",
                   "¿Qué EPS no tienen registrada su solicitud?",
                   "¿Qué subgerencia tiene más plazos vencidos?"]
    cs = st.columns(len(sugerencias))
    pregunta = None
    for c, s in zip(cs, sugerencias):
        if c.button(s, use_container_width=True):
            pregunta = s

    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.write(m["content"])
            for eid in m.get("eps", []):
                f = next((x for x in D.tablero(hoy) if x.eps["id"] == eid), None)
                if f and f.estado.etapas:
                    st.caption(f"{f.eps['nombre']} · {f.estado.texto}")
                    st.dataframe(tabla_cronograma(f.estado), hide_index=True, use_container_width=True)

    escrita = st.chat_input("Pregunta por una EPS, por lo que vence o por la carga del equipo")
    pregunta = escrita or pregunta
    if pregunta:
        st.session_state.chat.append({"role": "user", "content": pregunta})
        asis = Asistente(hoy, st.session_state.sel)
        try:
            with st.spinner("Consultando el motor de plazos…"):
                resp = asis.responder([{"role": m["role"], "content": m["content"]} for m in st.session_state.chat])
        except Exception as e:
            resp, asis.consultadas = f"No se pudo completar la respuesta ({e}).", []
        st.session_state.chat.append({"role": "assistant", "content": resp, "eps": asis.consultadas[:3]})
        if len(asis.consultadas) == 1:
            st.session_state.sel = asis.consultadas[0]
        st.rerun()


# ---------------- pestaña: equipo ----------------
with tab_equipo:
    carga = Asistente(hoy).carga_equipo(20)
    if not carga:
        st.info("No hay plazos en curso.")
    else:
        dfc = pd.DataFrame(carga).rename(columns={"subgerencia": "Subgerencia", "responsable": "Responsable",
                                                  "vencidos": "Vencidos", "por_vencer": "Por vencer (20 d.h.)",
                                                  "en_curso": "En curso"})
        g = dfc.melt(id_vars=["Subgerencia", "Responsable"], value_vars=["Vencidos", "Por vencer (20 d.h.)"],
                     var_name="Tipo", value_name="Plazos")
        fig = px.bar(g, x="Responsable", y="Plazos", color="Tipo", facet_col="Subgerencia", barmode="stack",
                     color_discrete_map={"Vencidos": COLORES["rojo"], "Por vencer (20 d.h.)": COLORES["ambar"]})
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(dfc, hide_index=True, use_container_width=True)
