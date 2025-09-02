# pages/solicitante.py
# Solicitudes: crear gasto, ver "Mis solicitudes", y "Detalles y actualizar"

import os
import uuid
from decimal import Decimal
import requests
import streamlit as st
import pandas as pd

from f_auth import require_solicitante, current_user, get_client
from f_read import (
    list_suppliers,
    list_my_expenses,
    recent_similar_expenses,
    signed_url_for_receipt,
    signed_url_for_payment,
    get_my_expense,
    list_expense_comments,
    list_expense_logs,
)
from f_cud import create_expense, add_expense_comment

# ===== Config =====
st.set_page_config(page_title="Solicitudes", page_icon="🧾", layout="wide")
require_solicitante()

# Categorías en código
CATEGORIAS = ["Viajes", "Comidas", "Software/SaaS", "Oficina", "Servicios", "Otros"]

st.write("**Solicitudes**")

me = current_user()
if not me:
    st.stop()
user_id = me["id"]

tab_nueva, tab_mias, tab_detalle = st.tabs(["Nueva solicitud", "Mis solicitudes", "Detalles y actualizar"])

# ==========================
# Tab 1: Nueva solicitud
# ==========================
with tab_nueva:
    st.subheader("Crear nueva solicitud")

    suppliers = list_suppliers()
    if not suppliers:
        st.info("Aún no hay proveedores. Pide a un administrador que cree al menos uno.")
    sup_opts = {s["name"]: s["id"] for s in suppliers}
    sup_name = st.selectbox("Proveedor *", options=list(sup_opts.keys()) if suppliers else [])
    supplier_id = sup_opts.get(sup_name) if sup_name else None

    col_a, col_b = st.columns([1, 1])
    with col_a:
        amount = st.number_input("Monto *", min_value=0.00, step=0.01, format="%.2f")
    with col_b:
        categoria = st.selectbox("Categoría *", options=CATEGORIAS)

    descripcion = st.text_input("Descripción breve *", placeholder="Ej. Suscripción anual de software...")

    file = st.file_uploader("Documento de respaldo (recibo/factura) *", type=["pdf", "png", "jpg", "jpeg", "webp"])
    comentario_inicial = st.text_area("Comentario (opcional)", placeholder="Ej. Detalles útiles para aprobación…")

    # Duplicados simples: mismo proveedor y mismo monto en los últimos 30 días
    if supplier_id and amount and amount > 0:
        dupes = recent_similar_expenses(supplier_id, float(amount), days=30)
        if dupes:
            with st.expander("⚠️ Posibles duplicados (últimos 30 días)", expanded=True):
                for d in dupes:
                    url = signed_url_for_receipt(d.get("supporting_doc_key") or "", expires=300)
                    st.markdown(
                        f"- **{d['created_at']}** — {sup_name} — Monto: **{d['amount']:.2f}** — Estado: **{d['status']}**  "
                        + (f"[Ver documento]({url})" if url else "")
                    )
        else:
            st.caption("No se encontraron solicitudes similares recientes.")

    # Enviar
    if st.button("Enviar solicitud", type="primary", use_container_width=False):
        if not supplier_id:
            st.error("Selecciona un proveedor.")
        elif not amount or amount <= 0:
            st.error("Ingresa un monto válido mayor a cero.")
        elif not categoria or categoria not in CATEGORIAS:
            st.error("Selecciona una categoría.")
        elif not file:
            st.error("Adjunta el documento de respaldo.")
        else:
            try:
                # Subir archivo a Storage (bucket 'quotes')
                sb = get_client()
                bucket = "quotes"  # tu bucket
                folder = f"{user_id}/{uuid.uuid4().hex}"             # carpeta
                file_name = file.name                                 # preserva nombre original
                file_path = f"{folder}/{file_name}"                   # archivo dentro de la carpeta

                res = sb.storage.from_(bucket).upload(file_path, file.getvalue())
                stored_key = (

                    (getattr(res, "path", None) if res else None)
                    or (getattr(res, "Key", None) if res else None)
                    or (getattr(res, "key", None) if res else None)
                    or (res.get("path") if isinstance(res, dict) else None)
                    or (res.get("Key") if isinstance(res, dict) else None)

                    or file_path
                )

                expense_id = create_expense(
                    requested_by=user_id,
                    supplier_id=supplier_id,
                    amount=float(Decimal(str(amount))),
                    category=categoria,
                    supporting_doc_key=stored_key,  # guardamos la key completa
                    description=descripcion.strip() if descripcion else None,
                )

                # Comentario inicial (si hay)
                if expense_id and (comentario_inicial or "").strip():
                    add_expense_comment(expense_id, user_id, comentario_inicial.strip())

                st.success("Solicitud creada correctamente.")
                st.balloons()
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo crear la solicitud: {e}")

