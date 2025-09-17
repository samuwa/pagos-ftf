# pages/pagador.py
# Rol Pagador: métricas, listas por estado, detalles+marcar pagado, historial

import mimetypes
import pandas as pd
import streamlit as st
import uuid
from pathlib import Path
from datetime import date

from f_auth import require_pagador, current_user, get_client
from f_read import (
    list_expenses_for_status,          # ya lo usamos en Aprobador
    get_expense_by_id_for_approver,    # sirve también para Pagador
    list_expense_logs,
    list_expense_comments,
    list_suppliers,
    list_categories,
    list_requesters_for_approver,      # reutilizamos
    list_expenses_by_supplier_id,
    list_expenses_by_category,
    list_expenses_by_requester,
    signed_url_for_receipt,
    signed_url_for_payment,
    payment_doc_url_for_expense,
    _render_download,
)

from f_cud import mark_expense_as_paid, add_expense_comment, update_expense_status

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


def _expense_label(expense: dict) -> str:
    """Texto consistente para identificar un gasto en selectores."""

    return (
        f"{expense['supplier_name']} — {expense.get('description','')} — "
        f"{_fmt_dt(expense['created_at'])} — {expense.get('requested_by_email','')}"
    )


# ---------------------------------------------------
# Tabs
# ---------------------------------------------------
tab1, tab2, tab3 = st.tabs(["Solicitudes", "Detalles y marcar pagado", "Historial"])

st.session_state.setdefault("pagador_resumen_refresh_token", 0)
st.session_state.setdefault("pagador_historial_refresh_token", 0)
st.session_state.setdefault("pagador_resumen_estado", "aprobado")
st.session_state.setdefault("pagador_estado_sel", "aprobado")
st.session_state.setdefault("pagador_sel", "")
st.session_state.setdefault("pagador_comment", "")
st.session_state.setdefault("pagador_selected_expense_id", None)

