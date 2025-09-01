import streamlit as st
from f_auth import login, current_user, sign_out

st.set_page_config(page_icon="📧", layout="centered")

st.subheader("Pagos • Iniciar sesión")

user = current_user()
if user:
    with st.sidebar:
        st.write(f"Conectado: **{user['email']}**")
        if st.button("Cerrar sesión"):
            sign_out()
            st.rerun()
    st.success("¡Listo! Ya estás autenticado.")
    st.stop()

with st.form("login_form", clear_on_submit=False):
    email = st.text_input("Email", placeholder="tu@empresa.com")
    password = st.text_input("Contraseña", type="password")
    submitted = st.form_submit_button("Entrar")
    if submitted:
        if login(email, password):
            st.switch_page("pages/administrador.py")
        else:
            st.error("Wrong credentials")
