# f_auth.py
# Auth helpers for Supabase + Streamlit (email OTP).
# NOTE: Domain constants like ESTADOS live in constants.py now.

from supabase import create_client, Client
import streamlit as st
import os
from typing import Optional, Set, Dict, Any, Mapping, Union, List

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# ---------- Client management ----------

@st.cache_resource(show_spinner=False)
def base_client() -> Client:
    """Singleton Supabase client (no session attached)."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY env vars.")
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def _attach_session(sb: Client) -> None:
    """Attach the saved session (if any) to the client so RLS sees auth.uid()."""
    sess = st.session_state.get("sb_session")
    if not sess:
        return
    at, rt = sess.get("access_token"), sess.get("refresh_token")
    if at and rt:
        try:
            sb.auth.set_session(at, rt)
        except Exception:
            # best-effort; UI guards will still block access
            pass


def get_client(use_session: bool = True) -> Client:
    """
    Return a Supabase client. If use_session=False, do NOT attach any JWT.
    If use_session=True and a session exists, attach it and try refreshing if needed.
    """
    sb = base_client()
    if not use_session:
        return sb

    sess = st.session_state.get("sb_session")
    if not (sess and sess.get("access_token") and sess.get("refresh_token")):
        return sb

    try:
        # attach current tokens
        sb.auth.set_session(sess["access_token"], sess["refresh_token"])
        cur = sb.auth.get_session()
        # if missing/invalid, try refresh
        if not cur or not getattr(cur, "user", None):
            sb.auth.refresh_session()
            cur = sb.auth.get_session()

        # persist refreshed tokens back into session state
        if cur and getattr(cur, "user", None):
            st.session_state["sb_session"] = {
                "access_token": cur.access_token,
                "refresh_token": cur.refresh_token,
                "user": {"id": cur.user.id, "email": cur.user.email},
            }
        else:
            # drop bad session
            st.session_state.pop("sb_session", None)
    except Exception:
        st.session_state.pop("sb_session", None)

    return sb



# ---------- Session helpers ----------

def _store_session_from_client(sb: Client) -> Optional[Dict[str, Any]]:
    """Read the session from the client and persist into session_state."""
    session = sb.auth.get_session()
    if session and session.user:
        st.session_state["sb_session"] = {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "user": {"id": session.user.id, "email": session.user.email},
        }
        return st.session_state["sb_session"]
    return None


def _first(qv: Union[str, List[str]]) -> str:
    """Return first value if a list is passed (Streamlit query params can be listy)."""
    return qv[0] if isinstance(qv, list) else qv


# ---------- OTP / Magic Link ----------

def send_login_otp(email: str) -> Dict[str, Any]:
    """
    Request an OTP / magic link for the email.
    shouldCreateUser=True allows first-time signups via OTP.
    """
    sb = get_client()
    return sb.auth.sign_in_with_otp({"email": email, "shouldCreateUser": True})


def verify_otp(email: str, code: str) -> Optional[Dict[str, Any]]:
    """Verify a 6-digit OTP code and persist the session."""
    sb = get_client()
    sb.auth.verify_otp({"email": email, "token": code, "type": "email"})
    return _store_session_from_client(sb)


def set_session_from_query_params() -> bool:
    """
    If using magic link redirects, capture access/refresh tokens from the URL
    and set the session. Returns True if a session was stored.
    """
    qp: Mapping[str, Union[str, List[str]]] = st.query_params  # Streamlit ≥ 1.27
    if "access_token" in qp and "refresh_token" in qp:
        at = _first(qp["access_token"])
        rt = _first(qp["refresh_token"])
        sb = get_client()
        sb.auth.set_session(at, rt)
        return _store_session_from_client(sb) is not None
    return False


# ---------- Role helpers ----------

def current_user() -> Optional[Dict[str, Any]]:
    """Return {'id', 'email'} for the logged-in user, or None."""
    sess = st.session_state.get("sb_session")
    return sess.get("user") if sess else None

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
    """Clear the Supabase session and any cached role data."""
    sb = get_client()
    try:
        sb.auth.sign_out()
    except Exception:
        pass
    for k in ("sb_session", "roles_cache"):
        st.session_state.pop(k, None)
