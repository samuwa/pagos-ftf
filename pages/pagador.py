# pages/pagador.py
# Rol Pagador: métricas, listas por estado, detalles+marcar pagado, historial

import pandas as pd
import streamlit as st
import uuid
from pathlib import Path

from f_auth import require_pagador, current_user, get_client
from f_read import (
    list_expenses_for_status,          # ya lo usamos en Aprobador
    get_expense_by_id_for_approver,    # sirve también para Pagador
    list_expense_logs,
    list_expense_comments,
    list_suppliers,
    list_categories_from_expenses,
    list_requesters_for_approver,      # reutilizamos
    list_expenses_by_supplier_id,
    list_expenses_by_category,
    list_expenses_by_requester,
    signed_url_for_receipt,
    signed_url_for_payment,
    payment_doc_url_for_expense,
    _render_download,
)

from f_cud import mark_expense_as_paid, add_expense_comment

st.set_page_config(page_title="Pagador", page_icon="💸", layout="wide")
require_pagador()

me = current_user()
if not me:
    st.stop()
user_id = me["id"]

ESTADOS = ["solicitado", "aprobado", "rechazado", "pagado"]

def _fmt_dt(s: str) -> str:
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return s


# ---------------------------------------------------
# Tabs
# ---------------------------------------------------
tab1, tab2, tab3 = st.tabs(["Solicitudes", "Detalles y marcar pagado", "Historial"])

# ---------------------------------------------------
# Tab 1 — Solicitudes
# ---------------------------------------------------
with tab1:
    st.write("**Solicitudes**")

    all_rows = list_expenses_for_status(status=None)

    # Métricas por estado
    counts = {e: 0 for e in ESTADOS}
    for r in all_rows:
        if r["status"] in counts:
            counts[r["status"]] += 1
    cols = st.columns(len(ESTADOS))
    for i, e in enumerate(ESTADOS):
        cols[i].metric(e.capitalize(), counts[e])

    st.divider()

    # Filtro por estado (por defecto, 'aprobado' es el más relevante para Pagador)
    default_index = 1  # "(todos)"=0, 'aprobado'=1 si armamos la lista dinámicamente
    estados_opts = ["(todos)"] + ESTADOS
    selected_status = st.selectbox(
        "Filtrar por estado",
        options=estados_opts,
        index=estados_opts.index("aprobado") if "aprobado" in estados_opts else 0,
    )
    rows = all_rows if selected_status == "(todos)" else [r for r in all_rows if r["status"] == selected_status]

    if not rows:
        st.caption("No hay solicitudes para este filtro.")
    else:
        df = pd.DataFrame(
            [
                {
                    "Solicitante": r.get("requested_by_email", ""),
                    "Monto": f"{r['amount']:.2f}",
                    "Descripción": r.get("description") or "",
                    "Categoría": r["category"],
                    "Proveedor": r["supplier_name"],
                    "Creado": _fmt_dt(r["created_at"]),
                }
                for r in rows
            ]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)

