# pages/administrador.py
# Admin: crear usuario, editar roles, y gestionar proveedores

import os
import time
import streamlit as st
import pandas as pd
from supabase import create_client
from f_auth import require_administrador, current_user, get_client
from f_read import get_all_users, list_suppliers, list_categories, list_people
from f_cud import (
    assign_role,
    remove_role,
    add_app_user,
    create_supplier,
    update_user_password,
    create_category,
    create_person,
)

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

tab_crear, tab_editar, tab_pass, tab_prov, tab_cats, tab_personas = st.tabs([
    "Crear usuario",
    "Editar usuario",
    "Actualizar contraseña",
    "Proveedores",
    "Categorías",
    "Personas",
])

FRAGMENT_KEYS = {
    "crear": "admin_crear",
    "editar": "admin_editar",
    "pass": "admin_pass",
    "prov": "admin_prov",
    "cats": "admin_cats",
    "personas": "admin_personas",
}

REFRESH_FLAGS_KEY = "admin_refresh_flags"

if REFRESH_FLAGS_KEY not in st.session_state:
    st.session_state[REFRESH_FLAGS_KEY] = {}


def mark_fragment_refresh(data_key: str, fragments) -> None:
    """Mark other fragments to refresh cached data when they run next."""

    flags = st.session_state.setdefault(REFRESH_FLAGS_KEY, {})
    current = set(flags.get(data_key, []))
    current.update(fragments or [])
    flags[data_key] = sorted(current)
    st.session_state[REFRESH_FLAGS_KEY] = flags


def consume_fragment_refresh(data_key: str, fragment_name: str, clear_fn=None) -> None:
    """Clear cached data when the current fragment needs to refresh it."""

    flags = st.session_state.get(REFRESH_FLAGS_KEY, {})
    targets = set(flags.get(data_key, []))
    if fragment_name not in targets:
        return

    if clear_fn is not None:
        try:
            clear_fn()
        except Exception:
            pass

    targets.discard(fragment_name)
    if targets:
        flags[data_key] = sorted(targets)
    else:
        flags.pop(data_key, None)
    st.session_state[REFRESH_FLAGS_KEY] = flags


@st.fragment
def admin_crear_fragment():
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
        if not submitted:
            return

        if not email:
            st.error("El email es obligatorio.")
            return

        try:
            sb_admin = ADMIN_CLIENT
            sb_admin.auth.admin.create_user(
                {
                    "email": email,
                    "email_confirm": True,
                    "user_metadata": {"display_name": display_name} if display_name else {},
                }
            )

            if allow_otp:
                try:
                    add_app_user(email)
                except Exception as e:
                    st.warning(
                        f"Usuario creado, pero no se pudo agregar a OTP allowlist: {e}"
                    )

            sb = get_client()
            uid = None
            for _ in range(3):
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

            try:
                get_all_users.clear()
            except Exception:
                pass

            mark_fragment_refresh(
                "users",
                [FRAGMENT_KEYS["editar"], FRAGMENT_KEYS["pass"]],
            )

            st.success(f"Usuario creado: {email}")
            st.rerun(scope="fragment")
        except Exception as e:
            st.error(f"No se pudo crear el usuario: {e}")


@st.fragment
def admin_editar_fragment():
    consume_fragment_refresh(
        "users",
        FRAGMENT_KEYS["editar"],
        getattr(get_all_users, "clear", None),
    )

    users = get_all_users()
    if not users:
        st.info("Aún no hay usuarios.")
        st.stop()

    def _label(u: dict) -> str:
        nom = (u.get("display_name") or "").strip()
        return f"{u['email']} — {nom}" if nom else u["email"]

    options = {_label(u): u for u in users}
    selected_label = st.selectbox("Selecciona un usuario", list(options.keys()))
    selected_user = options[selected_label] if selected_label else None

    if not selected_user:
        return

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
                key=f"cb-{role}-{selected_user['id']}",
            )

    if not st.button("Guardar cambios"):
        return

    try:
        desired = {r for r, v in role_boxes.items() if v}
        current = roles_set

        to_add = desired - current
        to_remove = current - desired

        if selected_user["id"] == my_id and "administrador" in to_remove:
            st.warning("No puedes quitarte el rol 'administrador' a ti mismo.")
            to_remove.discard("administrador")

        for r in sorted(to_add):
            assign_role(selected_user["id"], r)
            st.success(f"Rol '{r}' asignado.")

        for r in sorted(to_remove):
            remove_role(selected_user["id"], r)
            st.success(f"Rol '{r}' removido.")

        try:
            get_all_users.clear()
        except Exception:
            pass

        mark_fragment_refresh("users", [FRAGMENT_KEYS["pass"]])

        st.rerun(scope="fragment")
    except Exception as e:
        st.error(f"Error al guardar cambios: {e}")


