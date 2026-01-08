import streamlit as st
import sqlite3

# Connexió a base de dades (fitxer local)
conn = sqlite3.connect("clients.db", check_same_thread=False)
cursor = conn.cursor()

# Crear taula si no existeix
cursor.execute("""
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom TEXT NOT NULL,
    email TEXT,
    telefon TEXT
)
""")
conn.commit()

st.title("Prova 1: Agenda de Clients (MVP)")

# Formulari per afegir clients
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
            cursor.execute("INSERT INTO clients (nom, email, telefon) VALUES (?, ?, ?)",
                           (nom, email, telefon))
            conn.commit()
            st.success("Client guardat correctament!")

# Mostrar llista de clients
st.subheader("Llista de clients")
cursor.execute("SELECT id, nom, email, telefon FROM clients ORDER BY id DESC")
clients = cursor.fetchall()

if clients:
    for c in clients:
        col1, col2 = st.columns([4, 1])
        with col1:
            st.write(f"**{c[1]}** | {c[2]} | {c[3]}")
        with col2:
            if st.button("Esborrar", key=f"del_{c[0]}"):
                cursor.execute("DELETE FROM clients WHERE id = ?", (c[0],))
                conn.commit()
                st.rerun()
else:
    st.info("Encara no hi ha clients.")
