import streamlit as st
import pandas as pd
import gspread
from pathlib import Path
from datetime import datetime

# ─── Constants ────────────────────────────────────────────────────────────────

FIXED_FACTORS = {
    'SAC': 0.0833,
    'Plus Vacacional': 0.0078,
    'Plus Feriado': 0.0089,
}

FIXED_FACTORS_CO = {
    'alto': {
        'Contribuciones Empleador': 0.3002,
        'Prima/Cesantías/Int. Cesantías': 0.1674,
    },
    'bajo': {
        'Contribuciones Empleador': 0.1652,
        'Prima/Cesantías/Int. Cesantías': 0.1674,
    },
}

CONECTIVIDAD_CO_POR_EMPLEADO = 200_000

SPREADSHEET_ID = '1s0zzBQEfsBVR0D5jgVhDWedyyvpmrb2krYsrFz0Ht_c'
SHEET_AR = 'argentina'
SHEET_CO = 'colombia'

MESES = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
    5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
    9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
}

COLUMNS_AR = [
    'año', 'mes', 'rem9_f931', 'conectividad', 'salarios_brutos',
    'contrib_ss', 'contrib_os', 'art', 'seguro_vida', 'prepaga_total'
]

COLUMNS_CO = [
    'año', 'mes', 'salarios_brutos', 'empleados', 'prepaga_total',
]


# ─── Google Sheets ─────────────────────────────────────────────────────────────

def _get_gc():
    from google.oauth2.service_account import Credentials
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    except Exception:
        creds_file = Path(__file__).parent.parent / 'talentserviceproject-1ce2ed91696b.json'
        creds = Credentials.from_service_account_file(str(creds_file), scopes=scopes)
    return gspread.authorize(creds)


@st.cache_data(ttl=60)
def load_data_ar() -> pd.DataFrame:
    gc = _get_gc()
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET_AR)
        records = ws.get_all_records()
        return pd.DataFrame(records) if records else pd.DataFrame(columns=COLUMNS_AR)
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=COLUMNS_AR)


@st.cache_data(ttl=60)
def load_data_co() -> pd.DataFrame:
    gc = _get_gc()
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET_CO)
        records = ws.get_all_records()
        return pd.DataFrame(records) if records else pd.DataFrame(columns=COLUMNS_CO)
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=COLUMNS_CO)


# ─── Factor calculations ───────────────────────────────────────────────────────

def calculate_factor_ar(df: pd.DataFrame, year: int, up_to_month: int) -> dict:
    mask = (
        ((df['año'] == year) & (df['mes'] <= up_to_month)) |
        ((df['año'] == year - 1) & (df['mes'] == 12))
    )
    sub = df[mask]
    if sub.empty:
        return {}

    total_salarios = sub['salarios_brutos'].sum()
    if total_salarios == 0:
        return {}

    total_prepaga = sub['prepaga_total'].sum()
    total_cargas = (sub['contrib_ss'].sum() + sub['contrib_os'].sum() +
                    sub['art'].sum() + sub['seguro_vida'].sum())
    total_conectividad = sub['conectividad'].sum()

    ratios = {
        'SAC': FIXED_FACTORS['SAC'],
        'Plus Vacacional': FIXED_FACTORS['Plus Vacacional'],
        'Plus Feriado': FIXED_FACTORS['Plus Feriado'],
        'Prepaga': total_prepaga / total_salarios,
        'Conectividad': total_conectividad / total_salarios,
        'Cargas Sociales': total_cargas / total_salarios,
    }
    ratios['TOTAL'] = sum(ratios.values())

    return {
        **ratios,
        '_total_salarios': total_salarios,
        '_total_prepaga': total_prepaga,
        '_total_cargas': total_cargas,
        '_total_conectividad': total_conectividad,
    }


def calculate_factor_co(df: pd.DataFrame, year: int, up_to_month: int) -> dict:
    mask = (df['año'] == year) & (df['mes'] <= up_to_month)
    sub = df[mask]
    if sub.empty:
        return {}

    total_salarios = sub['salarios_brutos'].fillna(0).sum()
    if total_salarios == 0:
        return {}

    total_prepaga = sub['prepaga_total'].fillna(0).sum()
    total_empleados = int(sub['empleados'].fillna(0).sum())
    total_conectividad = total_empleados * CONECTIVIDAD_CO_POR_EMPLEADO

    prepaga_ratio = total_prepaga / total_salarios
    conectividad_ratio = total_conectividad / total_salarios

    bases = {
        '_total_salarios': total_salarios,
        '_total_prepaga': total_prepaga,
        '_total_conectividad': total_conectividad,
        '_total_empleados': total_empleados,
    }

    result = {}
    for group, fixed in FIXED_FACTORS_CO.items():
        ratios = {k: v for k, v in fixed.items()}
        ratios['Prepaga'] = prepaga_ratio
        ratios['Conectividad'] = conectividad_ratio
        ratios['TOTAL'] = sum(ratios.values())
        result[group] = {**ratios, **bases}

    return result


# ─── UI helpers ───────────────────────────────────────────────────────────────

def pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def num(v: float) -> str:
    return f"${v:,.2f}"


def num_cop(v: float) -> str:
    return f"${v:,.0f} COP"


# ─── App ──────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Factor Costo Empresa", layout="wide")
st.title("Factor Costo Empresa")
st.caption("Vista de consulta — solo lectura")

