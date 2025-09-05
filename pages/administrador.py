# pages/administrador.py
# Admin: crear usuario, editar roles, y gestionar proveedores

import os
import time
import streamlit as st
import pandas as pd
from supabase import create_client
from f_auth import require_administrador, current_user, get_client
from f_read import get_all_users, list_suppliers, list_categories
from f_cud import (
    assign_role,
    remove_role,
    add_app_user,
    create_supplier,
    update_user_password,
    create_category,
)

st.set_page_config(page_icon="🛡️", layout="wide")
require_administrador()

st.write("**Administración**")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")

SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

ROLES_ES = ["administrador", "solicitante", "aprobador", "pagador", "lector"]


@st.cache_resource(show_spinner=False)
def get_admin_client():
    if not SUPABASE_URL or not SERVICE_ROLE_KEY:
        raise RuntimeError(
            "Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY en variables de entorno."
        )
    return create_client(SUPABASE_URL, SERVICE_ROLE_KEY)


ADMIN_CLIENT = get_admin_client()

tab_crear, tab_editar, tab_pass, tab_prov, tab_cats = st.tabs([
    "Crear usuario",
    "Editar usuario",
    "Actualizar contraseña",
    "Proveedores",
    "Categorías",
])

# =======================
# Tab 1: Crear usuario
# =======================
with tab_crear:
    with st.form("crear_usuario_form", clear_on_submit=True):
        email = st.text_input("Email *", placeholder="persona@empresa.com")
        display_name = st.text_input("Nombre para mostrar (opcional)")

        st.caption("Asigna uno o varios roles para este usuario:")
        cols = st.columns(5)
        role_checks = {}
        for i, role in enumerate(ROLES_ES):
            with cols[i % 5]:
                role_checks[role] = st.checkbox(role, value=(role == "solicitante"))

        allow_otp = st.checkbox("Agregar a la lista permitida (OTP)", value=True)

        submitted = st.form_submit_button("Crear usuario")
        if submitted:
            if not email:
                st.error("El email es obligatorio.")
            else:
                try:
                    sb_admin = ADMIN_CLIENT
                    # Crea el usuario en Auth (confirmado para permitir OTP inmediato).
                    resp = sb_admin.auth.admin.create_user(
                        {
                            "email": email,
                            "email_confirm": True,
                            "user_metadata": {"display_name": display_name} if display_name else {},
                        }
                    )

                    # (Opcional) Agregar a allowlist para el flujo OTP por email
                    if allow_otp:
                        try:
                            add_app_user(email)
                        except Exception as e:
                            st.warning(f"Usuario creado, pero no se pudo agregar a OTP allowlist: {e}")

                    # Intentar encontrar el id del espejo en public.users (puede tardar un segundo)
                    sb = get_client()
                    uid = None
                    for _ in range(3):  # pequeño retry para esperar el trigger espejo
                        row = (
                            sb.schema("public")
                            .table("users")
                            .select("id,email")
                            .ilike("email", email.strip())
                            .limit(1)
                            .execute()
                        ).data
                        if row:
                            uid = row[0]["id"]
                            break
                        time.sleep(0.5)

                    # Asignar roles si ya tenemos uid
                    selected_roles = [r for r, v in role_checks.items() if v]
                    if uid and selected_roles:
                        for r in selected_roles:
                            try:
                                assign_role(uid, r)
                            except Exception as e:
                                st.warning(f"No se pudo asignar rol {r}: {e}")
                    elif not uid:
                        st.warning(
                            "Usuario creado en Auth. Aún no aparece en public.users; "
                            "refresca esta página en unos segundos para asignar roles."
                        )

                    # Limpiar cache para que aparezca en 'Editar usuario'
                    try:
                        get_all_users.clear()
                    except Exception:
                        pass

                    st.success(f"Usuario creado: {email}")
                    st.rerun()
                except Exception as e:
                    st.error(f"No se pudo crear el usuario: {e}")

