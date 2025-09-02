import streamlit as st
from typing import List, Dict, Any, Optional, Tuple, Set, Iterable
from f_auth import get_client
from collections import defaultdict
import datetime as dt
import requests
from streamlit_pdf_viewer import pdf_viewer
import pandas as pd

# ==========================
# ==== AUTH AND ADMIN ======
# ==========================

def is_registered_email(email: str) -> bool:
    sb = get_client(use_session=False)
    q = (
        sb.table("app_users")
        .select("email")
        .eq("email", (email or "").strip().lower())
        .execute()
    )
    if q.data:
        return True
    # fallback: also allow if the email already has at least one role
    # (useful if admin assigned roles after first login)
    r = (
        sb.table("users")
        .select("id")
        .ilike("email", (email or "").strip())
        .limit(1)
        .execute()
    )
    if r.data:
        uid = r.data[0]["id"]
        roles = (
            sb.table("user_roles").select("role").eq("user_id", uid).limit(1).execute()
        )
        return bool(roles.data)
    return False

@st.cache_data(ttl=30, show_spinner=False)
def list_registered_users() -> List[Tuple[str, str]]:
    """
    Returns list of (user_id, email) for users who have logged in (public.users).
    """
    sb = get_client()
    res = sb.schema("public").table("users").select("id,email").order("email").execute()
    rows = res.data or []
    return [(r["id"], r.get("email") or "") for r in rows]

@st.cache_data(ttl=30, show_spinner=False)
def fetch_user_roles_map() -> Dict[str, Set[str]]:
    """
    Returns a mapping user_id -> set(roles) from public.user_roles.
    """
    sb = get_client()
    res = sb.schema("public").table("user_roles").select("user_id,role").execute()
    out: Dict[str, Set[str]] = {}
    for r in (res.data or []):
        out.setdefault(r["user_id"], set()).add(r["role"])
    return out

@st.cache_data(ttl=30, show_spinner=False)
def list_app_users() -> List[str]:
    """Emails allowed to request OTP (from public.app_users)."""
    sb = get_client()
    res = sb.schema("public").table("app_users").select("email").order("email").execute()
    return [row["email"] for row in (res.data or [])]

