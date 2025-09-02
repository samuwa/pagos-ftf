# pages/lector.py
# Rol Lector: dashboard con filtros por fechas, comparativas y detalle

import os
import uuid

import requests
import pandas as pd
import streamlit as st

from f_auth import require_lector, current_user
from f_read import (
    list_suppliers,
    list_categories_from_expenses,
    list_requesters_for_approver,   # reutilizamos para obtener solicitantes
    list_approvers_for_viewer,      # NUEVO helper abajo
    list_paid_expenses_enriched,    # NUEVO helper abajo
    signed_url_for_receipt,
    signed_url_for_payment,
)

st.set_page_config(page_title="Lector", page_icon="📊", layout="wide")
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

def _render_download(url: str, file_key: str, title: str):
    """Renderiza link y botón de descarga; deshabilita si no hay archivo."""
    dl_key = f"dl-{title}-{uuid.uuid4().hex}"
    if url:
        st.link_button(f"Abrir {title} en pestaña nueva", url, use_container_width=True)
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            st.download_button(
                f"Descargar {title}",
                resp.content,
                file_name=os.path.basename(file_key) if file_key else title.replace(" ", "_"),
                use_container_width=True,
                key=dl_key,
            )
        except Exception as e:
            st.caption(f"No se pudo obtener el archivo de {title}: {e}")
    else:
        st.download_button(
            f"Descargar {title}",
            b"",
            file_name=title.replace(" ", "_"),
            use_container_width=True,
            disabled=True,
            key=dl_key,
        )

# --------------------------
# Filtros globales
# --------------------------
st.title("Dashboard de gastos pagados")

col_dates = st.container()
with col_dates:
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Rango por **fecha de creación**")
        created_range = st.date_input("Desde / Hasta (creado)", value=None, key="created_range", help="Filtra por expenses.created_at")
    with c2:
        st.caption("Rango por **fecha de pago**")
        paid_range = st.date_input("Desde / Hasta (pagado)", value=None, key="paid_range", help="Filtra por fecha de marcado como pagado (logs)")

# Opciones de filtros por dimensión
suppliers = list_suppliers()
supplier_names = [s["name"] for s in suppliers]
cats = list_categories_from_expenses()
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
    if isinstance(r, list) and len(r) == 2 and r[0] and r[1]:
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

    tab_resumen, tab_comparar = st.tabs(["Resumen", "Comparar"])

    with tab_resumen:
        st.subheader("Resumen por dimensión")

        def _top_table(series, title, n=10):
            if series.empty:
                st.caption(f"Sin datos para {title}.")
                return
            tb = (series.value_counts().head(n)).rename("Gastos")
            st.write(f"**Top {n} por {title}**")
            st.dataframe(tb, use_container_width=True)

        cA, cB, cC = st.columns(3)
        with cA:
            _top_table(df["supplier_name"], "Proveedor")
        with cB:
            _top_table(df["requested_by_email"], "Solicitante")
        with cC:
            _top_table(df["approved_by_email"], "Aprobador")

        st.subheader("Evolución (por fecha de pago)")
        ts = df.copy()
        ts["paid_date"] = pd.to_datetime(ts["paid_at"]).dt.date
        if not ts.empty:
            grp = ts.groupby("paid_date").agg(
                gastos=("id", "count"),
                monto=("amount", "sum")
            ).reset_index()
            c1, c2 = st.columns(2)
            with c1:
                st.line_chart(grp, x="paid_date", y="gastos", use_container_width=True)
            with c2:
                st.line_chart(grp, x="paid_date", y="monto", use_container_width=True)
        else:
            st.caption("Sin datos para serie temporal.")

    with tab_comparar:
        st.subheader("Comparar por dimensión")

        dim = st.radio("Dimensión", options=["Proveedores", "Solicitantes", "Aprobadores", "Categorías"], horizontal=True)

        if dim == "Proveedores":
            ser = df.groupby("supplier_name")["amount"].sum().sort_values(ascending=False)
        elif dim == "Solicitantes":
            ser = df.groupby("requested_by_email")["amount"].sum().sort_values(ascending=False)
        elif dim == "Aprobadores":
            ser = df.groupby("approved_by_email")["amount"].sum().sort_values(ascending=False)
        else:
            ser = df.groupby("category")["amount"].sum().sort_values(ascending=False)

        st.bar_chart(ser.head(20), use_container_width=True)

# === Tab Detalle: tabla y detalle de un gasto ===
with tab_detalle:
    st.subheader("Gastos filtrados")

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
    st.caption("Documento de respaldo")
    rec_key = row.get("supporting_doc_key") or ""
    rec_url = signed_url_for_receipt(rec_key, 600)

    _render_download(rec_url, rec_key or "", "documento de respaldo")


    st.caption("Comprobante de pago")
    pay_key = row.get("payment_doc_key") or ""
    pay_url = signed_url_for_payment(pay_key, 600)
    _render_download(pay_url, pay_key or "", "comprobante de pago")
