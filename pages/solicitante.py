# pages/solicitante.py
# Solicitudes: crear gasto, ver "Mis solicitudes", y "Detalles y actualizar"

import uuid
from pathlib import Path
from decimal import Decimal
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
    st.write("**Crear nueva solicitud**")

    # Estado inicial para widgets, así podemos reiniciarlos tras enviar
    if "sup_name" not in st.session_state:
        st.session_state["sup_name"] = ""
    if "monto" not in st.session_state:
        st.session_state["monto"] = 0.0
    if "descripcion" not in st.session_state:
        st.session_state["descripcion"] = ""
    if "comentario" not in st.session_state:
        st.session_state["comentario"] = ""
    if "archivo" not in st.session_state:
        st.session_state["archivo"] = None

    suppliers = list_suppliers()
    if not suppliers:
        st.info("Aún no hay proveedores. Pide a un administrador que cree al menos uno.")
    sup_opts = {s["name"]: {"id": s["id"], "category": s.get("category") or ""} for s in suppliers}
    sup_name = st.selectbox(
        "Proveedor *", options=[""] + list(sup_opts.keys()), key="sup_name"
    )
    sel_sup = sup_opts.get(sup_name) if sup_name else None
    supplier_id = sel_sup["id"] if sel_sup else None
    categoria = sel_sup["category"] if sel_sup else ""

    col_a, col_b = st.columns([1, 1])
    with col_a:
        amount = st.number_input(
            "Monto *", min_value=0.00, step=0.01, format="%.2f", key="monto"
        )
    with col_b:
        # Categoría fija, tomada del proveedor
        st.session_state["categoria"] = categoria
        st.selectbox(
            "Categoría *",
            options=[categoria] if categoria else [""],
            key="categoria",
            disabled=True,
        )

    descripcion = st.text_input(
        "Descripción breve *",
        placeholder="Ej. Suscripción anual de software...",
        key="descripcion",
    )

    file = st.file_uploader(
        "Documento de respaldo (recibo/factura) *",
        type=["pdf", "png", "jpg", "jpeg", "webp"],
        key="archivo",
    )
    comentario_inicial = st.text_area(
        "Comentario (opcional)",
        placeholder="Ej. Detalles útiles para aprobación…",
        key="comentario",
    )

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
    if st.button("Enviar solicitud", type="primary", use_container_width=False, disabled=not suppliers):
        if not supplier_id:
            st.error("Selecciona un proveedor.")
        elif not amount or amount <= 0:
            st.error("Ingresa un monto válido mayor a cero.")
        elif not categoria:
            st.error("Selecciona una categoría.")
        elif not file:
            st.error("Adjunta el documento de respaldo.")
        else:
            try:
                # Subir archivo a Storage (bucket 'quotes')
                sb = get_client()
                bucket = "quotes"  # tu bucket
                file_id = uuid.uuid4().hex + Path(file.name).suffix

                sb.storage.from_(bucket).upload(
                    file_id,
                    file.getvalue(),
                    {"content-type": file.type},
                )

                expense_id = create_expense(
                    requested_by=user_id,
                    supplier_id=supplier_id,
                    amount=float(Decimal(str(amount))),
                    category=categoria,
                    supporting_doc_key=file_id,
                    description=descripcion.strip() if descripcion else None,
                )

                # Comentario inicial (si hay)
                if expense_id and (comentario_inicial or "").strip():
                    add_expense_comment(expense_id, user_id, comentario_inicial.strip())

                st.success("Solicitud creada correctamente.")
                st.balloons()

                # Reiniciar campos
                st.session_state.sup_name = ""
                st.session_state.monto = 0.0
                st.session_state.descripcion = ""
                st.session_state.comentario = ""
                st.session_state.archivo = None
                st.session_state.categoria = ""

                st.rerun()
            except Exception as e:
                st.error(f"No se pudo crear la solicitud: {e}")

# ==========================
# Tab 2: Mis solicitudes
# ==========================
with tab_mias:
    st.write("**Mis solicitudes**")

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
        options=estados,
        index=0,
    )

    rows = [r for r in rows_all if r["status"] == estado_filtro]
    if not rows:
        st.caption("No tienes solicitudes en este filtro.")
    else:

        def _fmt_fecha(s: str) -> str:
            try:
                return pd.to_datetime(s).strftime("%Y-%m-%d")
            except Exception:
                return s


        df = pd.DataFrame(
            [
                {
                    "Fecha Creado": _fmt_fecha(r["created_at"]),
                    "Proveedor": r["supplier_name"],
                    "Monto": f"{r['amount']:.2f}",
                    "Categoría": r["category"],
                    "Descripción": r.get("description") or "",
                    "Estado": r["status"],

                }
                for r in rows
            ]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)

# ==========================
# Tab 3: Detalles y actualizar
# ==========================
with tab_detalle:
    st.write("**Detalles y actualización de una solicitud**")

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
        f"**Fecha Creado:** {pd.to_datetime(exp['created_at']).strftime('%Y-%m-%d')}"
)

    # Enlaces rápidos a archivos
    rec_key = exp.get("supporting_doc_key")
    pay_key = exp.get("payment_doc_key")
    rec_url = signed_url_for_receipt(rec_key, 600)
    pay_url = signed_url_for_payment(pay_key, 600)
    colf1, colf2 = st.columns(2)
    with colf1:
        st.link_button(
            "Ver recibo",
            rec_url or "#",
            use_container_width=True,
            disabled=not bool(rec_url),
        )
    with colf2:
        st.link_button(
            "Ver comprobante de pago",
            pay_url or "#",
            use_container_width=True,
            disabled=not bool(pay_url),
        )

    st.divider()

    # Agregar comentario
    st.write("**Agregar comentario**")
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

    def _fmt_dt(s: str) -> str:
        try:
            return pd.to_datetime(s).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return s

    # Comentarios (solo los de tipo 'comment')
    st.write("**Comentarios**")
    comentarios = list_expense_comments(sel_id)
    if not comentarios:
        st.caption("No hay comentarios.")
    else:
        com_df = pd.DataFrame(
            [
                {
                    "Fecha": _fmt_dt(c["created_at"]),
                    "Autor": c.get("actor_email", ""),
                    "Comentario": c["message"],
                }
                for c in comentarios
            ]
        )
        st.dataframe(com_df, use_container_width=True, hide_index=True)

    st.divider()

    # Historial completo (logs)
    st.write("**Historial de cambios**")
    logs = list_expense_logs(sel_id)
    if not logs:
        st.caption("No hay historial.")
    else:
        log_df = pd.DataFrame(
            [
                {
                    "Fecha": _fmt_dt(lg["created_at"]),
                    "Actor": lg.get("actor_email", ""),
                    "Mensaje": lg.get("message", ""),
                }
                for lg in logs
            ]
        )
        st.dataframe(log_df, use_container_width=True, hide_index=True)
