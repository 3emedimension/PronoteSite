import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    flash,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
    g,
    send_from_directory,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    import psycopg2
    import psycopg2.extras
except Exception:
    psycopg2 = None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-moi-en-secret-tres-long")
app.config["SESSION_PERMANENT"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL and psycopg2)
DB_NAME = "mini_pronote_v8.db"
ADMIN_DEFAULT_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Azsqerfd2012")
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "EcoleR2026")

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "gif", "webp",
    "doc", "docx", "txt", "zip", "rar", "ppt", "pptx",
    "xls", "xlsx"
}


# =========================
# Base de données
# =========================
def get_conn():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def adapt_sql(sql: str) -> str:
    if USE_POSTGRES:
        return sql.replace("?", "%s")
    return sql


# =========================
# Helpers DB
# =========================
def query_all(sql, params=()):
    conn = get_conn()
    try:
        if USE_POSTGRES:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(adapt_sql(sql), params)
                return cur.fetchall()
        rows = conn.execute(adapt_sql(sql), params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_one(sql, params=()):
    conn = get_conn()
    try:
        if USE_POSTGRES:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(adapt_sql(sql), params)
                return cur.fetchone()
        row = conn.execute(adapt_sql(sql), params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def execute_db(sql, params=()):
    conn = get_conn()
    try:
        if USE_POSTGRES:
            with conn.cursor() as cur:
                cur.execute(adapt_sql(sql), params)
            conn.commit()
        else:
            conn.execute(adapt_sql(sql), params)
            conn.commit()
    finally:
        conn.close()


def executemany_db(sql, params_list):
    conn = get_conn()
    try:
        if USE_POSTGRES:
            with conn.cursor() as cur:
                cur.executemany(adapt_sql(sql), params_list)
            conn.commit()
        else:
            conn.executemany(adapt_sql(sql), params_list)
            conn.commit()
    finally:
        conn.close()


def table_has_column(table_name, column_name):
    if USE_POSTGRES:
        row = query_one(
            """
            SELECT 1 AS ok
            FROM information_schema.columns
            WHERE table_name = ? AND column_name = ?
            LIMIT 1
            """,
            (table_name, column_name),
        )
        return bool(row)

    conn = get_conn()
    try:
        columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(col[1] == column_name for col in columns)
    finally:
        conn.close()


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def unique_filename(filename: str) -> str:
    base = secure_filename(filename)
    name, ext = os.path.splitext(base)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"{name}_{timestamp}{ext}"


def init_db():
    conn = get_conn()
    try:
        cur = conn.cursor()

        if USE_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS classes (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'prof', 'eleve', 'parent')),
                    full_name TEXT NOT NULL,
                    class_id INTEGER REFERENCES classes(id),
                    child_id INTEGER REFERENCES users(id),
                    child_id_2 INTEGER REFERENCES users(id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS subjects (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS grades (
                    id SERIAL PRIMARY KEY,
                    student_id INTEGER NOT NULL REFERENCES users(id),
                    subject_id INTEGER NOT NULL REFERENCES subjects(id),
                    teacher_id INTEGER NOT NULL REFERENCES users(id),
                    value REAL NOT NULL,
                    comment TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS homework (
                    id SERIAL PRIMARY KEY,
                    class_id INTEGER REFERENCES classes(id),
                    subject_id INTEGER NOT NULL REFERENCES subjects(id),
                    teacher_id INTEGER NOT NULL REFERENCES users(id),
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    attachment TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id SERIAL PRIMARY KEY,
                    class_id INTEGER NOT NULL REFERENCES classes(id),
                    subject_id INTEGER NOT NULL REFERENCES subjects(id),
                    teacher_id INTEGER NOT NULL REFERENCES users(id),
                    day_name TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    room TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS absences (
                    id SERIAL PRIMARY KEY,
                    student_id INTEGER NOT NULL REFERENCES users(id),
                    teacher_id INTEGER NOT NULL REFERENCES users(id),
                    absence_date TEXT NOT NULL,
                    reason TEXT,
                    status TEXT NOT NULL DEFAULT 'Non justifiée',
                    created_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    sender_id INTEGER NOT NULL REFERENCES users(id),
                    receiver_id INTEGER NOT NULL REFERENCES users(id),
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS classes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'prof', 'eleve', 'parent')),
                    full_name TEXT NOT NULL,
                    class_id INTEGER,
                    child_id INTEGER,
                    child_id_2 INTEGER,
                    FOREIGN KEY(class_id) REFERENCES classes(id),
                    FOREIGN KEY(child_id) REFERENCES users(id),
                    FOREIGN KEY(child_id_2) REFERENCES users(id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS subjects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS grades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    subject_id INTEGER NOT NULL,
                    teacher_id INTEGER NOT NULL,
                    value REAL NOT NULL,
                    comment TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(student_id) REFERENCES users(id),
                    FOREIGN KEY(subject_id) REFERENCES subjects(id),
                    FOREIGN KEY(teacher_id) REFERENCES users(id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS homework (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_id INTEGER,
                    subject_id INTEGER NOT NULL,
                    teacher_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    attachment TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(class_id) REFERENCES classes(id),
                    FOREIGN KEY(subject_id) REFERENCES subjects(id),
                    FOREIGN KEY(teacher_id) REFERENCES users(id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_id INTEGER NOT NULL,
                    subject_id INTEGER NOT NULL,
                    teacher_id INTEGER NOT NULL,
                    day_name TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    room TEXT,
                    FOREIGN KEY(class_id) REFERENCES classes(id),
                    FOREIGN KEY(subject_id) REFERENCES subjects(id),
                    FOREIGN KEY(teacher_id) REFERENCES users(id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS absences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    teacher_id INTEGER NOT NULL,
                    absence_date TEXT NOT NULL,
                    reason TEXT,
                    status TEXT NOT NULL DEFAULT 'Non justifiée',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(student_id) REFERENCES users(id),
                    FOREIGN KEY(teacher_id) REFERENCES users(id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(sender_id) REFERENCES users(id),
                    FOREIGN KEY(receiver_id) REFERENCES users(id)
                )
            """)

        conn.commit()
    finally:
        conn.close()

    if not table_has_column("users", "child_id"):
        execute_db("ALTER TABLE users ADD COLUMN child_id INTEGER")
    if not table_has_column("users", "child_id_2"):
        execute_db("ALTER TABLE users ADD COLUMN child_id_2 INTEGER")
    if not table_has_column("homework", "attachment"):
        execute_db("ALTER TABLE homework ADD COLUMN attachment TEXT")

    if query_one("SELECT COUNT(*) AS total FROM classes")["total"] == 0:
        executemany_db(
            "INSERT INTO classes (name) VALUES (?)",
            [("6A",), ("6B",), ("5A",), ("5B",), ("4A",), ("4B",), ("3A",), ("3B",)],
        )

    if query_one("SELECT COUNT(*) AS total FROM subjects")["total"] == 0:
        executemany_db(
            "INSERT INTO subjects (name) VALUES (?)",
            [("Mathématiques",), ("Français",), ("Histoire",), ("Anglais",), ("SVT",), ("Physique",)],
        )

    admin_user = query_one("SELECT id FROM users WHERE username = ?", ("admin",))
    admin_hash = generate_password_hash(ADMIN_DEFAULT_PASSWORD)
    if not admin_user:
        execute_db(
            "INSERT INTO users (username, password, role, full_name, class_id, child_id, child_id_2) VALUES (?, ?, ?, ?, NULL, NULL, NULL)",
            ("admin", admin_hash, "admin", "Administrateur"),
        )
    else:
        execute_db(
            "UPDATE users SET password = ?, role = 'admin', full_name = 'Administrateur', child_id = NULL, child_id_2 = NULL WHERE username = ?",
            (admin_hash, "admin"),
        )


# =========================
# Session / sécurité
# =========================
@app.before_request
def load_logged_user():
    allowed_routes = {"site_access", "static"}
    if request.endpoint not in allowed_routes and not session.get("site_unlocked"):
        return redirect(url_for("site_access"))

    g.user = None
    user_id = session.get("user_id")
    if not user_id:
        return

    user = query_one(
        """
        SELECT u.*, c.name AS class_name
        FROM users u
        LEFT JOIN classes c ON c.id = u.class_id
        WHERE u.id = ?
        """,
        (user_id,),
    )

    if not user:
        session.pop("user_id", None)
        session.pop("username", None)
        session.pop("role", None)
        session.pop("full_name", None)
        return

    session["username"] = user["username"]
    session["role"] = user["role"]
    session["full_name"] = user["full_name"]
    g.user = user


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not g.user:
            flash("Connecte-toi d'abord.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not g.user or g.user["role"] not in roles:
                flash("Accès refusé.")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return wrapper
    return decorator


def get_parent_children(user):
    if user["role"] != "parent":
        return []

    child_ids = []
    if user.get("child_id"):
        child_ids.append(user["child_id"])
    if user.get("child_id_2") and user["child_id_2"] not in child_ids:
        child_ids.append(user["child_id_2"])

    children = []
    for child_id in child_ids:
        child = query_one(
            """
            SELECT u.*, c.name AS class_name
            FROM users u
            LEFT JOIN classes c ON c.id = u.class_id
            WHERE u.id = ?
            """,
            (child_id,),
        )
        if child:
            children.append(child)
    return children


# =========================
# UI
# =========================
BASE_TOP = """
<!doctype html>
<html lang='fr'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>{{ title }}</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, Arial, Helvetica, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(96,165,250,0.18), transparent 26%),
        radial-gradient(circle at top right, rgba(59,130,246,0.16), transparent 24%),
        linear-gradient(135deg, #eff6ff, #f8fbff 55%, #eef4ff);
      color: #18212f;
    }
    .nav {
      background: linear-gradient(90deg, #0f172a, #1d4ed8);
      color: white;
      padding: 15px 22px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.22);
      position: sticky;
      top: 0;
      z-index: 20;
    }
    .nav strong { font-size: 20px; }
    .nav a {
      color: white;
      text-decoration: none;
      margin-left: 14px;
      font-weight: 700;
      opacity: 0.95;
    }
    .nav a:hover { opacity: 1; text-decoration: underline; }
    .container {
      max-width: 1260px;
      margin: 28px auto;
      padding: 0 18px;
    }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 18px; }
    .card {
      background: rgba(255,255,255,0.93);
      backdrop-filter: blur(10px);
      border-radius: 24px;
      padding: 24px;
      box-shadow: 0 18px 36px rgba(37,99,235,0.10);
      border: 1px solid rgba(255,255,255,0.85);
    }
    .hero {
      background: linear-gradient(135deg, #1d4ed8, #60a5fa);
      color: white;
      border-radius: 28px;
      padding: 30px;
      box-shadow: 0 20px 36px rgba(37,99,235,0.24);
      margin-bottom: 20px;
    }
    .hero p { opacity: 0.96; }
    h1, h2, h3 { margin-top: 0; }
    input, select, textarea {
      width: 100%; padding: 12px 13px; border: 1px solid #d5e0f3; border-radius: 13px;
      margin-top: 6px; margin-bottom: 14px; font-size: 15px; background: #fff; outline: none;
    }
    input:focus, select:focus, textarea:focus { border-color: #60a5fa; box-shadow: 0 0 0 4px rgba(96,165,250,0.16); }
    textarea { min-height: 110px; resize: vertical; }
    button {
      background: linear-gradient(90deg, #1d4ed8, #2563eb); color: white; border: none;
      padding: 11px 16px; border-radius: 12px; font-weight: 700; cursor: pointer;
      box-shadow: 0 10px 20px rgba(37,99,235,0.18);
    }
    button:hover { transform: translateY(-1px); }
    .danger { background: linear-gradient(90deg, #c0392b, #e74c3c); }
    .muted { color: #5f6b7a; }
    .flash { background: #fff9db; border: 1px solid #f2dd7d; padding: 11px 13px; border-radius: 12px; margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; overflow: hidden; border-radius: 16px; background: white; }
    th, td { padding: 12px 10px; border-bottom: 1px solid #ebf0f8; text-align: left; vertical-align: top; }
    th { background: #edf4ff; }
    .badge { display: inline-block; padding: 6px 10px; border-radius: 999px; background: #e7efff; color: #1d4ed8; font-weight: 700; font-size: 13px; }
    .small { font-size: 13px; }
    .metric { font-size: 34px; font-weight: 800; margin: 0; }
    .two-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
    .login-wrap { max-width: 980px; margin: 40px auto; }
    @media (max-width: 900px) { .two-cols { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
"""

NAV = """
<div class='nav'>
  <div>
    <strong>Mini Pronote+</strong>
    {% if session.get('user_id') %}
      <span style='margin-left:10px;'>{{ session.get('full_name') }} ({{ session.get('role') }})</span>
    {% endif %}
  </div>
  <div>
    {% if session.get('user_id') %}
      <a href='{{ url_for("dashboard") }}'>Accueil</a>
      <a href='{{ url_for("grades") }}'>Notes</a>
      <a href='{{ url_for("homework_page") }}'>Devoirs</a>
      <a href='{{ url_for("schedule_page") }}'>Emploi du temps</a>
      <a href='{{ url_for("absences_page") }}'>Absences</a>
      <a href='{{ url_for("messages_page") }}'>Messagerie</a>
      {% if session.get('role') in ['prof', 'admin'] %}
        <a href='{{ url_for("add_grade") }}'>Ajouter note</a>
        <a href='{{ url_for("manage_users") }}'>Comptes</a>
      {% endif %}
      {% if session.get('role') == 'admin' %}
        <a href='{{ url_for("manage_school") }}'>École</a>
      {% endif %}
      <a href='{{ url_for("logout") }}'>Déconnexion</a>
    {% else %}
      <a href='{{ url_for("login") }}'>Connexion</a>
    {% endif %}
  </div>
</div>
"""


def render_page(content, **context):
    template = BASE_TOP + NAV + """
    <div class='container'>
      {% with messages = get_flashed_messages() %}
        {% if messages %}
          {% for message in messages %}
            <div class='flash'>{{ message }}</div>
          {% endfor %}
        {% endif %}
      {% endwith %}
      """ + content + """
    </div>
</body>
</html>
    """
    return render_template_string(template, **context)


# =========================
# Accès protégé au site
# =========================
@app.route("/site-access", methods=["GET", "POST"])
def site_access():
    if session.get("site_unlocked"):
        return redirect(url_for("dashboard")) if session.get("user_id") else redirect(url_for("login"))

    if request.method == "POST":
        password = request.form.get("password", "").strip()

        if password == SITE_PASSWORD:
            session["site_unlocked"] = True
            flash("Accès autorisé.")
            return redirect(url_for("login"))

        flash("Mot de passe du site incorrect.")

    content = """
    <div class='card' style='max-width:520px; margin:60px auto;'>
      <h1>Accès protégé</h1>
      <p class='muted'>Ce site est privé. Entre le mot de passe d'accès.</p>
      <form method='post'>
        <label>Mot de passe du site</label>
        <input type='password' name='password' required>
        <button type='submit'>Entrer</button>
      </form>
    </div>
    """
    return render_page(content, title="Accès protégé")


# =========================
# Auth
# =========================
@app.route("/")
def index():
    if not session.get("site_unlocked"):
        return redirect(url_for("site_access"))
    return redirect(url_for("dashboard")) if session.get("user_id") else redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not session.get("site_unlocked"):
        return redirect(url_for("site_access"))

    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = query_one("SELECT * FROM users WHERE username = ?", (username,))

        if user and check_password_hash(user["password"], password):
            site_unlocked = session.get("site_unlocked")
            session.clear()
            if site_unlocked:
                session["site_unlocked"] = True
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"]
            flash("Connexion réussie.")
            return redirect(url_for("dashboard"))

        flash("Identifiants invalides.")

    content = """
    <div class='login-wrap'>
      <div class='grid'>
        <div class='card'>
          <h1>Connexion</h1>
          <p class='muted'>Version propre avec sessions stabilisées, mots de passe sécurisés et interface améliorée.</p>
          <p class='muted'>Pas de compte ? <a href='{{ url_for("register") }}'>Créer un compte</a></p>
          <form method='post' autocomplete='off'>
            <label>Nom d'utilisateur</label>
            <input name='username' required>
            <label>Mot de passe</label>
            <input name='password' type='password' required>
            <button type='submit'>Se connecter</button>
          </form>
        </div>
        <div class='card'>
          <h2>Fonctions</h2>
          <p><span class='badge'>Classes</span> gestion des classes et matières</p>
          <p><span class='badge'>Notes</span> moyennes automatiques par matière</p>
          <p><span class='badge'>Vie scolaire</span> devoirs, emploi du temps, absences</p>
          <p><span class='badge'>Messagerie</span> échanges prof ↔ élève ↔ parent</p>
        </div>
      </div>
    </div>
    """
    return render_page(content, title="Connexion")


@app.route("/register", methods=["GET", "POST"])
def register():
    if not session.get("site_unlocked"):
        return redirect(url_for("site_access"))

    classes = query_all("SELECT id, name FROM classes ORDER BY name")
    students = query_all("SELECT id, full_name FROM users WHERE role='eleve' ORDER BY full_name")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        full_name = request.form.get("full_name", "").strip()
        role = request.form.get("role", "").strip()
        class_id = request.form.get("class_id") or None
        child_id = request.form.get("child_id") or None
        child_id_2 = request.form.get("child_id_2") or None

        if not username or not password or not full_name or role not in ["eleve", "prof", "parent"]:
            flash("Champs invalides.")
            return redirect(url_for("register"))
        if role == "parent" and not child_id and not child_id_2:
            flash("Un compte parent doit être lié à au moins un élève.")
            return redirect(url_for("register"))
        if child_id and child_id_2 and child_id == child_id_2:
            flash("Tu ne peux pas choisir deux fois le même enfant.")
            return redirect(url_for("register"))

        if role == "parent":
            class_id = None
        else:
            child_id = None
            child_id_2 = None

        try:
            execute_db(
                "INSERT INTO users (username, password, role, full_name, class_id, child_id, child_id_2) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, generate_password_hash(password), role, full_name, class_id, child_id, child_id_2),
            )
            flash("Compte créé. Tu peux maintenant te connecter.")
            return redirect(url_for("login"))
        except Exception:
            flash("Nom d'utilisateur déjà utilisé.")

    content = """
    <div class='card' style='max-width:700px; margin:auto;'>
      <h1>Créer un compte</h1>
      <form method='post' autocomplete='off'>
        <label>Nom complet</label>
        <input name='full_name' required>
        <label>Nom d'utilisateur</label>
        <input name='username' required>
        <label>Mot de passe</label>
        <input type='password' name='password' required>
        <label>Type de compte</label>
        <select name='role' id='role_select' required onchange='toggleRegisterFields()'>
          <option value='eleve'>Élève</option>
          <option value='prof'>Professeur</option>
          <option value='parent'>Parent</option>
        </select>
        <div id='class_block'>
          <label>Classe (élève / professeur)</label>
          <select name='class_id'>
            <option value=''>Aucune</option>
            {% for c in classes %}<option value='{{ c.id }}'>{{ c.name }}</option>{% endfor %}
          </select>
        </div>
        <div id='child_block' style='display:none;'>
          <label>Enfant lié 1</label>
          <select name='child_id'>
            <option value=''>Aucun</option>
            {% for s in students %}<option value='{{ s.id }}'>{{ s.full_name }}</option>{% endfor %}
          </select>
          <label>Enfant lié 2</label>
          <select name='child_id_2'>
            <option value=''>Aucun</option>
            {% for s in students %}<option value='{{ s.id }}'>{{ s.full_name }}</option>{% endfor %}
          </select>
        </div>
        <button type='submit'>Créer le compte</button>
      </form>
    </div>
    <script>
      function toggleRegisterFields() {
        const role = document.getElementById('role_select').value;
        document.getElementById('class_block').style.display = role === 'parent' ? 'none' : 'block';
        document.getElementById('child_block').style.display = role === 'parent' ? 'block' : 'none';
      }
      toggleRegisterFields();
    </script>
    """
    return render_page(content, title="Créer un compte", classes=classes, students=students)


@app.route("/logout")
def logout():
    site_unlocked = session.get("site_unlocked")
    session.clear()
    if site_unlocked:
        session["site_unlocked"] = True
    flash("Tu es déconnecté.")
    return redirect(url_for("login"))


# =========================
# Dashboard
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    user = g.user
    parent_children = get_parent_children(user)
    parent_child_names = ", ".join(child["full_name"] for child in parent_children)

    if user["role"] == "eleve":
        grades_list = query_all("SELECT value FROM grades WHERE student_id = ?", (user["id"],))
        avg = round(sum(g["value"] for g in grades_list) / len(grades_list), 2) if grades_list else "-"
        stats = {
            "Moyenne générale": avg,
            "Notes": len(grades_list),
            "Devoirs": query_one("SELECT COUNT(*) AS total FROM homework WHERE class_id IS NULL OR class_id = ?", (user["class_id"],))["total"],
            "Absences": query_one("SELECT COUNT(*) AS total FROM absences WHERE student_id = ?", (user["id"],))["total"],
        }
    elif user["role"] == "parent":
        if parent_children:
            child_ids = [child["id"] for child in parent_children]
            placeholders = ",".join(["?"] * len(child_ids))
            grades_list = query_all(f"SELECT value FROM grades WHERE student_id IN ({placeholders})", tuple(child_ids))
            avg = round(sum(g["value"] for g in grades_list) / len(grades_list), 2) if grades_list else "-"
            abs_total = query_one(f"SELECT COUNT(*) AS total FROM absences WHERE student_id IN ({placeholders})", tuple(child_ids))["total"]
            stats = {
                "Enfants": len(parent_children),
                "Noms": parent_child_names,
                "Moyenne générale": avg,
                "Notes": len(grades_list),
                "Absences": abs_total,
            }
        else:
            stats = {"Enfants": 0, "Noms": "Non liés", "Moyenne générale": "-", "Notes": 0, "Absences": 0}
    elif user["role"] == "prof":
        stats = {
            "Notes saisies": query_one("SELECT COUNT(*) AS total FROM grades WHERE teacher_id = ?", (user["id"],))["total"],
            "Devoirs publiés": query_one("SELECT COUNT(*) AS total FROM homework WHERE teacher_id = ?", (user["id"],))["total"],
            "Messages reçus": query_one("SELECT COUNT(*) AS total FROM messages WHERE receiver_id = ?", (user["id"],))["total"],
            "Élèves": query_one("SELECT COUNT(*) AS total FROM users WHERE role='eleve'")["total"],
        }
    else:
        stats = {
            "Utilisateurs": query_one("SELECT COUNT(*) AS total FROM users")["total"],
            "Classes": query_one("SELECT COUNT(*) AS total FROM classes")["total"],
            "Notes": query_one("SELECT COUNT(*) AS total FROM grades")["total"],
            "Messages": query_one("SELECT COUNT(*) AS total FROM messages")["total"],
        }

    latest_messages = query_all(
        """
        SELECT m.subject, m.created_at, u.full_name AS sender_name
        FROM messages m JOIN users u ON u.id = m.sender_id
        WHERE m.receiver_id = ?
        ORDER BY m.id DESC LIMIT 5
        """,
        (user["id"],),
    )

    content = """
    <div class='hero'>
      <h1>Bienvenue {{ user.full_name }}</h1>
      <p>
        Rôle : <strong>{{ user.role }}</strong>
        {% if parent_child_names %} · Enfant(s) lié(s) : <strong>{{ parent_child_names }}</strong>
        {% elif user.class_name %} · Classe : <strong>{{ user.class_name }}</strong>{% endif %}
      </p>
    </div>
    <div class='grid'>
      {% for key, value in stats.items() %}<div class='card'><h3>{{ key }}</h3><p class='metric'>{{ value }}</p></div>{% endfor %}
    </div>
    <div class='grid' style='margin-top:18px;'>
      <div class='card'>
        <h2>Accès rapide</h2>
        <p><a href='{{ url_for("grades") }}'>Voir les notes</a></p>
        <p><a href='{{ url_for("homework_page") }}'>Voir les devoirs</a></p>
        <p><a href='{{ url_for("schedule_page") }}'>Voir l'emploi du temps</a></p>
        <p><a href='{{ url_for("absences_page") }}'>Voir les absences</a></p>
        <p><a href='{{ url_for("messages_page") }}'>Ouvrir la messagerie</a></p>
      </div>
      <div class='card'>
        <h2>Derniers messages</h2>
        {% for m in latest_messages %}
          <div style='padding:10px 0; border-bottom:1px solid #eef3fb;'><strong>{{ m.subject }}</strong><br><span class='small muted'>De {{ m.sender_name }} · {{ m.created_at }}</span></div>
        {% else %}<p class='muted'>Aucun message reçu.</p>{% endfor %}
      </div>
    </div>
    """
    return render_page(content, title="Tableau de bord", user=user, stats=stats, latest_messages=latest_messages, parent_child_names=parent_child_names)


# =========================
# Notes
# =========================
@app.route("/grades")
@login_required
def grades():
    user = g.user

    if user["role"] == "eleve":
        rows = query_all(
            """
            SELECT g.id, g.value, g.comment, g.created_at, s.name AS subject_name, u.full_name AS teacher_name
            FROM grades g JOIN subjects s ON s.id = g.subject_id JOIN users u ON u.id = g.teacher_id
            WHERE g.student_id = ? ORDER BY g.id DESC
            """,
            (user["id"],),
        )
        averages = query_all(
            """
            SELECT s.name AS subject_name, ROUND(AVG(g.value), 2) AS average_value
            FROM grades g JOIN subjects s ON s.id = g.subject_id
            WHERE g.student_id = ? GROUP BY s.name ORDER BY s.name
            """,
            (user["id"],),
        )
        show_student_col = False
    elif user["role"] == "parent":
        children = get_parent_children(user)
        if not children:
            return render_page("<div class='card'><h1>Notes</h1><p>Aucun enfant lié à ce compte parent.</p></div>", title="Notes")
        student_ids = [child["id"] for child in children]
        placeholders = ",".join(["?"] * len(student_ids))
        rows = query_all(
            f"""
            SELECT g.id, g.value, g.comment, g.created_at, s.name AS subject_name,
                   u.full_name AS teacher_name, stu.full_name AS student_name
            FROM grades g
            JOIN subjects s ON s.id = g.subject_id
            JOIN users u ON u.id = g.teacher_id
            JOIN users stu ON stu.id = g.student_id
            WHERE g.student_id IN ({placeholders}) ORDER BY g.id DESC
            """,
            tuple(student_ids),
        )
        averages = query_all(
            f"""
            SELECT stu.full_name AS student_name, s.name AS subject_name, ROUND(AVG(g.value), 2) AS average_value
            FROM grades g JOIN users stu ON stu.id = g.student_id JOIN subjects s ON s.id = g.subject_id
            WHERE g.student_id IN ({placeholders})
            GROUP BY stu.full_name, s.name ORDER BY stu.full_name, s.name
            """,
            tuple(student_ids),
        )
        show_student_col = True
    else:
        rows = query_all(
            """
            SELECT g.id, g.value, g.comment, g.created_at, s.name AS subject_name,
                   stu.full_name AS student_name, tea.full_name AS teacher_name
            FROM grades g
            JOIN subjects s ON s.id = g.subject_id
            JOIN users stu ON stu.id = g.student_id
            JOIN users tea ON tea.id = g.teacher_id
            ORDER BY g.id DESC
            """
        )
        averages = query_all(
            """
            SELECT stu.full_name AS student_name, s.name AS subject_name, ROUND(AVG(g.value), 2) AS average_value
            FROM grades g JOIN users stu ON stu.id = g.student_id JOIN subjects s ON s.id = g.subject_id
            GROUP BY stu.full_name, s.name ORDER BY stu.full_name, s.name
            """
        )
        show_student_col = True

    content = """
    <div class='two-cols'>
      <div class='card'>
        <h1>Notes</h1>
        <table>
          <thead><tr>{% if show_student_col %}<th>Élève</th>{% endif %}<th>Matière</th><th>Note</th><th>Professeur</th><th>Commentaire</th><th>Date</th></tr></thead>
          <tbody>
            {% for row in rows %}
              <tr>
                {% if show_student_col %}<td>{{ row.student_name }}</td>{% endif %}
                <td>{{ row.subject_name }}</td><td><strong>{{ row.value }}/20</strong></td><td>{{ row.teacher_name }}</td><td>{{ row.comment or '-' }}</td><td>{{ row.created_at }}</td>
              </tr>
            {% else %}<tr><td colspan='6'>Aucune note.</td></tr>{% endfor %}
          </tbody>
        </table>
      </div>
      <div class='card'>
        <h2>Moyennes automatiques</h2>
        <table>
          <thead><tr>{% if show_student_col %}<th>Élève</th>{% endif %}<th>Matière</th><th>Moyenne</th></tr></thead>
          <tbody>
            {% for avg in averages %}
              <tr>{% if show_student_col %}<td>{{ avg.student_name }}</td>{% endif %}<td>{{ avg.subject_name }}</td><td><strong>{{ avg.average_value }}/20</strong></td></tr>
            {% else %}<tr><td colspan='3'>Aucune moyenne disponible.</td></tr>{% endfor %}
          </tbody>
        </table>
      </div>
    </div>
    """
    return render_page(content, title="Notes", rows=rows, averages=averages, show_student_col=show_student_col)


@app.route("/add-grade", methods=["GET", "POST"])
@login_required
@role_required("prof", "admin")
def add_grade():
    students = query_all("SELECT u.id, u.full_name, c.name AS class_name FROM users u LEFT JOIN classes c ON c.id=u.class_id WHERE role='eleve' ORDER BY u.full_name")
    subjects = query_all("SELECT id, name FROM subjects ORDER BY name")

    if request.method == "POST":
        try:
            value_float = float(request.form.get("value"))
            if value_float < 0 or value_float > 20:
                raise ValueError
        except Exception:
            flash("La note doit être un nombre entre 0 et 20.")
            return redirect(url_for("add_grade"))

        execute_db(
            "INSERT INTO grades (student_id, subject_id, teacher_id, value, comment, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                request.form.get("student_id"),
                request.form.get("subject_id"),
                g.user["id"],
                value_float,
                request.form.get("comment", "").strip(),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        flash("Note ajoutée.")
        return redirect(url_for("grades"))

    content = """
    <div class='card' style='max-width:760px; margin:auto;'>
      <h1>Ajouter une note</h1>
      <form method='post'>
        <label>Élève</label>
        <select name='student_id' required>{% for s in students %}<option value='{{ s.id }}'>{{ s.full_name }}{% if s.class_name %} - {{ s.class_name }}{% endif %}</option>{% endfor %}</select>
        <label>Matière</label>
        <select name='subject_id' required>{% for s in subjects %}<option value='{{ s.id }}'>{{ s.name }}</option>{% endfor %}</select>
        <label>Note sur 20</label>
        <input name='value' type='number' step='0.1' min='0' max='20' required>
        <label>Commentaire</label>
        <textarea name='comment'></textarea>
        <button type='submit'>Enregistrer</button>
      </form>
    </div>
    """
    return render_page(content, title="Ajouter note", students=students, subjects=subjects)


# =========================
# Devoirs
# =========================
@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/homework", methods=["GET", "POST"])
@login_required
def homework_page():
    user = g.user
    subjects = query_all("SELECT id, name FROM subjects ORDER BY name")
    classes = query_all("SELECT id, name FROM classes ORDER BY name")

    if request.method == "POST":
        if user["role"] not in ["prof", "admin"]:
            flash("Accès refusé.")
            return redirect(url_for("homework_page"))

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        due_date = request.form.get("due_date", "").strip()
        if not title or not description or not due_date:
            flash("Remplis tous les champs du devoir.")
            return redirect(url_for("homework_page"))

        uploaded = request.files.get("attachment")
        attachment_name = None

        if uploaded and uploaded.filename:
            if not allowed_file(uploaded.filename):
                flash("Type de fichier non autorisé.")
                return redirect(url_for("homework_page"))
            attachment_name = unique_filename(uploaded.filename)
            uploaded.save(os.path.join(UPLOAD_FOLDER, attachment_name))

        execute_db(
            "INSERT INTO homework (class_id, subject_id, teacher_id, title, description, due_date, attachment, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.form.get("class_id") or None,
                request.form.get("subject_id"),
                user["id"],
                title,
                description,
                due_date,
                attachment_name,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        flash("Devoir ajouté.")
        return redirect(url_for("homework_page"))

    if user["role"] == "eleve":
        target_class_ids = [user["class_id"]] if user.get("class_id") else []
    elif user["role"] == "parent":
        target_class_ids = []
        for child in get_parent_children(user):
            if child.get("class_id") and child["class_id"] not in target_class_ids:
                target_class_ids.append(child["class_id"])
    else:
        target_class_ids = []

    if user["role"] in ["eleve", "parent"]:
        if target_class_ids:
            placeholders = ",".join(["?"] * len(target_class_ids))
            items = query_all(
                f"""
                SELECT h.*, s.name AS subject_name, u.full_name AS teacher_name, c.name AS class_name
                FROM homework h JOIN subjects s ON s.id = h.subject_id JOIN users u ON u.id = h.teacher_id
                LEFT JOIN classes c ON c.id = h.class_id
                WHERE h.class_id IS NULL OR h.class_id IN ({placeholders})
                ORDER BY h.due_date ASC
                """,
                tuple(target_class_ids),
            )
        else:
            items = query_all(
                """
                SELECT h.*, s.name AS subject_name, u.full_name AS teacher_name, c.name AS class_name
                FROM homework h JOIN subjects s ON s.id = h.subject_id JOIN users u ON u.id = h.teacher_id
                LEFT JOIN classes c ON c.id = h.class_id
                WHERE h.class_id IS NULL
                ORDER BY h.due_date ASC
                """
            )
    else:
        items = query_all(
            """
            SELECT h.*, s.name AS subject_name, u.full_name AS teacher_name, c.name AS class_name
            FROM homework h JOIN subjects s ON s.id = h.subject_id JOIN users u ON u.id = h.teacher_id
            LEFT JOIN classes c ON c.id = h.class_id
            ORDER BY h.due_date ASC
            """
        )

    content = """
    <div class='grid'>
      {% if user.role in ['prof', 'admin'] %}
      <div class='card'>
        <h2>Ajouter un devoir</h2>
        <form method='post' enctype='multipart/form-data'>
          <label>Classe</label>
          <select name='class_id'><option value=''>Toutes les classes</option>{% for c in classes %}<option value='{{ c.id }}'>{{ c.name }}</option>{% endfor %}</select>
          <label>Matière</label>
          <select name='subject_id' required>{% for s in subjects %}<option value='{{ s.id }}'>{{ s.name }}</option>{% endfor %}</select>
          <label>Titre</label><input name='title' required>
          <label>Description</label><textarea name='description' required></textarea>
          <label>Date limite</label><input type='date' name='due_date' required>
          <label>Pièce jointe</label><input type='file' name='attachment'>
          <button type='submit'>Publier</button>
        </form>
      </div>
      {% endif %}
      <div class='card'>
        <h1>Devoirs</h1>
        {% for item in items %}
          <div style='border:1px solid #e5ebf5; border-radius:16px; padding:16px; margin-bottom:14px;'>
            <div style='display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap;'><strong>{{ item.title }}</strong><span class='badge'>{{ item.subject_name }}</span></div>
            <p>{{ item.description }}</p>
            <p class='muted'>Classe : {{ item.class_name or 'Toutes' }} · Professeur : {{ item.teacher_name }} · Date limite : {{ item.due_date }}</p>
            {% if item.attachment %}
              <p><a href='{{ url_for("uploaded_file", filename=item.attachment) }}' target='_blank'>Télécharger la pièce jointe</a></p>
            {% endif %}
          </div>
        {% else %}<p>Aucun devoir.</p>{% endfor %}
      </div>
    </div>
    """
    return render_page(content, title="Devoirs", user=user, items=items, subjects=subjects, classes=classes)


# =========================
# Emploi du temps
# =========================
@app.route("/schedule", methods=["GET", "POST"])
@login_required
def schedule_page():
    user = g.user
    classes = query_all("SELECT id, name FROM classes ORDER BY name")
    subjects = query_all("SELECT id, name FROM subjects ORDER BY name")
    teachers = query_all("SELECT id, full_name FROM users WHERE role IN ('prof', 'admin') ORDER BY full_name")

    if request.method == "POST":
        if user["role"] != "admin":
            flash("Seul l'admin peut ajouter un cours à l'emploi du temps.")
            return redirect(url_for("schedule_page"))

        execute_db(
            "INSERT INTO schedules (class_id, subject_id, teacher_id, day_name, start_time, end_time, room) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                request.form.get("class_id"), request.form.get("subject_id"), request.form.get("teacher_id"),
                request.form.get("day_name"), request.form.get("start_time"), request.form.get("end_time"),
                request.form.get("room", "").strip(),
            ),
        )
        flash("Cours ajouté à l'emploi du temps.")
        return redirect(url_for("schedule_page"))

    if user["role"] == "eleve":
        target_class_ids = [user["class_id"]] if user.get("class_id") else []
    elif user["role"] == "parent":
        target_class_ids = []
        for child in get_parent_children(user):
            if child.get("class_id") and child["class_id"] not in target_class_ids:
                target_class_ids.append(child["class_id"])
    else:
        target_class_ids = []

    if user["role"] in ["eleve", "parent"]:
        if target_class_ids:
            placeholders = ",".join(["?"] * len(target_class_ids))
            rows = query_all(
                f"""
                SELECT sc.id, sc.day_name, sc.start_time, sc.end_time, sc.room, s.name AS subject_name, u.full_name AS teacher_name, c.name AS class_name
                FROM schedules sc JOIN subjects s ON s.id = sc.subject_id JOIN users u ON u.id = sc.teacher_id JOIN classes c ON c.id = sc.class_id
                WHERE sc.class_id IN ({placeholders})
                ORDER BY c.name,
                  CASE sc.day_name WHEN 'Lundi' THEN 1 WHEN 'Mardi' THEN 2 WHEN 'Mercredi' THEN 3 WHEN 'Jeudi' THEN 4 WHEN 'Vendredi' THEN 5 ELSE 6 END,
                  sc.start_time
                """,
                tuple(target_class_ids),
            )
        else:
            rows = []
    else:
        rows = query_all(
            """
            SELECT sc.id, sc.day_name, sc.start_time, sc.end_time, sc.room, s.name AS subject_name, u.full_name AS teacher_name, c.name AS class_name
            FROM schedules sc JOIN subjects s ON s.id = sc.subject_id JOIN users u ON u.id = sc.teacher_id JOIN classes c ON c.id = sc.class_id
            ORDER BY c.name,
              CASE sc.day_name WHEN 'Lundi' THEN 1 WHEN 'Mardi' THEN 2 WHEN 'Mercredi' THEN 3 WHEN 'Jeudi' THEN 4 WHEN 'Vendredi' THEN 5 ELSE 6 END,
              sc.start_time
            """
        )

    content = """
    <div class='grid'>
      {% if user.role == 'admin' %}
      <div class='card'>
        <h2>Ajouter un cours</h2>
        <form method='post'>
          <label>Classe</label><select name='class_id' required>{% for c in classes %}<option value='{{ c.id }}'>{{ c.name }}</option>{% endfor %}</select>
          <label>Matière</label><select name='subject_id' required>{% for s in subjects %}<option value='{{ s.id }}'>{{ s.name }}</option>{% endfor %}</select>
          <label>Professeur</label><select name='teacher_id' required>{% for t in teachers %}<option value='{{ t.id }}'>{{ t.full_name }}</option>{% endfor %}</select>
          <label>Jour</label><select name='day_name' required><option>Lundi</option><option>Mardi</option><option>Mercredi</option><option>Jeudi</option><option>Vendredi</option></select>
          <label>Début</label><input type='time' name='start_time' required>
          <label>Fin</label><input type='time' name='end_time' required>
          <label>Salle</label><input name='room'>
          <button type='submit'>Ajouter</button>
        </form>
      </div>
      {% endif %}
      <div class='card'>
        <h1>Emploi du temps</h1>
        <table>
          <thead><tr><th>Classe</th><th>Jour</th><th>Horaire</th><th>Matière</th><th>Prof</th><th>Salle</th></tr></thead>
          <tbody>
            {% for r in rows %}<tr><td>{{ r.class_name }}</td><td>{{ r.day_name }}</td><td>{{ r.start_time }} - {{ r.end_time }}</td><td>{{ r.subject_name }}</td><td>{{ r.teacher_name }}</td><td>{{ r.room or '-' }}</td></tr>
            {% else %}<tr><td colspan='6'>Aucun cours programmé.</td></tr>{% endfor %}
          </tbody>
        </table>
      </div>
    </div>
    """
    return render_page(content, title="Emploi du temps", user=user, rows=rows, classes=classes, subjects=subjects, teachers=teachers)


# =========================
# Absences
# =========================
@app.route("/absences", methods=["GET", "POST"])
@login_required
def absences_page():
    user = g.user
    students = query_all("SELECT u.id, u.full_name, c.name AS class_name FROM users u LEFT JOIN classes c ON c.id=u.class_id WHERE u.role='eleve' ORDER BY u.full_name")

    if request.method == "POST":
        if user["role"] not in ["prof", "admin"]:
            flash("Accès refusé.")
            return redirect(url_for("absences_page"))

        execute_db(
            "INSERT INTO absences (student_id, teacher_id, absence_date, reason, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (request.form.get("student_id"), user["id"], request.form.get("absence_date"), request.form.get("reason", "").strip(), request.form.get("status"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        flash("Absence enregistrée.")
        return redirect(url_for("absences_page"))

    if user["role"] == "eleve":
        rows = query_all("SELECT a.*, u.full_name AS teacher_name FROM absences a JOIN users u ON u.id=a.teacher_id WHERE student_id=? ORDER BY absence_date DESC", (user["id"],))
    elif user["role"] == "parent":
        children = get_parent_children(user)
        if children:
            child_ids = [child["id"] for child in children]
            placeholders = ",".join(["?"] * len(child_ids))
            rows = query_all(
                f"""
                SELECT a.*, u.full_name AS teacher_name, s.full_name AS student_name, c.name AS class_name
                FROM absences a JOIN users u ON u.id = a.teacher_id JOIN users s ON s.id = a.student_id
                LEFT JOIN classes c ON c.id = s.class_id
                WHERE a.student_id IN ({placeholders}) ORDER BY absence_date DESC
                """,
                tuple(child_ids),
            )
        else:
            rows = []
    else:
        rows = query_all(
            """
            SELECT a.*, s.full_name AS student_name, t.full_name AS teacher_name, c.name AS class_name
            FROM absences a JOIN users s ON s.id = a.student_id JOIN users t ON t.id = a.teacher_id
            LEFT JOIN classes c ON c.id = s.class_id ORDER BY a.absence_date DESC
            """
        )

    content = """
    <div class='grid'>
      {% if user.role in ['prof', 'admin'] %}
      <div class='card'>
        <h2>Ajouter une absence</h2>
        <form method='post'>
          <label>Élève</label><select name='student_id' required>{% for s in students %}<option value='{{ s.id }}'>{{ s.full_name }}{% if s.class_name %} - {{ s.class_name }}{% endif %}</option>{% endfor %}</select>
          <label>Date</label><input type='date' name='absence_date' required>
          <label>Motif</label><textarea name='reason'></textarea>
          <label>Statut</label><select name='status' required><option>Non justifiée</option><option>Justifiée</option></select>
          <button type='submit'>Enregistrer</button>
        </form>
      </div>
      {% endif %}
      <div class='card'>
        <h1>Absences</h1>
        <table>
          <thead><tr>{% if user.role in ['admin','prof','parent'] %}<th>Élève</th><th>Classe</th>{% endif %}<th>Date</th><th>Motif</th><th>Statut</th><th>Déclarée par</th></tr></thead>
          <tbody>
            {% for r in rows %}
              <tr>{% if user.role in ['admin','prof','parent'] %}<td>{{ r.student_name }}</td><td>{{ r.class_name or '-' }}</td>{% endif %}<td>{{ r.absence_date }}</td><td>{{ r.reason or '-' }}</td><td>{{ r.status }}</td><td>{{ r.teacher_name }}</td></tr>
            {% else %}<tr><td colspan='6'>Aucune absence.</td></tr>{% endfor %}
          </tbody>
        </table>
      </div>
    </div>
    """
    return render_page(content, title="Absences", user=user, students=students, rows=rows)


# =========================
# Messagerie
# =========================
@app.route("/messages", methods=["GET", "POST"])
@login_required
def messages_page():
    user = g.user

    if user["role"] == "eleve":
        contacts = query_all("SELECT id, full_name, role FROM users WHERE role IN ('prof', 'admin', 'parent') AND id != ? ORDER BY full_name", (user["id"],))
    elif user["role"] == "parent":
        children = get_parent_children(user)
        child_ids = [child["id"] for child in children]
        if child_ids:
            placeholders = ",".join(["?"] * len(child_ids))
            contacts = query_all(
                f"SELECT id, full_name, role FROM users WHERE (role IN ('prof', 'admin') OR id IN ({placeholders})) AND id != ? ORDER BY full_name",
                tuple(child_ids) + (user["id"],),
            )
        else:
            contacts = query_all("SELECT id, full_name, role FROM users WHERE role IN ('prof', 'admin') ORDER BY full_name")
    else:
        contacts = query_all("SELECT id, full_name, role FROM users WHERE id != ? ORDER BY full_name", (user["id"],))

    if request.method == "POST":
        receiver_id = request.form.get("receiver_id")
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()
        if not receiver_id or not subject or not body:
            flash("Remplis tous les champs du message.")
            return redirect(url_for("messages_page"))

        execute_db(
            "INSERT INTO messages (sender_id, receiver_id, subject, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (user["id"], receiver_id, subject, body, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        flash("Message envoyé.")
        return redirect(url_for("messages_page"))

    inbox = query_all(
        """
        SELECT m.*, s.full_name AS sender_name, r.full_name AS receiver_name
        FROM messages m JOIN users s ON s.id = m.sender_id JOIN users r ON r.id = m.receiver_id
        WHERE m.receiver_id = ? OR m.sender_id = ? ORDER BY m.id DESC
        """,
        (user["id"], user["id"]),
    )

    content = """
    <div class='grid'>
      <div class='card'>
        <h2>Nouveau message</h2>
        <form method='post'>
          <label>Destinataire</label><select name='receiver_id' required>{% for c in contacts %}<option value='{{ c.id }}'>{{ c.full_name }} ({{ c.role }})</option>{% endfor %}</select>
          <label>Sujet</label><input name='subject' required>
          <label>Message</label><textarea name='body' required></textarea>
          <button type='submit'>Envoyer</button>
        </form>
      </div>
      <div class='card'>
        <h1>Messagerie</h1>
        {% for m in inbox %}<div style='border:1px solid #e6edf8; border-radius:16px; padding:14px; margin-bottom:12px;'><strong>{{ m.subject }}</strong><p style='margin:8px 0;'>{{ m.body }}</p><p class='muted small'>De {{ m.sender_name }} à {{ m.receiver_name }} · {{ m.created_at }}</p></div>
        {% else %}<p>Aucun message.</p>{% endfor %}
      </div>
    </div>
    """
    return render_page(content, title="Messagerie", user=user, contacts=contacts, inbox=inbox)


# =========================
# Comptes
# =========================
@app.route("/manage-users", methods=["GET", "POST"])
@login_required
@role_required("admin", "prof")
def manage_users():
    user = g.user
    classes = query_all("SELECT id, name FROM classes ORDER BY name")
    students = query_all("SELECT id, full_name FROM users WHERE role='eleve' ORDER BY full_name")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        full_name = request.form.get("full_name", "").strip()
        role = request.form.get("role", "").strip()
        class_id = request.form.get("class_id") or None
        child_id = request.form.get("child_id") or None
        child_id_2 = request.form.get("child_id_2") or None

        if not username or not password or not full_name or role not in ["admin", "prof", "eleve", "parent"]:
            flash("Champs invalides.")
            return redirect(url_for("manage_users"))
        if user["role"] == "prof" and role == "admin":
            flash("Un professeur ne peut pas créer un compte admin.")
            return redirect(url_for("manage_users"))
        if role == "parent" and not child_id and not child_id_2:
            flash("Un parent doit être lié à au moins un élève.")
            return redirect(url_for("manage_users"))
        if child_id and child_id_2 and child_id == child_id_2:
            flash("Tu ne peux pas choisir deux fois le même enfant.")
            return redirect(url_for("manage_users"))

        if role == "parent":
            class_id = None
        else:
            child_id = None
            child_id_2 = None

        try:
            execute_db(
                "INSERT INTO users (username, password, role, full_name, class_id, child_id, child_id_2) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, generate_password_hash(password), role, full_name, class_id, child_id, child_id_2),
            )
            flash("Utilisateur ajouté.")
        except Exception:
            flash("Nom d'utilisateur déjà utilisé.")
        return redirect(url_for("manage_users"))

    users = query_all(
        """
        SELECT u.id, u.username, u.full_name, u.role, c.name AS class_name,
               child.full_name AS child_name, child2.full_name AS child_name_2
        FROM users u
        LEFT JOIN classes c ON c.id = u.class_id
        LEFT JOIN users child ON child.id = u.child_id
        LEFT JOIN users child2 ON child2.id = u.child_id_2
        ORDER BY u.id DESC
        """
    )

    content = """
    <div class='grid'>
      <div class='card'>
        <h1>Créer un compte</h1>
        <form method='post' autocomplete='off'>
          <label>Nom complet</label><input name='full_name' required>
          <label>Nom d'utilisateur</label><input name='username' required>
          <label>Mot de passe</label><input name='password' required>
          <label>Rôle</label>
          <select name='role' id='manage_role_select' required onchange='toggleManageFields()'>
            <option value='eleve'>Élève</option>
            <option value='prof'>Professeur</option>
            <option value='parent'>Parent</option>
            {% if user.role == 'admin' %}<option value='admin'>Admin</option>{% endif %}
          </select>
          <div id='manage_class_block'>
            <label>Classe</label>
            <select name='class_id'><option value=''>Aucune</option>{% for c in classes %}<option value='{{ c.id }}'>{{ c.name }}</option>{% endfor %}</select>
          </div>
          <div id='manage_child_block' style='display:none;'>
            <label>Enfant lié 1</label>
            <select name='child_id'><option value=''>Aucun</option>{% for s in students %}<option value='{{ s.id }}'>{{ s.full_name }}</option>{% endfor %}</select>
            <label>Enfant lié 2</label>
            <select name='child_id_2'><option value=''>Aucun</option>{% for s in students %}<option value='{{ s.id }}'>{{ s.full_name }}</option>{% endfor %}</select>
          </div>
          <button type='submit'>Créer</button>
        </form>
      </div>
      <div class='card'>
        <h2>Liste des utilisateurs</h2>
        <table>
          <thead><tr><th>ID</th><th>Nom</th><th>Utilisateur</th><th>Rôle</th><th>Classe</th><th>Enfant lié 1</th><th>Enfant lié 2</th></tr></thead>
          <tbody>
            {% for u in users %}<tr><td>{{ u.id }}</td><td>{{ u.full_name }}</td><td>{{ u.username }}</td><td>{{ u.role }}</td><td>{{ u.class_name or '-' }}</td><td>{{ u.child_name or '-' }}</td><td>{{ u.child_name_2 or '-' }}</td></tr>{% endfor %}
          </tbody>
        </table>
      </div>
    </div>
    <script>
      function toggleManageFields() {
        const role = document.getElementById('manage_role_select').value;
        document.getElementById('manage_class_block').style.display = role === 'parent' ? 'none' : 'block';
        document.getElementById('manage_child_block').style.display = role === 'parent' ? 'block' : 'none';
      }
      toggleManageFields();
    </script>
    """
    return render_page(content, title="Comptes", users=users, user=user, classes=classes, students=students)


# =========================
# École
# =========================
@app.route("/manage-school", methods=["GET", "POST"])
@login_required
@role_required("admin")
def manage_school():
    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type in ["class", "subject"]:
            name = request.form.get("name", "").strip()
            if not name:
                flash("Nom invalide.")
                return redirect(url_for("manage_school"))
            try:
                execute_db("INSERT INTO classes (name) VALUES (?)" if form_type == "class" else "INSERT INTO subjects (name) VALUES (?)", (name,))
                flash("Classe ajoutée." if form_type == "class" else "Matière ajoutée.")
            except Exception:
                flash("Ce nom existe déjà.")
            return redirect(url_for("manage_school"))

        if form_type == "delete_class":
            class_id = request.form.get("class_id")
            linked_users = query_one("SELECT COUNT(*) AS total FROM users WHERE class_id = ?", (class_id,))["total"]
            linked_homework = query_one("SELECT COUNT(*) AS total FROM homework WHERE class_id = ?", (class_id,))["total"]
            linked_schedule = query_one("SELECT COUNT(*) AS total FROM schedules WHERE class_id = ?", (class_id,))["total"]
            if linked_users or linked_homework or linked_schedule:
                flash("Impossible de supprimer cette classe : elle est encore utilisée.")
                return redirect(url_for("manage_school"))
            execute_db("DELETE FROM classes WHERE id = ?", (class_id,))
            flash("Classe supprimée.")
            return redirect(url_for("manage_school"))

        if form_type == "delete_subject":
            subject_id = request.form.get("subject_id")
            linked_grades = query_one("SELECT COUNT(*) AS total FROM grades WHERE subject_id = ?", (subject_id,))["total"]
            linked_homework = query_one("SELECT COUNT(*) AS total FROM homework WHERE subject_id = ?", (subject_id,))["total"]
            linked_schedule = query_one("SELECT COUNT(*) AS total FROM schedules WHERE subject_id = ?", (subject_id,))["total"]
            if linked_grades or linked_homework or linked_schedule:
                flash("Impossible de supprimer cette matière : elle est encore utilisée.")
                return redirect(url_for("manage_school"))
            execute_db("DELETE FROM subjects WHERE id = ?", (subject_id,))
            flash("Matière supprimée.")
            return redirect(url_for("manage_school"))

    classes = query_all("SELECT * FROM classes ORDER BY name")
    subjects = query_all("SELECT * FROM subjects ORDER BY name")
    content = """
    <div class='hero'><h1>Gestion de l'école</h1><p>Ajoute ou supprime des classes et des matières depuis cette page.</p></div>
    <div class='grid'>
      <div class='card'>
        <h2>Ajouter une classe</h2>
        <form method='post'><input type='hidden' name='form_type' value='class'><label>Nom de la classe</label><input name='name' placeholder='6A' required><button type='submit'>Ajouter la classe</button></form>
      </div>
      <div class='card'>
        <h2>Ajouter une matière</h2>
        <form method='post'><input type='hidden' name='form_type' value='subject'><label>Nom de la matière</label><input name='name' placeholder='Physique' required><button type='submit'>Ajouter la matière</button></form>
      </div>
    </div>
    <div class='grid' style='margin-top:18px;'>
      <div class='card'>
        <h2>Classes</h2>
        {% for c in classes %}<div style='display:flex; justify-content:space-between; align-items:center; gap:12px; padding:12px 0; border-bottom:1px solid #eef3fb;'><div><strong>{{ c.name }}</strong></div><form method='post' style='margin:0;'><input type='hidden' name='form_type' value='delete_class'><input type='hidden' name='class_id' value='{{ c.id }}'><button type='submit' class='danger'>Supprimer</button></form></div>{% else %}<p class='muted'>Aucune classe.</p>{% endfor %}
      </div>
      <div class='card'>
        <h2>Matières</h2>
        {% for s in subjects %}<div style='display:flex; justify-content:space-between; align-items:center; gap:12px; padding:12px 0; border-bottom:1px solid #eef3fb;'><div><strong>{{ s.name }}</strong></div><form method='post' style='margin:0;'><input type='hidden' name='form_type' value='delete_subject'><input type='hidden' name='subject_id' value='{{ s.id }}'><button type='submit' class='danger'>Supprimer</button></form></div>{% else %}<p class='muted'>Aucune matière.</p>{% endfor %}
      </div>
    </div>
    """
    return render_page(content, title="École", classes=classes, subjects=subjects)


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
