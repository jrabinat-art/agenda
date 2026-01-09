import streamlit as st
import os
import psycopg2
import bcrypt
import pandas as pd
from datetime import date, datetime, timedelta

st.set_page_config(page_title="Family Habits & Goals", layout="wide")


# -------------------------
# DB UTILITIES
# -------------------------
def get_db_url():
    try:
        return st.secrets["DB_URL"]
    except Exception:
        return os.getenv("DB_URL")


def get_conn():
    db_url = get_db_url()
    if not db_url:
        st.error("DB_URL no està configurada. Revisa Secrets a Streamlit Cloud.")
        st.stop()
    return psycopg2.connect(db_url)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # users: multiuser login
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        password_hash BYTEA NOT NULL,
        is_admin BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    # categories: shared (no user_id)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        sort_order INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    # goals: per user, in category
    cur.execute("""
    CREATE TABLE IF NOT EXISTS goals (
        id SERIAL PRIMARY KEY,
        user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        category_id INT REFERENCES categories(id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        description TEXT,
        unit TEXT,
        target_value NUMERIC,
        current_value NUMERIC,
        start_date DATE,
        end_date DATE,
        status TEXT DEFAULT 'active', -- active/paused/completed/cancelled
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    # tasks: per user, optional goal
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id SERIAL PRIMARY KEY,
        user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        goal_id INT REFERENCES goals(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        description TEXT,
        due_date DATE,
        status TEXT DEFAULT 'todo', -- todo/doing/done
        priority TEXT DEFAULT 'medium', -- low/medium/high
        completed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    # habits: per user, optional goal, must have category
    cur.execute("""
    CREATE TABLE IF NOT EXISTS habits (
        id SERIAL PRIMARY KEY,
        user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        category_id INT REFERENCES categories(id) ON DELETE SET NULL,
        goal_id INT REFERENCES goals(id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        description TEXT,
        measure_type TEXT NOT NULL, -- boolean/numeric
        unit TEXT,
        schedule_type TEXT NOT NULL, -- daily/weekdays/monthly
        weekdays_mask TEXT, -- "1,0,1,0,1,0,0" (Mon..Sun) for weekdays
        target_count INT DEFAULT 1, -- for boolean or monthly count
        target_value NUMERIC, -- for numeric
        active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    # habit logs: one log per habit per day (recommended)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS habit_logs (
        id SERIAL PRIMARY KEY,
        habit_id INT NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
        log_date DATE NOT NULL,
        done BOOLEAN,
        value NUMERIC,
        note TEXT,
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(habit_id, log_date)
    );
    """)

    # mood logs (diary)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mood_logs (
        id SERIAL PRIMARY KEY,
        user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        log_date DATE NOT NULL,
        mood INT CHECK (mood >= 1 AND mood <= 10),
        text TEXT,
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(user_id, log_date)
    );
    """)

    conn.commit()
    cur.close()
    conn.close()


init_db()


# -------------------------
# AUTH
# -------------------------
def hash_password(pw: str) -> bytes:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt())


def verify_password(pw: str, pw_hash: bytes) -> bool:
    return bcrypt.checkpw(pw.encode("utf-8"), pw_hash)


