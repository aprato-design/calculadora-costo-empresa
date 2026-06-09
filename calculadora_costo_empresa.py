import streamlit as st
import pandas as pd
import pdfplumber
import re
from pathlib import Path
from datetime import datetime

# ─── Constants ────────────────────────────────────────────────────────────────

FIXED_FACTORS = {
    'SAC': 0.0833,
    'Plus Vacacional': 0.0078,
    'Plus Feriado': 0.0089,
}

FIXED_FACTORS_CO = {
    'alto': {  # > 10 salarios mínimos
        'Contribuciones Empleador': 0.3002,
        'Prima/Cesantías/Int. Cesantías': 0.1674,
    },
    'bajo': {  # ≤ 10 salarios mínimos
        'Contribuciones Empleador': 0.1652,
        'Prima/Cesantías/Int. Cesantías': 0.1674,
    },
}

CONECTIVIDAD_CO_POR_EMPLEADO = 200_000  # COP por empleado por mes

DATA_FILE = Path(__file__).parent / 'data_costo_empresa.xlsx'
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


# ─── Data persistence ──────────────────────────────────────────────────────────

def _read_all_sheets() -> dict:
    if not DATA_FILE.exists():
        return {}
    try:
        xl = pd.ExcelFile(DATA_FILE)
        return {s: pd.read_excel(DATA_FILE, sheet_name=s) for s in xl.sheet_names}
    except Exception:
        return {}


def _write_sheet(sheet: str, df: pd.DataFrame):
    """Write a sheet preserving all other existing sheets."""
    sheets = _read_all_sheets()
    sheets[sheet] = df
    with pd.ExcelWriter(DATA_FILE, engine='openpyxl') as writer:
        for s, sdf in sheets.items():
            sdf.to_excel(writer, sheet_name=s, index=False)


def load_data_ar() -> pd.DataFrame:
    sheets = _read_all_sheets()
    return sheets.get(SHEET_AR, pd.DataFrame(columns=COLUMNS_AR))


def save_period_ar(row: dict):
    df = load_data_ar()
    df = df[~((df['año'] == row['año']) & (df['mes'] == row['mes']))]
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df = df.sort_values(['año', 'mes']).reset_index(drop=True)
    _write_sheet(SHEET_AR, df)


def load_data_co() -> pd.DataFrame:
    sheets = _read_all_sheets()
    return sheets.get(SHEET_CO, pd.DataFrame(columns=COLUMNS_CO))


def save_period_co(row: dict):
    df = load_data_co()
    df = df[~((df['año'] == row['año']) & (df['mes'] == row['mes']))]
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df = df.sort_values(['año', 'mes']).reset_index(drop=True)
    _write_sheet(SHEET_CO, df)


# ─── Parsing helpers ───────────────────────────────────────────────────────────

def extract_text(pdf_file) -> str:
    text = ''
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or '') + '\n'
    return text


def parse_ar_amount(raw: str) -> float:
    """Convert Argentine number format (1.234.567,89) to float."""
    return float(raw.strip().replace('.', '').replace(',', '.'))


def find_amount(text: str, patterns: list) -> float:
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return parse_ar_amount(m.group(1))
            except ValueError:
                continue
    return 0.0


# ─── F931 Parser ──────────────────────────────────────────────────────────────
# Structure based on ARCA F931 v47 two-column PDF layout.