@st.fragment
def admin_pass_fragment():
    consume_fragment_refresh(
        "users",
        FRAGMENT_KEYS["pass"],
        getattr(get_all_users, "clear", None),
    )

    users = get_all_users()
    emails = [u["email"] for u in users]
    if not emails:
        st.info("Aún no hay usuarios.")
        return

    with st.form("password_form", clear_on_submit=True):
        email = st.selectbox("Selecciona un usuario", emails)
        new_pwd = st.text_input("Nueva contraseña", type="password")
        submitted = st.form_submit_button("Actualizar")
        if not submitted:
            return

        if not new_pwd:
            st.error("La contraseña no puede estar vacía.")
            return

        try:
            update_user_password(email, new_pwd)
            st.success("Contraseña actualizada.")
        except Exception as e:
            st.error(f"No se pudo actualizar: {e}")


@st.fragment
def admin_prov_fragment():
    consume_fragment_refresh(
        "suppliers",
        FRAGMENT_KEYS["prov"],
        getattr(list_suppliers, "clear", None),
    )
    consume_fragment_refresh(
        "categories",
        FRAGMENT_KEYS["prov"],
        getattr(list_categories, "clear", None),
    )

    st.subheader("Proveedores")

    sups = list_suppliers()
    if sups:
        df = pd.DataFrame(
            [{"Proveedor": s["name"], "Categoría": s.get("category", "")} for s in sups]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("Aún no hay proveedores.")

    st.markdown("### Agregar proveedor")
    cats = list_categories()
    if not cats:
        st.info("No hay categorías. Primero agrega categorías en la pestaña 'Categorías'.")

    with st.form("form_add_supplier", clear_on_submit=True):
        nombre = st.text_input("Nombre del proveedor *").strip()
        categoria = st.selectbox("Categoría *", cats, disabled=not bool(cats))
        submitted = st.form_submit_button("Crear", disabled=not bool(cats))
        if not submitted:
            return

        try:
            create_supplier(nombre, categoria)
            try:
                list_suppliers.clear()
            except Exception:
                pass
            st.success("Proveedor creado.")
            st.balloons()
            st.rerun(scope="fragment")
        except Exception as e:
            msg = str(e).lower()
            if "duplicate" in msg or "unique" in msg:
                st.warning("Ese proveedor ya existe.")
            else:
                st.error(f"No se pudo crear el proveedor: {e}")


@st.fragment
def admin_cats_fragment():
    consume_fragment_refresh(
        "categories",
        FRAGMENT_KEYS["cats"],
        getattr(list_categories, "clear", None),
    )

    st.subheader("Categorías")

    cats = list_categories()
    if cats:
        st.dataframe(
            pd.DataFrame({"Categoría": cats}), use_container_width=True, hide_index=True
        )
    else:
        st.caption("Aún no hay categorías.")

    st.markdown("### Agregar categoría")
    with st.form("form_add_category", clear_on_submit=True):
        cat_name = st.text_input("Nombre de categoría *", placeholder="Ej. Software").strip()
        submitted = st.form_submit_button("Agregar")
        if not submitted:
            return

        if not cat_name:
            st.error("El nombre es obligatorio.")
            return

        try:
            create_category(cat_name)
            try:
                list_categories.clear()
            except Exception:
                pass
            mark_fragment_refresh("categories", [FRAGMENT_KEYS["prov"]])
            st.success("Categoría agregada.")
            st.rerun(scope="fragment")
        except Exception as e:
            msg = str(e)
            if "duplicate" in msg.lower() or "unique" in msg.lower():
                st.warning("Esa categoría ya existe.")
            else:
                st.error(f"No se pudo agregar: {e}")


@st.fragment
def admin_personas_fragment():
    consume_fragment_refresh(
        "people",
        FRAGMENT_KEYS["personas"],
        getattr(list_people, "clear", None),
    )

    st.subheader("Personas")

    personas = list_people()
    if personas:
        st.dataframe(
            pd.DataFrame({"Nombre": personas}),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Aún no hay personas.")

    st.markdown("### Agregar persona")
    with st.form("form_add_person", clear_on_submit=True):
        nombre = st.text_input("Nombre *").strip()
        submitted = st.form_submit_button("Agregar")
        if not submitted:
            return

        if not nombre:
            st.error("El nombre es obligatorio.")
            return

        try:
            create_person(nombre)
            try:
                list_people.clear()
            except Exception:
                pass
            st.success("Persona agregada.")
            st.rerun(scope="fragment")
        except Exception as e:
            msg = str(e)
            if "duplicate" in msg.lower() or "unique" in msg.lower():
                st.warning("Esa persona ya existe.")
            else:
                st.error(f"No se pudo agregar: {e}")


# =======================
# Tab 1: Crear usuario
# =======================
with tab_crear:
    admin_crear_fragment()

# =======================
# Tab 2: Editar usuario
# =======================
with tab_editar:
    admin_editar_fragment()

# =======================
# Tab 3: Actualizar contraseña
# =======================
with tab_pass:
    admin_pass_fragment()

# =======================
# Tab 4: Proveedores
# =======================
with tab_prov:
    admin_prov_fragment()

# =======================
# Tab 5: Categorías
# =======================
with tab_cats:
    admin_cats_fragment()

# =======================
# Tab 6: Personas
# =======================
with tab_personas:
    admin_personas_fragment()