# ---------------------------------------------------
# Tab 1 — Solicitudes
# ---------------------------------------------------
with tab1:
    with st.fragment("pagador_resumen"):
        st.write("**Solicitudes**")

        _ = st.session_state.get("pagador_resumen_refresh_token")
        all_rows = list_expenses_for_status(status=None) or []

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
        estados_opts = ["(todos)"] + ESTADOS
        default_status = st.session_state.get("pagador_resumen_estado", "aprobado")
        if default_status not in estados_opts:
            default_status = estados_opts[0]
        selected_status = st.selectbox(
            "Filtrar por estado",
            options=estados_opts,
            index=estados_opts.index(default_status),
            key="pagador_resumen_estado",
        )
        rows = (
            all_rows
            if selected_status == "(todos)"
            else [r for r in all_rows if r["status"] == selected_status]
        )

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
    with st.fragment("pagador_detalle"):
        st.write("**Detalles y marcar pagado**")

        estado_options = ["aprobado", "pagado", "solicitado", "rechazado"]
        st.session_state.setdefault("pagador_estado_sel", estado_options[0])
        estado_sel = st.radio(
            "Elegir estado para seleccionar solicitudes:",
            options=estado_options,
            horizontal=True,
            key="pagador_estado_sel",
        )

        state_rows = list_expenses_for_status(status=estado_sel) or []
        rows = list(state_rows)
        selected_id = st.session_state.get("pagador_selected_expense_id")
        if selected_id and all(r["id"] != selected_id for r in rows):
            extra = get_expense_by_id_for_approver(selected_id)
            if extra:
                rows.append(extra)

        labels = [""]
        label_to_id = {}
        for r in rows:
            label = _expense_label(r)
            if label not in label_to_id:
                labels.append(label)
                label_to_id[label] = r["id"]

        if len(labels) == 1:
            st.caption("No hay solicitudes en este estado.")

        sel_label = st.selectbox(
            "Selecciona una solicitud",
            labels,
            key="pagador_sel",
        )

        if sel_label:
            expense_id = label_to_id.get(sel_label)
            if not expense_id:
                st.error("No se encontró la solicitud seleccionada.")
            else:
                st.session_state.pagador_selected_expense_id = expense_id
                exp = get_expense_by_id_for_approver(expense_id)
                if not exp:
                    st.error("No se encontró la solicitud seleccionada.")
                else:
                    rec_key = exp.get("supporting_doc_key")
                    pay_key = exp.get("payment_doc_key")
                    left, mid, right = st.columns([2, 1, 3])

                    # ---- Izquierda: detalles + docs + logs + comentarios
                    with left:
                        detalles_md = (
                            f"**Proveedor:** {exp['supplier_name']}  \n"
                            f"**Descripción:** {exp.get('description','')}  \n"
                            f"**Monto:** {exp['amount']:.2f}  \n"
                            f"**Categoría:** {exp['category']}  \n"
                            f"**Estado actual:** {exp['status']}  \n"
                            f"**Creado:** {_fmt_dt(exp['created_at'])}  \n"
                            f"**Solicitante:** {exp.get('requested_by_email','')}  \n"
                            f"**Reembolso:** {'Sí' if exp.get('reimbursement') else 'No'}"
                        )
                        if exp.get("reimbursement"):
                            detalles_md += (
                                f"  \n**Persona a reembolsar:** {exp.get('reimbursement_person') or '(no especificada)'}"
                            )
                        st.markdown(detalles_md)

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
                                [
                                    {
                                        "Fecha": _fmt_dt(l["created_at"]),
                                        "Actor": l.get("actor_email", ""),
                                        "Mensaje": l.get("message", ""),
                                    }
                                    for l in logs
                                ]
                            )
                            st.dataframe(log_df, use_container_width=True, hide_index=True)
                        else:
                            st.caption("Sin historial.")

                        st.write("**Comentarios**")
                        comments = list_expense_comments(expense_id)
                        if comments:
                            com_df = pd.DataFrame(
                                [
                                    {
                                        "Fecha": _fmt_dt(c["created_at"]),
                                        "Autor": c.get("actor_email", ""),
                                        "Comentario": c["message"],
                                    }
                                    for c in comments
                                ]
                            )
                            st.dataframe(com_df, use_container_width=True, hide_index=True)
                        else:
                            st.caption("No hay comentarios.")

                    # ---- Medio: historial del proveedor
                    with right:
                        st.write("**Historial del proveedor**")
                        sup_id = exp.get("supplier_id")
                        hist_rows = list_expenses_by_supplier_id(sup_id) if sup_id else []
                        if hist_rows:
                            hist_df = pd.DataFrame(
                                [
                                    {
                                        "Descripción": r.get("description", "") or "",
                                        "Monto": f"{r['amount']:.2f}",
                                        "Categoría": r["category"],
                                        "Estado": r["status"],
                                        "Solicitante": r.get("requested_by_email", ""),
                                        "Creado": _fmt_dt(r["created_at"]),
                                    }
                                    for r in hist_rows
                                ]
                            )
                            st.dataframe(hist_df, use_container_width=True, hide_index=True)
                        else:
                            st.caption("Sin historial.")

                    # ---- Derecha: marcar pagado / comentario
                    with mid:
                        st.write("**Actualizar estado / marcar pagado**")

                        estados_pagador = ["aprobado", "pagado", "rechazado"]  # Pagador puede rechazar
                        new_status = st.selectbox(
                            "Nuevo estado",
                            options=estados_pagador,
                            index=estados_pagador.index(exp["status"]) if exp["status"] in estados_pagador else 0,
                        )

                        existing_payment_date_raw = exp.get("payment_date")
                        existing_payment_date = None
                        if existing_payment_date_raw:
                            try:
                                existing_payment_date = pd.to_datetime(existing_payment_date_raw).date()
                            except Exception:
                                existing_payment_date = None

                        payment_date = existing_payment_date or date.today()
                        if new_status == "pagado":
                            hoy = st.checkbox(
                                "fecha de pago hoy",
                                value=existing_payment_date is None,
                            )
                            if hoy:
                                payment_date = date.today()
                            else:
                                payment_date = st.date_input(
                                    "Fecha de pago",
                                    value=payment_date,
                                )
                        else:
                            payment_date = None
                        pay_file = st.file_uploader(
                            "Comprobante de pago (opcional)",
                            type=["pdf", "png", "jpg", "jpeg", "webp"],
                        )
                        comment = st.text_area("Comentario (opcional)", key="pagador_comment")

                        if st.button("Guardar cambios", type="primary", use_container_width=True):
                            try:
                                comment_clean = (comment or "").strip()
                                status_changed = new_status != exp["status"]
                                triggered_refresh = False

                                if new_status == "pagado":
                                    payment_doc_key = None
                                    if pay_file:
                                        sb = get_client()
                                        bucket = "payments"
                                        file_id = uuid.uuid4().hex + Path(pay_file.name).suffix
                                        sb.storage.from_(bucket).upload(
                                            file_id,
                                            pay_file.getvalue(),
                                            {"content-type": pay_file.type},
                                        )
                                        payment_doc_key = file_id

                                    payment_date_dt = payment_date or date.today()
                                    payment_date_changed = False
                                    if status_changed:
                                        payment_date_changed = True
                                    elif existing_payment_date:
                                        payment_date_changed = payment_date_dt != existing_payment_date
                                    else:
                                        payment_date_changed = True

                                    if status_changed or payment_doc_key or payment_date_changed:
                                        mark_expense_as_paid(
                                            expense_id=expense_id,
                                            actor_id=user_id,
                                            payment_doc_key=payment_doc_key,
                                            payment_date=payment_date_dt.strftime("%Y-%m-%d"),
                                            comment=comment_clean or None,
                                        )
                                        msg = "Solicitud marcada como pagada."
                                        if not status_changed:
                                            msg = "Solicitud actualizada."
                                        st.success(msg)
                                        triggered_refresh = True
                                        if status_changed:
                                            st.session_state.pagador_resumen_refresh_token += 1
                                            st.session_state.pagador_historial_refresh_token += 1
                                    elif comment_clean:
                                        add_expense_comment(expense_id, user_id, comment_clean)
                                        st.success("Comentario agregado.")
                                        triggered_refresh = True
                                    else:
                                        st.info("No hay cambios que guardar.")
                                else:
                                    if pay_file:
                                        st.warning("El comprobante de pago solo se adjunta al marcar como pagado.")
                                    if comment_clean:
                                        add_expense_comment(expense_id, user_id, comment_clean)
                                        st.success("Comentario agregado.")
                                        triggered_refresh = True
                                    else:
                                        st.info("No hay cambios que guardar.")

                                if triggered_refresh:
                                    st.session_state.pagador_comment = ""
                                    st.session_state.pagador_estado_sel = new_status
                                    st.session_state.pagador_sel = _expense_label(exp)
                                    st.rerun(fragment="pagador_detalle")
                            except Exception as e:
                                st.error(f"No se pudo actualizar: {e}")
        else:
            st.session_state.pagador_selected_expense_id = None