def parse_f931(pdf_file) -> tuple:
    text = extract_text(pdf_file)

    rem9 = find_amount(text, [r'Suma de Rem\. 9:\s*([\d.,]+)'])

    contrib_ss = find_amount(text, [
        r'Subtotal contribuciones S\.S\.\s+([\d.,]+)',
        r'b1\s*-Total de contribuciones\s+([\d.,]+)',
    ])
    contrib_os = find_amount(text, [
        r'Subtotal contribuciones O\.S\.\s+([\d.,]+)',
        r'b1\s*-\s+Total de contribuciones\s+([\d.,]+)',
    ])

    art = find_amount(text, [
        r'L\.R\.T\. total a pagar\s+([\d.,]+)',
        r'LRT\s+total\s+a\s+pagar\s+([\d.,]+)',
    ])

    seguro_vida = find_amount(text, [
        r'S\.C\.V\.O\. a Pagar:\s*([\d.,]+)',
        r'S\.C\.V\.O\.\s+a\s+Pagar[:\s]+([\d.,]+)',
    ])

    return {
        'rem9': rem9,
        'contrib_ss': contrib_ss,
        'contrib_os': contrib_os,
        'art': art,
        'seguro_vida': seguro_vida,
    }, text


# ─── Prepaga Parsers ───────────────────────────────────────────────────────────

def _file_result(filename: str, doc_type: str, amount: float, warning: str = '') -> dict:
    return {'Archivo': filename, 'Tipo': doc_type, 'Monto': amount, '_warning': warning}


def parse_swiss_medical(files: list) -> list:
    """
    Factura  → sumar  Subtotal general
    Nota de Crédito → restar Subtotal general
    Nota de Débito  → sumar  Subtotal general
    """
    results = []
    for f in files:
        text = extract_text(f)

        if re.search(r'nota\s+de\s+cr[eé]dito', text, re.IGNORECASE):
            doc_type = 'Nota de Crédito'
            sign = -1
        elif re.search(r'nota\s+de\s+d[eé]bito', text, re.IGNORECASE):
            doc_type = 'Nota de Débito'
            sign = 1
        else:
            doc_type = 'Factura'
            sign = 1

        # La línea "Subtotal general" tiene 4 columnas: Detalle | Imp.Gravado | Imp.Exento | Total
        # Tomamos el último valor numérico de esa línea (columna Total)
        amount = 0.0
        for line in text.split('\n'):
            if re.search(r'Subtotal\s+[Gg]eneral', line, re.IGNORECASE):
                numbers = re.findall(r'[\d.,]+', line)
                if numbers:
                    try:
                        amount = parse_ar_amount(numbers[-1])
                        break
                    except ValueError:
                        pass

        warning = 'No se encontró "Subtotal general". Corregí el monto.' if amount == 0 else ''
        results.append(_file_result(f.name, doc_type, sign * amount, warning))

    return results


def parse_osde(files: list) -> list:
    """
    Talón directos     → recuadro "Importe" (lado derecho, sobre el código de barras)
    Talón obligatorios → columna "Total" (último valor de la fila de totales)
    """
    results = []
    for f in files:
        text = extract_text(f)

        if re.search(r'INFORME DE LIQUIDACI', text, re.IGNORECASE):
            doc_type = 'Talón Obligatorios'
            amount = _extract_osde_obligatorios_total(f, text)
            warning = 'No se encontró "Total". Corregí el monto.' if amount == 0 else ''
        else:
            doc_type = 'Talón Directos'
            amount = _extract_osde_directos_importe(f, text)
            warning = 'No se encontró "Importe". Corregí el monto.' if amount == 0 else ''

        results.append(_file_result(f.name, doc_type, amount, warning))

    return results


def _find_last_importe(text: str) -> float:
    """
    Busca todas las ocurrencias de IMPORTE seguido de un monto en el texto
    y devuelve la ÚLTIMA (que corresponde al total en talones con múltiples líneas).
    """
    found = []
    for line in text.split('\n'):
        if 'IMPORTE' in line.upper():
            nums = re.findall(r'[\d]+[.,][\d.,]*', line)
            for n in reversed(nums):
                try:
                    val = parse_ar_amount(n)
                    if val > 0:
                        found.append(val)
                        break
                except ValueError:
                    pass
    return found[-1] if found else 0.0


def _extract_osde_directos_importe(pdf_file, fallback_text: str) -> float:
    return _find_last_importe(fallback_text)


