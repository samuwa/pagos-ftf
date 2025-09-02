# pages/aprobador.py
# Rol Aprobador: métricas, listas por estado, detalles+actualizar, historial

import pandas as pd
import streamlit as st
from f_auth import require_aprobador, current_user, get_client
from f_read import (
    list_expenses_for_status,          # -> for metrics/table in Tab 1
    get_expense_by_id_for_approver,    # -> full row for details
    list_expense_logs,
    list_expense_comments,
    signed_url_for_receipt,
    signed_url_for_payment,
    list_suppliers,
    list_categories_from_expenses,
    list_requesters_for_approver,
    list_expenses_by_supplier_id,
    list_expenses_by_category,
    list_expenses_by_requester,
    _render_download,
)
from f_cud import update_expense_status, add_expense_comment



st.set_page_config(page_title="Aprobador", page_icon="✅", layout="wide")
require_aprobador()

me = current_user()
if not me:
    st.stop()
user_id = me["id"]

ESTADOS = ["solicitado", "aprobado", "rechazado", "pagado"]

# ---------------------------------------------------
# Tab 1 — Solicitudes
# ---------------------------------------------------
tab1, tab2, tab3 = st.tabs(["Solicitudes", "Detalles y actualizar", "Historial"])

with tab1:
    st.subheader("Solicitudes")

    # Pull once for metrics (all statuses) and reuse
    all_rows = list_expenses_for_status(status=None)

    # Metrics row
    counts = {e: 0 for e in ESTADOS}
    for r in all_rows:
        if r["status"] in counts:
            counts[r["status"]] += 1
    cols = st.columns(len(ESTADOS))
    for i, e in enumerate(ESTADOS):
        cols[i].metric(e.capitalize(), counts[e])

    st.divider()

    # Filter by status
    selected_status = st.selectbox(
        "Filtrar por estado",
        options=["(todos)"] + ESTADOS,
        index=0,
    )
    rows = all_rows if selected_status == "(todos)" else [r for r in all_rows if r["status"] == selected_status]

    if not rows:
        st.caption("No hay solicitudes para este filtro.")
    else:
        # Build DataFrame with Spanish headers & formatted date
        def _fmt_dt(s: str) -> str:
            try:
                return pd.to_datetime(s).strftime("%Y-%m-%d %H:%M")
            except Exception:
                return s

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
# Tab 2 — Detalles y actualizar
# ---------------------------------------------------
with tab2:
    st.subheader("Detalles y actualizar")

    # Horizontal radio to choose status to select from
    estado_sel = st.radio(
        "Elegir estado para seleccionar solicitudes:",
        options=ESTADOS,
        horizontal=True,
    )

    rows = list_expenses_for_status(status=estado_sel)
    if not rows:
        st.caption("No hay solicitudes en este estado.")
        st.stop()

    # Build labels: "proveedor - descripcion - created_at - created_by"
    def _fmt_dt(s: str) -> str:
        try:
            return pd.to_datetime(s).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return s

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

    # ---- Left: details, logs, comments
    with left:
        rec_key = exp.get("supporting_doc_key")
        pay_key = exp.get("payment_doc_key")
        details_md = (
            f"**Proveedor:** {exp['supplier_name']}  \n"
            f"**Descripción:** {exp.get('description','')}  \n"
            f"**Monto:** {exp['amount']:.2f}  \n"
            f"**Categoría:** {exp['category']}  \n"
            f"**Estado actual:** {exp['status']}  \n"
            f"**Creado:** {_fmt_dt(exp['created_at'])}  \n"
            f"**Solicitante:** {exp.get('requested_by_email','')}"
        )
        st.markdown(details_md)
        cols_files = st.columns(2)
        with cols_files[0]:

            _render_download(rec_key, "Documento de respaldo", signed_url_for_receipt)
        with cols_files[1]:
            _render_download(pay_key, "Comprobante de pago", signed_url_for_payment)


        st.divider()
        st.subheader("Historial (logs)")
        logs = list_expense_logs(expense_id)
        if not logs:
            st.caption("Sin historial.")
        else:
            log_df = pd.DataFrame(
                [
                    {
                        "Fecha": _fmt_dt(lg["created_at"]),
                        "Acción": lg["action"],
                        "Actor": lg.get("actor_email", ""),
                        "Detalles": lg.get("details_text", ""),
                    }
                    for lg in logs
                ]
            )
            st.dataframe(log_df, use_container_width=True, hide_index=True)

        st.subheader("Comentarios")
        comments = list_expense_comments(expense_id)
        if not comments:
            st.caption("No hay comentarios.")
        else:
            com_df = pd.DataFrame(
                [
                    {
                        "Fecha": _fmt_dt(c["created_at"]),
                        "Autor": c.get("actor_email", ""),
                        "Comentario": c["text"],
                    }
                    for c in comments
                ]
            )
            st.dataframe(com_df, use_container_width=True, hide_index=True)

    # ---- Right: update status + add comment
    with right:
        st.subheader("Actualizar estado / agregar comentario")
        # Aprobador puede dejar 'solicitado', 'aprobado' o 'rechazado'
        estados_actualizables = ["solicitado", "aprobado", "rechazado"]
        new_status = st.selectbox(
            "Nuevo estado",
            options=estados_actualizables,
            index=estados_actualizables.index(exp["status"]) if exp["status"] in estados_actualizables else 0,
        )
        comment = st.text_area("Comentario (opcional)")

        if st.button("Guardar cambios", type="primary", use_container_width=True):
            try:
                if new_status == exp["status"] and comment.strip():
                    # Solo comentario
                    add_expense_comment(expense_id, user_id, comment.strip())
                else:
                    # Cambia estado (y opcionalmente agrega comentario en el log)
                    update_expense_status(expense_id, user_id, new_status, comment or None)
                st.success("Actualización guardada.")
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo actualizar: {e}")

# ---------------------------------------------------
# Tab 3 — Historial
# ---------------------------------------------------
with tab3:
    st.subheader("Historial")

    modo = st.radio(
        "Ver por:",
        options=["Proveedores", "Categorías", "Solicitantes"],
        horizontal=True,
    )

    # Load options
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

    # Table of all matching expenses + select one to view
    if not rows:
        st.caption("No hay gastos para este filtro.")
        st.stop()

    def _fmt_dt(s: str) -> str:
        try:
            return pd.to_datetime(s).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return s

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

    # Select one to show details, comments and logs
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
                [{"Fecha": _fmt_dt(l["created_at"]), "Acción": l["action"], "Actor": l.get("actor_email",""), "Detalles": l.get("details_text", "")} for l in logs]
            )
            st.subheader("Historial (logs)")
            st.dataframe(log_df, use_container_width=True, hide_index=True)

        comments = list_expense_comments(eid)
        if comments:
            com_df = pd.DataFrame(
                [{"Fecha": _fmt_dt(c["created_at"]), "Autor": c.get("actor_email",""), "Comentario": c["text"]} for c in comments]
            )
            st.subheader("Comentarios")
            st.dataframe(com_df, use_container_width=True, hide_index=True)