@st.cache_data(ttl=30, show_spinner=False)
def list_suppliers() -> List[Dict[str, Any]]:
    """Return suppliers as [{'id','name','created_at'}, ...]."""
    sb = get_client()
    res = (
        sb.schema("public")
        .table("suppliers")
        .select("id,name,created_at")
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []

def get_user_id_by_email(email: str) -> Optional[str]:
    """Get public.users.id by email (case-insensitive)."""
    sb = get_client()
    q = (
        sb.schema("public")
        .table("users")
        .select("id")
        .ilike("email", (email or "").strip())
        .limit(1)
        .execute()
    )
    if q.data:
        return q.data[0]["id"]
    return None

@st.cache_data(ttl=30, show_spinner=False)
def get_all_users() -> List[Dict[str, Any]]:
    """
    Return users with their roles for the Admin UI.
    Structure per user:
      {
        "id": str,
        "email": str,
        "display_name": str | None,   # reserved; we keep None unless you sync metadata
        "roles": [str, ...],          # e.g., ["administrador","aprobador"]
        "created_at": str | None
      }
    """
    sb = get_client()

    users_res = (
        sb.schema("public")
        .table("users")
        .select("id,email,created_at")
        .order("email")
        .execute()
    )
    users = users_res.data or []

    roles_res = (
        sb.schema("public")
        .table("user_roles")
        .select("user_id,role")
        .execute()
    )
    roles_map = defaultdict(list)
    for r in (roles_res.data or []):
        roles_map[r["user_id"]].append(r["role"])

    out: List[Dict[str, Any]] = []
    for u in users:
        out.append(
            {
                "id": u["id"],
                "email": u.get("email") or "",
                "display_name": None,  # placeholder; can later read from auth.users metadata
                "roles": sorted(roles_map.get(u["id"], [])),
                "created_at": u.get("created_at"),
            }
        )
    return out


# ==========================
# ==== Solicitador ======
# ==========================

@st.cache_data(ttl=30, show_spinner=False)
def list_my_expenses(user_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    sb = get_client()
    q = (
        sb.schema("public")
        .table("v_expenses_basic")
        .select("id,supplier_name,amount,category,status,supporting_doc_key,created_at,requested_by,description")  # <---
        .eq("requested_by", user_id)
        .order("created_at", desc=True)
    )
    if status:
        q = q.eq("status", status)
    res = q.execute()
    return res.data or []

@st.cache_data(ttl=20, show_spinner=False)
def recent_similar_expenses(supplier_id: str, amount: float, days: int = 30) -> List[Dict[str, Any]]:
    """
    Very simple duplicate hint: same supplier AND same amount within N days.
    Returns minimal fields for display.
    """
    sb = get_client()
    since = (dt.datetime.utcnow() - dt.timedelta(days=days)).isoformat() + "Z"
    res = (
        sb.schema("public")
        .table("expenses")
        .select("id,amount,category,status,supporting_doc_key,created_at")
        .eq("supplier_id", supplier_id)
        .eq("amount", round(float(amount), 2))
        .gte("created_at", since)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    return res.data or []

def signed_url_for_receipt(key: str, expires: int = 300) -> Optional[str]:
    """
    Create a short-lived signed URL for a receipt path (bucket 'receipts').
    """
    if not key:
        return None
    try:
        sb = get_client()
        out = sb.storage.from_("quotes").create_signed_url(key, expires)
        return (out or {}).get("signed_url")
    except Exception:
        return None

def _emails_by_ids(ids: Iterable[str]) -> dict:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    sb = get_client()
    res = sb.schema("public").table("users").select("id,email").in_("id", ids).execute()
    return {r["id"]: r.get("email") for r in (res.data or [])}

def get_my_expense(user_id: str, expense_id: str) -> Optional[Dict[str, Any]]:
    sb = get_client()
    res = (
        sb.schema("public")
        .table("v_expenses_basic")
        .select("id,supplier_name,amount,category,status,supporting_doc_key,payment_doc_key,created_at,requested_by,description")  # <---
        .eq("id", expense_id)
        .eq("requested_by", user_id)
        .single()
        .execute()
    )
    return res.data

def list_expense_logs(expense_id: str) -> List[Dict[str, Any]]:
    """
    Lista todos los logs de una solicitud y agrega actor_email.
    """
    sb = get_client()
    res = (
        sb.schema("public")
        .table("expense_logs")
        .select("actor_id,action,details,created_at")
        .eq("expense_id", expense_id)
        .order("created_at", desc=True)
        .execute()
    )
    rows = res.data or []
    emails = _emails_by_ids({r["actor_id"] for r in rows})
    for r in rows:
        r["actor_email"] = emails.get(r["actor_id"])
    return rows

def list_expense_comments(expense_id: str) -> List[Dict[str, Any]]:
    """Devuelve comentarios [{created_at, text, actor_email}, ...]"""
    sb = get_client()
    res = (
        sb.schema("public")
        .table("expense_comments")
        .select("actor_id,text,created_at")
        .eq("expense_id", expense_id)
        .order("created_at", desc=True)
        .execute()
    )
    rows = res.data or []
    emails = _emails_by_ids({r["actor_id"] for r in rows})
    out = []
    for r in rows:
        out.append({
            "created_at": r["created_at"],
            "text": r.get("text", ""),
            "actor_email": emails.get(r["actor_id"]),
        })
    return out

def signed_url_for_payment(key: str, expires: int = 300) -> Optional[str]:
    """
    Crea URL firmada corta para el comprobante de pago.
    En este MVP usamos el mismo bucket 'quotes'; si luego usas otro (p.ej. 'payments'),
    cambia aquí el nombre del bucket.
    """
    if not key:
        return None
    try:
        sb = get_client()
        out = sb.storage.from_("quotes").create_signed_url(key, expires)  # mismo bucket
        return (out or {}).get("signed_url")
    except Exception:
        return None


def _first_object_in_folder(bucket: str, folder: str) -> Optional[str]:
    """
    Devuelve la ruta (key) del primer archivo dentro de 'folder' en el bucket.
    Si 'folder' en realidad es ya un archivo, lo intentamos usar tal cual.
    """
    sb = get_client()
    folder = (folder or "").strip().strip("/")  # normaliza
    if not folder:
        return None

    # 1) Intento: listar archivos dentro del folder
    try:
        listing = sb.storage.from_(bucket).list(path=folder, sortBy={"column": "updated_at", "order": "desc"})
        if listing:
            # Tomamos el más reciente
            name = listing[0].get("name")
            if name:
                return f"{folder}/{name}"
    except Exception:
        pass

    # 2) Fallback: quizá 'folder' ya es una ruta de archivo
    return folder  # lo usamos tal cual

def signed_url_for_receipt(key_or_folder: str, expires: int = 300) -> Optional[str]:
    """
    Si key_or_folder es una carpeta, toma el primer archivo adentro y crea URL firmada.
    Bucket: 'quotes' (tu caso actual).
    """
    file_key = _first_object_in_folder("quotes", key_or_folder)
    if not file_key:
        return None
    try:
        sb = get_client()
        out = sb.storage.from_("quotes").create_signed_url(file_key, expires)
        return (out or {}).get("signed_url")
    except Exception:
        return None

def signed_url_for_payment(key_or_folder: str, expires: int = 300) -> Optional[str]:
    """
    Igual que el recibo. Si guardas el comprobante como carpeta, funciona igual.
    """
    file_key = _first_object_in_folder("quotes", key_or_folder)
    if not file_key:
        return None
    try:
        sb = get_client()
        out = sb.storage.from_("quotes").create_signed_url(file_key, expires)
        return (out or {}).get("signed_url")
    except Exception:
        return None

## APROBADOR

def _emails_by_ids(ids: Iterable[str]) -> dict:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    sb = get_client()
    res = sb.schema("public").table("users").select("id,email").in_("id", ids).execute()
    return {r["id"]: r.get("email") for r in (res.data or [])}

@st.cache_data(ttl=20, show_spinner=False)
def list_expenses_for_status(status: Optional[str]) -> List[Dict[str, Any]]:
    """
    For Aprobador: all expenses (optionally filtered by status) with requester email.
    """
    sb = get_client()
    q = (
        sb.schema("public")
        .table("v_expenses_basic")
        .select("id,supplier_name,amount,category,description,status,created_at,supporting_doc_key,requested_by")
        .order("created_at", desc=True)
    )
    if status:
        q = q.eq("status", status)
    res = q.execute()
    rows = res.data or []
    emails = _emails_by_ids({r["requested_by"] for r in rows})
    for r in rows:
        r["requested_by_email"] = emails.get(r["requested_by"], "")
    return rows

def get_expense_by_id_for_approver(expense_id: str) -> Optional[Dict[str, Any]]:
    sb = get_client()
    res = (
        sb.schema("public")
        .table("v_expenses_basic")
        .select("id,supplier_name,amount,category,description,status,created_at,supporting_doc_key,requested_by")
        .eq("id", expense_id)
        .single()
        .execute()
    )
    row = res.data
    if not row:
        return None
    row["requested_by_email"] = _emails_by_ids([row["requested_by"]]).get(row["requested_by"], "")
    return row

@st.cache_data(ttl=60, show_spinner=False)
def list_categories_from_expenses() -> List[str]:
    sb = get_client()
    res = sb.schema("public").table("expenses").select("category").execute()
    vals = sorted({(r["category"] or "").strip() for r in (res.data or []) if r.get("category")})
    return vals

@st.cache_data(ttl=30, show_spinner=False)
def list_requesters_for_approver() -> List[Dict[str, Any]]:
    """
    Distinct requesters with at least one expense.
    """
    sb = get_client()
    res = sb.schema("public").table("expenses").select("requested_by").execute()
    ids = sorted({r["requested_by"] for r in (res.data or []) if r.get("requested_by")})
    emails = _emails_by_ids(ids)
    return [{"id": i, "email": emails.get(i, "")} for i in ids]

@st.cache_data(ttl=20, show_spinner=False)
def list_expenses_by_supplier_id(supplier_id: str) -> List[Dict[str, Any]]:
    sb = get_client()
    res = (
        sb.schema("public")
        .table("v_expenses_basic")
        .select("id,supplier_name,amount,category,description,status,created_at,supporting_doc_key,requested_by")
        .execute()
    )
    rows = [r for r in (res.data or []) if r.get("supplier_name")]  # filter later if names collide
    # Better: query expenses table by supplier_id then enrich with supplier_name
    res2 = (
        sb.schema("public")
        .table("expenses")
        .select("id,amount,category,description,status,created_at,supporting_doc_key,requested_by,supplier_id")
        .eq("supplier_id", supplier_id)
        .order("created_at", desc=True)
        .execute()
    )
    base = res2.data or []
    # get name
    sups = {s["id"]: s["name"] for s in (get_client().schema("public").table("suppliers").select("id,name").execute().data or [])}
    emails = _emails_by_ids({r["requested_by"] for r in base})
    for r in base:
        r["supplier_name"] = sups.get(supplier_id, "")
        r["requested_by_email"] = emails.get(r["requested_by"], "")
    return base

@st.cache_data(ttl=20, show_spinner=False)
def list_expenses_by_category(category: str) -> List[Dict[str, Any]]:
    sb = get_client()
    res = (
        sb.schema("public")
        .table("v_expenses_basic")
        .select("id,supplier_name,amount,category,description,status,created_at,supporting_doc_key,requested_by")
        .eq("category", category)
        .order("created_at", desc=True)
        .execute()
    )
    rows = res.data or []
    emails = _emails_by_ids({r["requested_by"] for r in rows})
    for r in rows:
        r["requested_by_email"] = emails.get(r["requested_by"], "")
    return rows

@st.cache_data(ttl=20, show_spinner=False)
def list_expenses_by_requester(user_id: str) -> List[Dict[str, Any]]:
    sb = get_client()
    res = (
        sb.schema("public")
        .table("v_expenses_basic")
        .select("id,supplier_name,amount,category,description,status,created_at,supporting_doc_key,requested_by")
        .eq("requested_by", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    rows = res.data or []
    email = _emails_by_ids([user_id]).get(user_id, "")
    for r in rows:
        r["requested_by_email"] = email
    return rows


def receipt_file_key(key_or_folder: str) -> Optional[str]:
    """
    Devuelve la ruta del primer archivo dentro del 'folder' guardado en expenses.supporting_doc_key
    (bucket 'quotes'). Si ya es un archivo, la devuelve tal cual.
    """
    from .f_read import _first_object_in_folder  # si ya la tienes en este módulo, omite este import relativo
    try:
        return _first_object_in_folder("quotes", (key_or_folder or "").strip())
    except Exception:
        return None

@st.cache_data(ttl=30, show_spinner=False)
def _first_object_in_folder(bucket: str, key_or_folder: str) -> Optional[str]:
    """
    Devuelve la key del primer archivo dentro de 'key_or_folder' (carpeta).
    Si 'key_or_folder' ya es un archivo, la retorna tal cual.
    """
    sb = get_client()
    key = (key_or_folder or "").strip().strip("/")
    if not key:
        return None

    # 1) Intentar listar "dentro" de la carpeta
    try:
        items = sb.storage.from_(bucket).list(
            path=key, sortBy={"column": "updated_at", "order": "desc"}
        )
        # La API devuelve archivos/carpetas con campo 'name'
        for it in (items or []):
            name = it.get("name")
            if name:
                return f"{key}/{name}"
    except Exception:
        pass

    # 2) Fallback: podría ser ya una key de archivo
    return key if "/" in key else None


def receipt_file_key(key_or_folder: str) -> Optional[str]:
    """Conveniencia: primer archivo del folder (bucket 'quotes') para el recibo/cotización."""
    return _first_object_in_folder("quotes", key_or_folder)


def signed_url_for_receipt(key_or_folder: str, expires: int = 600) -> Optional[str]:
    """
    Crea una URL firmada para el primer archivo dentro del folder guardado en expenses.supporting_doc_key.
    """
    file_key = receipt_file_key(key_or_folder)
    if not file_key:
        return None
    try:
        sb = get_client()
        out = sb.storage.from_("quotes").create_signed_url(file_key, expires)
        return (out or {}).get("signed_url")
    except Exception:
        return None


def signed_url_for_payment(key_or_folder: str, expires: int = 600) -> Optional[str]:
    """
    Igual que arriba, pero para el comprobante de pago si lo guardas también como carpeta.
    """
    file_key = _first_object_in_folder("quotes", key_or_folder)
    if not file_key:
        return None
    try:
        sb = get_client()
        out = sb.storage.from_("quotes").create_signed_url(file_key, expires)
        return (out or {}).get("signed_url")
    except Exception:
        return None


def render_quote_preview_if_pdf(supporting_doc_key: str):
    """
    Si el quote es PDF, muestra la primera página con streamlit-pdf-viewer y
    siempre muestra un botón para abrir el archivo en una pestaña nueva.
    """
    file_key = receipt_file_key(supporting_doc_key)
    url = signed_url_for_receipt(supporting_doc_key, expires=600)
    if not url:
        return

    # Link siempre
    st.link_button("Abrir documento en pestaña nueva", url, use_container_width=True)

    # ¿Es PDF?
    if not file_key or not file_key.lower().endswith(".pdf"):
        return

    # Cargar bytes del PDF y mostrar solo la primera página
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        pdf_bytes = resp.content
        # Importante: en tabs hay que fijar width; pages_to_render permite 1ª página.
        pdf_viewer(
            pdf_bytes,
            width=700,            # ver nota en PyPI: definir width dentro de tabs
            height=900,
            pages_to_render=[1],  # solo la primera página
        )
    except Exception as e:
        # Si algo falla, nos quedamos con el botón de abrir en nueva pestaña
        st.caption(f"No se pudo previsualizar el PDF: {e}")

@st.cache_data(ttl=30, show_spinner=False)
def payment_file_key(key_or_folder: str) -> Optional[str]:
    """
    Primer archivo dentro del folder de 'payment_doc_key' en el bucket 'quotes'.
    """
    sb = get_client()
    key = (key_or_folder or "").strip().strip("/")
    if not key:
        return None
    try:
        items = sb.storage.from_("quotes").list(path=key, sortBy={"column": "updated_at", "order": "desc"})
        for it in (items or []):
            name = it.get("name")
            if name:
                return f"{key}/{name}"
    except Exception:
        pass
    return key if "/" in key else None

def signed_url_for_payment(key_or_folder: str, expires: int = 600) -> Optional[str]:
    file_key = payment_file_key(key_or_folder)
    if not file_key:
        return None
    try:
        sb = get_client()
        out = sb.storage.from_("quotes").create_signed_url(file_key, expires)
        return (out or {}).get("signed_url")
    except Exception:
        return None


def _emails_by_ids(ids: Iterable[str]) -> dict:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    sb = get_client()
    res = sb.schema("public").table("users").select("id,email").in_("id", ids).execute()
    return {r["id"]: r.get("email") for r in (res.data or [])}

@st.cache_data(ttl=60, show_spinner=False)
def list_approvers_for_viewer() -> List[Dict[str, Any]]:
    """
    Distinct approved_by with at least one expense (any status). Mapped to email.
    """
    sb = get_client()
    res = sb.schema("public").table("expenses").select("approved_by").execute()
    ids = sorted({r["approved_by"] for r in (res.data or []) if r.get("approved_by")})
    emails = _emails_by_ids(ids)
    return [{"id": i, "email": emails.get(i, "")} for i in ids]

@st.cache_data(ttl=30, show_spinner=False)
def _paid_at_map_for_expenses(expense_ids: List[str]) -> Dict[str, str]:
    """
    Devuelve expense_id -> paid_at (ISO str) usando expense_logs con details.new_status='pagado'.
    Si hay múltiples cambios a 'pagado', tomamos el más reciente.
    """
    if not expense_ids:
        return {}
    sb = get_client()
    res = (
        sb.schema("public")
        .table("expense_logs")
        .select("expense_id,created_at,details,action")
        .in_("expense_id", expense_ids)
        .eq("action", "update")
        .eq("details->>new_status", "pagado")
        .order("created_at", desc=True)
        .execute()
    )
    rows = res.data or []
    out = {}
    for r in rows:
        eid = r["expense_id"]
        if eid not in out:
            out[eid] = r["created_at"]  # primera (más reciente) por el order desc
    return out

@st.cache_data(ttl=30, show_spinner=False)
def list_paid_expenses_enriched(
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    supplier_names: Optional[set] = None,
    categories: Optional[set] = None,
    requester_emails: Optional[set] = None,
    approver_emails: Optional[set] = None,
    paid_from: Optional[str] = None,
    paid_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Trae expenses pagados desde v_expenses_basic y enriquece con:
      - requested_by_email, approved_by_email, paid_by_email
      - paid_at (desde expense_logs)
    Aplica filtros de creación en SQL y filtros por nombre/categoría/email en Python.
    También filtra por rango de paid_at en Python.
    """
    sb = get_client()
    q = (
        sb.schema("public")
        .table("v_expenses_basic")
        .select("id,supplier_name,amount,category,description,status,created_at,supporting_doc_key,payment_doc_key,requested_by,approved_by,paid_by")
        .eq("status", "pagado")
        .order("created_at", desc=True)
    )
    if created_from:
        q = q.gte("created_at", created_from)
    if created_to:
        q = q.lte("created_at", created_to)

    res = q.execute()
    rows = res.data or []

    # Map emails
    uid_set = set()
    for r in rows:
        uid_set.update([r.get("requested_by"), r.get("approved_by"), r.get("paid_by")])
    emails = _emails_by_ids([u for u in uid_set if u])

    for r in rows:
        r["requested_by_email"] = emails.get(r.get("requested_by"), "")
        r["approved_by_email"] = emails.get(r.get("approved_by"), "")
        r["paid_by_email"] = emails.get(r.get("paid_by"), "")

    # paid_at desde logs
    paid_map = _paid_at_map_for_expenses([r["id"] for r in rows])
    for r in rows:
        r["paid_at"] = paid_map.get(r["id"], r["created_at"])  # fallback: created_at

    # Filtros por proveedor/categoría/solicitante/aprobador
    def _keep(r):
        if supplier_names and r["supplier_name"] not in supplier_names:
            return False
        if categories and r["category"] not in categories:
            return False
        if requester_emails and r.get("requested_by_email") not in requester_emails:
            return False
        if approver_emails and r.get("approved_by_email") not in approver_emails:
            return False
        return True

    rows = [r for r in rows if _keep(r)]

    # Filtro por rango de paid_at (en Python)
    if paid_from or paid_to:
        def _in_paid_range(r):
            ts = pd.to_datetime(r["paid_at"])
            if paid_from and ts < pd.to_datetime(paid_from):
                return False
            if paid_to and ts > pd.to_datetime(paid_to):
                return False
            return True
        rows = [r for r in rows if _in_paid_range(r)]

    # Asegura tipos correctos para métricas
    for r in rows:
        r["amount"] = float(r["amount"])
    return rows
