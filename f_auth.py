import streamlit as st
from supabase import create_client, Client
import os
from typing import Optional, Set, Dict, Any

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# ---------- Client ----------

@st.cache_resource(show_spinner=False)
def get_client() -> Client:
    """Return a Supabase client using the anon key."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY env vars.")
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


# ---------- Login / Session ----------

def login(email: str, password: str) -> Optional[Dict[str, Any]]:
    """Validate email/password against users table. Returns user row or None."""
    sb = get_client()
    res = (
        sb.table("users")
        .select("id,email")
        .eq("email", (email or "").strip())
        .eq("password", (password or "").strip())
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if rows:
        user = rows[0]
        st.session_state["user"] = user
        return user
    return None


def current_user() -> Optional[Dict[str, Any]]:
    """Return {'id','email'} for the logged-in user, or None."""
    return st.session_state.get("user")

# --- Role helpers (Spanish) ---

def user_roles(user_id: str) -> set[str]:
    sb = get_client()
    res = (
        sb.schema("public")
        .table("user_roles")
        .select("role")
        .eq("user_id", user_id)
        .execute()
    )
    return {row["role"] for row in (res.data or [])}

def has_role(role_es: str) -> bool:
    user = current_user()
    if not user:
        return False
    return role_es in user_roles(user["id"])

def es_administrador() -> bool: return has_role("administrador")
def es_solicitante()  -> bool: return has_role("solicitante")
def es_aprobador()    -> bool: return has_role("aprobador")
def es_pagador()      -> bool: return has_role("pagador")
def es_lector()       -> bool: return has_role("lector")

# --- Guards (Spanish) ---

def require_login():
    if not current_user():
        st.error("Debes iniciar sesión para acceder a esta página.")
        st.stop()

def require_administrador():
    require_login()
    if not es_administrador():
        st.error("Esta página es solo para administradores.")
        st.stop()

def require_solicitante():
    require_login()
    if not es_solicitante() and not es_administrador():
        st.error("No tienes permisos para acceder como Solicitante.")
        st.stop()

def require_aprobador():
    require_login()
    if not es_aprobador() and not es_administrador():
        st.error("No tienes permisos para acceder como Aprobador.")
        st.stop()

def require_pagador():
    require_login()
    if not es_pagador() and not es_administrador():
        st.error("No tienes permisos para acceder como Pagador.")
        st.stop()

def require_lector():
    require_login()
    if not es_lector() and not es_administrador():
        st.error("No tienes permisos para acceder como Lector.")
        st.stop()


def sign_out():
    """Clear stored user info and role cache."""
    for k in ("user", "roles_cache"):
        st.session_state.pop(k, None)