def _extract_osde_obligatorios_total(pdf_file, fallback_text: str) -> float:
    """
    Tabla: EMPRESA | MONTO FACT | ... | TOTAL
    La fila "TOTAL ..." tiene el último número = total a pagar.
    """
    for line in fallback_text.split('\n'):
        if re.match(r'\s*TOTAL\b', line.strip(), re.IGNORECASE):
            nums = re.findall(r'[\d]+[.,][\d.,]*', line)
            if nums:
                try:
                    return parse_ar_amount(nums[-1])
                except ValueError:
                    pass
    return _find_last_importe(fallback_text)


def parse_sancor(files: list) -> list:
    """Toma la línea 'Subtotal' (excluye impuestos)."""
    results = []
    for f in files:
        text = extract_text(f)
        amount = find_amount(text, [
            r'Subtotal[:\s]+([\d.,]+)',
            r'SUB\s*TOTAL[:\s]+([\d.,]+)',
        ])
        warning = 'No se encontró "Subtotal". Corregí el monto.' if amount == 0 else ''
        results.append(_file_result(f.name, 'Factura', amount, warning))

    return results


# ─── Factor calculations ───────────────────────────────────────────────────────

def calculate_factor_ar(df: pd.DataFrame, year: int, up_to_month: int) -> dict:
    # Diciembre del año anterior se incluye como base del acumulado
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
    """Returns {'alto': {...}, 'bajo': {...}} using a shared salary/prepaga/conectividad base."""
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


def render_prepaga_section(label: str, parse_fn, key: str) -> float:
    """Renders upload + breakdown table for one prepaga provider. Returns subtotal."""
    files = st.file_uploader(
        f"{label} — subir comprobantes", type="pdf",
        accept_multiple_files=True, key=key
    )

    subtotal = 0.0

    if files:
        rows = parse_fn(files)

        for r in rows:
            if r['_warning']:
                st.warning(f"{r['Archivo']}: {r['_warning']}")

        df_rows = pd.DataFrame([
            {'Archivo': r['Archivo'], 'Tipo': r['Tipo'], 'Monto': r['Monto']}
            for r in rows
        ])
        st.dataframe(df_rows, use_container_width=True, hide_index=True)

        subtotal = sum(r['Monto'] for r in rows)
        st.caption(f"Subtotal {label}: {num(subtotal)}")

        with st.expander(f"Ver texto extraído — {label}"):
            for f in files:
                st.markdown(f"**{f.name}**")
                f.seek(0)
                st.text(extract_text(f)[:3000])

    return subtotal




# ─── App ──────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Factor Costo Empresa", layout="wide")
st.title("Factor Costo Empresa")

tab_ar_carga, tab_ar_factor, tab_co_carga, tab_co_factor = st.tabs([
    "Argentina — Cargar",
    "Argentina — Factor",
    "Colombia — Cargar",
    "Colombia — Factor",
])


