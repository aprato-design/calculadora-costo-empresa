# -*- coding: utf-8 -*-
"""
Tableros Doppler - Meets mensuales con HoD
Muestra datos de compensaciones por departamento, filtrado por email.
"""
import streamlit as st
import streamlit.components.v1 as components
import gspread
from google.oauth2.service_account import Credentials
from pathlib import Path
from datetime import datetime
import html as html_lib

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
C_EMAIL_EMP = 2
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
        --dp-yellow:      #FFEEA7;
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

    [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
        color: var(--dp-green-dark) !important;
        border-bottom-color: var(--dp-green) !important;
        font-weight: 600 !important;
    }
    [data-testid="stTabs"] [role="tab"] { color: var(--dp-text-light) !important; }

    .dp-comment-label {
        font-size: 0.88rem; font-weight: 700;
        color: var(--dp-green-dark); margin-bottom: 0.2rem;
    }

    [data-testid="stButton"] > button {
        background-color: var(--dp-green) !important;
        color: white !important; border: none !important;
        border-radius: 8px !important; font-weight: 600 !important;
        padding: 0.4rem 1.2rem !important;
    }
    [data-testid="stButton"] > button:hover {
        background-color: var(--dp-green-dark) !important;
    }

    hr { border-color: var(--dp-border) !important; margin: 1rem 0 !important; }
    #MainMenu, footer { visibility: hidden; }
    </style>
    """, unsafe_allow_html=True)


def _header(subtitle='Tableros · Compensaciones'):
    st.markdown(f"""
    <div class="dp-header">
        <div class="dp-logo">Doppler<span>Talent Care</span></div>
        <div class="dp-header-right">{subtitle}</div>
    </div>
    """, unsafe_allow_html=True)


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


def _fmt_num(val):
    """Format as $X,XXX or return raw string if not numeric."""
    try:
        n = float(str(val).replace(',', '').replace('$', '').replace(' ', '').strip())
        if n == 0:
            return ''
        return f'${n:,.0f}'
    except (ValueError, TypeError):
        return str(val) if val else ''


# ─── Auth ──────────────────────────────────────────────────────────────────────

def _get_gc():
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    try:
        info = dict(st.secrets['gcp_service_account'])
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    except Exception:
        creds = Credentials.from_service_account_file(str(CREDS_FILE), scopes=scopes)
    return gspread.authorize(creds)


@st.cache_data(ttl=300)
def load_accesos():
    gc = _get_gc()
    rows = gc.open_by_key(MAIN_SHEET_ID).worksheet(ACCESOS_TAB).get_all_values()
    mapping = {}
    for row in rows[1:]:
        if len(row) >= 2 and row[0].strip() and row[1].strip():
            email = row[0].strip().lower()
            tablero = row[1].strip()
            if tablero in DASHBOARDS:
                mapping[email] = tablero
    return mapping


# ─── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_main_data():
    gc = _get_gc()
    ws = gc.open_by_key(MAIN_SHEET_ID).worksheet(MAIN_TAB)
    rows = ws.get_all_values()
    # Row 0: empty header, Row 1: column names, Row 2+: data
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


# ─── HTML table ────────────────────────────────────────────────────────────────

_TABLE_CSS = """
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    color: #525845; font-size: 13px; background: #fff;
}
.wrap { width: 100%; height: 100%; overflow: auto; }

table { border-collapse: collapse; white-space: nowrap; width: max-content; min-width: 100%; }

thead th {
    position: sticky; top: 0; z-index: 2;
    background: #27945E; color: white;
    font-weight: 600; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.05em;
    padding: 10px 14px; text-align: left;
    border-right: 1px solid rgba(255,255,255,0.18);
}
thead th:last-child { border-right: none; }

tbody td {
    padding: 8px 14px; vertical-align: middle;
    border-bottom: 1px solid #ECF0E8;
    border-right: 1px solid #F3F5F0;
}
tbody td:last-child { border-right: none; }

