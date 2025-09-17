from typing import Optional, Set

import streamlit as st
from f_auth import (
    login,
    current_user,
    sign_out,
    current_user_roles,
)

ROLE_PAGE_MAP = {
    "administrador": "pages/administrador.py",
    "solicitante": "pages/solicitante.py",
    "aprobador": "pages/aprobador.py",
    "pagador": "pages/pagador.py",
    "lector": "pages/lector.py",
}

ROLE_LABELS = {
    "administrador": "Administración",
    "solicitante": "Solicitudes",
    "aprobador": "Aprobación",
    "pagador": "Pagos",
    "lector": "Lectura",
}

ROLE_PRIORITY = [
    "administrador",
    "solicitante",
    "aprobador",
    "pagador",
    "lector",
]


def _first_page_for_roles(roles: Set[str]) -> Optional[str]:
    for role in ROLE_PRIORITY:
        if role in roles:
            return ROLE_PAGE_MAP[role]
    return None

st.set_page_config(page_icon="📧", layout="centered")

st.write("**Pagos • Iniciar sesión**")

user = current_user()
if user:
    with st.sidebar:
        st.write(f"Conectado: **{user['email']}**")
        if st.button("Cerrar sesión"):
            sign_out()
            st.rerun()
    roles = current_user_roles()
    if not roles:
        st.warning(
            "Tu usuario no tiene roles asignados todavía. "
            "Pide apoyo a un administrador."
        )
        st.stop()

    st.success("¡Listo! Ya estás autenticado.")
    st.write("Selecciona una sección para continuar:")
    for role in ROLE_PRIORITY:
        if role in roles:
            st.page_link(ROLE_PAGE_MAP[role], label=f"{ROLE_LABELS[role]}")
    st.stop()

with st.form("login_form", clear_on_submit=False):
    email = st.text_input("Email", placeholder="tu@empresa.com")
    password = st.text_input("Contraseña", type="password")
    submitted = st.form_submit_button("Entrar")
    if submitted:
        if login(email, password):
            roles = current_user_roles()
            if not roles:
                st.warning(
                    "Inicio de sesión exitoso, pero tu usuario aún no tiene roles. "
                    "Pide apoyo a un administrador."
                )
                st.stop()

            target = _first_page_for_roles(roles)
            if target:
                st.switch_page(target)
            else:
                st.error("No se encontró una página asignada para tus roles.")
        else:
            st.error("Wrong credentials")
