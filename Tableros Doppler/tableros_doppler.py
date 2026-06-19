# -*- coding: utf-8 -*-
"""
Tableros Doppler - Meets mensuales con HoD
Muestra datos de compensaciones por departamento, filtrado por email.
"""
import streamlit as st
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from pathlib import Path
from datetime import datetime

# ─── Config ────────────────────────────────────────────────────────────────────
MAIN_SHEET_ID   = '1Y48bEHGvkFEgLYzauEzihQVEE_Zzz82FbyG840x9bTo'
MAIN_TAB        = 'Compensaciones - Sueldos Doppler'
ACCESOS_TAB     = 'Accesos Tableros'
COMENTARIOS_TAB = 'Comentarios Tableros'
CREDS_FILE      = Path(__file__).parent.parent.parent / 'talentserviceproject-1ce2ed91696b.json'

MONTH_NAMES = {
    1: 'Enero',   2: 'Febrero',   3: 'Marzo',    4: 'Abril',
    5: 'Mayo',    6: 'Junio',     7: 'Julio',     8: 'Agosto',
    9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre',
}

DASHBOARDS = {
    'Product+Tech': {
        'label': 'Product & Technology',
        'depts': ['Doppler - Product', 'Doppler - Technology'],
    },
    'CX': {
        'label': 'Customer Experience',
        'depts': ['Doppler - Customer Experience'],
    },
    'MS': {
        'label': 'Marketing & Sales',
        'depts': ['Doppler - Marketing & Sales'],
    },
}

# Column indices in "Compensaciones - Sueldos Doppler" (0-based)
C_PERIODO   = 0
C_NOMBRE    = 1
C_HIRE_DATE = 3
C_DEPT      = 4
C_JOB_TITLE = 6
C_CODE      = 8
C_AGREEMENT = 10
C_BILL      = 12
C_PAYROLL   = 13
C_BANDA_MIN = 19
C_BANDA_MAX = 21
C_GAP_BANDA = 22
C_CE        = 28


# ─── Brand CSS ─────────────────────────────────────────────────────────────────

def _inject_css():
    st.markdown("""
    <style>
    :root {
        --dp-green:       #33AD73;
        --dp-green-dark:  #27945E;
        --dp-green-light: #EAF7F1;
        --dp-text:        #525845;
        --dp-text-light:  #7A8070;
        --dp-border:      #D8EDE5;
    }
    html, body, [class*="css"] { color: var(--dp-text); }
    h1, h2, h3 { color: var(--dp-text) !important; font-weight: 700 !important; }

    .dp-header {
        background: linear-gradient(135deg, #33AD73 0%, #27945E 100%);
        padding: 1.1rem 2rem;
        margin-bottom: 1.5rem;
        border-radius: 0 0 12px 12px;
        display: flex; align-items: center; justify-content: space-between;
    }
    .dp-logo {
        font-size: 1.4rem; font-weight: 900; color: white;
        letter-spacing: -0.03em; text-transform: uppercase;
    }
    .dp-logo span {
        opacity: 0.7; font-weight: 400; font-size: 0.7rem;
        letter-spacing: 0.12em; margin-left: 0.6rem; vertical-align: middle;
    }
    .dp-header-right { color: rgba(255,255,255,0.85); font-size: 0.85rem; }

    .dp-badge {
        display: inline-flex; align-items: center; gap: 0.4rem;
        background: var(--dp-green-light); border: 1px solid var(--dp-border);
        border-radius: 999px; padding: 0.3rem 0.9rem;
        font-size: 0.82rem; color: var(--dp-green-dark); font-weight: 600;
    }
    .dp-badge-dot {
        width: 7px; height: 7px; border-radius: 50%;
        background: var(--dp-green); display: inline-block; flex-shrink: 0;
    }

    /* Data editor: highlight editable Comentarios column header */
    [data-testid="stDataEditor"] th:last-child {
        background: #EAF7F1 !important;
        color: var(--dp-green-dark) !important;
    }

    [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
        color: var(--dp-green-dark) !important;
        border-bottom-color: var(--dp-green) !important;
        font-weight: 600 !important;
    }
    [data-testid="stTabs"] [role="tab"] { color: var(--dp-text-light) !important; }

    [data-testid="stButton"] > button {
        background-color: var(--dp-green) !important;
        color: white !important; border: none !important;
        border-radius: 8px !important; font-weight: 600 !important;
    }
    [data-testid="stButton"] > button:hover {
        background-color: var(--dp-green-dark) !important;
    }

    hr { border-color: var(--dp-border) !important; margin: 1rem 0 !important; }
    #MainMenu, footer { visibility: hidden; }
    </style>
    """, unsafe_allow_html=True)


def _header():
    st.markdown("""
    <div class="dp-header">
        <div class="dp-logo">Doppler<span>Talent Care</span></div>
        <div class="dp-header-right">Tableros · Compensaciones</div>
    </div>
    """, unsafe_allow_html=True)


