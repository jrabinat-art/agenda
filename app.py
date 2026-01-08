import streamlit as st
import os
import psycopg2

st.set_page_config(page_title="Agenda de Clients", layout="centered")

# --- Connexió a PostgreSQL (Neon) via Secrets ---
def get_conn():
    db_url = None

    # Streamlit Cloud: st.secrets
    try:
        db_url = st.secrets["DB_URL"]
    except Exception:
        # Local (opcional): variable d'entorn DB_URL
        db_url = os.getenv("DB_URL")

    if not db_url:
        st.error("No s'ha trobat DB_URL. Revisa Secrets a Streamlit Cloud.")
        st.stop()

    return psycopg2.connect(db_url)

# --- Inicialitzar DB (crear taula si no existeix) ---
def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            nom TEXT NOT NULL,
            email TEXT,
            telefon TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

init_db()

st.title("Agenda de Clients (Persistència real amb PostgreSQL)")

# --- Afegir client ---
st.subheader("Afegir client")
with st.form("form_client"):
    nom = st.text_input("Nom del client *")
    email = st.text_input("Email")
    telefon = st.text_input("Telèfon")
    submit = st.form_submit_button("Guardar")

    if submit:
        if nom.strip() == "":
            st.error("El nom és obligatori.")
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO clients (nom, email, telefon) VALUES (%s, %s, %s)",
                (nom, email, telefon)
            )
            conn.commit()
            cur.close()
            conn.close()
            st.success("Client guardat correctament!")
            st.rerun()

# --- Mostrar llista i esborrar ---
st.subheader("Llista de clients")

conn = get_conn()
cur = conn.cursor()
cur.execute("SELECT id, nom, email, telefon FROM clients ORDER BY id DESC;")
clients = cur.fetchall()
cur.close()
conn.close()

if clients:
    for c in clients:
        col1, col2 = st.columns([4, 1])
        with col1:
            st.write(f"**{c[1]}** | {c[2] or ''} | {c[3] or ''}")
        with col2:
            if st.button("Esborrar", key=f"del_{c[0]}"):
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("DELETE FROM clients WHERE id = %s", (c[0],))
                conn.commit()
                cur.close()
                conn.close()
                st.rerun()
else:
    st.info("Encara no hi ha clients.")