# =======================
# Tab 2: Editar usuario
# =======================
with tab_editar:
    users = get_all_users()
    if not users:
        st.info("Aún no hay usuarios.")
        st.stop()

    # Opciones del selectbox: "email — nombre"
    def _label(u: dict) -> str:
        nom = (u.get("display_name") or "").strip()
        return f"{u['email']} — {nom}" if nom else u["email"]

    options = { _label(u): u for u in users }
    selected_label = st.selectbox("Selecciona un usuario", list(options.keys()))
    selected_user = options[selected_label] if selected_label else None

    if selected_user:
        roles_set = set(selected_user.get("roles", []))
        me = current_user()
        my_id = me["id"] if me else None

        st.caption("Marca/Desmarca para asignar o quitar roles:")
        cols2 = st.columns(5)
        role_boxes = {}
        for i, role in enumerate(ROLES_ES):
            with cols2[i % 5]:
                role_boxes[role] = st.checkbox(
                    role,
                    value=(role in roles_set),
                    key=f"cb-{role}-{selected_user['id']}"
                )

        if st.button("Guardar cambios"):
            try:
                # Aplicar diferencias
                desired = {r for r, v in role_boxes.items() if v}
                current = roles_set

                to_add = desired - current
                to_remove = current - desired

                # Evitar que un admin se quite a sí mismo el rol administrador
                if selected_user["id"] == my_id and "administrador" in to_remove:
                    st.warning("No puedes quitarte el rol 'administrador' a ti mismo.")
                    to_remove.discard("administrador")

                for r in sorted(to_add):
                    assign_role(selected_user["id"], r)
                    st.success(f"Rol '{r}' asignado.")

                for r in sorted(to_remove):
                    remove_role(selected_user["id"], r)
                    st.success(f"Rol '{r}' removido.")

                st.rerun()
            except Exception as e:
                st.error(f"Error al guardar cambios: {e}")

# =======================
# Tab 3: Actualizar contraseña
# =======================
with tab_pass:
    users = get_all_users()
    emails = [u["email"] for u in users]
    if not emails:
        st.info("Aún no hay usuarios.")
    else:
        with st.form("password_form", clear_on_submit=True):
            email = st.selectbox("Selecciona un usuario", emails)
            new_pwd = st.text_input("Nueva contraseña", type="password")
            submitted = st.form_submit_button("Actualizar")
            if submitted:
                if not new_pwd:
                    st.error("La contraseña no puede estar vacía.")
                else:
                    try:
                        update_user_password(email, new_pwd)
                        st.success("Contraseña actualizada.")
                    except Exception as e:
                        st.error(f"No se pudo actualizar: {e}")

# =======================
# Tab 4: Proveedores
# =======================
with tab_prov:

    col1, col2 = st.columns(2)

    with col1:
        st.write("**Crear proveedor**")

        with st.form("crear_proveedor_form", clear_on_submit=True):
            sup_name = st.text_input("Nombre del proveedor *", placeholder="Ej. 'Acme S.A.'")
            btn_create = st.form_submit_button("Crear")
            if btn_create:
                if not sup_name or not sup_name.strip():
                    st.error("Ingresa un nombre válido.")
                else:
                    try:
                        create_supplier(sup_name.strip())
                        st.success(f"Proveedor creado: {sup_name.strip()}")
                        # limpiar cache de lista
                        try:
                            list_suppliers.clear()
                        except Exception:
                            pass
                        st.rerun()
                    except Exception as e:
                        st.error(f"No se pudo crear el proveedor: {e}")

    with col2:
        st.write("**Listado de proveedores**")
        suppliers = list_suppliers()
        if not suppliers:
            st.caption("Aún no hay proveedores.")
        else:
            # Vista solo lectura
            # (Puedes formatear created_at a tu gusto; aquí lo mostramos tal cual)
            df = pd.DataFrame(suppliers)[["name", "created_at"]]
            df["created_at"] = pd.to_datetime(df["created_at"]).dt.date
            df.columns = ["Nombre","Creado"]
            st.dataframe(df, use_container_width=True, hide_index=True)

# =======================
# Tab 5: Categorías
# =======================
with tab_cats:
    st.subheader("Categorías")

    cats = list_categories()
    if cats:
        st.dataframe(pd.DataFrame({"Categoría": cats}), use_container_width=True, hide_index=True)
    else:
        st.caption("Aún no hay categorías.")

    st.markdown("### Agregar categoría")
    with st.form("form_add_category", clear_on_submit=True):
        cat_name = st.text_input("Nombre de categoría *", placeholder="Ej. Software").strip()
        submitted = st.form_submit_button("Agregar")
        if submitted:
            if not cat_name:
                st.error("El nombre es obligatorio.")
            else:
                try:
                    create_category(cat_name)
                    try:
                        list_categories.clear()
                    except Exception:
                        pass
                    st.success("Categoría agregada.")
                    st.rerun()
                except Exception as e:
                    msg = str(e)
                    if "duplicate" in msg.lower() or "unique" in msg.lower():
                        st.warning("Esa categoría ya existe.")
                    else:
                        st.error(f"No se pudo agregar: {e}")