# ---------------------------------------------------
# Tab 2 — Detalles y marcar pagado
# ---------------------------------------------------
with tab2:
    st.write("**Detalles y marcar pagado**")

    # Elegir estado desde el cual seleccionar (tiene sentido 'aprobado' y 'pagado')
    estado_sel = st.radio(
        "Elegir estado para seleccionar solicitudes:",
        options=["aprobado", "pagado", "solicitado", "rechazado"],
        horizontal=True,
        index=0,
    )
    rows = list_expenses_for_status(status=estado_sel)
    if not rows:
        st.caption("No hay solicitudes en este estado.")
        st.stop()

    # Selector: "proveedor — descripcion — creado — solicitante"
    opts = {
        f"{r['supplier_name']} — {r.get('description','')} — {_fmt_dt(r['created_at'])} — {r.get('requested_by_email','')}"
        : r["id"]
        for r in rows
    }
    sel_label = st.selectbox("Selecciona una solicitud", list(opts.keys()))
    expense_id = opts[sel_label]

    exp = get_expense_by_id_for_approver(expense_id)
    if not exp:
        st.error("No se encontró la solicitud seleccionada.")
        st.stop()

    left, right = st.columns([2, 1])

    # ---- Izquierda: detalles + docs + logs + comentarios
    with left:
        st.markdown(
            f"**Proveedor:** {exp['supplier_name']}  \n"
            f"**Descripción:** {exp.get('description','')}  \n"
            f"**Monto:** {exp['amount']:.2f}  \n"
            f"**Categoría:** {exp['category']}  \n"
            f"**Estado actual:** {exp['status']}  \n"
            f"**Creado:** {_fmt_dt(exp['created_at'])}  \n"
            f"**Solicitante:** {exp.get('requested_by_email','')}"
        )

        rec_key = exp.get("supporting_doc_key")

        pay_key = exp.get("payment_doc_key")
        cols_files = st.columns(2)
        with cols_files[0]:
            _render_download(rec_key, "Documento de respaldo", signed_url_for_receipt)
        with cols_files[1]:
            _render_download(pay_key, "Comprobante de pago", signed_url_for_payment)


        st.divider()
        st.write("**Historial (logs)**")
        logs = list_expense_logs(expense_id)
        if logs:
            log_df = pd.DataFrame(
                [{"Fecha": _fmt_dt(l["created_at"]), "Actor": l.get("actor_email",""), "Mensaje": l.get("message", "")} for l in logs]
            )
            st.dataframe(log_df, use_container_width=True, hide_index=True)
        else:
            st.caption("Sin historial.")

        st.write("**Comentarios**")
        comments = list_expense_comments(expense_id)
        if comments:
            com_df = pd.DataFrame(
                [{"Fecha": _fmt_dt(c["created_at"]), "Autor": c.get("actor_email",""), "Comentario": c["message"]} for c in comments]
            )
            st.dataframe(com_df, use_container_width=True, hide_index=True)
        else:
            st.caption("No hay comentarios.")

    # ---- Derecha: marcar pagado / comentario
    with right:
        st.write("**Actualizar estado / marcar pagado**")

        estados_pagador = ["aprobado", "pagado"]  # Pagador solo debería usar estos
        new_status = st.selectbox(
            "Nuevo estado",
            options=estados_pagador,
            index=estados_pagador.index(exp["status"]) if exp["status"] in estados_pagador else 0,
        )

        pay_file = st.file_uploader(
            "Comprobante de pago (obligatorio si marcas 'Pagado')",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
        )
        comment = st.text_area("Comentario (opcional)")

        if st.button("Guardar cambios", type="primary", use_container_width=True):
            try:
                # Solo comentario
                if new_status == exp["status"] and (comment or "").strip() and not pay_file:
                    add_expense_comment(expense_id, user_id, comment.strip())
                    st.success("Comentario agregado.")
                    st.rerun()

                # Marcar como pagado → requiere archivo
                elif new_status == "pagado":
                    if not pay_file:
                        st.error("Debes adjuntar un comprobante para marcar como pagado.")
                        st.stop()

                    # Subir archivo al bucket 'payments' con un identificador único
                    sb = get_client()
                    bucket = "payments"
                    file_id = uuid.uuid4().hex + Path(pay_file.name).suffix
                    sb.storage.from_(bucket).upload(
                        file_id,
                        pay_file.getvalue(),
                        {"content-type": pay_file.type},
                    )

                    # Actualizar estado + payment_doc_key y log
                    mark_expense_as_paid(
                        expense_id=expense_id,
                        actor_id=user_id,
                        payment_doc_key=file_id,
                        comment=(comment or "").strip() or None,
                    )
                    st.success("Solicitud marcada como pagada.")
                    st.rerun()

                # Cambiar de pagado → aprobado (raro) o simplemente de aprobado sin archivo
                else:
                    # Si baja a 'aprobado' no hay comprobante; si sube a 'pagado' ya lo tratamos arriba
                    # En este caso (aprobado) permitimos solo comentario adicional
                    if (comment or "").strip():
                        add_expense_comment(expense_id, user_id, comment.strip())
                        st.success("Comentario agregado.")
                        st.rerun()
                    else:
                        st.info("No hay cambios que guardar.")
            except Exception as e:
                st.error(f"No se pudo actualizar: {e}")

