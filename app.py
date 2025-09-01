# app.py
# Streamlit login page (Supabase email OTP / magic link) — two-step with DB check

import streamlit as st
from f_auth import (
    send_login_otp,
    verify_otp,
    set_session_from_query_params,
    current_user,
    sign_out,
)
from f_read import is_registered_email

st.set_page_config(page_icon="📧", layout="centered")

st.subheader("Pagos • Iniciar sesión")

# Handle magic-link tokens if you ever use them
if set_session_from_query_params():
    st.session_state.pop("otp_sent", None)
    st.session_state.pop("otp_email", None)
    st.success("Sesión iniciada por enlace.")

user = current_user()
if user:
    with st.sidebar:
        st.write(f"Conectado: **{user['email']}**")
        if st.button("Cerrar sesión"):
            sign_out()
            st.rerun()
    st.success("¡Listo! Ya estás autenticado.")
    st.stop()

# ---------- Not signed in: two-step ----------
otp_sent = st.session_state.get("otp_sent", False)
otp_email = st.session_state.get("otp_email", "")

if not otp_sent:
    st.write("Ingresa tu correo. Solo usuarios registrados pueden solicitar código.")
    with st.form("email_form", clear_on_submit=False):
        email = st.text_input("Email", placeholder="tu@empresa.com")
        send_clicked = st.form_submit_button("Enviar código")
        if send_clicked:
            email_clean = (email or "").strip()
            if not email_clean:
                st.error("El email es obligatorio.")
            elif not is_registered_email(email_clean):
                st.error("Este correo no está registrado. Pide acceso al administrador.")
            else:
                try:
                    send_login_otp(email_clean)
                    st.session_state["otp_sent"] = True
                    st.session_state["otp_email"] = email_clean
                    st.success("Código enviado. Revisa tu correo.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error enviando OTP: {e}")
else:
    st.write(f"Enviamos un código a **{otp_email}**.")
    with st.form("otp_form", clear_on_submit=False):
        code = st.text_input("Código de 6 dígitos", placeholder="123456")
        colA, colB, colC = st.columns([1, 1, 1])
        with colA:
            verify_clicked = st.form_submit_button("Verificar código")
        with colB:
            resend_clicked = st.form_submit_button("Reenviar código")
        with colC:
            change_email_clicked = st.form_submit_button("Cambiar correo")

        if verify_clicked:
            if not (code and code.strip()):
                st.error("Ingresa el código.")
            else:
                try:
                    sess = verify_otp(otp_email, code.strip())
                    if sess:
                        st.success("¡Autenticado!")
                        st.session_state.pop("otp_sent", None)
                        st.session_state.pop("otp_email", None)
                        st.rerun()
                    else:
                        st.error("No se pudo verificar el código.")
                except Exception as e:
                    st.error(f"Error verificando OTP: {e}")

        if resend_clicked:
            try:
                # Re-check registration before resending (in case admin removed user)
                if not is_registered_email(otp_email):
                    st.error("Este correo ya no está registrado.")
                else:
                    send_login_otp(otp_email)
                    st.info("Te reenviamos el código. Revisa tu correo.")
            except Exception as e:
                st.error(f"No se pudo reenviar el código: {e}")

        if change_email_clicked:
            st.session_state.pop("otp_sent", None)
            st.session_state.pop("otp_email", None)
            st.rerun()