def get_user_by_email(email: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, email, name, password_hash, is_admin FROM users WHERE email = %s", (email.lower(),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def create_user(email: str, name: str, password: str, is_admin: bool = False):
    conn = get_conn()
    cur = conn.cursor()
    pw_hash = hash_password(password)
    cur.execute("""
        INSERT INTO users (email, name, password_hash, is_admin)
        VALUES (%s, %s, %s, %s)
    """, (email.lower(), name, pw_hash, is_admin))
    conn.commit()
    cur.close()
    conn.close()


def ensure_first_admin():
    """If no users exist, allow creating the first admin."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users;")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count == 0


def logout():
    st.session_state.pop("user_id", None)
    st.session_state.pop("user_name", None)
    st.session_state.pop("is_admin", None)
    st.rerun()


# -------------------------
# HELPERS
# -------------------------
def today():
    return date.today()


def week_start(d: date):
    # Monday as start
    return d - timedelta(days=d.weekday())


def days_in_month(d: date):
    # next month first day - this month first day
    first = d.replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1, day=1)
    else:
        nxt = first.replace(month=first.month + 1, day=1)
    return (nxt - first).days


def get_categories():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM categories ORDER BY sort_order ASC, name ASC;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def upsert_default_categories_if_empty():
    defaults = ["Salud", "Profesional", "Social", "Finanzas personales", "Desarrollo personal"]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM categories;")
    cnt = cur.fetchone()[0]
    if cnt == 0:
        for i, name in enumerate(defaults, start=1):
            cur.execute("INSERT INTO categories (name, sort_order) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING;", (name, i))
        conn.commit()
    cur.close()
    conn.close()


upsert_default_categories_if_empty()


def get_goals(user_id: int, status_filter=None):
    conn = get_conn()
    cur = conn.cursor()
    base = """
        SELECT g.id, c.name, g.title, g.target_value, g.current_value, g.unit, g.start_date, g.end_date, g.status
        FROM goals g
        LEFT JOIN categories c ON c.id = g.category_id
        WHERE g.user_id = %s
    """
    params = [user_id]
    if status_filter:
        base += " AND g.status = %s"
        params.append(status_filter)
    base += " ORDER BY g.created_at DESC;"
    cur.execute(base, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_tasks(user_id: int, only_open=False):
    conn = get_conn()
    cur = conn.cursor()
    q = """
        SELECT t.id, t.title, t.due_date, t.status, t.priority,
               g.title as goal_title
        FROM tasks t
        LEFT JOIN goals g ON g.id = t.goal_id
        WHERE t.user_id = %s
    """
    params = [user_id]
    if only_open:
        q += " AND t.status != 'done'"
    q += " ORDER BY (t.due_date IS NULL) ASC, t.due_date ASC, t.priority DESC, t.created_at DESC;"
    cur.execute(q, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def habit_should_run_today(schedule_type, weekdays_mask):
    if schedule_type == "daily":
        return True
    if schedule_type == "weekdays":
        if not weekdays_mask:
            return False
        parts = weekdays_mask.split(",")
        if len(parts) != 7:
            return False
        # Monday=0..Sunday=6
        return parts[today().weekday()] == "1"
    if schedule_type == "monthly":
        # monthly habits are not "due today" by fixed day; we show them daily as progress
        return True
    return False


def get_habits_for_user(user_id: int, active_only=True):
    conn = get_conn()
    cur = conn.cursor()
    q = """
    SELECT h.id, h.name, h.measure_type, h.unit, h.schedule_type, h.weekdays_mask,
           h.target_count, h.target_value, h.active,
           c.name as category_name,
           g.title as goal_title
    FROM habits h
    LEFT JOIN categories c ON c.id = h.category_id
    LEFT JOIN goals g ON g.id = h.goal_id
    WHERE h.user_id = %s
    """
    params = [user_id]
    if active_only:
        q += " AND h.active = TRUE"
    q += " ORDER BY h.created_at DESC;"
    cur.execute(q, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_habit_log(habit_id: int, log_date: date):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT done, value FROM habit_logs WHERE habit_id = %s AND log_date = %s;", (habit_id, log_date))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def upsert_habit_log(habit_id: int, log_date: date, done=None, value=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO habit_logs (habit_id, log_date, done, value)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (habit_id, log_date)
        DO UPDATE SET done = EXCLUDED.done, value = EXCLUDED.value;
    """, (habit_id, log_date, done, value))
    conn.commit()
    cur.close()
    conn.close()