# ---------------------------------------------------
# Tab 3 — Historial
# ---------------------------------------------------
with tab3:
    with st.fragment("pagador_historial"):
        st.write("**Historial**")

        _ = st.session_state.get("pagador_historial_refresh_token")

        modo_opts = ["Proveedores", "Categorías", "Solicitantes"]
        st.session_state.setdefault("pagador_historial_modo", modo_opts[0])
        modo = st.radio(
            "Ver por:",
            options=modo_opts,
            horizontal=True,
            key="pagador_historial_modo",
        )

        rows = []
        has_options = True

        if modo == "Proveedores":
            sups = list_suppliers()
            if not sups:
                has_options = False
                st.caption("No hay proveedores.")
            else:
                sup_names = [s["name"] for s in sups]
                sup_map = {s["name"]: s["id"] for s in sups}
                if st.session_state.get("pagador_historial_supplier") not in sup_map:
                    st.session_state["pagador_historial_supplier"] = sup_names[0]
                sel_sup_name = st.selectbox(
                    "Proveedor",
                    sup_names,
                    key="pagador_historial_supplier",
                )
                rows = list_expenses_by_supplier_id(sup_map[sel_sup_name])

        elif modo == "Categorías":
            cats = list_categories()
            if not cats:
                has_options = False
                st.caption("No hay categorías.")
            else:
                if st.session_state.get("pagador_historial_category") not in cats:
                    st.session_state["pagador_historial_category"] = cats[0]
                sel_cat = st.selectbox(
                    "Categoría",
                    cats,
                    key="pagador_historial_category",
                )
                rows = list_expenses_by_category(sel_cat)

        else:  # "Solicitantes"
            reqs = list_requesters_for_approver()
            if not reqs:
                has_options = False
                st.caption("No hay solicitantes con gastos.")
            else:
                emails = [r["email"] for r in reqs]
                req_map = {r["email"]: r["id"] for r in reqs}
                if st.session_state.get("pagador_historial_requester") not in req_map:
                    st.session_state["pagador_historial_requester"] = emails[0]
                sel_email = st.selectbox(
                    "Solicitante",
                    emails,
                    key="pagador_historial_requester",
                )
                rows = list_expenses_by_requester(req_map[sel_email])

        if has_options:
            if not rows:
                st.caption("No hay gastos para este filtro.")
            else:
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

                label_to_id = {}
                labels = []
                for r in rows:
                    label = _expense_label(r)
                    label_to_id[label] = r["id"]
                    labels.append(label)

                if labels:
                    current_hist_sel = st.session_state.get("pagador_historial_sel")
                    if current_hist_sel not in label_to_id:
                        st.session_state["pagador_historial_sel"] = labels[0]
                    sel_label = st.selectbox(
                        "Selecciona una solicitud para revisar",
                        labels,
                        key="pagador_historial_sel",
                    )
                    eid = label_to_id.get(sel_label)

                    if eid:
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
                                    [
                                        {
                                            "Fecha": _fmt_dt(l["created_at"]),
                                            "Actor": l.get("actor_email", ""),
                                            "Mensaje": l.get("message", ""),
                                        }
                                        for l in logs
                                    ]
                                )
                                st.write("**Historial (logs)**")
                                st.dataframe(log_df, use_container_width=True, hide_index=True)

                            comments = list_expense_comments(eid)
                            if comments:
                                com_df = pd.DataFrame(
                                    [
                                        {
                                            "Fecha": _fmt_dt(c["created_at"]),
                                            "Autor": c.get("actor_email", ""),
                                            "Comentario": c["message"],
                                        }
                                        for c in comments
                                    ]
                                )
                                st.write("**Comentarios**")
                                st.dataframe(com_df, use_container_width=True, hide_index=True)
                            else:
                                st.caption("No hay comentarios.")
                        else:
                            st.warning("No se encontró la solicitud seleccionada.")
                else:
                    st.caption("No hay solicitudes para revisar.")