tab_ar, tab_co = st.tabs(["Argentina — Factor", "Colombia — Factor"])


# ── Tab AR Factor ─────────────────────────────────────────────────────────────
with tab_ar:
    st.subheader("Argentina — Factor acumulado")

    df_ar = load_data_ar()

    if df_ar.empty:
        st.info("No hay datos disponibles.")
    else:
        col_y, col_m = st.columns(2)
        years_ar = sorted(df_ar['año'].unique(), reverse=True)
        sel_year_ar = col_y.selectbox("Año", years_ar, key="sel_year_ar")

        avail_months_ar = sorted(df_ar[df_ar['año'] == sel_year_ar]['mes'].unique())
        sel_month_ar = col_m.selectbox(
            "Acumulado hasta", avail_months_ar,
            format_func=MESES.get, index=len(avail_months_ar) - 1, key="sel_month_ar"
        )

        result_ar = calculate_factor_ar(df_ar, sel_year_ar, sel_month_ar)

        if result_ar:
            st.markdown(f"### Factor — Acumulado a {MESES[sel_month_ar]} {sel_year_ar}")
            st.metric("Factor Total", pct(result_ar['TOTAL']))

            st.divider()
            st.markdown("#### Desglose")

            componentes = ['SAC', 'Plus Vacacional', 'Plus Feriado',
                           'Prepaga', 'Conectividad', 'Cargas Sociales', 'TOTAL']
            df_desglose = pd.DataFrame({
                'Componente': componentes,
                'Porcentaje': [pct(result_ar[c]) for c in componentes],
                'Factor':     [round(result_ar[c], 6) for c in componentes],
            })
            st.dataframe(df_desglose, use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("#### Bases acumuladas del período")
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("Salarios Brutos",  num(result_ar['_total_salarios']))
            b2.metric("Prepaga",          num(result_ar['_total_prepaga']))
            b3.metric("Cargas Sociales",  num(result_ar['_total_cargas']))
            b4.metric("Conectividad",     num(result_ar['_total_conectividad']))

            st.divider()
            st.markdown("#### Datos mensuales cargados")
            mask_show = (
                ((df_ar['año'] == sel_year_ar) & (df_ar['mes'] <= sel_month_ar)) |
                ((df_ar['año'] == sel_year_ar - 1) & (df_ar['mes'] == 12))
            )
            df_show = df_ar[mask_show].copy()
            df_show.insert(1, 'Mes', df_show['mes'].map(MESES))
            df_show = df_show.drop(columns=['mes'])
            st.dataframe(df_show, use_container_width=True, hide_index=True)


# ── Tab CO Factor ─────────────────────────────────────────────────────────────
with tab_co:
    st.subheader("Colombia — Factor acumulado")
    st.caption("Todos los montos en COP.")

    df_co = load_data_co()

    if df_co.empty:
        st.info("No hay datos disponibles.")
    else:
        col_y_cf, col_m_cf = st.columns(2)
        years_co = sorted(df_co['año'].unique(), reverse=True)
        sel_year_co = col_y_cf.selectbox("Año", years_co, key="sel_year_co")

        avail_months_co = sorted(df_co[df_co['año'] == sel_year_co]['mes'].unique())
        sel_month_co = col_m_cf.selectbox(
            "Acumulado hasta", avail_months_co,
            format_func=MESES.get, index=len(avail_months_co) - 1, key="sel_month_co"
        )

        result_co = calculate_factor_co(df_co, sel_year_co, sel_month_co)

        if not result_co:
            st.warning("No se pudo calcular el factor. Verificá que haya datos para el período seleccionado.")
        else:
            componentes_co = (
                list(FIXED_FACTORS_CO['alto'].keys()) + ['Prepaga', 'Conectividad', 'TOTAL']
            )
            labels_co = {
                'alto': 'Grupo A — Más de 10 SM',
                'bajo': 'Grupo B — Hasta 10 SM',
            }

            cols = st.columns(len(result_co))
            for idx, (group, res) in enumerate(result_co.items()):
                with cols[idx]:
                    st.markdown(f"### {labels_co[group]}")
                    st.metric("Factor Total", pct(res['TOTAL']))

                    st.markdown("**Desglose**")
                    df_desglose_co = pd.DataFrame({
                        'Componente': componentes_co,
                        'Porcentaje': [pct(res[c]) for c in componentes_co],
                        'Factor':     [round(res[c], 6) for c in componentes_co],
                    })
                    st.dataframe(df_desglose_co, use_container_width=True, hide_index=True)

                    st.markdown("**Bases acumuladas**")
                    st.metric("Salarios Brutos", num_cop(res['_total_salarios']))
                    st.metric("Prepaga",         num_cop(res['_total_prepaga']))
                    st.metric("Conectividad",    num_cop(res['_total_conectividad']))

        st.divider()
        st.markdown("#### Datos mensuales cargados")
        mask_co_show = (df_co['año'] == sel_year_co) & (df_co['mes'] <= sel_month_co)
        df_co_show = df_co[mask_co_show].copy()
        df_co_show.insert(1, 'Mes', df_co_show['mes'].map(MESES))
        df_co_show = df_co_show.drop(columns=['mes'])
        st.dataframe(df_co_show, use_container_width=True, hide_index=True)