def get_month_progress_for_habit(habit_id: int, measure_type: str, d: date):
    # monthly progress for current month
    first = d.replace(day=1)
    last = first + timedelta(days=days_in_month(d) - 1)

    conn = get_conn()
    cur = conn.cursor()
    if measure_type == "boolean":
        cur.execute("""
            SELECT COUNT(*) FROM habit_logs
            WHERE habit_id = %s AND log_date BETWEEN %s AND %s AND done = TRUE;
        """, (habit_id, first, last))
        done_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return done_count
    else:
        cur.execute("""
            SELECT COALESCE(SUM(value),0) FROM habit_logs
            WHERE habit_id = %s AND log_date BETWEEN %s AND %s;
        """, (habit_id, first, last))
        s = cur.fetchone()[0]
        cur.close()
        conn.close()
        return float(s)


def upsert_mood_log(user_id: int, log_date: date, mood: int, text: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO mood_logs (user_id, log_date, mood, text)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, log_date)
        DO UPDATE SET mood = EXCLUDED.mood, text = EXCLUDED.text;
    """, (user_id, log_date, mood, text))
    conn.commit()
    cur.close()
    conn.close()


def get_mood_log(user_id: int, log_date: date):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT mood, text FROM mood_logs WHERE user_id = %s AND log_date = %s;", (user_id, log_date))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


# -------------------------
# LOGIN / FIRST ADMIN
# -------------------------
st.sidebar.title("Menú")

if ensure_first_admin():
    st.header("Config inicial: crear primer Admin")
    st.info("No hi ha cap usuari creat. Crea el primer admin (tu) per començar.")
    with st.form("first_admin"):
        name = st.text_input("Nom")
        email = st.text_input("Email")
        pw = st.text_input("Contrasenya", type="password")
        pw2 = st.text_input("Repetir contrasenya", type="password")
        submitted = st.form_submit_button("Crear Admin")
        if submitted:
            if not name or not email or not pw:
                st.error("Omple tots els camps.")
            elif pw != pw2:
                st.error("Les contrasenyes no coincideixen.")
            else:
                try:
                    create_user(email, name, pw, is_admin=True)
                    st.success("Admin creat! Ja pots fer login.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error creant admin: {e}")
    st.stop()


if "user_id" not in st.session_state:
    st.header("Login")
    with st.form("login_form"):
        email = st.text_input("Email").strip().lower()
        pw = st.text_input("Contrasenya", type="password")
        submitted = st.form_submit_button("Entrar")
        if submitted:
            user = get_user_by_email(email)
            if not user:
                st.error("Usuari no trobat.")
            else:
                uid, uemail, uname, pw_hash, is_admin = user
                if verify_password(pw, pw_hash.tobytes() if hasattr(pw_hash, "tobytes") else pw_hash):
                    st.session_state["user_id"] = uid
                    st.session_state["user_name"] = uname
                    st.session_state["is_admin"] = is_admin
                    st.success("Login correcte.")
                    st.rerun()
                else:
                    st.error("Contrasenya incorrecta.")
    st.stop()


# -------------------------
# MAIN APP
# -------------------------
user_id = st.session_state["user_id"]
user_name = st.session_state["user_name"]
is_admin = st.session_state["is_admin"]

st.sidebar.markdown(f"**Usuari:** {user_name}")
if st.sidebar.button("Logout"):
    logout()

section = st.sidebar.radio(
    "Seccions",
    ["Avui", "Hàbits", "Metes", "Tasques", "Categories", "Diari", "Report", "Administració" if is_admin else "Report"]
)

# Fix section list for non-admin
if not is_admin and section == "Administració":
    section = "Avui"


# -------------------------
# ADMIN PANEL
# -------------------------
def admin_panel():
    st.header("Administració (Admin)")
    st.write("Crear usuaris familiars (5–10).")

    with st.form("create_user_form"):
        name = st.text_input("Nom")
        email = st.text_input("Email (únic)").strip().lower()
        pw = st.text_input("Contrasenya", type="password")
        isadm = st.checkbox("És admin?")
        submit = st.form_submit_button("Crear usuari")
        if submit:
            if not name or not email or not pw:
                st.error("Omple tots els camps.")
            else:
                try:
                    create_user(email, name, pw, is_admin=isadm)
                    st.success("Usuari creat.")
                except Exception as e:
                    st.error(f"Error: {e}")

    st.subheader("Usuaris existents")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name, email, is_admin, created_at FROM users ORDER BY created_at DESC;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    df = pd.DataFrame(rows, columns=["id", "name", "email", "is_admin", "created_at"])
    st.dataframe(df, use_container_width=True)


# -------------------------
# CATEGORIES
# -------------------------
def categories_screen():
    st.header("Categories (Compartides)")
    cats = get_categories()
    df = pd.DataFrame(cats, columns=["id", "name"])
    st.dataframe(df, use_container_width=True)

    st.subheader("Afegir categoria")
    with st.form("add_cat"):
        name = st.text_input("Nom categoria")
        order = st.number_input("Ordre", min_value=0, value=0, step=1)
        submit = st.form_submit_button("Guardar")
        if submit:
            if not name.strip():
                st.error("Nom obligatori.")
            else:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("INSERT INTO categories (name, sort_order) VALUES (%s,%s) ON CONFLICT (name) DO NOTHING;",
                            (name.strip(), order))
                conn.commit()
                cur.close()
                conn.close()
                st.success("Categoria guardada.")
                st.rerun()

    st.subheader("Eliminar categoria")
    cat_names = [c[1] for c in cats]
    if cat_names:
        sel = st.selectbox("Selecciona", cat_names)
        if st.button("Eliminar"):
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM categories WHERE name = %s;", (sel,))
            conn.commit()
            cur.close()
            conn.close()
            st.warning("Categoria eliminada.")
            st.rerun()


# -------------------------
# GOALS
# -------------------------
def goals_screen():
    st.header("Metes (Objectius quantificables)")

    cats = get_categories()
    cat_map = {c[1]: c[0] for c in cats}

    st.subheader("Crear nova meta")
    with st.form("add_goal"):
        col1, col2, col3 = st.columns(3)
        with col1:
            title = st.text_input("Títol *")
            category = st.selectbox("Categoria", list(cat_map.keys()))
            status = st.selectbox("Estat", ["active", "paused", "completed", "cancelled"])
        with col2:
            unit = st.text_input("Unitat (kg, €, sessions...)")
            target_value = st.number_input("Valor objectiu", value=0.0, step=1.0)
            current_value = st.number_input("Valor actual", value=0.0, step=1.0)
        with col3:
            start = st.date_input("Inici", value=today())
            end = st.date_input("Fi", value=today() + timedelta(days=180))

        desc = st.text_area("Descripció")
        submit = st.form_submit_button("Guardar meta")

        if submit:
            if not title.strip():
                st.error("Títol obligatori.")
            else:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO goals (user_id, category_id, title, description, unit, target_value, current_value, start_date, end_date, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (user_id, cat_map[category], title.strip(), desc, unit.strip() if unit else None,
                      target_value if target_value > 0 else None,
                      current_value if current_value > 0 else None,
                      start, end, status))
                conn.commit()
                cur.close()
                conn.close()
                st.success("Meta creada.")
                st.rerun()

    st.subheader("Les meves metes")
    goals = get_goals(user_id, status_filter=None)
    if goals:
        df = pd.DataFrame(goals, columns=["id", "categoria", "títol", "objectiu", "actual", "unitat", "inici", "fi", "estat"])
        st.dataframe(df, use_container_width=True)

        st.subheader("Actualitzar / eliminar meta")
        goal_ids = [g[0] for g in goals]
        sel = st.selectbox("Meta (ID)", goal_ids)
        g = next(x for x in goals if x[0] == sel)

        col1, col2 = st.columns(2)
        with col1:
            new_current = st.number_input("Valor actual (actualitza)", value=float(g[4] or 0), step=1.0)
            new_status = st.selectbox("Nou estat", ["active", "paused", "completed", "cancelled"], index=["active","paused","completed","cancelled"].index(g[8]))
        with col2:
            if st.button("Guardar canvis meta"):
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("UPDATE goals SET current_value = %s, status = %s WHERE id = %s AND user_id = %s;",
                            (new_current if new_current > 0 else None, new_status, sel, user_id))
                conn.commit()
                cur.close()
                conn.close()
                st.success("Meta actualitzada.")
                st.rerun()

            if st.button("Eliminar meta (i tasques/hàbits vinculats)"):
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("DELETE FROM goals WHERE id = %s AND user_id = %s;", (sel, user_id))
                conn.commit()
                cur.close()
                conn.close()
                st.warning("Meta eliminada.")
                st.rerun()
    else:
        st.info("Encara no tens metes.")


# -------------------------
# TASKS
# -------------------------
def tasks_screen():
    st.header("Tasques")

    goals = get_goals(user_id, status_filter=None)
    goal_map = {"(Sense meta)": None}
    for g in goals:
        goal_map[f"[{g[0]}] {g[2]}"] = g[0]

    st.subheader("Crear tasca")
    with st.form("add_task"):
        title = st.text_input("Títol tasca *")
        goal_sel = st.selectbox("Meta (opcional)", list(goal_map.keys()))
        due = st.date_input("Data límit", value=today())
        priority = st.selectbox("Prioritat", ["low", "medium", "high"])
        desc = st.text_area("Descripció")
        submit = st.form_submit_button("Guardar tasca")
        if submit:
            if not title.strip():
                st.error("Títol obligatori.")
            else:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO tasks (user_id, goal_id, title, description, due_date, status, priority)
                    VALUES (%s,%s,%s,%s,%s,'todo',%s)
                """, (user_id, goal_map[goal_sel], title.strip(), desc, due, priority))
                conn.commit()
                cur.close()
                conn.close()
                st.success("Tasca creada.")
                st.rerun()

    st.subheader("Tasques obertes")
    tasks = get_tasks(user_id, only_open=True)
    if tasks:
        df = pd.DataFrame(tasks, columns=["id", "títol", "límit", "estat", "prioritat", "meta"])
        st.dataframe(df, use_container_width=True)

        st.subheader("Actualitzar tasca")
        task_ids = [t[0] for t in tasks]
        sel = st.selectbox("Tasca (ID)", task_ids)
        t = next(x for x in tasks if x[0] == sel)

        new_status = st.selectbox("Nou estat", ["todo", "doing", "done"], index=["todo","doing","done"].index(t[3]))
        if st.button("Guardar estat"):
            conn = get_conn()
            cur = conn.cursor()
            if new_status == "done":
                cur.execute("UPDATE tasks SET status=%s, completed_at=NOW() WHERE id=%s AND user_id=%s;", (new_status, sel, user_id))
            else:
                cur.execute("UPDATE tasks SET status=%s, completed_at=NULL WHERE id=%s AND user_id=%s;", (new_status, sel, user_id))
            conn.commit()
            cur.close()
            conn.close()
            st.success("Tasca actualitzada.")
            st.rerun()

        if st.button("Eliminar tasca"):
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM tasks WHERE id=%s AND user_id=%s;", (sel, user_id))
            conn.commit()
            cur.close()
            conn.close()
            st.warning("Tasca eliminada.")
            st.rerun()
    else:
        st.info("No tens tasques obertes.")

    st.subheader("Tasques completades (últimes 30)")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, due_date, completed_at, priority
        FROM tasks
        WHERE user_id = %s AND status='done'
        ORDER BY completed_at DESC
        LIMIT 30;
    """, (user_id,))
    done = cur.fetchall()
    cur.close()
    conn.close()
    if done:
        df = pd.DataFrame(done, columns=["id", "títol", "límit", "completada", "prioritat"])
        st.dataframe(df, use_container_width=True)


# -------------------------
# HABITS
# -------------------------
def habits_screen():
    st.header("Hàbits")

    cats = get_categories()
    cat_map = {c[1]: c[0] for c in cats}

    goals = get_goals(user_id, status_filter=None)
    goal_map = {"(Sense meta)": None}
    for g in goals:
        goal_map[f"[{g[0]}] {g[2]}"] = g[0]

    st.subheader("Crear nou hàbit")
    with st.form("add_habit"):
        col1, col2, col3 = st.columns(3)
        with col1:
            name = st.text_input("Nom hàbit *")
            category = st.selectbox("Categoria", list(cat_map.keys()))
            goal_sel = st.selectbox("Meta (opcional)", list(goal_map.keys()))
            active = st.checkbox("Actiu", value=True)

        with col2:
            measure_type = st.selectbox("Tipus", ["boolean", "numeric"])
            unit = st.text_input("Unitat (si numeric) ex: min, km, €")
            schedule_type = st.selectbox("Programació", ["daily", "weekdays", "monthly"])

        with col3:
            target_count = st.number_input("Objectiu (vegades) (boolean o monthly)", min_value=1, value=1, step=1)
            target_value = st.number_input("Objectiu valor (numeric)", value=0.0, step=1.0)

        desc = st.text_area("Descripció")

        # Weekdays selection
        weekdays_mask = None
        if schedule_type == "weekdays":
            st.markdown("**Dies de la setmana**")
            cols = st.columns(7)
            days = ["Dl", "Dt", "Dc", "Dj", "Dv", "Ds", "Dg"]
            selected = []
            for i, dname in enumerate(days):
                selected.append(cols[i].checkbox(dname, value=(i < 5)))
            weekdays_mask = ",".join(["1" if x else "0" for x in selected])

        submit = st.form_submit_button("Guardar hàbit")
        if submit:
            if not name.strip():
                st.error("Nom obligatori.")
            else:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO habits (user_id, category_id, goal_id, name, description, measure_type, unit,
                                        schedule_type, weekdays_mask, target_count, target_value, active)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (user_id, cat_map[category], goal_map[goal_sel], name.strip(), desc,
                      measure_type, unit.strip() if unit else None,
                      schedule_type, weekdays_mask,
                      int(target_count),
                      float(target_value) if (measure_type == "numeric" and target_value > 0) else None,
                      active))
                conn.commit()
                cur.close()
                conn.close()
                st.success("Hàbit creat.")
                st.rerun()

    st.subheader("Els meus hàbits")
    habits = get_habits_for_user(user_id, active_only=False)
    if habits:
        df = pd.DataFrame(habits, columns=[
            "id", "nom", "tipus", "unitat", "programació", "mask", "obj_count", "obj_val", "actiu",
            "categoria", "meta"
        ])
        st.dataframe(df, use_container_width=True)

        st.subheader("Pausar/activar o eliminar hàbit")
        habit_ids = [h[0] for h in habits]
        sel = st.selectbox("Hàbit (ID)", habit_ids)
        h = next(x for x in habits if x[0] == sel)

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Toggle Actiu/Pausat"):
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("UPDATE habits SET active = NOT active WHERE id=%s AND user_id=%s;", (sel, user_id))
                conn.commit()
                cur.close()
                conn.close()
                st.success("Actualitzat.")
                st.rerun()

        with col2:
            if st.button("Eliminar hàbit (i logs)"):
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("DELETE FROM habits WHERE id=%s AND user_id=%s;", (sel, user_id))
                conn.commit()
                cur.close()
                conn.close()
                st.warning("Eliminat.")
                st.rerun()

        with col3:
            st.caption("Historial ràpid (últims 14 dies)")
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT log_date, done, value
                FROM habit_logs
                WHERE habit_id=%s
                ORDER BY log_date DESC
                LIMIT 14;
            """, (sel,))
            logs = cur.fetchall()
            cur.close()
            conn.close()
            if logs:
                df2 = pd.DataFrame(logs, columns=["data", "done", "valor"])
                st.dataframe(df2, use_container_width=True)
            else:
                st.write("Sense logs.")