# ── Tab AR Carga ──────────────────────────────────────────────────────────────
with tab_ar_carga:
    st.subheader("Argentina — Datos del período")

    col_y, col_m = st.columns(2)
    year  = col_y.selectbox("Año",  range(datetime.now().year - 1, datetime.now().year + 2), index=1)
    month = col_m.selectbox("Mes", list(MESES.keys()), format_func=MESES.get,
                            index=datetime.now().month - 1)

    st.divider()

    st.markdown("#### F931 (ARCA)")
    f931_file = st.file_uploader("Subir F931", type="pdf", key=f"f931_{year}_{month}")

    defaults = dict(rem9=0.0, contrib_ss=0.0, contrib_os=0.0, art=0.0, seguro_vida=0.0)

    if f931_file:
        with st.spinner("Procesando F931..."):
            parsed, raw_text = parse_f931(f931_file)

        not_found = [k for k, v in parsed.items() if v == 0.0]
        if not_found:
            st.warning(f"No se detectaron automáticamente: {', '.join(not_found)}. Ingresalos manualmente.")

        with st.expander("Ver texto extraído del PDF"):
            st.text(raw_text[:5000])

        defaults = parsed

    c1, c2, c3 = st.columns(3)
    rem9       = c1.number_input("Remuneración 9",       value=defaults['rem9'],       format="%.2f", min_value=0.0)
    contrib_ss = c2.number_input("Contrib. Seg. Social", value=defaults['contrib_ss'], format="%.2f", min_value=0.0)
    contrib_os = c3.number_input("Contrib. Obra Social", value=defaults['contrib_os'], format="%.2f", min_value=0.0)

    c4, c5, _ = st.columns(3)
    art         = c4.number_input("ART",            value=defaults['art'],         format="%.2f", min_value=0.0)
    seguro_vida = c5.number_input("Seguro de Vida", value=defaults['seguro_vida'], format="%.2f", min_value=0.0)

    st.divider()

    st.markdown("#### Prepagas (sin IVA)")
    st.caption("Podés subir múltiples comprobantes por prestador. Las Notas de Crédito se restan automáticamente.")

    swiss_total  = render_prepaga_section("Swiss Medical", parse_swiss_medical, f"swiss_{year}_{month}")
    osde_total   = render_prepaga_section("OSDE",          parse_osde,          f"osde_{year}_{month}")
    sancor_total = render_prepaga_section("Sancor",        parse_sancor,        f"sancor_{year}_{month}")

    auto_prepaga_total = swiss_total + osde_total + sancor_total

    st.divider()
    prepaga_total = st.number_input(
        "Total prepaga (sin IVA) — editable si algún comprobante no fue reconocido",
        value=auto_prepaga_total, format="%.2f"
    )

    st.divider()

    st.markdown("#### Gastos de conectividad")

    conectividad_default = 0.0
    excel_file = st.file_uploader("Subir Excel de conectividad (opcional)", type=["xlsx", "xls"], key=f"excel_con_{year}_{month}")
    if excel_file:
        try:
            xl = pd.ExcelFile(excel_file)
            sheet = st.selectbox("Hoja", xl.sheet_names)
            df_con = pd.read_excel(excel_file, sheet_name=sheet)
            col_name = st.selectbox("Columna con montos", df_con.columns)
            conectividad_default = float(pd.to_numeric(df_con[col_name], errors='coerce').sum())
            st.caption(f"Total detectado: {num(conectividad_default)}")
        except Exception as e:
            st.error(f"Error leyendo Excel: {e}")

    conectividad = st.number_input(
        "Total conectividad del período",
        value=conectividad_default, format="%.2f", min_value=0.0
    )

    st.divider()

    salarios_brutos = rem9 - conectividad

    st.markdown("#### Resumen del período")
    m1, m2, m3 = st.columns(3)
    m1.metric("Remuneración 9 (F931)", num(rem9))
    m2.metric("Conectividad", num(conectividad))
    m3.metric("Salarios Brutos Base (Rem9 − Conect.)", num(salarios_brutos))

    if st.button("Guardar período", type="primary", key="btn_save_ar"):
        if rem9 == 0:
            st.error("La Remuneración 9 no puede ser 0.")
        elif salarios_brutos <= 0:
            st.error("Los salarios brutos resultantes son 0 o negativos. Revisá los valores.")
        else:
            save_period_ar({
                'año': year, 'mes': month,
                'rem9_f931': rem9, 'conectividad': conectividad,
                'salarios_brutos': salarios_brutos,
                'contrib_ss': contrib_ss, 'contrib_os': contrib_os,
                'art': art, 'seguro_vida': seguro_vida,
                'prepaga_total': prepaga_total,
            })
            st.success(f"Período {MESES[month]} {year} guardado correctamente.")


