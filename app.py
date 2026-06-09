"""App web (Streamlit) para cribar estados financieros de empresas de la SEC.

Ejecutar:
    streamlit run app.py
"""

from __future__ import annotations

import io
import os

import pandas as pd
import streamlit as st

from client import SECClient, SECError
from frames import attach_tickers, build_dataset
from metrics import METRICS, RATIOS, metrics_needed_for
from screen import Filter, add_ratios, apply_filters

st.set_page_config(page_title="Cribado financiero SEC", page_icon="📊", layout="wide")

OPERATORS = [">=", ">", "<=", "<", "==", "!=", "between"]


# --------------------------------------------------------------------------- #
# Carga de datos (cacheada entre reruns de Streamlit)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False, ttl=60 * 60 * 6)
def load_dataset(
    contact: str,
    metric_keys: tuple[str, ...],
    year: int,
    quarter: int | None,
    include_yoy: bool,
    add_ticker: bool,
) -> pd.DataFrame:
    client = SECClient(contact=contact)
    df = build_dataset(client, list(metric_keys), year, quarter, include_yoy=include_yoy)
    if add_ticker and not df.empty:
        df = attach_tickers(df, client)
    return df


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="cribado")
    return buf.getvalue()


def default_contact() -> str:
    """Email de contacto para la SEC, leído de secretos/entorno (no del código).

    En Streamlit Cloud se define en Settings → Secrets como `sec_contact`.
    En local, vía variable de entorno SEC_CONTACT o `.streamlit/secrets.toml`.
    """
    try:
        if "sec_contact" in st.secrets:
            return str(st.secrets["sec_contact"])
    except Exception:
        pass  # no hay secrets.toml en local: es normal
    return os.environ.get("SEC_CONTACT", "")


# --------------------------------------------------------------------------- #
# Barra lateral: configuración de la consulta
# --------------------------------------------------------------------------- #
st.sidebar.header("⚙️ Configuración")

contact = st.sidebar.text_input(
    "Email de contacto (obligatorio para la SEC)",
    value=default_contact(),
    help="La SEC exige identificarse en la cabecera User-Agent. Usa un email real.",
)

col_y, col_q = st.sidebar.columns(2)
year = col_y.number_input("Año fiscal", min_value=2009, max_value=2025, value=2023, step=1)
period_kind = col_q.selectbox("Periodo", ["Anual", "Q1", "Q2", "Q3", "Q4"], index=0)
quarter = None if period_kind == "Anual" else int(period_kind[1])

st.sidebar.markdown("**Métricas a descargar**")
default_metrics = ["revenue", "net_income", "assets", "equity"]
selected_metrics = st.sidebar.multiselect(
    "Magnitudes",
    options=list(METRICS.keys()),
    default=default_metrics,
    format_func=lambda k: METRICS[k].label,
)

selected_ratios = st.sidebar.multiselect(
    "Ratios a calcular",
    options=list(RATIOS.keys()),
    default=["net_margin", "roe"],
    format_func=lambda k: RATIOS[k].label,
)

include_yoy = st.sidebar.checkbox(
    "Crecimiento interanual (YoY)", value=True,
    help="Descarga también el año anterior para métricas de flujo y calcula el % de variación.",
)
add_ticker = st.sidebar.checkbox("Añadir ticker bursátil", value=True)

# Las métricas necesarias para los ratios se descargan aunque no se marquen.
needed = set(selected_metrics) | metrics_needed_for(selected_ratios)
metric_keys = tuple(k for k in METRICS if k in needed)

run = st.sidebar.button("🔎 Cargar datos de la SEC", type="primary", width="stretch")


# --------------------------------------------------------------------------- #
# Cabecera y avisos
# --------------------------------------------------------------------------- #
st.title("📊 Cribado de estados financieros · SEC EDGAR")
st.caption(
    "Filtra el universo de empresas que reportan a la SEC usando la API de *frames* XBRL. "
    "Los datos provienen directamente de EDGAR y **no están homogeneizados**."
)

with st.expander("ℹ️ Cómo se manejan los datos no homogéneos (léeme)"):
    st.markdown(
        """
- Cada **magnitud lógica** (p. ej. *Ingresos*) se mapea a varias **etiquetas US-GAAP**
  candidatas. Para cada empresa se toma la primera disponible, y la columna
  `…__tag` indica de qué etiqueta salió cada valor.
- La API de *frames* alinea los periodos al **año natural** (`CY`). Empresas con
  ejercicio fiscal no-calendario se aproximan al trimestre natural más cercano,
  lo que puede mezclar contextos.
- **Hay errores en origen**: empresas que etiquetan mal una cifra (escala, signo,
  contexto). Verás márgenes imposibles (>100%) o valores absurdos. No se corrigen
  automáticamente: usa filtros de cordura (p. ej. *Margen neto entre -100 y 100*)
  y revisa la columna `…__tag`.
- Las partidas de **balance** son fotos a fin de periodo (`…Q4I` para el cierre anual).
        """
    )