# -------------------------
# TODAY DASHBOARD
# -------------------------
def today_screen():
    st.header("Avui")
    st.write("Check-in ràpid d'hàbits, tasques i diari.")

    # Habits due today
    habits = get_habits_for_user(user_id, active_only=True)

    st.subheader("Hàbits d'avui")
    due = []
    for h in habits:
        hid, name, mtype, unit, sched, mask, tcount, tval, active, cat_name, goal_title = h
        if habit_should_run_today(sched, mask):
            due.append(h)

    if due:
        for h in due:
            hid, name, mtype, unit, sched, mask, tcount, tval, active, cat_name, goal_title = h

            with st.container(border=True):
                left, right = st.columns([3, 2])

                with left:
                    st.markdown(f"**{name}**")
                    st.caption(f"Categoria: {cat_name or '-'} | Meta: {goal_title or '-'} | Programació: {sched}")

                existing = get_habit_log(hid, today())

                with right:
                    if mtype == "boolean":
                        default_done = True if (existing and existing[0] is True) else False
                        done = st.checkbox("Fet", value=default_done, key=f"done_{hid}")
                        if st.button("Guardar", key=f"saveb_{hid}"):
                            upsert_habit_log(hid, today(), done=done, value=None)
                            st.success("Guardat.")
                            st.rerun()

                        # Monthly progress if monthly
                        if sched == "monthly":
                            done_count = get_month_progress_for_habit(hid, mtype, today())
                            st.caption(f"Progrés mensual: {done_count}/{tcount}")

                    else:
                        default_val = float(existing[1]) if (existing and existing[1] is not None) else 0.0
                        val = st.number_input(f"Valor ({unit or ''})", value=default_val, step=1.0, key=f"val_{hid}")
                        if st.button("Guardar", key=f"saven_{hid}"):
                            upsert_habit_log(hid, today(), done=None, value=val)
                            st.success("Guardat.")
                            st.rerun()

                        if sched == "monthly":
                            s = get_month_progress_for_habit(hid, mtype, today())
                            st.caption(f"Progrés mensual: {s:.0f}/{float(tval or 0):.0f} {unit or ''}")
    else:
        st.info("No tens hàbits actius per avui.")

    st.subheader("Tasques properes (7 dies)")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.title, t.due_date, t.status, t.priority, COALESCE(g.title,'-') as goal_title
        FROM tasks t
        LEFT JOIN goals g ON g.id = t.goal_id
        WHERE t.user_id = %s AND t.status != 'done'
          AND (t.due_date IS NULL OR t.due_date <= %s)
        ORDER BY (t.due_date IS NULL) ASC, t.due_date ASC, t.priority DESC;
    """, (user_id, today() + timedelta(days=7)))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if rows:
        df = pd.DataFrame(rows, columns=["id", "títol", "límit", "estat", "prioritat", "meta"])
        st.dataframe(df, use_container_width=True)
    else:
        st.write("Cap tasca propera.")

    st.subheader("Diari d'avui (opcional)")
    existing = get_mood_log(user_id, today())
    mood_default = int(existing[0]) if existing else 7
    text_default = existing[1] if existing else ""

    with st.form("mood_form"):
        mood = st.slider("Com t'has sentit avui? (1-10)", min_value=1, max_value=10, value=mood_default)
        text = st.text_area("Nota breu", value=text_default, height=120)
        submitted = st.form_submit_button("Guardar diari")
        if submitted:
            upsert_mood_log(user_id, today(), mood, text)
            st.success("Diari guardat.")
            st.rerun()


# -------------------------
# DIARY
# -------------------------
def diary_screen():
    st.header("Diari")
    st.write("Registre diari d'ànim i notes.")

    col1, col2 = st.columns([2, 3])
    with col1:
        d = st.date_input("Dia", value=today())
    with col2:
        existing = get_mood_log(user_id, d)
        mood_default = int(existing[0]) if existing else 7
        text_default = existing[1] if existing else ""

        with st.form("mood_edit"):
            mood = st.slider("Ànim (1-10)", 1, 10, mood_default)
            text = st.text_area("Text", value=text_default, height=140)
            submitted = st.form_submit_button("Guardar")
            if submitted:
                upsert_mood_log(user_id, d, mood, text)
                st.success("Guardat.")
                st.rerun()

    st.subheader("Històric (últims 30 dies)")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT log_date, mood, text
        FROM mood_logs
        WHERE user_id=%s
        ORDER BY log_date DESC
        LIMIT 30;
    """, (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if rows:
        df = pd.DataFrame(rows, columns=["data", "ànim", "text"])
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Sense entrades.")


# -------------------------
# REPORTS
# -------------------------
def report_screen():
    st.header("Report (bàsic)")

    # Weekly habit completion (boolean)
    ws = week_start(today())
    we = ws + timedelta(days=6)

    st.subheader("Hàbits: compliment setmanal (boolean)")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT h.name,
               COUNT(*) FILTER (WHERE hl.done = TRUE) as done_count,
               COUNT(*) as total_logs
        FROM habits h
        LEFT JOIN habit_logs hl ON hl.habit_id = h.id AND hl.log_date BETWEEN %s AND %s
        WHERE h.user_id = %s AND h.active = TRUE AND h.measure_type='boolean'
        GROUP BY h.name
        ORDER BY done_count DESC;
    """, (ws, we, user_id))
    rows = cur.fetchall()
    if rows:
        df = pd.DataFrame(rows, columns=["hàbit", "dies fets", "dies amb log"])
        st.dataframe(df, use_container_width=True)
    else:
        st.write("No hi ha hàbits boolean.")

    st.subheader("Hàbits numèrics: suma setmanal")
    cur.execute("""
        SELECT h.name,
               COALESCE(SUM(hl.value),0) as sum_value,
               h.unit
        FROM habits h
        LEFT JOIN habit_logs hl ON hl.habit_id = h.id AND hl.log_date BETWEEN %s AND %s
        WHERE h.user_id = %s AND h.active = TRUE AND h.measure_type='numeric'
        GROUP BY h.name, h.unit
        ORDER BY sum_value DESC;
    """, (ws, we, user_id))
    rows = cur.fetchall()
    if rows:
        df = pd.DataFrame(rows, columns=["hàbit", "suma setmanal", "unitat"])
        st.dataframe(df, use_container_width=True)
    else:
        st.write("No hi ha hàbits numèrics.")

    st.subheader("Diari: promig setmanal d'ànim")
    cur.execute("""
        SELECT AVG(mood)::numeric(10,2) as avg_mood
        FROM mood_logs
        WHERE user_id=%s AND log_date BETWEEN %s AND %s;
    """, (user_id, ws, we))
    avg_mood = cur.fetchone()[0]
    st.write(f"Promig d'ànim aquesta setmana: **{avg_mood if avg_mood else 'N/A'}**")

    st.subheader("Tasques: completades aquesta setmana")
    cur.execute("""
        SELECT COUNT(*) FROM tasks
        WHERE user_id=%s AND status='done' AND completed_at::date BETWEEN %s AND %s;
    """, (user_id, ws, we))
    done_tasks = cur.fetchone()[0]
    st.write(f"Tasques completades: **{done_tasks}**")

    cur.close()
    conn.close()


# -------------------------
# ROUTER
# -------------------------
if section == "Avui":
    today_screen()
elif section == "Hàbits":
    habits_screen()
elif section == "Metes":
    goals_screen()
elif section == "Tasques":
    tasks_screen()
elif section == "Categories":
    categories_screen()
elif section == "Diari":
    diary_screen()
elif section == "Report":
    report_screen()
elif section == "Administració" and is_admin:
    admin_panel()
else:
    report_screen()
