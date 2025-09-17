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
        "path": "pages/administrador.py",
        "icon": "🛡️",
    },
    "solicitante": {
        "path": "pages/solicitante.py",
        "icon": "🧾",
    },
    "aprobador": {
        "path": "pages/aprobador.py",
        "icon": "✅",
    },
    "pagador": {
        "path": "pages/pagador.py",
        "icon": "💸",
    },
    "lector": {
        "path": "pages/lector.py",
        "icon": "📊",
    },
}


def _render_login_page():
    st.write("**Pagos • Iniciar sesión**")

    with st.form("login_form", clear_on_submit=False):
        email = st.text_input("Email", placeholder="tu@empresa.com")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Entrar")
        if submitted:
            if login(email, password):
                st.rerun()
            else:
                st.error("Wrong credentials")


def _render_no_roles_page():
    st.warning(
        "Tu usuario no tiene roles asignados todavía. "
        "Pide apoyo a un administrador."
    )


def _render_missing_pages_page():
    st.error("No se encontró una página asignada para tus roles.")


st.set_page_config(page_icon="📧", layout="wide")

user = current_user()
pages = []
nav_position = "top"

if user:
    with st.sidebar:
        st.write(f"Conectado: **{user['email']}**")
        if st.button("Cerrar sesión"):
            sign_out()
            st.rerun()

    roles = current_user_roles()
    if not roles:
        pages = [
            st.Page(
                _render_no_roles_page,
                title="Sin roles asignados",
                icon=":material/warning:",
                default=True,
            )
        ]
    else:
        for role in ROLE_PRIORITY:
            if role not in roles:
                continue
            config = ROLE_PAGE_CONFIG[role]
            pages.append(
                st.Page(
                    config["path"],
                    title=ROLE_LABELS[role],
                    icon=config.get("icon") or "",
                    default=len(pages) == 0,
                )
            )

        if not pages:
            pages = [
                st.Page(
                    _render_missing_pages_page,
                    title="Página no disponible",
                    icon=":material/error:",
                    default=True,
                )
            ]
else:
    nav_position = "hidden"
    pages = [
        st.Page(
            _render_login_page,
            title="Iniciar sesión",
            icon=":material/login:",
            default=True,
        )
    ]

pg = st.navigation(pages, position=nav_position)
pg.run()