if not contact or "@" not in contact:
    st.warning("Introduce un email de contacto válido en la barra lateral antes de cargar datos.")
    st.stop()


# --------------------------------------------------------------------------- #
# Estado: cargar dataset
# --------------------------------------------------------------------------- #
if run:
    if not metric_keys:
        st.error("Selecciona al menos una métrica o un ratio.")
        st.stop()
    try:
        with st.spinner("Descargando frames de la SEC (la primera vez tarda; luego usa caché)…"):
            data = load_dataset(contact, metric_keys, int(year), quarter, include_yoy, add_ticker)
        st.session_state["data"] = data
        st.session_state["params"] = (int(year), period_kind, selected_ratios)
    except SECError as exc:
        st.error(f"Error hablando con la SEC: {exc}")
        st.stop()

data: pd.DataFrame | None = st.session_state.get("data")

if data is None:
    st.info("Configura la consulta en la barra lateral y pulsa **Cargar datos de la SEC**.")
    st.stop()

if data.empty:
    st.warning("La SEC no devolvió datos para ese periodo/métricas. Prueba otro año o trimestre.")
    st.stop()

# Calcular ratios sobre lo descargado.
data = add_ratios(data, selected_ratios)


# --------------------------------------------------------------------------- #
# Construcción de filtros
# --------------------------------------------------------------------------- #
st.subheader("🎚️ Filtros de cribado")

# Columnas numéricas filtrables: métricas, sus YoY y ratios.
filterable: dict[str, str] = {}
for k in selected_metrics:
    if k in data.columns:
        filterable[k] = METRICS[k].label
    yoy = f"{k}__yoy"
    if yoy in data.columns:
        filterable[yoy] = f"{METRICS[k].label} (crec. % YoY)"
for rk in selected_ratios:
    if rk in data.columns:
        filterable[rk] = RATIOS[rk].label

if "n_filters" not in st.session_state:
    st.session_state["n_filters"] = 1

c_add, c_clear, _ = st.columns([1, 1, 4])
if c_add.button("➕ Añadir filtro"):
    st.session_state["n_filters"] += 1
if c_clear.button("🧹 Limpiar filtros"):
    st.session_state["n_filters"] = 1

filters: list[Filter] = []
filter_keys = list(filterable.keys())
for i in range(st.session_state["n_filters"]):
    c1, c2, c3, c4 = st.columns([3, 1.4, 2, 2])
    col = c1.selectbox(
        "Columna", options=["—"] + filter_keys,
        format_func=lambda k: "— (sin filtro)" if k == "—" else filterable.get(k, k),
        key=f"f_col_{i}",
    )
    op = c2.selectbox("Op.", options=OPERATORS, key=f"f_op_{i}")
    v1 = c3.number_input("Valor", value=0.0, key=f"f_v1_{i}", format="%g")
    v2 = None
    if op == "between":
        v2 = c4.number_input("y", value=0.0, key=f"f_v2_{i}", format="%g")
    else:
        c4.markdown("&nbsp;")
    if col != "—":
        filters.append(Filter(column=col, op=op, value=float(v1),
                              value2=float(v2) if v2 is not None else None))

result = apply_filters(data, filters)


# --------------------------------------------------------------------------- #
# Resultados
# --------------------------------------------------------------------------- #
st.subheader(f"📋 Resultados: {len(result)} de {len(data)} empresas")

# Ordenar columnas: identificación primero, luego métricas/ratios, luego __tag al final.
id_cols = [c for c in ["cik", "ticker", "entityName"] if c in result.columns]
tag_cols = [c for c in result.columns if c.endswith("__tag")]
mid_cols = [c for c in result.columns if c not in id_cols and c not in tag_cols]
ordered = result[id_cols + mid_cols + tag_cols]

show_tags = st.checkbox("Mostrar columnas de auditoría (etiqueta US-GAAP de origen)", value=False)
display = ordered if show_tags else ordered.drop(columns=tag_cols)

# Formato de columnas para legibilidad.
col_config: dict[str, object] = {}
for c in display.columns:
    if c in RATIOS or c.endswith("__yoy"):
        col_config[c] = st.column_config.NumberColumn(format="%.1f")
    elif c in METRICS and METRICS[c].unit == "USD":
        col_config[c] = st.column_config.NumberColumn(format="$%.0f")

st.dataframe(display, width="stretch", hide_index=True, column_config=col_config)

# Descargas (siempre con valores crudos, sin formato, mejor para análisis).
c_csv, c_xlsx, _ = st.columns([1, 1, 4])
c_csv.download_button(
    "⬇️ CSV", data=ordered.to_csv(index=False).encode("utf-8"),
    file_name=f"cribado_sec_{year}.csv", mime="text/csv", width="stretch",
)
c_xlsx.download_button(
    "⬇️ Excel", data=to_excel_bytes(ordered),
    file_name=f"cribado_sec_{year}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
)
