# pages/lector.py
# Rol Lector: dashboard con filtros por fechas, comparativas y detalle

import pandas as pd
import streamlit as st
import datetime as dt

from f_auth import require_lector, current_user
from f_read import (
    list_suppliers,
    list_categories,
    list_requesters_for_approver,   # reutilizamos para obtener solicitantes
    list_approvers_for_viewer,      # NUEVO helper abajo
    list_paid_expenses_enriched,    # NUEVO helper abajo
    signed_url_for_receipt,
    signed_url_for_payment,
    _render_download,
)

require_lector()

me = current_user()
if not me:
    st.stop()

# --------------------------
# Utilidades locales
# --------------------------
def _fmt_dt(dt_str: str) -> str:
    try:
        return pd.to_datetime(dt_str).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str

# --------------------------
# Filtros globales
# --------------------------
st.write("**Dashboard de gastos pagados**")

col_dates = st.container()
with col_dates:
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Rango por **fecha de creación**")
        created_range = st.date_input(
            "Desde / Hasta (creado)",
            value=(dt.date.today() - dt.timedelta(days=30), dt.date.today()),
            key="created_range",
            help="Filtra por expenses.created_at",
        )
    with c2:
        st.caption("Rango por **fecha de pago**")
        paid_range = st.date_input(
            "Desde / Hasta (pagado)",
            value=(dt.date.today() - dt.timedelta(days=30), dt.date.today()),
            key="paid_range",
            help="Filtra por fecha de marcado como pagado (logs)",
        )

# Opciones de filtros por dimensión
suppliers = list_suppliers()
supplier_names = [s["name"] for s in suppliers]
cats = list_categories()
reqs = list_requesters_for_approver()        # [{id,email}]
aprs = list_approvers_for_viewer()           # [{id,email}]

c3, c4, c5, c6 = st.columns(4)
with c3:
    sel_sups = st.multiselect("Proveedores", supplier_names, help="Filtra por nombre de proveedor")
with c4:
    sel_cats = st.multiselect("Categorías", cats)
with c5:
    sel_reqs = st.multiselect("Solicitantes", [r["email"] for r in reqs])
with c6:
    sel_aprs = st.multiselect("Aprobadores", [a["email"] for a in aprs])

# Normaliza fechas → ISO límites (inicio de día, fin de día)
def _range_to_iso(r):
    if isinstance(r, (list, tuple)) and len(r) == 2 and r[0] and r[1]:
        start = pd.to_datetime(r[0]).strftime("%Y-%m-%dT00:00:00Z")
        end = pd.to_datetime(r[1]).strftime("%Y-%m-%dT23:59:59Z")
        return start, end
    return None, None

created_from, created_to = _range_to_iso(created_range)
paid_from, paid_to = _range_to_iso(paid_range)

# --------------------------
# Carga datos (siempre pagados) + filtros
# --------------------------
rows = list_paid_expenses_enriched(
    created_from=created_from,
    created_to=created_to,
    supplier_names=set(sel_sups) if sel_sups else None,
    categories=set(sel_cats) if sel_cats else None,
    requester_emails=set(sel_reqs) if sel_reqs else None,
    approver_emails=set(sel_aprs) if sel_aprs else None,
    paid_from=paid_from,
    paid_to=paid_to,
)

# DataFrame base
df = pd.DataFrame(rows)
if df.empty:
    st.info("No hay gastos que coincidan con los filtros.")
    st.stop()

# --------------------------
# Tabs: Reporte y Detalle
# --------------------------
tab_reporte, tab_detalle = st.tabs(["Reporte", "Detalle"])

# === Tab Reporte ===
with tab_reporte:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Gastos (conteo)", f"{len(df):,}")
    m2.metric("Monto total", f"{df['amount'].sum():,.2f}")
    m3.metric("Monto promedio", f"{df['amount'].mean():,.2f}")
    m4.metric("Monto mediano", f"{df['amount'].median():,.2f}")

    st.divider()

    st.write("**Comparar por dimensión**")

    dim = st.radio(
        "Dimensión",
        options=["Proveedores", "Solicitantes", "Aprobadores", "Categorías"],
        horizontal=True,
    )
    metric = st.radio(
        "Métrica",
        options=["Monto total", "Número de gastos"],
        horizontal=True,
    )
    top_n = st.slider("Top N", min_value=5, max_value=50, value=10)

    if dim == "Proveedores":
        field = "supplier_name"
    elif dim == "Solicitantes":
        field = "requested_by_email"
    elif dim == "Aprobadores":
        field = "approved_by_email"
    else:
        field = "category"

    if metric == "Monto total":
        ser = df.groupby(field)["amount"].sum()
    else:
        ser = df.groupby(field)["id"].count()

    ser = ser.sort_values(ascending=False).head(top_n)

    st.bar_chart(ser, use_container_width=True)
    st.dataframe(
        ser.rename(metric),
        use_container_width=True,
        height=400,
    )

# === Tab Detalle: tabla y detalle de un gasto ===
with tab_detalle:
    st.write("**Gastos filtrados**")

    # Tabla con columnas claves
    show_df = df.copy()
    show_df["Creado"] = show_df["created_at"].map(_fmt_dt)
    show_df["Pagado"] = show_df["paid_at"].map(_fmt_dt)
    show_df = show_df[[
        "supplier_name", "description", "amount", "category",
        "requested_by_email", "approved_by_email", "paid_by_email",
        "Creado", "Pagado"
    ]].rename(columns={
        "supplier_name": "Proveedor",
        "description": "Descripción",
        "amount": "Monto",
        "category": "Categoría",
        "requested_by_email": "Solicitante",
        "approved_by_email": "Aprobador",
        "paid_by_email": "Pagador",
    })

    st.dataframe(show_df, use_container_width=True, hide_index=True)

    # Selector de un gasto
    opt_map = {
        f"{r['supplier_name']} — {r.get('description','')} — {_fmt_dt(r['paid_at'])} — {r.get('requested_by_email','')}"
        : r["id"]
        for _, r in df.iterrows()
    }
    sel_label = st.selectbox("Selecciona un gasto para ver detalles", list(opt_map.keys()))
    eid = opt_map[sel_label]

    # Mostrar detalle con documentos y previsualización
    row = df[df["id"] == eid].iloc[0]
    st.markdown(
        f"**Proveedor:** {row['supplier_name']}  \n"
        f"**Descripción:** {row.get('description','')}  \n"
        f"**Monto:** {row['amount']:.2f}  \n"
        f"**Categoría:** {row['category']}  \n"
        f"**Solicitante:** {row.get('requested_by_email','')}  \n"
        f"**Aprobador:** {row.get('approved_by_email','')}  \n"
        f"**Pagador:** {row.get('paid_by_email','')}  \n"
        f"**Creado:** {_fmt_dt(row['created_at'])}  \n"
        f"**Pagado:** {_fmt_dt(row['paid_at'])}"
    )

    st.divider()
    rec_key = row.get("supporting_doc_key")
    pay_key = row.get("payment_doc_key")
    cols_files = st.columns(2)
    with cols_files[0]:
        _render_download(rec_key, "Documento de respaldo", signed_url_for_receipt)
    with cols_files[1]:
        _render_download(pay_key, "Comprobante de pago", signed_url_for_payment)
