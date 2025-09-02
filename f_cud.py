# f_cud.py
# Create/Update/Delete actions for Admin (allowlist, roles, suppliers)
import streamlit as st
from typing import List, Optional, Dict, Any
from f_auth import get_client
from f_read import get_user_id_by_email

# ------------- Allowlist (app_users) -------------

def add_app_user(email: str) -> None:
    sb = get_client()
    email_norm = (email or "").strip().lower()
    if not email_norm:
        raise ValueError("Correo inválido.")
    sb.schema("public").table("app_users").upsert({"email": email_norm}).execute()

def delete_app_user(email: str) -> None:
    sb = get_client()
    email_norm = (email or "").strip().lower()
    sb.schema("public").table("app_users").delete().eq("email", email_norm).execute()

# ------------- Passwords -------------

def update_user_password(email: str, new_password: str) -> None:
    sb = get_client()
    email_norm = (email or "").strip()
    if not email_norm:
        raise ValueError("Correo inválido.")
    sb.schema("public").table("users").update({"password": new_password}).eq("email", email_norm).execute()

# ------------- Roles (user_roles) -------------

VALID_ROLES = {"administrador", "solicitante", "aprobador", "pagador", "lector"}

def set_user_roles(user_id: str, roles: List[str]) -> None:
    """
    Replace all roles for a user with the given set (atomic).
    Requires the user already exists in public.users (i.e., has logged in at least once).
    """
    if not user_id:
        raise ValueError("Usuario inválido.")
    roles_clean = [r for r in roles if r in VALID_ROLES]
    sb = get_client()
    # Remove current roles
    sb.schema("public").table("user_roles").delete().eq("user_id", user_id).execute()
    # Insert new ones (if any)
    if roles_clean:
        rows = [{"user_id": user_id, "role": r} for r in roles_clean]
        sb.schema("public").table("user_roles").insert(rows).execute()

def set_user_roles_by_email(email: str, roles: List[str]) -> None:
    """
    Convenience: assign roles by email if the user has already logged in.
    """
    uid = get_user_id_by_email(email)
    if not uid:
        raise RuntimeError("El usuario aún no ha iniciado sesión; no se puede asignar roles.")
    set_user_roles(uid, roles)

# ------------- Suppliers -------------

def create_supplier(name: str) -> None:
    sb = get_client()
    nm = (name or "").strip()
    if not nm:
        raise ValueError("Nombre inválido.")
    sb.schema("public").table("suppliers").insert({"name": nm}).execute()

def assign_role(user_id: str, role: str) -> None:
    """
    Grant a single role (Spanish enum string) to the user.
    Idempotent: won't error if the role is already assigned.
    """
    if not user_id:
        raise ValueError("Usuario inválido.")
    role_es = (role or "").strip().lower()
    if role_es not in VALID_ROLES:
        raise ValueError(f"Rol inválido: {role_es}")
    sb = get_client()
    (
        sb.schema("public")
        .table("user_roles")
        .upsert({"user_id": user_id, "role": role_es})
        .execute()
    )

def remove_role(user_id: str, role: str) -> None:
    """
    Remove a single role from the user. No-op if not present.
    """
    if not user_id:
        raise ValueError("Usuario inválido.")
    role_es = (role or "").strip().lower()
    if role_es not in VALID_ROLES:
        raise ValueError(f"Rol inválido: {role_es}")
    sb = get_client()
    (
        sb.schema("public")
        .table("user_roles")
        .delete()
        .eq("user_id", user_id)
        .eq("role", role_es)
        .execute()
    )

def create_expense_log(expense_id: str, actor_id: str, action: str, details: Optional[Dict[str, Any]] = None) -> None:
    """
    Inserta un log en expense_logs. 'action' debe respetar el CHECK del schema.
    details es JSON libre (comentarios, diffs, etc.).
    """
    if not (expense_id and actor_id and action):
        raise ValueError("Faltan datos para crear el log.")
    sb = get_client()
    payload = {
        "expense_id": expense_id,
        "actor_id": actor_id,
        "action": action,
        "details": (details or {}),
    }
    sb.schema("public").table("expense_logs").insert(payload).execute()