# ==========================
# Tab 2: Mis solicitudes
# ==========================
with tab_mias:
    st.subheader("Mis solicitudes")

    # Trae todo para métricas
    rows_all = list_my_expenses(user_id, status=None)

    # Métricas por estado
    estados = ["solicitado", "aprobado", "rechazado", "pagado"]
    counts = {e: 0 for e in estados}
    for r in rows_all:
        if r["status"] in counts:
            counts[r["status"]] += 1
    ccols = st.columns(len(estados))
    for i, e in enumerate(estados):
        ccols[i].metric(e.capitalize(), counts[e])

    st.divider()

    estado_filtro = st.selectbox(
        "Filtrar por estado",
        options= estados,
        index=0,
    )
    estado = None if estado_filtro == "(todos)" else estado_filtro

    rows = rows_all if estado is None else [r for r in rows_all if r["status"] == estado]
    if not rows:
        st.caption("No tienes solicitudes en este filtro.")
    else:

        def _signed_link(key: str) -> str:
            url = signed_url_for_receipt(key or "", expires=300)
            return f"[Documento]({url})" if url else ""

        df = pd.DataFrame(
    [
        {
            "Fecha": r["created_at"],
            "Proveedor": r["supplier_name"],
            "Monto": f"{r['amount']:.2f}",
            "Categoría": r["category"],
            "Descripción": r.get("description") or "",
            "Estado": r["status"],
            "Recibo": _signed_link(r.get("supporting_doc_key")),
        }
        for r in rows
    ]
)
        st.dataframe(df, use_container_width=True, hide_index=True)

# ==========================
# Tab 3: Detalles y actualizar
# ==========================
with tab_detalle:
    st.subheader("Detalles y actualización de una solicitud")

    mis = list_my_expenses(user_id, status=None)
    if not mis:
        st.caption("Aún no tienes solicitudes.")
        st.stop()

    # Selector de una solicitud
    opts = {
        f"{m['created_at']} — {m['supplier_name']} — {m['amount']:.2f} — {m['status']}": m["id"]
        for m in mis
    }
    sel_label = st.selectbox("Selecciona una solicitud", list(opts.keys()))
    sel_id = opts[sel_label]

    exp = get_my_expense(user_id, sel_id)
    if not exp:
        st.error("No se encontró la solicitud seleccionada.")
        st.stop()

    # Encabezado de detalles
    st.markdown(
        f"**Proveedor:** {exp['supplier_name']}  \n"
        f"**Monto:** {exp['amount']:.2f}  \n"
        f"**Categoría:** {exp['category']}  \n"
        f"**Descripción:** {exp.get('description','')}  \n"
        f"**Estado:** {exp['status']}  \n"
        f"**Fecha:** {exp['created_at']}"
)

    # Enlaces rápidos a archivos
    rec_key = exp.get("supporting_doc_key") or ""
    pay_key = exp.get("payment_doc_key") or ""
    rec_url = signed_url_for_receipt(rec_key, 600)
    pay_url = signed_url_for_payment(pay_key, 600)
    colf1, colf2 = st.columns(2)
    with colf1:
        if rec_url:
            st.link_button("Ver recibo", rec_url, use_container_width=True)
            try:
                resp = requests.get(rec_url, timeout=10)
                resp.raise_for_status()
                st.download_button(
                    "Descargar recibo",
                    resp.content,
                    file_name=os.path.basename(rec_key) if rec_key else "recibo",
                    use_container_width=True,
                    key=f"dl-recibo-{uuid.uuid4().hex}",
                )
            except Exception as e:
                st.caption(f"No se pudo descargar el recibo: {e}")
        else:
            st.download_button(
                "Descargar recibo",
                b"",
                file_name="recibo",
                use_container_width=True,
                disabled=True,
                key=f"dl-recibo-{uuid.uuid4().hex}",
            )
    with colf2:
        if pay_url:
            st.link_button("Ver comprobante de pago", pay_url, use_container_width=True)
            try:
                resp = requests.get(pay_url, timeout=10)
                resp.raise_for_status()
                st.download_button(
                    "Descargar comprobante",
                    resp.content,
                    file_name=os.path.basename(pay_key) if pay_key else "comprobante",
                    use_container_width=True,
                    key=f"dl-comprobante-{uuid.uuid4().hex}",
                )
            except Exception as e:
                st.caption(f"No se pudo descargar el comprobante: {e}")
        else:
            st.download_button(
                "Descargar comprobante",
                b"",
                file_name="comprobante",
                use_container_width=True,
                disabled=True,
                key=f"dl-comprobante-{uuid.uuid4().hex}",
            )

    st.divider()

    # Agregar comentario
    st.subheader("Agregar comentario")
    with st.form("form_comentario", clear_on_submit=True):
        txt = st.text_area("Comentario", placeholder="Escribe tu comentario…")
        if st.form_submit_button("Guardar comentario"):
            if not txt or not txt.strip():
                st.error("Escribe un comentario.")
            else:
                try:
                    add_expense_comment(sel_id, user_id, txt.strip())
                    st.success("Comentario agregado.")
                    st.rerun()
                except Exception as e:
                    st.error(f"No se pudo guardar el comentario: {e}")

    # Comentarios (solo los de tipo 'comment')
    st.subheader("Comentarios")
    comentarios = list_expense_comments(sel_id)
    if not comentarios:
        st.caption("No hay comentarios.")
    else:
        for c in comentarios:
            st.markdown(f"- **{c['created_at']}** — {c.get('actor_email','(sin email)')}: {c['text']}")

    st.divider()

    # Historial completo (logs)
    st.subheader("Historial de cambios")
    logs = list_expense_logs(sel_id)
    if not logs:
        st.caption("No hay historial.")
    else:
        for lg in logs:
            det_txt = lg.get("details_text", "")
            st.markdown(
                f"- **{lg['created_at']}** — {lg['action']} — {lg.get('actor_email','(sin email)')}  "
                + (f"— {det_txt}" if det_txt else "")
            )
