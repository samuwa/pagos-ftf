import streamlit as st

from f_auth import (
    login,
    current_user,
    sign_out,
    current_user_roles,
)

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

ROLE_PAGE_CONFIG = {
    "administrador": {
        "path": "administrador.py",
        "icon": "🛡️",
    },
    "solicitante": {
        "path": "solicitante.py",
        "icon": "🧾",
    },
    "aprobador": {
        "path": "aprobador.py",
        "icon": "✅",
    },
    "pagador": {
        "path": "pagador.py",
        "icon": "💸",
    },
    "lector": {
        "path": "lector.py",
        "icon": "📊",
    },
}

st.set_page_config(page_icon="📧", layout="centered")

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

    available_pages = []
    for role in ROLE_PRIORITY:
        if role not in roles:
            continue
        config = ROLE_PAGE_CONFIG[role]
        available_pages.append(
            st.Page(
                config["path"],
                title=ROLE_LABELS[role],
                icon=config.get("icon") or "",
                default=len(available_pages) == 0,
            )
        )

    if not available_pages:
        st.error("No se encontró una página asignada para tus roles.")
        st.stop()

    pg = st.navigation(available_pages, position="top")
    pg.run()
    st.stop()

st.write("**Pagos • Iniciar sesión**")

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

            st.rerun()
        else:
            st.error("Wrong credentials")