# ── Tab AR Factor ─────────────────────────────────────────────────────────────
with tab_ar_factor:
    st.subheader("Argentina — Factor acumulado")

    df_ar = load_data_ar()

    if df_ar.empty:
        st.info("No hay datos cargados. Usá la pestaña 'Argentina — Cargar' para ingresar datos.")
    else:
        col_y2, col_m2 = st.columns(2)
        years_ar = sorted(df_ar['año'].unique(), reverse=True)
        sel_year_ar = col_y2.selectbox("Año", years_ar, key="sel_year_ar")

        avail_months_ar = sorted(df_ar[df_ar['año'] == sel_year_ar]['mes'].unique())
        sel_month_ar = col_m2.selectbox(
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
            b1.metric("Salarios Brutos", num(result_ar['_total_salarios']))
            b2.metric("Prepaga",         num(result_ar['_total_prepaga']))
            b3.metric("Cargas Sociales", num(result_ar['_total_cargas']))
            b4.metric("Conectividad",    num(result_ar['_total_conectividad']))

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


# ── Tab CO Carga ──────────────────────────────────────────────────────────────
with tab_co_carga:
    st.subheader("Colombia — Datos del período")
    st.caption("Todos los montos en COP. Los factores fijos de contribución varían por grupo pero se calculan sobre la misma base de salarios.")

    col_y_co, col_m_co = st.columns(2)
    year_co  = col_y_co.selectbox("Año",  range(datetime.now().year - 1, datetime.now().year + 2), index=1, key="year_co")
    month_co = col_m_co.selectbox("Mes", list(MESES.keys()), format_func=MESES.get,
                                  index=datetime.now().month - 1, key="month_co")

    st.divider()

    c1, c2 = st.columns(2)
    co_salarios = c1.number_input(
        "Salarios brutos del período (COP)",
        value=0.0, format="%.0f", min_value=0.0,
        key=f"co_sal_{year_co}_{month_co}"
    )
    co_empleados = c2.number_input(
        "Cantidad de empleados",
        value=0, min_value=0, step=1,
        key=f"co_emp_{year_co}_{month_co}"
    )

    co_conectividad = int(co_empleados) * CONECTIVIDAD_CO_POR_EMPLEADO
    st.caption(f"Conectividad calculada: {num_cop(co_conectividad)}  ({int(co_empleados)} emp × $200.000)")
    if month_co == 1:
        st.warning("Enero: verificar si el valor de conectividad por empleado ($200.000 COP) sigue vigente antes de cargar el período.")

    co_prepaga = st.number_input(
        "Prepaga total del período (COP) — SURA + Colmedica",
        value=0.0, format="%.0f", min_value=0.0,
        key=f"co_prep_{year_co}_{month_co}"
    )

    st.divider()
    st.markdown("#### Resumen del período")
    r1, r2, r3 = st.columns(3)
    r1.metric("Salarios Brutos", num_cop(co_salarios))
    r2.metric("Empleados", int(co_empleados))
    r3.metric("Prepaga", num_cop(co_prepaga))

    if st.button("Guardar período", type="primary", key="btn_save_co"):
        if co_salarios == 0:
            st.error("Los salarios brutos no pueden ser 0.")
        else:
            save_period_co({
                'año': year_co, 'mes': month_co,
                'salarios_brutos': co_salarios,
                'empleados': int(co_empleados),
                'prepaga_total': co_prepaga,
            })
            st.success(f"Período {MESES[month_co]} {year_co} guardado correctamente.")


# ── Tab CO Factor ─────────────────────────────────────────────────────────────
with tab_co_factor:
    st.subheader("Colombia — Factor acumulado")
    st.caption("Todos los montos en COP.")

    df_co = load_data_co()

    if df_co.empty:
        st.info("No hay datos cargados. Usá la pestaña 'Colombia — Cargar' para ingresar datos.")
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
            mask_co_show = (
                ((df_co['año'] == sel_year_co) & (df_co['mes'] <= sel_month_co)) |
                ((df_co['año'] == sel_year_co - 1) & (df_co['mes'] == 12))
            )
            df_co_show = df_co[mask_co_show].copy()
            df_co_show.insert(1, 'Mes', df_co_show['mes'].map(MESES))
            df_co_show = df_co_show.drop(columns=['mes'])
            st.dataframe(df_co_show, use_container_width=True, hide_index=True)
