import streamlit as st
import os
import psycopg2
from datetime import datetime

st.set_page_config(page_title="FIF Lleida - Gestió Equip", layout="wide")

# ---------- DB ----------
def get_conn():
    db_url = None
    try:
        db_url = st.secrets["DB_URL"]
    except Exception:
        db_url = os.getenv("DB_URL")

    if not db_url:
        st.error("No s'ha trobat DB_URL. Revisa Secrets a Streamlit Cloud.")
        st.stop()

    return psycopg2.connect(db_url)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jugadors (
            id SERIAL PRIMARY KEY,
            nom TEXT NOT NULL,
            cognoms TEXT NOT NULL,
            telefon TEXT,
            email TEXT,
            posicio TEXT,
            dorsal INT,
            gols INT DEFAULT 0,
            assistencies INT DEFAULT 0,
            partits INT DEFAULT 0,
            notes TEXT,
            creat_el TIMESTAMP DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

init_db()

# ---------- UI ----------
st.title("FIF Lleida - Gestió de Jugadors i Estadístiques")

st.markdown("""
Aquesta aplicació serveix per portar la **plantilla del FIF Lleida** i fer seguiment de les estadístiques.
""")

tab1, tab2 = st.tabs(["Plantilla", "Afegir jugador"])

# ---------- Afegir jugador ----------
with tab2:
    st.subheader("Afegir jugador nou")
    with st.form("form_afegir"):
        col1, col2, col3 = st.columns(3)

        with col1:
            nom = st.text_input("Nom *")
            cognoms = st.text_input("Cognoms *")
            posicio = st.selectbox("Posició", ["Porter", "Defensa", "Migcampista", "Davanter", "Altres"])

        with col2:
            dorsal = st.number_input("Dorsal", min_value=0, max_value=99, value=0, step=1)
            telefon = st.text_input("Telèfon")
            email = st.text_input("Email")

        with col3:
            gols = st.number_input("Gols", min_value=0, value=0, step=1)
            assistencies = st.number_input("Assistències", min_value=0, value=0, step=1)
            partits = st.number_input("Partits jugats", min_value=0, value=0, step=1)

        notes = st.text_area("Notes (ex: peu bo, observacions, lesions...)")

        submit = st.form_submit_button("Guardar jugador")

        if submit:
            if nom.strip() == "" or cognoms.strip() == "":
                st.error("Nom i cognoms són obligatoris.")
            else:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO jugadors (nom, cognoms, telefon, email, posicio, dorsal, gols, assistencies, partits, notes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (nom, cognoms, telefon, email, posicio, dorsal, gols, assistencies, partits, notes))
                conn.commit()
                cur.close()
                conn.close()
                st.success("Jugador guardat correctament!")
                st.rerun()

# ---------- Plantilla ----------
with tab1:
    st.subheader("Plantilla i estadístiques")

    # Filters
    colf1, colf2, colf3 = st.columns(3)
    with colf1:
        filtre_posicio = st.selectbox("Filtrar per posició", ["Totes", "Porter", "Defensa", "Migcampista", "Davanter", "Altres"])
    with colf2:
        ordre = st.selectbox("Ordenar per", ["Dorsal", "Nom", "Gols", "Assistències", "Partits"])
    with colf3:
        cerca = st.text_input("Cerca (nom o cognoms)")

    order_map = {
        "Dorsal": "dorsal ASC NULLS LAST, nom ASC",
        "Nom": "nom ASC, cognoms ASC",
        "Gols": "gols DESC, assistencies DESC",
        "Assistències": "assistencies DESC, gols DESC",
        "Partits": "partits DESC, gols DESC"
    }

    query = "SELECT id, nom, cognoms, telefon, email, posicio, dorsal, gols, assistencies, partits, notes FROM jugadors"
    params = []

    conditions = []
    if filtre_posicio != "Totes":
        conditions.append("posicio = %s")
        params.append(filtre_posicio)

    if cerca.strip():
        conditions.append("(LOWER(nom) LIKE %s OR LOWER(cognoms) LIKE %s)")
        params.append(f"%{cerca.lower()}%")
        params.append(f"%{cerca.lower()}%")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += f" ORDER BY {order_map[ordre]};"

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        st.info("No hi ha jugadors encara o no coincideixen amb el filtre.")
        st.stop()

    st.write(f"Jugadors trobats: **{len(rows)}**")

    # Display players with controls
    for r in rows:
        pid, nom, cognoms, telefon, email, posicio, dorsal, gols, assist, partits, notes = r

        with st.expander(f"#{dorsal if dorsal else '-'}  {nom} {cognoms}  |  {posicio}  |  G:{gols} A:{assist} P:{partits}", expanded=False):
            col1, col2, col3 = st.columns([2, 2, 2])

            with col1:
                st.markdown("**Dades de contacte**")
                st.write(f"📞 Tel: {telefon or ''}")
                st.write(f"✉️ Email: {email or ''}")
                st.write(f"🧩 Posició: {posicio}")
                st.write(f"🔢 Dorsal: {dorsal if dorsal else ''}")

            with col2:
                st.markdown("**Estadístiques**")
                st.write(f"Gols: **{gols}**")
                st.write(f"Assistències: **{assist}**")
                st.write(f"Partits: **{partits}**")

                st.markdown("**Actualitzar ràpid**")
                cbtn1, cbtn2, cbtn3 = st.columns(3)
                if cbtn1.button("➕ Gol", key=f"gol_{pid}"):
                    conn = get_conn(); cur = conn.cursor()
                    cur.execute("UPDATE jugadors SET gols = gols + 1 WHERE id = %s", (pid,))
                    conn.commit(); cur.close(); conn.close()
                    st.rerun()

                if cbtn2.button("➕ Assist.", key=f"assist_{pid}"):
                    conn = get_conn(); cur = conn.cursor()
                    cur.execute("UPDATE jugadors SET assistencies = assistencies + 1 WHERE id = %s", (pid,))
                    conn.commit(); cur.close(); conn.close()
                    st.rerun()

                if cbtn3.button("➕ Partit", key=f"partit_{pid}"):
                    conn = get_conn(); cur = conn.cursor()
                    cur.execute("UPDATE jugadors SET partits = partits + 1 WHERE id = %s", (pid,))
                    conn.commit(); cur.close(); conn.close()
                    st.rerun()

            with col3:
                st.markdown("**Notes**")
                st.write(notes or "")

                st.markdown("---")
                st.markdown("**Editar estadístiques manualment**")
                new_gols = st.number_input("Gols", min_value=0, value=gols, step=1, key=f"ng_{pid}")
                new_assist = st.number_input("Assistències", min_value=0, value=assist, step=1, key=f"na_{pid}")
                new_partits = st.number_input("Partits", min_value=0, value=partits, step=1, key=f"np_{pid}")

                if st.button("Guardar canvis", key=f"save_{pid}"):
                    conn = get_conn(); cur = conn.cursor()
                    cur.execute("""
                        UPDATE jugadors
                        SET gols = %s, assistencies = %s, partits = %s
                        WHERE id = %s
                    """, (new_gols, new_assist, new_partits, pid))
                    conn.commit(); cur.close(); conn.close()
                    st.success("Canvis guardats.")
                    st.rerun()

                st.markdown("---")
                if st.button("🗑️ Esborrar jugador", key=f"del_{pid}"):
                    conn = get_conn(); cur = conn.cursor()
                    cur.execute("DELETE FROM jugadors WHERE id = %s", (pid,))
                    conn.commit(); cur.close(); conn.close()
                    st.warning("Jugador esborrat.")
                    st.rerun()