def create_expense(
    requested_by: str,
    supplier_id: str,
    amount: float,
    category: str,
    supporting_doc_key: str,
    description: Optional[str] = None,   # <--- NEW
) -> Optional[str]:
    """
    Crea un expense (status 'solicitado') con descripción opcional.
    """
    sb = get_client()
    payload = {
        "requested_by": requested_by,
        "supplier_id": supplier_id,
        "amount": round(float(amount), 2),
        "category": category,
        "supporting_doc_key": supporting_doc_key,
    }
    if description:
        payload["description"] = description

    res = (
        sb.schema("public")
        .table("expenses")
        .insert(payload, returning="representation")
        .execute()
    )
    data = res.data or []
    expense_id = data[0]["id"] if data else None

    if expense_id:
        try:
            create_expense_log(
                expense_id=expense_id,
                actor_id=requested_by,
                action="create",
                details={
                    "kind": "create",
                    "supplier_id": supplier_id,
                    "amount": round(float(amount), 2),
                    "category": category,
                    "supporting_doc_folder": supporting_doc_key,
                    "description": description,
                },
            )
        except Exception:
            pass

    return expense_id

def add_expense_comment(expense_id: str, actor_id: str, text: str) -> None:
    """Guarda un comentario para la solicitud sin generar un nuevo log."""
    if not (expense_id and actor_id and (text or "").strip()):
        raise ValueError("Faltan datos para comentar.")
    sb = get_client()
    payload = {
        "expense_id": expense_id,
        "actor_id": actor_id,
        "text": text.strip(),
    }
    sb.schema("public").table("expense_comments").insert(payload).execute()


## APROBADOR


def update_expense_status(expense_id: str, actor_id: str, new_status: str, comment: Optional[str] = None) -> None:
    """
    Cambia el estado de una solicitud (aprobado/rechazado).
    Inserta un log con acción 'update'.
    """
    if new_status not in ("aprobado", "rechazado"):
        raise ValueError("Estado inválido para aprobador.")

    sb = get_client()
    # Update en expenses
    sb.schema("public").table("expenses").update({"status": new_status}).eq("id", expense_id).execute()

    # Log
    details = {"kind": "status_change", "new_status": new_status}
    if comment:
        details["comment"] = comment
    create_expense_log(expense_id, actor_id, action="update", details=details)


VALID_FOR_APPROVER = {"solicitado", "aprobado", "rechazado"}  # 'pagado' is for Pagador

def update_expense_status(expense_id: str, actor_id: str, new_status: str, comment: Optional[str] = None) -> None:
    """
    Cambia estado a 'solicitado'/'aprobado'/'rechazado' y agrega un log.
    Si el estado es 'aprobado' o 'rechazado', también establece approved_by = actor_id.
    """
    ns = (new_status or "").strip().lower()
    if ns not in VALID_FOR_APPROVER:
        raise ValueError("Estado inválido para aprobador.")

    sb = get_client()
    update = {"status": ns}
    if ns in ("aprobado", "rechazado"):
        update["approved_by"] = actor_id

    sb.schema("public").table("expenses").update(update).eq("id", expense_id).execute()

    details = {"kind": "status_change", "new_status": ns}
    if comment:
        details["comment"] = comment
    create_expense_log(expense_id, actor_id, action="update", details=details)

def mark_expense_as_paid(expense_id: str, actor_id: str, payment_doc_folder: str, comment: Optional[str] = None) -> None:
    """
    Actualiza el expense a 'pagado', setea payment_doc_key con la CARPETA y registra un log.
    Requiere que el comprobante haya sido subido previamente a 'quotes/{folder}/archivo'.
    """
    if not (expense_id and actor_id and (payment_doc_folder or "").strip()):
        raise ValueError("Faltan datos para marcar como pagado.")
    sb = get_client()
    sb.schema("public").table("expenses").update(
        {"status": "pagado", "payment_doc_key": payment_doc_folder.strip(), "paid_by": actor_id}
    ).eq("id", expense_id).execute()

    details = {"kind": "status_change", "new_status": "pagado", "payment_doc_folder": payment_doc_folder}
    if comment:
        details["comment"] = comment
    create_expense_log(expense_id, actor_id, action="update", details=details)