# ---------------------------------------------------
# Tab 3 — Historial
# ---------------------------------------------------
with tab3:
    st.write("**Historial**")

    modo = st.radio(
        "Ver por:",
        options=["Proveedores", "Categorías", "Solicitantes"],
        horizontal=True,
    )

    if modo == "Proveedores":
        sups = list_suppliers()
        if not sups:
            st.caption("No hay proveedores.")
            st.stop()
        sup_map = {s["name"]: s["id"] for s in sups}
        sel_sup_name = st.selectbox("Proveedor", list(sup_map.keys()))
        rows = list_expenses_by_supplier_id(sup_map[sel_sup_name])

    elif modo == "Categorías":
        cats = list_categories_from_expenses()
        if not cats:
            st.caption("No hay categorías.")
            st.stop()
        sel_cat = st.selectbox("Categoría", cats)
        rows = list_expenses_by_category(sel_cat)

    else:  # "Solicitantes"
        reqs = list_requesters_for_approver()
        if not reqs:
            st.caption("No hay solicitantes con gastos.")
            st.stop()
        req_map = {r["email"]: r["id"] for r in reqs}
        sel_email = st.selectbox("Solicitante", list(req_map.keys()))
        rows = list_expenses_by_requester(req_map[sel_email])

    if not rows:
        st.caption("No hay gastos para este filtro.")
        st.stop()

    df = pd.DataFrame(
        [
            {
                "Proveedor": r["supplier_name"],
                "Descripción": r.get("description", "") or "",
                "Monto": f"{r['amount']:.2f}",
                "Categoría": r["category"],
                "Estado": r["status"],
                "Solicitante": r.get("requested_by_email", ""),
                "Creado": _fmt_dt(r["created_at"]),
            }
            for r in rows
        ]
    )
    st.dataframe(df, use_container_width=True, hide_index=True)

    opt_map = {
        f"{r['supplier_name']} — {r.get('description','')} — {_fmt_dt(r['created_at'])} — {r.get('requested_by_email','')}"
        : r["id"]
        for r in rows
    }
    sel_label = st.selectbox("Selecciona una solicitud para revisar", list(opt_map.keys()))
    eid = opt_map[sel_label]

    exp = get_expense_by_id_for_approver(eid)
    if exp:
        st.markdown(
            f"**Proveedor:** {exp['supplier_name']}  \n"
            f"**Descripción:** {exp.get('description','')}  \n"
            f"**Monto:** {exp['amount']:.2f}  \n"
            f"**Categoría:** {exp['category']}  \n"
            f"**Estado:** {exp['status']}  \n"
            f"**Solicitante:** {exp.get('requested_by_email','')}  \n"
            f"**Creado:** {_fmt_dt(exp['created_at'])}"
        )
        rec_key = exp.get("supporting_doc_key")

        pay_key = exp.get("payment_doc_key")
        cols_files = st.columns(2)
        with cols_files[0]:
            _render_download(rec_key, "Documento de respaldo", signed_url_for_receipt)
        with cols_files[1]:
            _render_download(pay_key, "Comprobante de pago", signed_url_for_payment)


        st.divider()
        logs = list_expense_logs(eid)
        if logs:
            log_df = pd.DataFrame(
                [{"Fecha": _fmt_dt(l["created_at"]), "Actor": l.get("actor_email",""), "Mensaje": l.get("message", "")} for l in logs]
            )
            st.write("**Historial (logs)**")
            st.dataframe(log_df, use_container_width=True, hide_index=True)

        comments = list_expense_comments(eid)
        if comments:
            com_df = pd.DataFrame(
                [{"Fecha": _fmt_dt(c["created_at"]), "Autor": c.get("actor_email",""), "Comentario": c["message"]} for c in comments]
            )
            st.write("**Comentarios**")
            st.dataframe(com_df, use_container_width=True, hide_index=True)