# ─── Auth ──────────────────────────────────────────────────────────────────────

def _get_gc():
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    try:
        info = dict(st.secrets['gcp_service_account'])
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    except Exception:
        creds = Credentials.from_service_account_file(str(CREDS_FILE), scopes=scopes)
    return gspread.authorize(creds)


@st.cache_data(ttl=60)
def load_accesos():
    """Returns {email: [tablero1, tablero2, ...]}. One email can access multiple dashboards."""
    gc = _get_gc()
    rows = gc.open_by_key(MAIN_SHEET_ID).worksheet(ACCESOS_TAB).get_all_values()
    mapping = {}
    for row in rows[1:]:
        if len(row) >= 2 and row[0].strip() and row[1].strip():
            email = row[0].strip().lower()
            tablero = row[1].strip()
            if tablero in DASHBOARDS:
                mapping.setdefault(email, [])
                if tablero not in mapping[email]:
                    mapping[email].append(tablero)
    return mapping


# ─── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_main_data():
    gc = _get_gc()
    ws = gc.open_by_key(MAIN_SHEET_ID).worksheet(MAIN_TAB)
    rows = ws.get_all_values()
    return rows[2:] if len(rows) > 2 else []


@st.cache_data(ttl=60)
def load_comentarios():
    gc = _get_gc()
    rows = gc.open_by_key(MAIN_SHEET_ID).worksheet(COMENTARIOS_TAB).get_all_values()
    mapping = {}
    for row in rows[1:]:
        if len(row) >= 4 and row[0] and row[1]:
            mapping[(row[0], row[1])] = row[3]
    return mapping


def save_comentario(periodo, nombre, dept, comentario, user_email):
    gc = _get_gc()
    ws = gc.open_by_key(MAIN_SHEET_ID).worksheet(COMENTARIOS_TAB)
    rows = ws.get_all_values()
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    for i, row in enumerate(rows[1:], start=2):
        if len(row) >= 2 and row[0] == periodo and row[1] == nombre:
            ws.update(range_name=f'D{i}:F{i}', values=[[comentario, user_email, now]])
            return
    ws.append_row([periodo, nombre, dept, comentario, user_email, now])


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _safe(row, idx, default=''):
    try:
        v = row[idx]
        return v if v is not None else default
    except IndexError:
        return default


def _parse_month(date_str):
    try:
        return int(str(date_str).split('/')[0])
    except Exception:
        return None


# ─── Dashboard ─────────────────────────────────────────────────────────────────

def show_dashboard(dashboard_key, all_data, user_email):
    dash = DASHBOARDS[dashboard_key]
    depts = dash['depts']

    def keep(row):
        return _safe(row, C_DEPT) in depts and _safe(row, C_CODE) != 'HoD'

    filtered = [r for r in all_data if keep(r)]

    # Collect and sort months
    periods_seen = {}
    for row in filtered:
        p = _safe(row, C_PERIODO)
        if p and p not in periods_seen:
            m = _parse_month(p)
            periods_seen[p] = (m if m else 99, MONTH_NAMES.get(m, p))

    sorted_periods = sorted(periods_seen, key=lambda p: periods_seen[p][0])

    st.markdown(
        f'<div style="margin-bottom:1rem;">'
        f'<span class="dp-badge"><span class="dp-badge-dot"></span>{dash["label"]}</span>'
        f'</div>',
        unsafe_allow_html=True
    )

    if not sorted_periods:
        st.info('No hay datos disponibles para este tablero.')
        return

    comentarios = load_comentarios()
    tabs = st.tabs([periods_seen[p][1] for p in sorted_periods])

    for tab, periodo in zip(tabs, sorted_periods):
        month_name = periods_seen[periodo][1]
        month_rows = [r for r in filtered if _safe(r, C_PERIODO) == periodo]

        with tab:
            if not month_rows:
                st.info('No hay datos para este período.')
                continue

            st.caption(f'{len(month_rows)} colaboradores · {month_name} · La columna **Comentarios** es editable.')

            # Build DataFrame
            rows_data = []
            for r in month_rows:
                nombre = _safe(r, C_NOMBRE)
                rows_data.append({
                    'Nombre':     nombre,
                    'Hire Date':  _safe(r, C_HIRE_DATE),
                    'Job Title':  _safe(r, C_JOB_TITLE),
                    'Code':       _safe(r, C_CODE),
                    'Agreement':  _safe(r, C_AGREEMENT),
                    'Bill':       _safe(r, C_BILL),
                    'Payroll':    _safe(r, C_PAYROLL),
                    'Banda Min':  _safe(r, C_BANDA_MIN),
                    'Banda Max':  _safe(r, C_BANDA_MAX),
                    'GAP Banda':  _safe(r, C_GAP_BANDA),
                    'CE':         _safe(r, C_CE),
                    'Comentarios': comentarios.get((periodo, nombre), ''),
                })

            df = pd.DataFrame(rows_data)
            read_only = [c for c in df.columns if c != 'Comentarios']
            height = max(200, min(580, len(month_rows) * 35 + 38))

            edited_df = st.data_editor(
                df,
                disabled=read_only,
                hide_index=True,
                use_container_width=True,
                height=height,
                key=f'tbl_{dashboard_key}_{periodo}',
                column_config={
                    'Nombre':      st.column_config.TextColumn('Nombre',      width='medium'),
                    'Hire Date':   st.column_config.TextColumn('Hire Date',   width='small'),
                    'Job Title':   st.column_config.TextColumn('Job Title',   width='medium'),
                    'Code':        st.column_config.TextColumn('Code',        width='small'),
                    'Agreement':   st.column_config.TextColumn('Agreement',   width='small'),
                    'Bill':        st.column_config.TextColumn('Bill',        width='small'),
                    'Payroll':     st.column_config.TextColumn('Payroll',     width='small'),
                    'Banda Min':   st.column_config.TextColumn('Banda Min',   width='small'),
                    'Banda Max':   st.column_config.TextColumn('Banda Max',   width='small'),
                    'GAP Banda':   st.column_config.TextColumn('GAP Banda',   width='small'),
                    'CE':          st.column_config.TextColumn('CE',          width='small'),
                    'Comentarios': st.column_config.TextColumn('Comentarios ✏️', width='large'),
                },
            )

            # Detect changes and save
            changes = []
            for _, row in edited_df.iterrows():
                nombre = row['Nombre']
                new_cmt = str(row.get('Comentarios', '') or '')
                old_cmt = comentarios.get((periodo, nombre), '')
                if new_cmt != old_cmt:
                    changes.append((nombre, new_cmt))

            if changes:
                person_lookup = {_safe(r, C_NOMBRE): r for r in month_rows}
                try:
                    with st.spinner('Guardando...'):
                        for nombre, cmt in changes:
                            pr = person_lookup.get(nombre)
                            save_comentario(periodo, nombre,
                                            _safe(pr, C_DEPT) if pr else depts[0],
                                            cmt, user_email)
                    load_comentarios.clear()
                    label = changes[0][0] if len(changes) == 1 else f'{len(changes)} colaboradores'
                    st.success(f'Comentario guardado — {label}')
                except Exception as e:
                    st.error(f'Error al guardar: {e}')