tbody tr:nth-child(even) td { background: #FAFDF8; }
tbody tr:hover td { background: #F0FBF6 !important; }

/* Sticky first column */
th.col-n, td.col-n {
    position: sticky; left: 0; z-index: 1;
    background: #EAF7F1;
    border-right: 2px solid #D0E9DC !important;
    font-weight: 600; min-width: 190px;
}
thead th.col-n { z-index: 3; background: #27945E; color: white; }
tbody tr:nth-child(even) td.col-n { background: #E3F5EC; }
tbody tr:hover td.col-n { background: #CFF0E0 !important; }

/* Number cells */
.num { text-align: right; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }

/* GAP coloring */
.g-ok  { color: #27945E; font-weight: 700; }
.g-warn { color: #B07A00; font-weight: 700; }
.g-bad  { color: #C0392B; font-weight: 700; }

/* Comment cell */
td.col-cmt { min-width: 200px; white-space: normal; line-height: 1.4; }
td.col-cmt.empty { color: #BBCCB8; font-style: italic; }
</style>
"""


def _gap_span(val):
    s = str(val).strip()
    sl = s.lower()
    if sl in ('ok', 'bien', 'dentro', 'en banda'):
        return f'<span class="g-ok">{html_lib.escape(s)}</span>'
    if sl in ('bajo', 'below', 'por debajo', 'debajo'):
        return f'<span class="g-bad">{html_lib.escape(s)}</span>'
    if sl in ('alto', 'above', 'por encima', 'encima'):
        return f'<span class="g-warn">{html_lib.escape(s)}</span>'
    # Try to parse as percentage (e.g. '-6.47%' or '8.2%')
    try:
        pct = float(s.replace('%', '').replace(',', '.').strip())
        if pct < 0:
            return f'<span class="g-bad">{html_lib.escape(s)}</span>'
        if pct > 0:
            return f'<span class="g-warn">{html_lib.escape(s)}</span>'
    except (ValueError, TypeError):
        pass
    return html_lib.escape(s)


def build_table_html(data_rows, comentarios, periodo):
    headers = [
        ('col-n', 'Nombre'),
        ('', 'Hire Date'),
        ('', 'Job Title'),
        ('', 'Code'),
        ('', 'Agreement'),
        ('num', 'Bill'),
        ('num', 'Payroll'),
        ('num', 'Banda Min'),
        ('num', 'Banda Max'),
        ('', 'GAP Banda'),
        ('num', 'CE'),
        ('col-cmt', 'Comentarios'),
    ]

    th_html = ''.join(
        f'<th class="{cls}">{lbl}</th>' for cls, lbl in headers
    )

    rows_html = ''
    for row in data_rows:
        nombre = _safe(row, C_NOMBRE)
        comment = comentarios.get((periodo, nombre), '')
        cmt_cls = 'col-cmt' if comment else 'col-cmt empty'
        cmt_val = html_lib.escape(comment) if comment else 'sin comentarios'

        cells = [
            ('col-n', html_lib.escape(nombre)),
            ('', html_lib.escape(_safe(row, C_HIRE_DATE))),
            ('', html_lib.escape(_safe(row, C_JOB_TITLE))),
            ('', html_lib.escape(_safe(row, C_CODE))),
            ('', html_lib.escape(_safe(row, C_AGREEMENT))),
            ('num', html_lib.escape(_fmt_num(_safe(row, C_BILL)))),
            ('num', html_lib.escape(_fmt_num(_safe(row, C_PAYROLL)))),
            ('num', html_lib.escape(_fmt_num(_safe(row, C_BANDA_MIN)))),
            ('num', html_lib.escape(_fmt_num(_safe(row, C_BANDA_MAX)))),
            ('', _gap_span(_safe(row, C_GAP_BANDA))),
            ('num', html_lib.escape(_fmt_num(_safe(row, C_CE)))),
            (cmt_cls, cmt_val),
        ]

        rows_html += '<tr>' + ''.join(
            f'<td class="{cls}">{val}</td>' for cls, val in cells
        ) + '</tr>'

    return f"""<!DOCTYPE html>
<html><head>{_TABLE_CSS}</head>
<body>
<div class="wrap">
  <table>
    <thead><tr>{th_html}</tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
</body></html>"""


# ─── Dashboard ─────────────────────────────────────────────────────────────────

def show_dashboard(dashboard_key, all_data, user_email):
    dash = DASHBOARDS[dashboard_key]
    depts = dash['depts']

    # Filter rows: target departments only, exclude HoD
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

    # Department badge
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
            st.caption(f'{len(month_rows)} colaboradores · {month_name}')

            if not month_rows:
                st.info('No hay datos para este período.')
                continue

            # Render table
            tbl_html = build_table_html(month_rows, comentarios, periodo)
            tbl_height = max(200, min(560, 42 + len(month_rows) * 38))
            components.html(tbl_html, height=tbl_height, scrolling=False)

            # ── Comment form ──────────────────────────────────────────────
            st.divider()
            st.markdown('<div class="dp-comment-label">Agregar / editar comentario</div>',
                        unsafe_allow_html=True)

            names = [_safe(r, C_NOMBRE) for r in month_rows if _safe(r, C_NOMBRE)]
            c1, c2, c3 = st.columns([1.5, 2.5, 1])

            with c1:
                selected = st.selectbox(
                    'Colaborador',
                    names,
                    label_visibility='collapsed',
                    key=f'sel_{dashboard_key}_{periodo}',
                )

            existing = comentarios.get((periodo, selected), '')

            with c2:
                new_comment = st.text_area(
                    'Comentario',
                    value=existing,
                    height=80,
                    placeholder='Escribí un comentario...',
                    label_visibility='collapsed',
                    key=f'txt_{dashboard_key}_{periodo}',
                )

            with c3:
                st.markdown('<br>', unsafe_allow_html=True)
                save_btn = st.button('Guardar', key=f'btn_{dashboard_key}_{periodo}',
                                     use_container_width=True)

            if save_btn:
                person_row = next((r for r in month_rows if _safe(r, C_NOMBRE) == selected), None)
                dept_val = _safe(person_row, C_DEPT) if person_row else depts[0]
                with st.spinner('Guardando...'):
                    save_comentario(periodo, selected, dept_val, new_comment, user_email)
                load_comentarios.clear()
                st.success(f'Comentario guardado para **{selected}**.')
                st.rerun()


# ─── Login screen ──────────────────────────────────────────────────────────────

def show_login():
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        st.markdown('<br>', unsafe_allow_html=True)
        st.markdown("""
        <div style="background:white; border:1px solid #D8EDE5; border-radius:16px;
                    padding:2rem 2.2rem; box-shadow:0 4px 24px rgba(51,173,115,0.08);">
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
                'Email',
                placeholder='nombre@fromdoppler.com',
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

    # Session: email gate
    if 'user_email' not in st.session_state:
        show_login()
        return

    user_email = st.session_state['user_email']

    # Verify access against sheet
    with st.spinner('Verificando acceso...'):
        accesos = load_accesos()

    dashboard_key = accesos.get(user_email)
    if not dashboard_key:
        st.error(
            f'El email **{user_email}** no tiene acceso a ningún tablero. '
            'Contactá a Talent Care para solicitar acceso.'
        )
        if st.button('Cambiar email'):
            del st.session_state['user_email']
            st.rerun()
        return

    # Top bar: user badge + logout
    col_info, col_logout = st.columns([8, 1])
    with col_info:
        st.markdown(
            f'<span class="dp-badge">'
            f'<span class="dp-badge-dot"></span>{user_email}'
            f'</span>',
            unsafe_allow_html=True
        )
    with col_logout:
        if st.button('Salir', use_container_width=True):
            del st.session_state['user_email']
            st.rerun()

    st.divider()

    # Load and render dashboard
    with st.spinner('Cargando datos...'):
        all_data = load_main_data()

    show_dashboard(dashboard_key, all_data, user_email)


if __name__ == '__main__':
    main()