# ─── Login screen ──────────────────────────────────────────────────────────────

def show_login():
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        st.markdown('<br>', unsafe_allow_html=True)
        st.markdown("""
        <div style="background:white;border:1px solid #D8EDE5;border-radius:16px;
                    padding:2rem 2.2rem;box-shadow:0 4px 24px rgba(51,173,115,0.08);">
            <div style="font-size:1.15rem;font-weight:700;color:#525845;margin-bottom:0.35rem;">
                Acceso al Tablero
            </div>
            <div style="font-size:0.85rem;color:#7A8070;margin-bottom:1.2rem;">
                Ingresá tu email corporativo para continuar.
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.form('login_form'):
            email_input = st.text_input(
                'Email', placeholder='nombre@fromdoppler.com',
                label_visibility='collapsed',
            )
            submitted = st.form_submit_button('Ingresar', use_container_width=True)

        if submitted:
            email_clean = email_input.strip().lower()
            if not email_clean:
                st.warning('Ingresá tu email para continuar.')
            else:
                st.session_state['user_email'] = email_clean
                st.rerun()


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title='Tableros – Doppler',
        page_icon='📋',
        layout='wide',
    )
    _inject_css()
    _header()

    if 'user_email' not in st.session_state:
        show_login()
        return

    user_email = st.session_state['user_email']

    with st.spinner('Verificando acceso...'):
        accesos = load_accesos()

    dashboard_keys = accesos.get(user_email, [])
    if not dashboard_keys:
        st.error(
            f'El email **{user_email}** no tiene acceso a ningún tablero. '
            'Contactá a Talent Care para solicitar acceso.'
        )
        if st.button('Cambiar email'):
            del st.session_state['user_email']
            st.rerun()
        return

    col_info, col_logout = st.columns([8, 1])
    with col_info:
        st.markdown(
            f'<span class="dp-badge"><span class="dp-badge-dot"></span>{user_email}</span>',
            unsafe_allow_html=True
        )
    with col_logout:
        if st.button('Salir', use_container_width=True):
            del st.session_state['user_email']
            st.rerun()

    st.divider()

    with st.spinner('Cargando datos...'):
        all_data = load_main_data()

    if len(dashboard_keys) == 1:
        show_dashboard(dashboard_keys[0], all_data, user_email)
    else:
        # Multiple dashboards: top-level tabs to select
        tab_labels = [DASHBOARDS[k]['label'] for k in dashboard_keys]
        dash_tabs = st.tabs(tab_labels)
        for dash_tab, key in zip(dash_tabs, dashboard_keys):
            with dash_tab:
                show_dashboard(key, all_data, user_email)


if __name__ == '__main__':
    main()
