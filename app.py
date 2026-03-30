import os
import sqlite3
from datetime import datetime
from functools import wraps

import cloudinary
import cloudinary.uploader
import cloudinary.api

from flask import (
    Flask,
    flash,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
    g,
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
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = __import__("datetime").timedelta(days=90)
app.config["TEMPLATES_AUTO_RELOAD"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False  # Mettre True si HTTPS uniquement

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL and psycopg2)
DB_NAME = "renote_v1.db"
ADMIN_DEFAULT_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Azsqerfd2012")
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "EcoleR2026")

# =========================
# Cloudinary configuration
# =========================
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True,
)

ALLOWED_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "gif", "webp",
    "doc", "docx", "txt", "zip", "rar", "ppt", "pptx",
    "xls", "xlsx"
}
PROFILE_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


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


def table_exists(table_name):
    if USE_POSTGRES:
        row = query_one(
            """
            SELECT 1 AS ok
            FROM information_schema.tables
            WHERE table_name = ?
            LIMIT 1
            """,
            (table_name,),
        )
        return bool(row)
    row = query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    )
    return bool(row)


# =========================
# Cloudinary helpers
# =========================
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_profile_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in PROFILE_IMAGE_EXTENSIONS


def is_image_file(filename: str) -> bool:
    if not filename:
        return False
    return "." in filename and filename.rsplit(".", 1)[1].lower() in IMAGE_EXTENSIONS


def upload_to_cloudinary(file_storage, folder="renote_uploads", resource_type="auto"):
    try:
        result = cloudinary.uploader.upload(
            file_storage,
            folder=folder,
            resource_type=resource_type,
        )
        return result.get("public_id"), result.get("secure_url")
    except Exception as e:
        print(f"Cloudinary upload error: {e}")
        return None, None


def delete_from_cloudinary(public_id, resource_type="auto"):
    if not public_id:
        return
    try:
        cloudinary.uploader.destroy(public_id, resource_type=resource_type)
    except Exception as e:
        print(f"Cloudinary delete error: {e}")


def get_cloudinary_url(public_id, resource_type="auto"):
    if not public_id:
        return None
    try:
        if resource_type == "image":
            return cloudinary.CloudinaryImage(public_id).build_url(secure=True)
        else:
            return cloudinary.CloudinaryImage(public_id).build_url(secure=True, resource_type=resource_type)
    except Exception:
        return None


def cloudinary_file_exists(public_id):
    if not public_id:
        return False
    try:
        cloudinary.api.resource(public_id)
        return True
    except Exception:
        return False


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
                    child_id_2 INTEGER REFERENCES users(id),
                    profile_picture TEXT,
                    profile_picture_url TEXT,
                    created_at TEXT,
                    last_login_at TEXT,
                    login_count INTEGER NOT NULL DEFAULT 0
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
                    attachment_url TEXT,
                    attachment_name TEXT,
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
                    end_date TEXT,
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS general_info (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    author_id INTEGER NOT NULL REFERENCES users(id),
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
                    profile_picture TEXT,
                    profile_picture_url TEXT,
                    created_at TEXT,
                    last_login_at TEXT,
                    login_count INTEGER NOT NULL DEFAULT 0,
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
                    attachment_url TEXT,
                    attachment_name TEXT,
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
                    end_date TEXT,
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS general_info (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    author_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(author_id) REFERENCES users(id)
                )
            """)

        if USE_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    username TEXT,
                    role TEXT,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Nouveau',
                    admin_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    resolved_at TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id SERIAL PRIMARY KEY,
                    action TEXT NOT NULL,
                    details TEXT,
                    entity_type TEXT,
                    entity_id INTEGER,
                    user_id INTEGER,
                    username TEXT,
                    role TEXT,
                    created_at TEXT NOT NULL
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    role TEXT,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Nouveau',
                    admin_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    resolved_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    details TEXT,
                    entity_type TEXT,
                    entity_id INTEGER,
                    user_id INTEGER,
                    username TEXT,
                    role TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)

        conn.commit()
    finally:
        conn.close()

    # Migrations
    for col in ["child_id", "child_id_2", "profile_picture", "profile_picture_url"]:
        if not table_has_column("users", col):
            execute_db(f"ALTER TABLE users ADD COLUMN {col} {'INTEGER' if 'id' in col else 'TEXT'}")

    if not table_has_column("users", "created_at"):
        execute_db("ALTER TABLE users ADD COLUMN created_at TEXT")
    if not table_has_column("users", "last_login_at"):
        execute_db("ALTER TABLE users ADD COLUMN last_login_at TEXT")
    if not table_has_column("users", "login_count"):
        execute_db("ALTER TABLE users ADD COLUMN login_count INTEGER NOT NULL DEFAULT 0")

    for col in ["attachment", "attachment_url", "attachment_name"]:
        if not table_has_column("homework", col):
            execute_db(f"ALTER TABLE homework ADD COLUMN {col} TEXT")

    # Migration: ajouter end_date aux absences si la colonne n'existe pas
    if not table_has_column("absences", "end_date"):
        execute_db("ALTER TABLE absences ADD COLUMN end_date TEXT")

    if not table_exists("general_info"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS general_info (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    author_id INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS general_info (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    author_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(author_id) REFERENCES users(id)
                )
            """)

    if not table_exists("reports"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS reports (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    username TEXT,
                    role TEXT,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Nouveau',
                    admin_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    resolved_at TEXT
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    role TEXT,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Nouveau',
                    admin_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    resolved_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)

    for col in ["status", "admin_note", "updated_at", "resolved_at"]:
        if not table_has_column("reports", col):
            execute_db(f"ALTER TABLE reports ADD COLUMN {col} TEXT")

    if not table_exists("activity_logs"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id SERIAL PRIMARY KEY,
                    action TEXT NOT NULL,
                    details TEXT,
                    entity_type TEXT,
                    entity_id INTEGER,
                    user_id INTEGER,
                    username TEXT,
                    role TEXT,
                    created_at TEXT NOT NULL
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    details TEXT,
                    entity_type TEXT,
                    entity_id INTEGER,
                    user_id INTEGER,
                    username TEXT,
                    role TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)

    # Migration: chat tables
    if not table_exists("chat_groups"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS chat_groups (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_by INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS chat_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(created_by) REFERENCES users(id)
                )
            """)

    if not table_exists("chat_group_members"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS chat_group_members (
                    id SERIAL PRIMARY KEY,
                    group_id INTEGER NOT NULL REFERENCES chat_groups(id),
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    UNIQUE(group_id, user_id)
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS chat_group_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    UNIQUE(group_id, user_id),
                    FOREIGN KEY(group_id) REFERENCES chat_groups(id),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)

    if not table_exists("chat_group_messages"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS chat_group_messages (
                    id SERIAL PRIMARY KEY,
                    group_id INTEGER NOT NULL REFERENCES chat_groups(id),
                    sender_id INTEGER NOT NULL REFERENCES users(id),
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS chat_group_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    sender_id INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(group_id) REFERENCES chat_groups(id),
                    FOREIGN KEY(sender_id) REFERENCES users(id)
                )
            """)

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

    # Colonnes question secrète
    if not table_has_column("users", "secret_question"):
        execute_db("ALTER TABLE users ADD COLUMN secret_question TEXT")
    if not table_has_column("users", "secret_answer"):
        execute_db("ALTER TABLE users ADD COLUMN secret_answer TEXT")

    # Table pièces jointes multiples devoirs
    if not table_exists("homework_attachments"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS homework_attachments (
                    id SERIAL PRIMARY KEY,
                    homework_id INTEGER NOT NULL REFERENCES homework(id) ON DELETE CASCADE,
                    public_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS homework_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    homework_id INTEGER NOT NULL,
                    public_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(homework_id) REFERENCES homework(id)
                )
            """)

    # Table reset mot de passe
    if not table_exists("password_resets"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    username TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'En attente',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'En attente',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)

    # Tables vie de classe
    if not table_exists("vie_posts"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS vie_posts (
                    id SERIAL PRIMARY KEY,
                    author_id INTEGER NOT NULL REFERENCES users(id),
                    body TEXT,
                    image_url TEXT,
                    image_public_id TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            execute_db("""
                CREATE TABLE IF NOT EXISTS vie_reactions (
                    id SERIAL PRIMARY KEY,
                    post_id INTEGER NOT NULL REFERENCES vie_posts(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    emoji TEXT NOT NULL,
                    UNIQUE(post_id, user_id)
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS vie_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    author_id INTEGER NOT NULL,
                    body TEXT,
                    image_url TEXT,
                    image_public_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(author_id) REFERENCES users(id)
                )
            """)
            execute_db("""
                CREATE TABLE IF NOT EXISTS vie_reactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    emoji TEXT NOT NULL,
                    UNIQUE(post_id, user_id),
                    FOREIGN KEY(post_id) REFERENCES vie_posts(id),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)

    # Table notifications vues
    if not table_exists("notif_seen"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS notif_seen (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    last_seen_message_id INTEGER NOT NULL DEFAULT 0,
                    last_seen_grade_id INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(user_id)
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS notif_seen (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    last_seen_message_id INTEGER NOT NULL DEFAULT 0,
                    last_seen_grade_id INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(user_id),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)

    # Table suivi devoirs
    if not table_exists("homework_done"):
        if USE_POSTGRES:
            execute_db("""
                CREATE TABLE IF NOT EXISTS homework_done (
                    id SERIAL PRIMARY KEY,
                    homework_id INTEGER NOT NULL REFERENCES homework(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    done_at TEXT NOT NULL,
                    UNIQUE(homework_id, user_id)
                )
            """)
        else:
            execute_db("""
                CREATE TABLE IF NOT EXISTS homework_done (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    homework_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    done_at TEXT NOT NULL,
                    UNIQUE(homework_id, user_id),
                    FOREIGN KEY(homework_id) REFERENCES homework(id),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)

    admin_hash = generate_password_hash(ADMIN_DEFAULT_PASSWORD)
    admin_user = query_one("SELECT id FROM users WHERE username = ?", ("admin",))
    if not admin_user:
        execute_db(
            "INSERT INTO users (username, password, role, full_name, class_id, child_id, child_id_2, profile_picture, profile_picture_url, created_at, last_login_at, login_count) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL, 0)",
            ("admin", admin_hash, "admin", "Administrateur", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
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
    g.notif_count = get_notifications(user)


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


def current_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_event(action, user=None, details=None, entity_type=None, entity_id=None):
    username = None
    role = None
    user_id = None

    if user:
        user_id = user.get("id")
        username = user.get("username")
        role = user.get("role")

    try:
        execute_db(
            "INSERT INTO activity_logs (action, details, entity_type, entity_id, user_id, username, role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (action, details, entity_type, entity_id, user_id, username, role, current_timestamp()),
        )
    except Exception as e:
        print(f"Activity log error: {e}")


def scalar(sql, params=(), default=0):
    row = query_one(sql, params)
    if not row:
        return default
    return next(iter(row.values()))


def get_notifications(user):
    """Retourne le nombre de notifications non lues pour l'utilisateur."""
    if not user:
        return 0
    try:
        # Messages non lus = messages reçus depuis la dernière visite
        # On utilise la table notif_seen pour tracker ce qui a été vu
        total = 0
        # Nouveaux messages privés non lus
        seen = query_one("SELECT last_seen_message_id FROM notif_seen WHERE user_id = ?", (user["id"],))
        last_seen_id = seen["last_seen_message_id"] if seen else 0
        new_msgs = scalar(
            "SELECT COUNT(*) AS total FROM messages WHERE receiver_id = ? AND id > ?",
            (user["id"], last_seen_id), 0
        )
        total += new_msgs
        # Nouvelles notes pour élèves/parents
        if user["role"] == "eleve":
            seen_grade = query_one("SELECT last_seen_grade_id FROM notif_seen WHERE user_id = ?", (user["id"],))
            last_seen_grade = seen_grade["last_seen_grade_id"] if seen_grade else 0
            new_grades = scalar(
                "SELECT COUNT(*) AS total FROM grades WHERE student_id = ? AND id > ?",
                (user["id"], last_seen_grade), 0
            )
            total += new_grades
        elif user["role"] == "parent":
            children = get_parent_children(user)
            if children:
                seen_grade = query_one("SELECT last_seen_grade_id FROM notif_seen WHERE user_id = ?", (user["id"],))
                last_seen_grade = seen_grade["last_seen_grade_id"] if seen_grade else 0
                child_ids = [c["id"] for c in children]
                placeholders = ",".join(["?"] * len(child_ids))
                new_grades = scalar(
                    f"SELECT COUNT(*) AS total FROM grades WHERE student_id IN ({placeholders}) AND id > ?",
                    tuple(child_ids) + (last_seen_grade,), 0
                )
                total += new_grades
        return total
    except Exception:
        return 0


# =========================
# UI
# =========================
BASE_TOP = """
<!doctype html>
<html lang='fr' data-theme='light'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>{{ title }} — Renote</title>
  <link rel='icon' type='image/png' href='data:image/png;base64,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'>
  <style>
    :root {
      --bg: linear-gradient(135deg,#eff6ff,#f8fbff 55%,#eef4ff);
      --card: rgba(255,255,255,0.93);
      --card-border: rgba(255,255,255,0.85);
      --text: #18212f;
      --text-muted: #5f6b7a;
      --input-bg: #fff;
      --input-border: #d5e0f3;
      --table-bg: white;
      --table-th: #edf4ff;
      --table-border: #ebf0f8;
      --admin-box: #f8fbff;
      --admin-box-border: #dbeafe;
      --info-box-bg: #fff;
      --info-box-border: #e5ebf5;
      --flash-bg: #fff9db;
      --flash-border: #f2dd7d;
      --badge-bg: #e7efff;
      --badge-color: #1d4ed8;
    }
    [data-theme='dark'] {
      --bg: linear-gradient(135deg,#0f172a,#1a1f3a 55%,#0f172a);
      --card: rgba(30,41,59,0.97);
      --card-border: rgba(99,102,241,0.18);
      --text: #e2e8f0;
      --text-muted: #94a3b8;
      --input-bg: #1e293b;
      --input-border: #334155;
      --table-bg: #1e293b;
      --table-th: #1a2540;
      --table-border: #334155;
      --admin-box: #1a2540;
      --admin-box-border: #334155;
      --info-box-bg: #1e293b;
      --info-box-border: #334155;
      --flash-bg: #3b3010;
      --flash-border: #a37c00;
      --badge-bg: #1e3a8a;
      --badge-color: #93c5fd;
    }
    * { box-sizing: border-box; }
    body { margin:0; font-family:Inter,Arial,Helvetica,sans-serif; background:var(--bg); color:var(--text); transition:background 0.3s,color 0.3s; }
    /* Dark mode overrides for elements that don't use variables */
    [data-theme='dark'] .hero { background:linear-gradient(135deg,#1e3a8a,#1d4ed8); }
    [data-theme='dark'] .nav { background:linear-gradient(90deg,#020617,#0f172a); }
    [data-theme='dark'] select option { background:#1e293b; color:#e2e8f0; }
    [data-theme='dark'] .edt-wrap { background:transparent; }
    [data-theme='dark'] .day-card { background:#1e293b; border-color:#334155; }
    [data-theme='dark'] .course { border-bottom-color:#334155; }
    [data-theme='dark'] .subject { color:#e2e8f0; }
    [data-theme='dark'] .hour { background:#1e3a8a; color:#93c5fd; }
    [data-theme='dark'] .week-btn { background:linear-gradient(135deg,#1e293b,#334155); color:#93c5fd; }
    [data-theme='dark'] .week-btn.active { background:linear-gradient(135deg,#1d4ed8,#2563eb); color:white; }
    [data-theme='dark'] .wa-wrap { background:#0f172a; }
    [data-theme='dark'] .wa-sidebar { background:#1e293b; border-right-color:#334155; }
    [data-theme='dark'] .wa-sidebar-header { background:#1a2540; }
    [data-theme='dark'] .wa-sidebar-header h2 { color:#e2e8f0; }
    [data-theme='dark'] .wa-contact-item { border-bottom-color:#334155; }
    [data-theme='dark'] .wa-contact-item:hover,.wa-contact-item.active { background:#1a2540; }
    [data-theme='dark'] .wa-contact-name { color:#e2e8f0; }
    [data-theme='dark'] .wa-messages-area { background:#0f172a; background-image:none; }
    [data-theme='dark'] .wa-msg.theirs .wa-bubble { background:#1e293b; color:#e2e8f0; }
    [data-theme='dark'] .wa-msg.mine .wa-bubble { background:#1e3a8a; color:#e2e8f0; }
    [data-theme='dark'] .wa-input-bar { background:#1a2540; border-top-color:#334155; }
    [data-theme='dark'] .wa-input-bar input { background:#1e293b; color:#e2e8f0; }
    [data-theme='dark'] .wa-chat-header { background:#1a2540; border-bottom-color:#334155; }
    [data-theme='dark'] .wa-chat-header-name { color:#e2e8f0; }
    [data-theme='dark'] .wa-empty { background:#0f172a; }
    [data-theme='dark'] .wa-sidebar-tabs { background:#1e293b; border-bottom-color:#334155; }
    [data-theme='dark'] .wa-modal { background:#1e293b; color:#e2e8f0; }
    [data-theme='dark'] .member-check-list { border-color:#334155; }
    [data-theme='dark'] .member-check-item:hover { background:#1a2540; }
    [data-theme='dark'] .profile-tab { background:#1e293b; border-color:#334155; color:#93c5fd; }
    [data-theme='dark'] .profile-tab.active { background:linear-gradient(90deg,#1d4ed8,#2563eb); color:white; border-color:transparent; }
    .nav { background:linear-gradient(90deg,#0f172a,#1d4ed8); color:white; padding:0 18px; height:62px; display:flex; align-items:center; justify-content:space-between; gap:12px; box-shadow:0 4px 20px rgba(15,23,42,0.28); position:sticky; top:0; z-index:100; }
    .brand-wrap { display:flex; align-items:center; gap:10px; min-width:0; text-decoration:none; }
    .brand-wrap strong { font-size:18px; color:white; white-space:nowrap; }
    .nav-center { display:flex; align-items:center; gap:3px; flex-wrap:nowrap; overflow-x:auto; scrollbar-width:none; }
    .nav-center::-webkit-scrollbar { display:none; }
    .nav-link { color:white; text-decoration:none; font-size:13px; font-weight:600; padding:7px 10px; border-radius:10px; white-space:nowrap; opacity:0.88; transition:background 0.15s,opacity 0.15s; }
    .nav-link:hover { background:rgba(255,255,255,0.14); opacity:1; }
    .nav-dropdown { position:relative; }
    .nav-dropdown-btn { color:white; background:rgba(255,255,255,0.1); border:none; font-size:13px; font-weight:600; padding:7px 11px; border-radius:10px; cursor:pointer; display:flex; align-items:center; gap:5px; white-space:nowrap; box-shadow:none; }
    .nav-dropdown-btn:hover { background:rgba(255,255,255,0.18); transform:none; }
    .nav-dropdown-menu { display:none; position:absolute; top:calc(100% + 8px); right:0; background:#1e293b; border:1px solid rgba(255,255,255,0.1); border-radius:14px; min-width:190px; padding:6px; box-shadow:0 12px 36px rgba(0,0,0,0.4); z-index:200; }
    .nav-dropdown:hover .nav-dropdown-menu { display:block; }
    .nav-dropdown-menu a { display:block; color:white; text-decoration:none; padding:9px 12px; border-radius:9px; font-size:13px; font-weight:600; }
    .nav-dropdown-menu a:hover { background:rgba(255,255,255,0.1); }
    .nav-right { display:flex; align-items:center; gap:8px; flex-shrink:0; }
    .notif-wrap { position:relative; display:inline-flex; }
    .notif-badge { position:absolute; top:-5px; right:-5px; background:#ef4444; color:white; border-radius:999px; font-size:10px; font-weight:800; min-width:18px; height:18px; display:flex; align-items:center; justify-content:center; padding:0 4px; border:2px solid #1d4ed8; pointer-events:none; }
    .dark-toggle { width:36px; height:36px; border-radius:50%; background:rgba(255,255,255,0.12); border:none; cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:17px; transition:background 0.2s; box-shadow:none; padding:0; }
    .dark-toggle:hover { background:rgba(255,255,255,0.22); transform:none; }
    .user-pill { display:flex; align-items:center; gap:8px; padding:5px 10px 5px 5px; border-radius:999px; background:rgba(255,255,255,0.1); border:none; cursor:pointer; color:white; font-size:13px; font-weight:600; position:relative; box-shadow:none; }
    .user-pill:hover { background:rgba(255,255,255,0.18); transform:none; }
    .user-pill-avatar { width:30px; height:30px; border-radius:50%; object-fit:cover; border:2px solid rgba(255,255,255,0.4); flex-shrink:0; background:#1e40af; display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:800; }
    .user-pill-name { max-width:90px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .user-pill-dropdown { display:none; position:absolute; top:100%; right:0; background:#1e293b; border:1px solid rgba(255,255,255,0.12); border-radius:14px; min-width:180px; padding:14px 6px 6px; box-shadow:0 16px 40px rgba(0,0,0,0.45); z-index:400; }
    .user-pill-wrap { position:relative; }
    .user-pill-wrap:hover .user-pill-dropdown { display:block; }
    .user-pill-dropdown a { display:flex; align-items:center; gap:9px; color:white; text-decoration:none; padding:10px 12px; border-radius:9px; font-size:13px; font-weight:600; }
    .user-pill-dropdown a:hover { background:rgba(255,255,255,0.1); }
    .user-pill-dropdown .sep { height:1px; background:rgba(255,255,255,0.1); margin:4px 0; }
    .burger-btn { width:42px; height:42px; border-radius:11px; border:1px solid rgba(255,255,255,0.25); background:rgba(255,255,255,0.1); display:flex; flex-direction:column; align-items:center; justify-content:center; gap:5px; cursor:pointer; padding:0; transition:background 0.2s; box-shadow:none; }
    .burger-btn:hover { background:rgba(255,255,255,0.2); transform:none; }
    .burger-btn span { display:block; width:20px; height:2px; background:white; border-radius:10px; }
    .mobile-drawer { display:flex; flex-direction:column; position:fixed; top:0; right:-310px; width:270px; max-width:88vw; height:100vh; background:linear-gradient(180deg,#0f172a,#1e3a8a); padding:0; box-shadow:-8px 0 28px rgba(0,0,0,0.35); z-index:300; transition:right 0.28s cubic-bezier(.4,0,.2,1); overflow-y:auto; }
    .mobile-drawer.open { right:0 !important; }
    .mobile-drawer-head { display:flex; justify-content:space-between; align-items:center; padding:18px 16px 12px; border-bottom:1px solid rgba(255,255,255,0.1); }
    .mobile-drawer-head strong { color:white; font-size:17px; }
    .close-drawer { background:rgba(255,255,255,0.1); border:none; color:white; width:34px; height:34px; border-radius:8px; cursor:pointer; font-size:16px; box-shadow:none; padding:0; }
    .mobile-drawer-section { padding:10px 10px 4px; }
    .mobile-drawer-section-label { font-size:10px; font-weight:800; letter-spacing:1.2px; color:rgba(255,255,255,0.4); text-transform:uppercase; padding:0 6px 6px; display:block; }
    .mobile-drawer a { display:flex; align-items:center; gap:10px; color:white; text-decoration:none; padding:11px 12px; border-radius:11px; font-weight:600; font-size:14px; margin-bottom:3px; background:rgba(255,255,255,0.05); transition:background 0.15s; }
    .mobile-drawer a:hover { background:rgba(255,255,255,0.12); }
    .mobile-drawer-bottom { padding:14px 10px; border-top:1px solid rgba(255,255,255,0.1); }
    .mobile-overlay { display:none; position:fixed; inset:0; background:rgba(15,23,42,0.5); z-index:290; }
    .mobile-overlay.show { display:block; }
    .container { max-width:1260px; margin:28px auto; padding:0 18px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:18px; }
    .card { background:var(--card); backdrop-filter:blur(10px); border-radius:24px; padding:24px; box-shadow:0 18px 36px rgba(37,99,235,0.08); border:1px solid var(--card-border); overflow-x:auto; }
    .hero { background:linear-gradient(135deg,#1d4ed8,#60a5fa); color:white; border-radius:28px; padding:30px; box-shadow:0 20px 36px rgba(37,99,235,0.24); margin-bottom:20px; }
    .hero p { opacity:0.96; }
    h1,h2,h3 { margin-top:0; color:var(--text); }
    input,select,textarea { width:100%; padding:12px 13px; border:1px solid var(--input-border); border-radius:13px; margin-top:6px; margin-bottom:14px; font-size:15px; background:var(--input-bg); color:var(--text); outline:none; }
    input:focus,select:focus,textarea:focus { border-color:#60a5fa; box-shadow:0 0 0 4px rgba(96,165,250,0.16); }
    textarea { min-height:110px; resize:vertical; }
    button { background:linear-gradient(90deg,#1d4ed8,#2563eb); color:white; border:none; padding:11px 16px; border-radius:12px; font-weight:700; cursor:pointer; box-shadow:0 10px 20px rgba(37,99,235,0.18); }
    button:hover { transform:translateY(-1px); }
    .danger { background:linear-gradient(90deg,#c0392b,#e74c3c); }
    .secondary { background:linear-gradient(90deg,#475569,#64748b); }
    .muted { color:var(--text-muted); }
    .flash { background:var(--flash-bg); border:1px solid var(--flash-border); padding:11px 13px; border-radius:12px; margin-bottom:16px; }
    table { width:100%; border-collapse:collapse; overflow:hidden; border-radius:16px; background:var(--table-bg); min-width:640px; }
    th,td { padding:12px 10px; border-bottom:1px solid var(--table-border); text-align:left; vertical-align:top; color:var(--text); }
    th { background:var(--table-th); }
    .badge { display:inline-block; padding:6px 10px; border-radius:999px; background:var(--badge-bg); color:var(--badge-color); font-weight:700; font-size:13px; }
    .small { font-size:13px; }
    .metric { font-size:34px; font-weight:800; margin:0; color:var(--text); }
    .two-cols { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
    .login-wrap { max-width:980px; margin:40px auto; }
    .avatar { width:68px; height:68px; border-radius:50%; object-fit:cover; border:3px solid rgba(255,255,255,0.7); background:#dbeafe; flex-shrink:0; }
    .avatar-large { width:110px; height:110px; border-radius:50%; object-fit:cover; border:4px solid rgba(255,255,255,0.8); background:#dbeafe; }
    .info-box { border:1px solid var(--info-box-border); border-radius:16px; padding:16px; margin-bottom:14px; background:var(--info-box-bg); }
    .actions-inline { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
    .admin-box { margin-top:14px; padding:14px; border-radius:14px; background:var(--admin-box); border:1px solid var(--admin-box-border); }
    @media (max-width:900px) {
      .two-cols { grid-template-columns:1fr; }
      .container { padding:0 12px; margin:18px auto; }
      .card { padding:18px; border-radius:18px; }
      .hero { padding:20px; border-radius:22px; }
      .metric { font-size:28px; }
      table { min-width:560px; }
    }
    /* Instant navigation */
    a { transition: none !important; }
    .nav-link, .mobile-drawer a { transition: background 0.1s !important; }
    /* Page transition overlay */
    #page-loader { display:none; position:fixed; inset:0; background:var(--bg); z-index:9999; align-items:center; justify-content:center; }
    #page-loader.show { display:flex; }
  </style>
</head>
<body>
<div id='page-loader'><div style='width:36px;height:36px;border:3px solid #1d4ed8;border-top-color:transparent;border-radius:50%;animation:spin 0.5s linear infinite;'></div></div>
<style>@keyframes spin{to{transform:rotate(360deg);}}</style>
<script>
(function(){var t=localStorage.getItem('theme')||'light';document.documentElement.setAttribute('data-theme',t);})();
</script>
"""

NAV = """
<div class='nav'>
  <a href='{{ url_for("dashboard") if session.get("user_id") else url_for("login") }}' class='brand-wrap'>
    <img src='data:image/png;base64,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' alt='Logo' style='height:42px;width:42px;object-fit:contain;border-radius:8px;flex-shrink:0;'>
    <strong>Renote</strong>
  </a>
  <div class='nav-right'>
    <button class='dark-toggle' onclick='toggleDark()'>🌙</button>
    {% if session.get('user_id') %}
    <!-- User pill -->
    <div class='user-pill-wrap'>
      <button class='user-pill'>
        {% if g.user and g.user.profile_picture_url %}
          <img src='{{ g.user.profile_picture_url }}' class='user-pill-avatar'>
        {% else %}
          <div class='user-pill-avatar'>{{ session.get('full_name','?')[:1] }}</div>
        {% endif %}
        <span class='user-pill-name'>{{ session.get('full_name','').split()[0] }}</span>
        <span style='opacity:0.6;font-size:11px;'>▾</span>
      </button>
      <div class='user-pill-dropdown'>
        <a href='{{ url_for("settings_page") }}'>⚙️ Paramètres</a>
        <div class='sep'></div>
        <a href='{{ url_for("logout") }}' style='color:#f87171;'>🚪 Déconnexion</a>
      </div>
    </div>
    <!-- Burger with notif badge -->
    <div class='notif-wrap'>
      <button class='burger-btn' onclick='openDrawer()' title='Menu'>
        <span></span><span></span><span></span>
      </button>
      {% if g.notif_count and g.notif_count > 0 %}
        <span class='notif-badge'>{{ g.notif_count if g.notif_count < 100 else "99+" }}</span>
      {% endif %}
    </div>
    {% else %}
    <a href='{{ url_for("login") }}' class='nav-link'>Connexion</a>
    {% endif %}
  </div>
</div>

{% if session.get('user_id') %}
<div id='mobileOverlay' class='mobile-overlay' onclick='closeDrawer()'></div>
<div id='mobileDrawer' class='mobile-drawer'>
  <div class='mobile-drawer-head'>
    <strong style='color:white;font-size:17px;'>Menu</strong>
    <button class='close-drawer' onclick='closeDrawer()'>✕</button>
  </div>
  <div style='padding:14px 16px 12px;display:flex;align-items:center;gap:12px;border-bottom:1px solid rgba(255,255,255,0.1);'>
    {% if g.user and g.user.profile_picture_url %}
      <img src='{{ g.user.profile_picture_url }}' style='width:44px;height:44px;border-radius:50%;object-fit:cover;border:2px solid rgba(255,255,255,0.4);flex-shrink:0;'>
    {% else %}
      <div style='width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,0.15);display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:800;color:white;flex-shrink:0;'>{{ session.get('full_name','?')[:1] }}</div>
    {% endif %}
    <div style='flex:1;min-width:0;'>
      <div style='color:white;font-weight:700;font-size:15px;'>{{ session.get('full_name','') }}</div>
      <div style='color:rgba(255,255,255,0.5);font-size:12px;'>{{ session.get('role','') }}</div>
    </div>
    {% if g.notif_count and g.notif_count > 0 %}
      <span style='background:#ef4444;color:white;border-radius:999px;font-size:11px;font-weight:800;padding:3px 8px;flex-shrink:0;'>{{ g.notif_count }}</span>
    {% endif %}
  </div>
  <div class='mobile-drawer-section'>
    <span class='mobile-drawer-section-label'>Navigation</span>
    <a href='{{ url_for("dashboard") }}' onclick='closeDrawer()'>🏠 Accueil</a>
    <a href='{{ url_for("general_info_page") }}' onclick='closeDrawer()'>📢 Infos générales</a>
    <a href='{{ url_for("grades") }}' onclick='closeDrawer()'>📊 Notes</a>
    <a href='{{ url_for("homework_page") }}' onclick='closeDrawer()'>📚 Devoirs</a>
    <a href='{{ url_for("schedule_page") }}' onclick='closeDrawer()'>🗓️ Emploi du temps</a>
    <a href='{{ url_for("absences_page") }}' onclick='closeDrawer()'>📋 Absences</a>
    <a href='{{ url_for("vie_de_classe") }}' onclick='closeDrawer()'>🌸 Vie de classe</a>
    <a href='{{ url_for("messages_page") }}' onclick='closeDrawer()'>💬 Messagerie
      {% if g.notif_count and g.notif_count > 0 %}<span style='background:#ef4444;color:white;border-radius:999px;font-size:10px;font-weight:800;padding:2px 6px;margin-left:6px;'>nouveau</span>{% endif %}
    </a>
    <a href='{{ url_for("signalement_page") }}' onclick='closeDrawer()'>🚨 Signalement</a>
  </div>
  {% if session.get('role') in ['prof','admin'] %}
  <div class='mobile-drawer-section'>
    <span class='mobile-drawer-section-label'>Gestion</span>
    <a href='{{ url_for("add_grade") }}' onclick='closeDrawer()'>➕ Ajouter une note</a>
    <a href='{{ url_for("manage_users") }}' onclick='closeDrawer()'>👥 Comptes</a>
    {% if session.get('role') == 'admin' %}
      <a href='{{ url_for("admin_panel") }}' onclick='closeDrawer()'>⚙️ Administration</a>
      <a href='{{ url_for("manage_school") }}' onclick='closeDrawer()'>🏫 École</a>
    {% endif %}
  </div>
  {% endif %}
  <div class='mobile-drawer-bottom'>
    <span class='mobile-drawer-section-label' style='display:block;padding:0 6px 8px;'>Mon compte</span>
    <a href='{{ url_for("settings_page") }}' onclick='closeDrawer()'>⚙️ Paramètres</a>
    <a href='{{ url_for("logout") }}' onclick='closeDrawer()' style='color:#f87171;margin-top:4px;'>🚪 Déconnexion</a>
    <div style='margin-top:14px;display:flex;align-items:center;justify-content:space-between;padding:0 6px;'>
      <span style='color:rgba(255,255,255,0.55);font-size:13px;'>Mode sombre</span>
      <button class='dark-toggle' onclick='toggleDark()'>🌙</button>
    </div>
  </div>
</div>
{% endif %}
<script>
function openDrawer(){document.getElementById('mobileDrawer').classList.add('open');document.getElementById('mobileOverlay').classList.add('show');document.body.style.overflow='hidden';}
function closeDrawer(){document.getElementById('mobileDrawer').classList.remove('open');document.getElementById('mobileOverlay').classList.remove('show');document.body.style.overflow='';}
function toggleDark(){var next=document.documentElement.getAttribute('data-theme')=='dark'?'light':'dark';document.documentElement.setAttribute('data-theme',next);localStorage.setItem('theme',next);document.querySelectorAll('.dark-toggle').forEach(function(b){b.textContent=next==='dark'?'☀️':'🌙';});}
(function(){var t=localStorage.getItem('theme')||'light';document.querySelectorAll('.dark-toggle').forEach(function(b){b.textContent=t==='dark'?'☀️':'🌙';});})();
// Instant page navigation
document.addEventListener('DOMContentLoaded',function(){
  var loader=document.getElementById('page-loader');
  document.querySelectorAll('a[href]').forEach(function(a){
    var href=a.getAttribute('href');
    if(href&&href.startsWith('/')&&!href.startsWith('//')&&!a.target){
      a.addEventListener('click',function(e){
        if(!e.metaKey&&!e.ctrlKey&&!e.shiftKey){
          loader.classList.add('show');
        }
      });
    }
  });
  // Hide loader when page is ready
  window.addEventListener('pageshow',function(){loader.classList.remove('show');});
});
</script>
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
            session.permanent = True
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
            session.permanent = True
            if site_unlocked:
                session["site_unlocked"] = True
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"]
            execute_db(
                "UPDATE users SET last_login_at = ?, login_count = COALESCE(login_count, 0) + 1 WHERE id = ?",
                (current_timestamp(), user["id"]),
            )
            log_event("Connexion réussie", user=user, details="Connexion utilisateur", entity_type="user", entity_id=user["id"])
            flash("Connexion réussie.")
            return redirect(url_for("dashboard"))
        flash("Identifiants invalides.")

    content = """
    <div class='login-wrap'>
      <div class='grid'>
        <div class='card'>
          <h1>Connexion</h1>
          <p class='muted'>Pas de compte ? <a href='{{ url_for("register") }}'>Créer un compte</a></p>
          <form method='post' autocomplete='off'>
            <label>Nom d'utilisateur</label>
            <input name='username' required>
            <label>Mot de passe</label>
            <input name='password' type='password' required>
            <button type='submit'>Se connecter</button>
          </form>
          <p style='margin-top:12px;text-align:center;'><a href='{{ url_for("forgot_password") }}' style='color:#6366f1;font-size:13px;font-weight:600;'>🔑 Mot de passe oublié ?</a></p>
        </div>
        <div class='card'>
          <h2>Fonctions</h2>
          <p><span class='badge'>Admin</span> gestion complète des comptes, devoirs, notes, absences, EDT</p>
          <p><span class='badge'>Fichiers</span> pièces jointes permanentes via Cloudinary</p>
          <p><span class='badge'>Profil</span> photo de profil permanente</p>
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
            secret_q = request.form.get("secret_question", "").strip()
            secret_a = request.form.get("secret_answer", "").strip().lower()
            execute_db(
                "INSERT INTO users (username, password, role, full_name, class_id, child_id, child_id_2, profile_picture, profile_picture_url, created_at, last_login_at, login_count, secret_question, secret_answer) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, 0, ?, ?)",
                (username, generate_password_hash(password), role, full_name, class_id, child_id, child_id_2, current_timestamp(), secret_q or None, secret_a or None),
            )
            new_user = query_one("SELECT id, username, role, full_name FROM users WHERE username = ?", (username,))
            log_event("Création de compte", user=new_user, details=f"Nouveau compte créé ({role})", entity_type="user", entity_id=new_user["id"] if new_user else None)
            flash("Compte créé. Tu peux maintenant te connecter.")
            return redirect(url_for("login"))
        except Exception:
            flash("Nom d'utilisateur déjà utilisé.")

    content = """
    <div class='card' style='max-width:700px; margin:auto;'>
      <h1>Créer un compte</h1>
      <form method='post' autocomplete='off'>
        <label>Nom complet</label><input name='full_name' required>
        <label>Nom d'utilisateur</label><input name='username' required>
        <label>Mot de passe</label><input type='password' name='password' required>
        <label>Type de compte</label>
        <select name='role' id='role_select' required onchange='toggleRegisterFields()'>
          <option value='eleve'>Élève</option>
          <option value='prof'>Professeur</option>
          <option value='parent'>Parent</option>
        </select>
        <div id='class_block'>
          <label>Classe</label>
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
        <div id='secret_block'>
          <label>Question secrète</label>
          <select name='secret_question' required>
            <option value=''>-- Choisir une question --</option>
            <option>Quel est le prénom de ta mère ?</option>
            <option>Quel est le nom de ton animal de compagnie ?</option>
            <option>Quelle est ta ville de naissance ?</option>
            <option>Quel est le prénom de ton meilleur ami ?</option>
            <option>Quel est ton plat préféré ?</option>
            <option>Quel est le prénom de ton père ?</option>
          </select>
          <label>Réponse secrète <span class='muted small'>(en minuscules)</span></label>
          <input name='secret_answer' placeholder='Ta réponse...' autocomplete='off'>
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
    if getattr(g, "user", None):
        log_event("Déconnexion", user=g.user, details="Déconnexion utilisateur", entity_type="user", entity_id=g.user["id"])
    session.clear()
    if site_unlocked:
        session["site_unlocked"] = True
    flash("Tu es déconnecté.")
    return redirect(url_for("login"))


# =========================
# Profil
# =========================
@app.route("/profile", methods=["GET", "POST"])
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    user = g.user

    if request.method == "POST":
        form_type = request.form.get("form_type", "photo")

        # --- Changement photo ---
        if form_type == "photo":
            uploaded = request.files.get("profile_picture")
            if not uploaded or not uploaded.filename:
                flash("Choisis une image.")
                return redirect(url_for("settings_page"))
            if not allowed_profile_image(uploaded.filename):
                flash("Format image non autorisé.")
                return redirect(url_for("settings_page"))
            old_user = query_one("SELECT profile_picture FROM users WHERE id = ?", (user["id"],))
            if old_user and old_user.get("profile_picture"):
                delete_from_cloudinary(old_user["profile_picture"], resource_type="image")
            public_id, secure_url = upload_to_cloudinary(uploaded, folder="renote_profiles", resource_type="image")
            if not public_id or not secure_url:
                flash("Impossible d'enregistrer la photo de profil.")
                return redirect(url_for("settings_page"))
            execute_db(
                "UPDATE users SET profile_picture = ?, profile_picture_url = ? WHERE id = ?",
                (public_id, secure_url, user["id"]),
            )
            log_event("Photo de profil mise à jour", user=user, entity_type="user", entity_id=user["id"])
            flash("Photo de profil mise à jour.")
            return redirect(url_for("settings_page"))

        # --- Modification infos personnelles ---
        elif form_type == "edit_info":
            new_full_name = request.form.get("full_name", "").strip()
            new_username = request.form.get("username", "").strip()
            new_password = request.form.get("new_password", "").strip()
            confirm_password = request.form.get("confirm_password", "").strip()

            if not new_full_name or not new_username:
                flash("Le nom et le nom d'utilisateur sont obligatoires.")
                return redirect(url_for("settings_page"))

            # Vérifier que le username n'est pas déjà pris par quelqu'un d'autre
            existing = query_one("SELECT id FROM users WHERE username = ? AND id != ?", (new_username, user["id"]))
            if existing:
                flash("Ce nom d'utilisateur est déjà utilisé.")
                return redirect(url_for("settings_page"))

            new_secret_q = request.form.get("secret_question", "").strip()
            new_secret_a = request.form.get("secret_answer", "").strip().lower()
            if new_password:
                if len(new_password) < 6:
                    flash("Le mot de passe doit faire au moins 6 caractères.")
                    return redirect(url_for("settings_page"))
                if new_password != confirm_password:
                    flash("Les deux mots de passe ne correspondent pas.")
                    return redirect(url_for("settings_page"))
                execute_db(
                    "UPDATE users SET full_name = ?, username = ?, password = ? WHERE id = ?",
                    (new_full_name, new_username, generate_password_hash(new_password), user["id"]),
                )
            else:
                execute_db(
                    "UPDATE users SET full_name = ?, username = ? WHERE id = ?",
                    (new_full_name, new_username, user["id"]),
                )
            if new_secret_q:
                execute_db("UPDATE users SET secret_question = ? WHERE id = ?", (new_secret_q, user["id"]))
            if new_secret_a:
                execute_db("UPDATE users SET secret_answer = ? WHERE id = ?", (new_secret_a, user["id"]))

            # Mettre à jour la session
            session["full_name"] = new_full_name
            session["username"] = new_username
            log_event("Profil modifié", user=user, details=f"Nouveau nom: {new_full_name}", entity_type="user", entity_id=user["id"])
            flash("Profil mis à jour avec succès.")
            return redirect(url_for("settings_page"))

    refreshed_user = query_one(
        """
        SELECT u.*, c.name AS class_name
        FROM users u
        LEFT JOIN classes c ON c.id = u.class_id
        WHERE u.id = ?
        """,
        (user["id"],),
    )

    # Courbe des notes pour élève et parent
    grades_chart_data = []
    if refreshed_user["role"] == "eleve":
        grades_chart_data = query_all(
            """
            SELECT g.value, g.created_at, s.name AS subject_name
            FROM grades g JOIN subjects s ON s.id = g.subject_id
            WHERE g.student_id = ?
            ORDER BY g.created_at ASC
            """,
            (user["id"],),
        )
    elif refreshed_user["role"] == "parent":
        children = get_parent_children(refreshed_user)
        if children:
            child_ids = [c["id"] for c in children]
            placeholders = ",".join(["?"] * len(child_ids))
            grades_chart_data = query_all(
                f"""
                SELECT g.value, g.created_at, s.name AS subject_name, u.full_name AS student_name
                FROM grades g JOIN subjects s ON s.id = g.subject_id JOIN users u ON u.id = g.student_id
                WHERE g.student_id IN ({placeholders})
                ORDER BY u.full_name, g.created_at ASC
                """,
                tuple(child_ids),
            )

    content = """
    <style>
      .profile-tabs { display:flex; gap:10px; margin-bottom:20px; flex-wrap:wrap; }
      .profile-tab { padding:10px 20px; border-radius:12px; cursor:pointer; font-weight:700; border:2px solid #dbeafe; background:#f0f7ff; color:#1d4ed8; transition:0.2s; }
      .profile-tab.active { background:linear-gradient(90deg,#1d4ed8,#2563eb); color:white; border-color:transparent; }
      .profile-section { display:none; }
      .profile-section.active { display:block; }
      .chart-container { position:relative; height:300px; width:100%; }
    </style>

    <div class='hero' style='display:flex; align-items:center; gap:22px; flex-wrap:wrap;'>
      {% if user.profile_picture_url %}
        <img src='{{ user.profile_picture_url }}' class='avatar-large' alt='Photo de profil' style='border:4px solid rgba(255,255,255,0.8);'>
      {% else %}
        <div class='avatar-large' style='display:inline-flex; align-items:center; justify-content:center; font-size:38px; font-weight:800;'>
          {{ user.full_name[:1] }}
        </div>
      {% endif %}
      <div>
        <h1 style='margin-bottom:6px;'>⚙️ Paramètres — {{ user.full_name }}</h1>
        <p style='opacity:0.9; margin:0;'>@{{ user.username }} · {{ user.role }}{% if user.class_name %} · {{ user.class_name }}{% endif %}</p>
      </div>
    </div>

    <div class='profile-tabs'>
      <div class='profile-tab active' onclick='switchTab("info")'>⚙️ Mes informations</div>
      <div class='profile-tab' onclick='switchTab("photo")'>🖼️ Photo de profil</div>
      {% if user.role in ['eleve', 'parent'] %}
      <div class='profile-tab' onclick='switchTab("courbe")'>📈 Courbe des notes</div>
      <div class='profile-tab' onclick='switchTab("bulletin")'>📄 Bulletin PDF</div>
      {% endif %}
    </div>

    <!-- TAB : Infos personnelles -->
    <div id='tab-info' class='profile-section active'>
      <div class='card' style='max-width:600px;'>
        <h2>Modifier mes informations</h2>
        <form method='post'>
          <input type='hidden' name='form_type' value='edit_info'>
          <label>Nom complet</label>
          <input name='full_name' value='{{ user.full_name }}' required>
          <label>Nom d'utilisateur</label>
          <input name='username' value='{{ user.username }}' required autocomplete='off'>
          <label>Nouveau mot de passe <span class='muted small'>(laisser vide pour ne pas changer)</span></label>
          <input type='password' name='new_password' autocomplete='new-password' placeholder='Minimum 6 caractères'>
          <label>Confirmer le nouveau mot de passe</label>
          <input type='password' name='confirm_password' autocomplete='new-password'>
          <hr style='border:none;border-top:1px solid var(--input-border);margin:8px 0 16px;'>
          <label style='font-weight:700;color:var(--text);'>🔑 Question secrète <span class='muted small'>(pour récupérer ton mot de passe)</span></label>
          <select name='secret_question'>
            <option value=''>-- Choisir une question --</option>
            <option {% if user.secret_question == 'Quel est le prénom de ta mère ?' %}selected{% endif %}>Quel est le prénom de ta mère ?</option>
            <option {% if user.secret_question == 'Quel est le nom de ton animal de compagnie ?' %}selected{% endif %}>Quel est le nom de ton animal de compagnie ?</option>
            <option {% if user.secret_question == 'Quelle est ta ville de naissance ?' %}selected{% endif %}>Quelle est ta ville de naissance ?</option>
            <option {% if user.secret_question == 'Quel est le prénom de ton meilleur ami ?' %}selected{% endif %}>Quel est le prénom de ton meilleur ami ?</option>
            <option {% if user.secret_question == 'Quel est ton plat préféré ?' %}selected{% endif %}>Quel est ton plat préféré ?</option>
            <option {% if user.secret_question == 'Quel est le prénom de ton père ?' %}selected{% endif %}>Quel est le prénom de ton père ?</option>
          </select>
          <label>Nouvelle réponse secrète <span class='muted small'>(laisser vide pour ne pas changer)</span></label>
          <input name='secret_answer' placeholder='Ta réponse en minuscules...' autocomplete='off'>
          <button type='submit'>Enregistrer les modifications</button>
        </form>
      </div>
    </div>

    <!-- TAB : Photo -->
    <div id='tab-photo' class='profile-section'>
      <div class='card' style='max-width:600px;'>
        <h2>Changer la photo de profil</h2>
        <form method='post' enctype='multipart/form-data'>
          <input type='hidden' name='form_type' value='photo'>
          <label>Choisir une image</label>
          <input type='file' name='profile_picture' accept='image/*' required>
          <button type='submit'>Mettre à jour la photo</button>
        </form>
        <p class='muted small'>Formats autorisés : png, jpg, jpeg, gif, webp</p>
      </div>
    </div>

    <!-- TAB : Courbe des notes -->
    {% if user.role in ['eleve', 'parent'] %}
    <div id='tab-courbe' class='profile-section'>
      <div class='card'>
        <h2>📈 Évolution des notes dans le temps</h2>
        {% if grades_chart_data %}
          <div class='chart-container'>
            <canvas id='gradesChart'></canvas>
          </div>
          <script src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js'></script>
          <script>
            const rawData = {{ grades_chart_data_json }};
            const colors = ['#2563eb','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#ec4899','#84cc16'];

            // Grouper par matière (ou par élève+matière pour parent)
            const groups = {};
            rawData.forEach(d => {
              const key = d.student_name ? d.student_name + ' - ' + d.subject_name : d.subject_name;
              if (!groups[key]) groups[key] = [];
              groups[key].push({ x: d.created_at.substring(0,10), y: d.value });
            });

            const datasets = Object.entries(groups).map(([label, data], i) => ({
              label,
              data,
              borderColor: colors[i % colors.length],
              backgroundColor: colors[i % colors.length] + '22',
              tension: 0.3,
              pointRadius: 5,
              pointHoverRadius: 7,
              fill: false,
              borderWidth: 2.5,
            }));

            new Chart(document.getElementById('gradesChart'), {
              type: 'line',
              data: { datasets },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                  x: {
                    type: 'category',
                    title: { display: true, text: 'Date' },
                    ticks: { maxRotation: 45, font: { size: 12 } }
                  },
                  y: {
                    min: 0, max: 20,
                    title: { display: true, text: 'Note /20' },
                    ticks: { stepSize: 2 }
                  }
                },
                plugins: {
                  legend: { position: 'bottom', labels: { font: { size: 13 }, padding: 16 } },
                  tooltip: {
                    callbacks: {
                      label: ctx => ctx.dataset.label + ' : ' + ctx.parsed.y + '/20'
                    }
                  }
                }
              }
            });
          </script>
        {% else %}
          <p class='muted'>Aucune note enregistrée pour le moment.</p>
        {% endif %}
      </div>
    </div>
    {% endif %}

    <!-- TAB : Bulletin PDF -->
    {% if user.role in ['eleve', 'parent'] %}
    <div id='tab-bulletin' class='profile-section'>
      <div class='card' style='max-width:600px;'>
        <h2>📄 Télécharger le bulletin</h2>
        <p class='muted'>Génère un document complet avec toutes tes notes, moyennes par matière et absences. Le fichier s'ouvre dans n'importe quel navigateur et peut être imprimé ou sauvegardé en PDF.</p>
        <div style='background:var(--admin-box);border:1px solid var(--admin-box-border);border-radius:14px;padding:20px;margin-top:10px;'>
          <div style='display:flex;align-items:center;gap:14px;flex-wrap:wrap;'>
            <div style='font-size:48px;'>📋</div>
            <div>
              <div style='font-weight:800;font-size:16px;color:var(--text);'>Bulletin scolaire</div>
              <div class='muted small'>Notes · Moyennes · Absences</div>
            </div>
          </div>
          <a href='{{ url_for("bulletin_pdf") }}' style='display:inline-flex;align-items:center;gap:8px;margin-top:16px;background:linear-gradient(90deg,#1d4ed8,#2563eb);color:white;text-decoration:none;padding:12px 20px;border-radius:12px;font-weight:700;font-size:15px;'>
            ⬇️ Télécharger le bulletin
          </a>
        </div>
        <p class='muted small' style='margin-top:12px;'>💡 Une fois ouvert dans le navigateur, utilise Ctrl+P (ou Cmd+P sur Mac) pour l'imprimer ou le sauvegarder en PDF.</p>
      </div>
    </div>
    {% endif %}

    <script>
      function switchTab(name) {
        document.querySelectorAll('.profile-section').forEach(s => s.classList.remove('active'));
        document.querySelectorAll('.profile-tab').forEach(t => t.classList.remove('active'));
        document.getElementById('tab-' + name).classList.add('active');
        event.target.classList.add('active');
      }
    </script>
    """

    import json
    grades_chart_data_json = json.dumps([dict(r) for r in grades_chart_data])
    return render_page(content, title="Paramètres", user=refreshed_user, grades_chart_data=grades_chart_data, grades_chart_data_json=grades_chart_data_json)


# =========================
# Infos générales
# =========================
@app.route("/general-info", methods=["GET", "POST"])
@login_required
def general_info_page():
    user = g.user

    if request.method == "POST":
        form_type = request.form.get("form_type", "").strip()

        if form_type == "create":
            if user["role"] not in ["prof", "admin"]:
                flash("Accès refusé.")
                return redirect(url_for("general_info_page"))
            title = request.form.get("title", "").strip()
            body = request.form.get("body", "").strip()
            if not title or not body:
                flash("Remplis le titre et le contenu.")
                return redirect(url_for("general_info_page"))
            execute_db(
                "INSERT INTO general_info (title, body, author_id, created_at) VALUES (?, ?, ?, ?)",
                (title, body, user["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            log_event("Information générale publiée", user=user, details=title, entity_type="general_info")
            flash("Information générale publiée.")
            return redirect(url_for("general_info_page"))

        elif form_type == "update":
            info_id = request.form.get("info_id")
            title = request.form.get("title", "").strip()
            body = request.form.get("body", "").strip()
            info = query_one("SELECT * FROM general_info WHERE id = ?", (info_id,))
            if not info:
                flash("Information introuvable.")
                return redirect(url_for("general_info_page"))
            if user["role"] != "admin" and str(info["author_id"]) != str(user["id"]):
                flash("Tu ne peux pas modifier cette information.")
                return redirect(url_for("general_info_page"))
            if not title or not body:
                flash("Titre ou contenu invalide.")
                return redirect(url_for("general_info_page"))
            execute_db(
                "UPDATE general_info SET title = ?, body = ? WHERE id = ?",
                (title, body, info_id),
            )
            log_event("Information générale modifiée", user=user, details=title, entity_type="general_info", entity_id=info_id)
            flash("Information modifiée.")
            return redirect(url_for("general_info_page"))

        elif form_type == "delete":
            info_id = request.form.get("info_id")
            info = query_one("SELECT * FROM general_info WHERE id = ?", (info_id,))
            if not info:
                flash("Information introuvable.")
                return redirect(url_for("general_info_page"))
            if user["role"] != "admin" and str(info["author_id"]) != str(user["id"]):
                flash("Tu ne peux pas supprimer cette information.")
                return redirect(url_for("general_info_page"))
            execute_db("DELETE FROM general_info WHERE id = ?", (info_id,))
            log_event("Information générale supprimée", user=user, details=info.get("title"), entity_type="general_info", entity_id=info_id)
            flash("Information supprimée.")
            return redirect(url_for("general_info_page"))

    infos = query_all(
        """
        SELECT gi.*, u.full_name AS author_name
        FROM general_info gi
        JOIN users u ON u.id = gi.author_id
        ORDER BY gi.id DESC
        """
    )

    content = """
    <div class='grid'>
      {% if user.role in ['prof', 'admin'] %}
      <div class='card'>
        <h2 style='background:linear-gradient(135deg,#10b981,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>📢 Publier une information</h2>
        <form method='post'>
          <input type='hidden' name='form_type' value='create'>
          <label>Titre</label><input name='title' required>
          <label>Contenu</label><textarea name='body' required></textarea>
          <button type='submit'>Publier</button>
        </form>
      </div>
      {% endif %}
      <div class='card'>
        <div style='display:flex;align-items:center;gap:10px;margin-bottom:16px;'>
          <span style='font-size:28px;'>📢</span>
          <h1 style='margin:0;background:linear-gradient(135deg,#10b981,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>Info général</h1>
        </div>
        {% for info in infos %}
          <div class='info-box'>
            <strong>{{ info.title }}</strong>
            <p style='margin:10px 0;'>{{ info.body }}</p>
            <p class='muted small'>Par {{ info.author_name }} · {{ info.created_at }}</p>
            {% if user.role == 'admin' or info.author_id == user.id %}
            <div class='admin-box'>
              <form method='post'>
                <input type='hidden' name='form_type' value='update'>
                <input type='hidden' name='info_id' value='{{ info.id }}'>
                <label>Titre</label><input name='title' value='{{ info.title }}' required>
                <label>Contenu</label><textarea name='body' required>{{ info.body }}</textarea>
                <div class='actions-inline'><button type='submit'>Modifier</button></div>
              </form>
              <form method='post' onsubmit="return confirm('Supprimer cette information ?');" style='margin-top:10px;'>
                <input type='hidden' name='form_type' value='delete'>
                <input type='hidden' name='info_id' value='{{ info.id }}'>
                <button type='submit' class='danger'>Supprimer</button>
              </form>
            </div>
            {% endif %}
          </div>
        {% else %}
          <p class='muted'>Aucune information générale pour le moment.</p>
        {% endfor %}
      </div>
    </div>
    """
    return render_page(content, title="Info général", user=user, infos=infos)


# =========================
# Dashboard
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    user = query_one(
        """
        SELECT u.*, c.name AS class_name
        FROM users u
        LEFT JOIN classes c ON c.id = u.class_id
        WHERE u.id = ?
        """,
        (g.user["id"],),
    )

    parent_children = get_parent_children(user)
    parent_child_names = ", ".join(child["full_name"] for child in parent_children)

    if user["role"] == "eleve":
        grades_list = query_all("SELECT value FROM grades WHERE student_id = ?", (user["id"],))
        avg = round(sum(r["value"] for r in grades_list) / len(grades_list), 2) if grades_list else "-"
        if user.get("class_id"):
            homework_total = query_one(
                "SELECT COUNT(*) AS total FROM homework WHERE class_id IS NULL OR class_id = ?",
                (user["class_id"],),
            )["total"]
        else:
            homework_total = query_one("SELECT COUNT(*) AS total FROM homework WHERE class_id IS NULL")["total"]
        stats = {
            "Moyenne générale": avg,
            "Notes": len(grades_list),
            "Devoirs": homework_total,
            "Absences": query_one("SELECT COUNT(*) AS total FROM absences WHERE student_id = ?", (user["id"],))["total"],
        }
    elif user["role"] == "parent":
        if parent_children:
            child_ids = [child["id"] for child in parent_children]
            placeholders = ",".join(["?"] * len(child_ids))
            grades_list = query_all(f"SELECT value FROM grades WHERE student_id IN ({placeholders})", tuple(child_ids))
            avg = round(sum(r["value"] for r in grades_list) / len(grades_list), 2) if grades_list else "-"
            abs_total = query_one(f"SELECT COUNT(*) AS total FROM absences WHERE student_id IN ({placeholders})", tuple(child_ids))["total"]
            stats = {"Enfants": len(parent_children), "Noms": parent_child_names, "Moyenne générale": avg, "Notes": len(grades_list), "Absences": abs_total}
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
        FROM messages m
        JOIN users u ON u.id = m.sender_id
        WHERE m.receiver_id = ?
        ORDER BY m.id DESC LIMIT 5
        """,
        (user["id"],),
    )
    general_infos = query_all(
        """
        SELECT gi.*, u.full_name AS author_name
        FROM general_info gi
        JOIN users u ON u.id = gi.author_id
        ORDER BY gi.id DESC LIMIT 5
        """
    )

    content = """
    <div class='hero'>
      <div style='display:flex; align-items:center; gap:18px; flex-wrap:wrap;'>
        {% if user.profile_picture_url %}
          <img src='{{ user.profile_picture_url }}' class='avatar' alt='Photo de profil'>
        {% else %}
          <div class='avatar' style='display:flex; align-items:center; justify-content:center; color:#1d4ed8; font-size:24px; font-weight:800;'>
            {{ user.full_name[:1] }}
          </div>
        {% endif %}
        <div>
          <h1 style='margin-bottom:8px;'>Bienvenue {{ user.full_name }}</h1>
          <p>
            Rôle : <strong>{{ user.role }}</strong>
            {% if parent_child_names %} · Enfant(s) lié(s) : <strong>{{ parent_child_names }}</strong>
            {% elif user.class_name %} · Classe : <strong>{{ user.class_name }}</strong>{% endif %}
          </p>
        </div>
      </div>
    </div>
    <div class='grid'>
      {% set stat_colors = ['linear-gradient(135deg,#1d4ed8,#38bdf8)','linear-gradient(135deg,#10b981,#06b6d4)','linear-gradient(135deg,#f59e0b,#ef4444)','linear-gradient(135deg,#8b5cf6,#ec4899)'] %}
      {% for key, value in stats.items() %}
        <div class='card' style='border-top:4px solid transparent;border-image:{{ stat_colors[loop.index0 % 4] }};border-image-slice:1;'>
          <h3 style='color:var(--text-muted);font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;'>{{ key }}</h3>
          <p class='metric' style='background:{{ stat_colors[loop.index0 % 4] }};-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>{{ value }}</p>
        </div>
      {% endfor %}
    </div>
    <div class='grid' style='margin-top:18px;'>

      <div class='card'>
        <h2 style='background:linear-gradient(135deg,#1d4ed8,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>💬 Derniers messages</h2>
        {% for m in latest_messages %}
          <div style='padding:10px 0; border-bottom:1px solid #eef3fb;'>
            <strong>{{ m.subject }}</strong><br>
            <span class='small muted'>De {{ m.sender_name }} · {{ m.created_at }}</span>
          </div>
        {% else %}
          <p class='muted'>Aucun message reçu.</p>
        {% endfor %}
      </div>
    </div>
    <div class='card' style='margin-top:18px; border:1px solid rgba(139,92,246,0.2); box-shadow:0 8px 24px rgba(139,92,246,0.08);'>
      <div style='display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; flex-wrap:wrap; gap:10px;'>
        <h2 style='margin:0; background:linear-gradient(135deg,#f59e0b,#ec4899,#8b5cf6); -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;'>🌸 Vie de classe</h2>
        <a href='{{ url_for("vie_de_classe") }}' style='font-size:13px; color:#8b5cf6; font-weight:700; text-decoration:none;'>Voir tout →</a>
      </div>
      {% if last_vie_posts %}
        {% for vp in last_vie_posts %}
          <div style='display:flex; gap:12px; align-items:flex-start; padding:12px 0; border-bottom:1px solid var(--table-border);'>
            {% if vp.author_pic %}
              <img src='{{ vp.author_pic }}' style='width:36px;height:36px;border-radius:50%;object-fit:cover;flex-shrink:0;'>
            {% else %}
              <div style='width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#8b5cf6,#ec4899);display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:800;color:white;flex-shrink:0;'>{{ vp.author_name[:1] }}</div>
            {% endif %}
            <div style='flex:1;min-width:0;'>
              <div style='font-weight:700;font-size:14px;color:var(--text);'>{{ vp.author_name }}</div>
              {% if vp.body %}<div style='font-size:13px;color:var(--text-muted);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>{{ vp.body[:80] }}{% if vp.body|length > 80 %}...{% endif %}</div>{% endif %}
              {% if vp.image_url and not vp.body %}<div style='font-size:13px;color:var(--text-muted);margin-top:2px;'>📷 Photo partagée</div>{% endif %}
              <div style='font-size:11px;color:var(--text-muted);margin-top:4px;'>{{ vp.created_at }}</div>
            </div>
            {% if vp.image_url %}
              <img src='{{ vp.image_url }}' style='width:52px;height:52px;border-radius:10px;object-fit:cover;flex-shrink:0;'>
            {% endif %}
          </div>
        {% endfor %}
      {% else %}
        <p style='color:var(--text-muted);font-size:14px;text-align:center;padding:20px 0;'>Aucune publication pour le moment 📷</p>
      {% endif %}
    </div>

    <div class='card' style='margin-top:18px;'>
      <h2 style='background:linear-gradient(135deg,#10b981,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>📢 Infos générales récentes</h2>
      {% for info in general_infos %}
        <div class='info-box'>
          <strong>{{ info.title }}</strong>
          <p style='margin:10px 0;'>{{ info.body }}</p>
          <p class='muted small'>Par {{ info.author_name }} · {{ info.created_at }}</p>
        </div>
      {% else %}
        <p class='muted'>Aucune information générale pour le moment.</p>
      {% endfor %}
      <p><a href='{{ url_for("general_info_page") }}'>Voir toutes les infos</a></p>
    </div>
    """
    last_vie_posts = query_all(
        """
        SELECT vp.*, u.full_name AS author_name, u.profile_picture_url AS author_pic
        FROM vie_posts vp JOIN users u ON u.id = vp.author_id
        ORDER BY vp.id DESC LIMIT 3
        """
    )

    return render_page(
        content,
        title="Tableau de bord",
        user=user,
        stats=stats,
        latest_messages=latest_messages,
        parent_child_names=parent_child_names,
        general_infos=general_infos,
        last_vie_posts=last_vie_posts,
    )


# =========================
# Notes
# =========================
@app.route("/grades", methods=["GET", "POST"])
@login_required
def grades():
    user = g.user

    # Marquer les notes comme vues
    if user["role"] in ["eleve", "parent"]:
        try:
            if user["role"] == "eleve":
                max_grade = query_one("SELECT MAX(id) AS max_id FROM grades WHERE student_id = ?", (user["id"],))
            else:
                children = get_parent_children(user)
                if children:
                    child_ids = [c["id"] for c in children]
                    placeholders = ",".join(["?"] * len(child_ids))
                    max_grade = query_one(f"SELECT MAX(id) AS max_id FROM grades WHERE student_id IN ({placeholders})", tuple(child_ids))
                else:
                    max_grade = None
            max_id = max_grade["max_id"] if max_grade and max_grade["max_id"] else 0
            existing = query_one("SELECT id FROM notif_seen WHERE user_id = ?", (user["id"],))
            if existing:
                execute_db("UPDATE notif_seen SET last_seen_grade_id = MAX(last_seen_grade_id, ?) WHERE user_id = ?", (max_id, user["id"]))
            else:
                execute_db("INSERT INTO notif_seen (user_id, last_seen_message_id, last_seen_grade_id) VALUES (?, 0, ?)", (user["id"], max_id))
        except Exception:
            pass

    if request.method == "POST":
        form_type = request.form.get("form_type", "").strip()

        if form_type == "update":
            if user["role"] not in ["admin", "prof"]:
                flash("Accès refusé.")
                return redirect(url_for("grades"))
            grade_id = request.form.get("grade_id")
            grade = query_one("SELECT * FROM grades WHERE id = ?", (grade_id,))
            if not grade:
                flash("Note introuvable.")
                return redirect(url_for("grades"))
            if user["role"] == "prof" and str(grade["teacher_id"]) != str(user["id"]):
                flash("Tu ne peux modifier que tes propres notes.")
                return redirect(url_for("grades"))
            try:
                value_float = float(request.form.get("value"))
                if value_float < 0 or value_float > 20:
                    raise ValueError
            except Exception:
                flash("La note doit être entre 0 et 20.")
                return redirect(url_for("grades"))
            execute_db(
                "UPDATE grades SET value = ?, comment = ? WHERE id = ?",
                (value_float, request.form.get("comment", "").strip(), grade_id),
            )
            flash("Note modifiée.")
            return redirect(url_for("grades"))

        elif form_type == "delete":
            if user["role"] not in ["admin", "prof"]:
                flash("Accès refusé.")
                return redirect(url_for("grades"))
            grade_id = request.form.get("grade_id")
            grade = query_one("SELECT * FROM grades WHERE id = ?", (grade_id,))
            if not grade:
                flash("Note introuvable.")
                return redirect(url_for("grades"))
            if user["role"] == "prof" and str(grade["teacher_id"]) != str(user["id"]):
                flash("Tu ne peux supprimer que tes propres notes.")
                return redirect(url_for("grades"))
            execute_db("DELETE FROM grades WHERE id = ?", (grade_id,))
            flash("Note supprimée.")
            return redirect(url_for("grades"))

    if user["role"] == "eleve":
        rows = query_all(
            """
            SELECT g.id, g.student_id, g.teacher_id, g.value, g.comment, g.created_at,
                   s.name AS subject_name, u.full_name AS teacher_name
            FROM grades g
            JOIN subjects s ON s.id = g.subject_id
            JOIN users u ON u.id = g.teacher_id
            WHERE g.student_id = ?
            ORDER BY g.id DESC
            """,
            (user["id"],),
        )
        averages = query_all(
            """
            SELECT s.name AS subject_name, ROUND(CAST(AVG(g.value) AS NUMERIC), 2) AS average_value
            FROM grades g JOIN subjects s ON s.id = g.subject_id
            WHERE g.student_id = ?
            GROUP BY s.name ORDER BY s.name
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
            SELECT g.id, g.student_id, g.teacher_id, g.value, g.comment, g.created_at,
                   s.name AS subject_name, u.full_name AS teacher_name, stu.full_name AS student_name
            FROM grades g
            JOIN subjects s ON s.id = g.subject_id
            JOIN users u ON u.id = g.teacher_id
            JOIN users stu ON stu.id = g.student_id
            WHERE g.student_id IN ({placeholders})
            ORDER BY stu.full_name, g.id DESC
            """,
            tuple(student_ids),
        )
        averages = query_all(
            f"""
            SELECT stu.full_name AS student_name, s.name AS subject_name, ROUND(CAST(AVG(g.value) AS NUMERIC), 2) AS average_value
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
            SELECT g.id, g.student_id, g.teacher_id, g.value, g.comment, g.created_at,
                   s.name AS subject_name, stu.full_name AS student_name, tea.full_name AS teacher_name
            FROM grades g
            JOIN subjects s ON s.id = g.subject_id
            JOIN users stu ON stu.id = g.student_id
            JOIN users tea ON tea.id = g.teacher_id
            ORDER BY stu.full_name, g.id DESC
            """
        )
        averages = query_all(
            """
            SELECT stu.full_name AS student_name, s.name AS subject_name, ROUND(CAST(AVG(g.value) AS NUMERIC), 2) AS average_value
            FROM grades g JOIN users stu ON stu.id = g.student_id JOIN subjects s ON s.id = g.subject_id
            GROUP BY stu.full_name, s.name ORDER BY stu.full_name, s.name
            """
        )
        show_student_col = True

    content = """
    <div style='background:linear-gradient(135deg,#1d4ed8,#38bdf8);color:white;border-radius:22px;padding:22px 26px;margin-bottom:20px;box-shadow:0 12px 28px rgba(29,78,216,0.2);display:flex;align-items:center;gap:14px;'>
      <span style='font-size:36px;'>📊</span>
      <div><h1 style='margin:0;color:white;font-size:22px;'>Notes</h1><p style='margin:4px 0 0;opacity:0.85;font-size:13px;'>Toutes tes notes et moyennes par matière</p></div>
    </div>
    <div class='two-cols'>
      <div class='card'>
        <h1>Notes</h1>
        <table>
          <thead><tr>{% if show_student_col %}<th>Élève</th>{% endif %}<th>Matière</th><th>Note</th><th>Professeur</th><th>Commentaire</th><th>Date</th></tr></thead>
          <tbody>
            {% for row in rows %}
              <tr>
                {% if show_student_col %}<td>{{ row.student_name }}</td>{% endif %}
                <td>{{ row.subject_name }}</td>
                <td><strong>{{ row.value }}/20</strong></td>
                <td>{{ row.teacher_name }}</td>
                <td>{{ row.comment or '-' }}</td>
                <td>{{ row.created_at }}</td>
              </tr>
              {% if user.role in ['admin', 'prof'] %}
              <tr><td colspan='6'>
                <div class='admin-box'>
                  <form method='post'>
                    <input type='hidden' name='form_type' value='update'>
                    <input type='hidden' name='grade_id' value='{{ row.id }}'>
                    <label>Nouvelle note</label>
                    <input type='number' step='0.1' min='0' max='20' name='value' value='{{ row.value }}' required>
                    <label>Commentaire</label>
                    <textarea name='comment'>{{ row.comment or '' }}</textarea>
                    <div class='actions-inline'><button type='submit'>Modifier</button></div>
                  </form>
                  <form method='post' onsubmit="return confirm('Supprimer cette note ?');" style='margin-top:10px;'>
                    <input type='hidden' name='form_type' value='delete'>
                    <input type='hidden' name='grade_id' value='{{ row.id }}'>
                    <button type='submit' class='danger'>Supprimer</button>
                  </form>
                </div>
              </td></tr>
              {% endif %}
            {% else %}
              <tr><td colspan='6'>Aucune note.</td></tr>
            {% endfor %}
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
    return render_page(content, title="Notes", rows=rows, averages=averages, show_student_col=show_student_col, user=user)


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
        log_event("Note ajoutée", user=g.user, details=f"Note {value_float}/20", entity_type="grade")
        flash("Note ajoutée.")
        return redirect(url_for("grades"))

    content = """
    <div class='card' style='max-width:760px; margin:auto;'>
      <div style='display:flex;align-items:center;gap:10px;margin-bottom:16px;'>
        <span style='font-size:28px;'>➕</span>
        <h1 style='margin:0;background:linear-gradient(135deg,#1d4ed8,#38bdf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>Ajouter une note</h1>
      </div>
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
@app.route("/homework", methods=["GET", "POST"])
@login_required
def homework_page():
    user = g.user
    subjects = query_all("SELECT id, name FROM subjects ORDER BY name")
    classes = query_all("SELECT id, name FROM classes ORDER BY name")

    if request.method == "POST":
        form_type = request.form.get("form_type", "create").strip()

        if form_type == "create":
            if user["role"] not in ["prof", "admin"]:
                flash("Accès refusé.")
                return redirect(url_for("homework_page"))

            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            due_date = request.form.get("due_date", "").strip()
            if not title or not description or not due_date:
                flash("Remplis tous les champs du devoir.")
                return redirect(url_for("homework_page"))

            # Gérer les pièces jointes multiples
            uploaded_files = request.files.getlist("attachments")

            execute_db(
                "INSERT INTO homework (class_id, subject_id, teacher_id, title, description, due_date, attachment, attachment_url, attachment_name, created_at) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)",
                (
                    request.form.get("class_id") or None,
                    request.form.get("subject_id"),
                    user["id"],
                    title,
                    description,
                    due_date,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            new_hw = query_one("SELECT id FROM homework WHERE teacher_id = ? ORDER BY id DESC LIMIT 1", (user["id"],))
            if new_hw:
                for f in uploaded_files:
                    if f and f.filename:
                        orig = secure_filename(f.filename)
                        if not orig or not allowed_file(orig):
                            continue
                        rtype = "image" if is_image_file(orig) else "raw"
                        pid, url = upload_to_cloudinary(f, folder="renote_homework", resource_type=rtype)
                        if pid:
                            execute_db(
                                "INSERT INTO homework_attachments (homework_id, public_id, url, name, created_at) VALUES (?, ?, ?, ?, ?)",
                                (new_hw["id"], pid, url, orig, current_timestamp())
                            )
            flash("Devoir ajouté.")
            return redirect(url_for("homework_page"))

        elif form_type == "update":
            if user["role"] != "admin":
                flash("Seul l'admin peut modifier les devoirs.")
                return redirect(url_for("homework_page"))

            homework_id = request.form.get("homework_id")
            hw = query_one("SELECT * FROM homework WHERE id = ?", (homework_id,))
            if not hw:
                flash("Devoir introuvable.")
                return redirect(url_for("homework_page"))

            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            due_date = request.form.get("due_date", "").strip()
            class_id = request.form.get("class_id") or None
            subject_id = request.form.get("subject_id")

            if not title or not description or not due_date or not subject_id:
                flash("Champs invalides.")
                return redirect(url_for("homework_page"))

            new_public_id = hw["attachment"]
            new_url = hw["attachment_url"]
            new_name = hw["attachment_name"]
            remove_attachment = request.form.get("remove_attachment") == "1"
            uploaded = request.files.get("attachment")

            if remove_attachment and new_public_id:
                resource_type = "image" if is_image_file(new_name or "") else "raw"
                delete_from_cloudinary(new_public_id, resource_type=resource_type)
                new_public_id = None
                new_url = None
                new_name = None

            if uploaded and uploaded.filename:
                original_name = secure_filename(uploaded.filename)
                if not original_name or not allowed_file(original_name):
                    flash("Nouvelle pièce jointe invalide.")
                    return redirect(url_for("homework_page"))

                if new_public_id:
                    resource_type = "image" if is_image_file(new_name or "") else "raw"
                    delete_from_cloudinary(new_public_id, resource_type=resource_type)

                resource_type = "image" if is_image_file(original_name) else "raw"
                new_public_id, new_url = upload_to_cloudinary(uploaded, folder="renote_homework", resource_type=resource_type)
                new_name = original_name

            execute_db(
                "UPDATE homework SET class_id = ?, subject_id = ?, title = ?, description = ?, due_date = ?, attachment = ?, attachment_url = ?, attachment_name = ? WHERE id = ?",
                (class_id, subject_id, title, description, due_date, new_public_id, new_url, new_name, homework_id),
            )
            flash("Devoir modifié.")
            return redirect(url_for("homework_page"))

        elif form_type == "delete":
            if user["role"] != "admin":
                flash("Seul l'admin peut supprimer les devoirs.")
                return redirect(url_for("homework_page"))

            homework_id = request.form.get("homework_id")
            hw = query_one("SELECT * FROM homework WHERE id = ?", (homework_id,))
            if not hw:
                flash("Devoir introuvable.")
                return redirect(url_for("homework_page"))

            if hw.get("attachment"):
                resource_type = "image" if is_image_file(hw.get("attachment_name") or "") else "raw"
                delete_from_cloudinary(hw["attachment"], resource_type=resource_type)

            execute_db("DELETE FROM homework WHERE id = ?", (homework_id,))
            flash("Devoir supprimé.")
            return redirect(url_for("homework_page"))

        elif form_type == "toggle_done":
            homework_id = request.form.get("homework_id")
            already = query_one(
                "SELECT id FROM homework_done WHERE homework_id = ? AND user_id = ?",
                (homework_id, user["id"])
            )
            if already:
                execute_db("DELETE FROM homework_done WHERE homework_id = ? AND user_id = ?", (homework_id, user["id"]))
            else:
                try:
                    execute_db(
                        "INSERT INTO homework_done (homework_id, user_id, done_at) VALUES (?, ?, ?)",
                        (homework_id, user["id"], current_timestamp())
                    )
                except Exception:
                    pass
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
                WHERE h.class_id IS NULL ORDER BY h.due_date ASC
                """
            )
    else:
        items = query_all(
            """
            SELECT h.*, s.name AS subject_name, u.full_name AS teacher_name, c.name AS class_name
            FROM homework h JOIN subjects s ON s.id = h.subject_id JOIN users u ON u.id = h.teacher_id
            LEFT JOIN classes c ON c.id = h.class_id ORDER BY h.due_date ASC
            """
        )


    content = """
    <div class='grid'>
      {% if user.role in ['prof', 'admin'] %}
      <div class='card' style='border-top:4px solid #f59e0b;'>
        <h2 style='background:linear-gradient(135deg,#f59e0b,#ef4444);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>📝 Ajouter un devoir</h2>
        <form method='post' enctype='multipart/form-data'>
          <input type='hidden' name='form_type' value='create'>
          <label>Classe</label>
          <select name='class_id'><option value=''>Toutes les classes</option>{% for c in classes %}<option value='{{ c.id }}'>{{ c.name }}</option>{% endfor %}</select>
          <label>Matière</label>
          <select name='subject_id' required>{% for s in subjects %}<option value='{{ s.id }}'>{{ s.name }}</option>{% endfor %}</select>
          <label>Titre</label><input name='title' required>
          <label>Description</label><textarea name='description' required></textarea>
          <label>Date limite</label><input type='date' name='due_date' required>
          <label>Pièces jointes <span class='muted small'>(plusieurs fichiers possibles)</span></label>
          <input type='file' name='attachments' multiple accept='.pdf,.png,.jpg,.jpeg,.gif,.webp,.doc,.docx,.txt,.zip,.ppt,.pptx,.xls,.xlsx'>
          <button type='submit'>Publier</button>
        </form>
      </div>
      {% endif %}
      <div class='card' style='border-top:4px solid #f59e0b;'>
        <div style='display:flex;align-items:center;gap:10px;margin-bottom:16px;'>
          <span style='font-size:28px;'>📚</span>
          <h1 style='margin:0;background:linear-gradient(135deg,#f59e0b,#ef4444);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>Devoirs</h1>
        </div>
        {% for item in items %}
          <div style='border:1px solid #e5ebf5; border-radius:16px; padding:16px; margin-bottom:14px;'>
            <div style='display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap;'>
              <strong>{{ item.title }}</strong><span class='badge'>{{ item.subject_name }}</span>
            </div>
            <p>{{ item.description }}</p>
            <p class='muted'>Classe : {{ item.class_name or 'Toutes' }} · Professeur : {{ item.teacher_name }} · Date limite : {{ item.due_date }}</p>
            {% set atts = hw_attachments.get(item.id, []) %}
            {% if atts %}
              <div style='margin-top:8px;display:flex;flex-wrap:wrap;gap:8px;'>
                {% for att in atts %}
                  <a href='{{ att.url }}' target='_blank' style='display:inline-flex;align-items:center;gap:6px;background:var(--admin-box);border:1px solid var(--admin-box-border);border-radius:10px;padding:6px 12px;font-size:13px;font-weight:600;color:#1d4ed8;text-decoration:none;'>
                    📎 {{ att.name }}
                  </a>
                {% endfor %}
              </div>
            {% elif item.attachment_url %}
              <p><a href='{{ item.attachment_url }}' target='_blank'>📎 Télécharger : {{ item.attachment_name or 'pièce jointe' }}</a></p>
            {% endif %}
            {% if user.role in ['eleve', 'parent'] %}
              <form method='post' style='margin-top:10px;'>
                <input type='hidden' name='form_type' value='toggle_done'>
                <input type='hidden' name='homework_id' value='{{ item.id }}'>
                {% if item.id in done_ids %}
                  <button type='submit' style='background:linear-gradient(90deg,#059669,#10b981);'>✅ Marqué comme fait — Annuler</button>
                {% else %}
                  <button type='submit' style='background:linear-gradient(90deg,#475569,#64748b);'>☐ Marquer comme fait</button>
                {% endif %}
              </form>
            {% endif %}
            {% if user.role in ['prof', 'admin'] %}
              <p class='muted small' style='margin-top:8px;'>
                ✅ {{ completion_stats.get(item.id, 0) }} / {{ total_students }} élève(s) ont marqué ce devoir comme fait
              </p>
            {% endif %}
            {% if user.role == 'admin' %}
            <div class='admin-box'>
              <form method='post' enctype='multipart/form-data'>
                <input type='hidden' name='form_type' value='update'>
                <input type='hidden' name='homework_id' value='{{ item.id }}'>
                <label>Titre</label><input name='title' value='{{ item.title }}' required>
                <label>Description</label><textarea name='description' required>{{ item.description }}</textarea>
                <label>Date limite</label><input type='date' name='due_date' value='{{ item.due_date }}' required>
                <label>Classe</label>
                <select name='class_id'>
                  <option value=''>Toutes les classes</option>
                  {% for c in classes %}<option value='{{ c.id }}' {% if item.class_id == c.id %}selected{% endif %}>{{ c.name }}</option>{% endfor %}
                </select>
                <label>Matière</label>
                <select name='subject_id' required>
                  {% for s in subjects %}<option value='{{ s.id }}' {% if item.subject_id == s.id %}selected{% endif %}>{{ s.name }}</option>{% endfor %}
                </select>
                <label>Nouvelle pièce jointe</label><input type='file' name='attachment'>
                {% if item.attachment_url %}
                  <label><input type='checkbox' name='remove_attachment' value='1' style='width:auto; margin-right:8px;'> Supprimer la pièce jointe actuelle</label>
                {% endif %}
                <div class='actions-inline'><button type='submit'>Modifier</button></div>
              </form>
              <form method='post' onsubmit="return confirm('Supprimer ce devoir ?');" style='margin-top:10px;'>
                <input type='hidden' name='form_type' value='delete'>
                <input type='hidden' name='homework_id' value='{{ item.id }}'>
                <button type='submit' class='danger'>Supprimer</button>
              </form>
            </div>
            {% endif %}
          </div>
        {% else %}
          <p>Aucun devoir.</p>
        {% endfor %}
      </div>
    </div>
    """
    # Pièces jointes multiples
    hw_attachments = {}
    if items:
        hw_ids = [str(i["id"]) for i in items]
        if hw_ids:
            placeholders_att = ",".join(["?"] * len(hw_ids))
            atts = query_all(f"SELECT * FROM homework_attachments WHERE homework_id IN ({placeholders_att}) ORDER BY id ASC", tuple(hw_ids))
            for att in atts:
                hw_attachments.setdefault(att["homework_id"], []).append(att)

    # IDs des devoirs cochés par l'utilisateur courant
    done_ids = set()
    if user["role"] in ["eleve", "parent"]:
        done_rows = query_all(
            "SELECT homework_id FROM homework_done WHERE user_id = ?",
            (user["id"],)
        )
        done_ids = {r["homework_id"] for r in done_rows}

    # Stats de completion par devoir pour profs/admin
    completion_stats = {}
    if user["role"] in ["prof", "admin"]:
        stats_rows = query_all(
            """
            SELECT hd.homework_id, COUNT(*) AS done_count
            FROM homework_done hd
            GROUP BY hd.homework_id
            """
        )
        completion_stats = {r["homework_id"]: r["done_count"] for r in stats_rows}
        # Nombre total d'élèves
        total_students = query_one("SELECT COUNT(*) AS total FROM users WHERE role = 'eleve'")["total"]
    else:
        total_students = 0

    return render_page(content, title="Devoirs", user=user, items=items, subjects=subjects, classes=classes, done_ids=done_ids, completion_stats=completion_stats, total_students=total_students, hw_attachments=hw_attachments)


# =========================
# Emploi du temps (version hardcodée semaines A/B - Document 1)
# =========================
@app.route("/schedule")
@login_required
def schedule_page():
    semaine = request.args.get("semaine", "A").upper()
    if semaine not in ["A", "B"]:
        semaine = "A"

    edt = {
        "A": {
            "Lundi": [
                ("8h30 - 8h45", "Point travail maison", "Christelle"),
                ("8h45 - 9h15", "Sophrologie", ""),
                ("9h15 - 10h15", "Français (grammaire)", ""),
                ("10h15 - 10h30", "Récréation", ""),
                ("10h30 - 12h40", "Français (étude de textes)", ""),
                ("12h40 - 13h30", "Pause déjeuner", ""),
                ("13h30 - 15h00", "Projets interdisciplinaires", ""),
                ("15h15 - 16h45", "Arts", ""),
                ("16h45 - 17h00", "Carnet de bord / Agenda", ""),
            ],
            "Mardi": [
                ("8h30 - 9h00", "Rituels (Flow & Voix)", ""),
                ("9h00 - 12h40", "Enquêtes et jeux", "Histoire / Géo / Citoyen du monde"),
                ("10h15 - 10h30", "Récréation", ""),
                ("12h40 - 13h30", "Pause déjeuner", ""),
                ("13h30 - 14h15", "Enquêtes et jeux", ""),
                ("14h15 - 15h00", "Espagnol", "Angélique"),
                ("15h00 - 15h15", "Récréation", ""),
                ("15h15 - 16h45", "EPS", "Mathéo"),
                ("16h45 - 17h00", "Carnet de bord", ""),
            ],
        },
        "B": {
            "Lundi": [
                ("8h30 - 9h15", "Point travail + Rituels", "Christelle"),
                ("9h15 - 10h15", "Français (grammaire)", ""),
                ("10h15 - 10h30", "Récréation", ""),
                ("10h30 - 12h40", "Français (étude de textes)", ""),
                ("12h40 - 13h30", "Pause déjeuner", ""),
                ("13h30 - 15h00", "Projets interdisciplinaires", ""),
                ("15h15 - 16h45", "Arts", ""),
                ("16h45 - 17h00", "Carnet de bord / Agenda", ""),
            ],
            "Mardi": [
                ("8h30 - 9h00", "Rituels (Flow & Voix)", ""),
                ("9h00 - 12h40", "Enquêtes et jeux", ""),
                ("10h15 - 10h30", "Récréation", ""),
                ("12h40 - 13h30", "Pause déjeuner", ""),
                ("13h30 - 14h15", "Yoga", "Julie"),
                ("14h15 - 15h00", "Espagnol", "Angélique"),
                ("15h00 - 15h15", "Récréation", ""),
                ("15h15 - 16h45", "EPS", "Mathéo"),
                ("16h45 - 17h00", "Carnet de bord", ""),
            ],
        },
        "COMMUN": {
            "Jeudi": [
                ("8h30 - 10h00", "Mathématiques", ""),
                ("10h15 - 12h30", "Sciences", ""),
                ("13h30 - 15h00", "Sciences", ""),
                ("15h15 - 16h15", "Espagnol", "Angélique"),
                ("16h15 - 17h00", "Anglais", ""),
            ],
            "Vendredi": [
                ("8h30 - 10h00", "Anglais", ""),
                ("10h15 - 12h30", "Mathématiques", ""),
                ("13h30 - 15h00", "Théâtre / Travaux", "Renaud"),
                ("15h15 - 16h45", "EPS", "Mathéo"),
            ],
        },
    }

    html = BASE_TOP + NAV + """
    <style>
        .edt-wrap{
            max-width:1200px;
            margin:30px auto;
            padding:20px;
        }

        .edt-title{
            text-align:center;
            font-size:32px;
            font-weight:800;
            color:#123c7a;
            margin-bottom:10px;
        }

        .edt-subtitle{
            text-align:center;
            color:#5d6b82;
            margin-bottom:25px;
            font-size:15px;
        }

        .week-switch{
            display:flex;
            justify-content:center;
            gap:12px;
            margin-bottom:30px;
            flex-wrap:wrap;
        }

        .week-btn{
            text-decoration:none;
            padding:12px 22px;
            border-radius:14px;
            font-weight:700;
            background:linear-gradient(135deg,#e8f1ff,#d8ebff);
            color:#124a9c;
            box-shadow:0 8px 20px rgba(0,0,0,0.08);
            transition:0.2s;
        }

        .week-btn:hover{
            transform:translateY(-2px);
        }

        .week-btn.active{
            background:linear-gradient(135deg,#1f6feb,#4ea1ff);
            color:white;
        }

        .days-grid{
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
            gap:22px;
        }

        .day-card{
            background:white;
            border-radius:22px;
            overflow:hidden;
            box-shadow:0 10px 30px rgba(20, 60, 120, 0.10);
            border:1px solid #e6eef8;
        }

        .day-header{
            padding:16px 18px;
            color:white;
            font-size:20px;
            font-weight:800;
        }

        .lundi{background:linear-gradient(135deg,#3b82f6,#2563eb);}
        .mardi{background:linear-gradient(135deg,#10b981,#059669);}
        .jeudi{background:linear-gradient(135deg,#f59e0b,#d97706);}
        .vendredi{background:linear-gradient(135deg,#ef4444,#dc2626);}

        .course{
            padding:14px 16px;
            border-bottom:1px solid #edf2f7;
        }

        .course:last-child{
            border-bottom:none;
        }

        .hour{
            display:inline-block;
            font-size:13px;
            font-weight:700;
            color:#2563eb;
            background:#eef5ff;
            padding:6px 10px;
            border-radius:999px;
            margin-bottom:8px;
        }

        .subject{
            font-size:16px;
            font-weight:800;
            color:#1f2937;
            margin-bottom:4px;
        }

        .teacher{
            font-size:13px;
            color:#6b7280;
        }

        .pause .subject{
            color:#9a6700;
        }

        .recre .subject{
            color:#0f766e;
        }

        @media (max-width:700px){
            .edt-title{
                font-size:25px;
            }
            .edt-wrap{
                padding:12px;
            }
        }
    </style>

    <div class="edt-wrap">
        <div class="edt-title">Emploi du temps</div>
        <div class="edt-subtitle">
            Semaine {{ semaine }} — alternance uniquement pour le lundi et le mardi
        </div>

        <div class="week-switch">
            <a href="{{ url_for('schedule_page', semaine='A') }}" class="week-btn {% if semaine == 'A' %}active{% endif %}">Semaine A</a>
            <a href="{{ url_for('schedule_page', semaine='B') }}" class="week-btn {% if semaine == 'B' %}active{% endif %}">Semaine B</a>
        </div>

        <div class="days-grid">
            {% for jour, cours in jours_affiches %}
                {% set css = jour.lower() %}
                <div class="day-card">
                    <div class="day-header {{ css }}">{{ jour }}</div>

                    {% for heure, matiere, prof in cours %}
                        <div class="course
                            {% if 'Pause' in matiere %}pause{% endif %}
                            {% if 'Récréation' in matiere %}recre{% endif %}
                        ">
                            <div class="hour">{{ heure }}</div>
                            <div class="subject">{{ matiere }}</div>
                            {% if prof %}
                                <div class="teacher">{{ prof }}</div>
                            {% endif %}
                        </div>
                    {% endfor %}
                </div>
            {% endfor %}
        </div>
    </div>
    """

    jours_affiches = [
        ("Lundi", edt[semaine]["Lundi"]),
        ("Mardi", edt[semaine]["Mardi"]),
        ("Jeudi", edt["COMMUN"]["Jeudi"]),
        ("Vendredi", edt["COMMUN"]["Vendredi"]),
    ]

    return render_template_string(html, semaine=semaine, jours_affiches=jours_affiches, session=session, url_for=url_for)


# =========================
# Absences
# =========================
@app.route("/absences", methods=["GET", "POST"])
@login_required
def absences_page():
    user = g.user
    students = query_all("SELECT u.id, u.full_name, c.name AS class_name FROM users u LEFT JOIN classes c ON c.id=u.class_id WHERE u.role='eleve' ORDER BY u.full_name")

    if request.method == "POST":
        form_type = request.form.get("form_type", "create").strip()

        if form_type == "create":
            if user["role"] not in ["prof", "admin"]:
                flash("Accès refusé.")
                return redirect(url_for("absences_page"))
            execute_db(
                "INSERT INTO absences (student_id, teacher_id, absence_date, end_date, reason, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    request.form.get("student_id"),
                    user["id"],
                    request.form.get("absence_date"),
                    request.form.get("end_date") or None,
                    request.form.get("reason", "").strip(),
                    request.form.get("status"),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            flash("Absence enregistrée.")
            return redirect(url_for("absences_page"))

        elif form_type == "update":
            if user["role"] != "admin":
                flash("Seul l'admin peut modifier les absences.")
                return redirect(url_for("absences_page"))
            absence_id = request.form.get("absence_id")
            execute_db(
                "UPDATE absences SET student_id = ?, absence_date = ?, end_date = ?, reason = ?, status = ? WHERE id = ?",
                (
                    request.form.get("student_id"),
                    request.form.get("absence_date"),
                    request.form.get("end_date") or None,
                    request.form.get("reason", "").strip(),
                    request.form.get("status"),
                    absence_id,
                ),
            )
            flash("Absence modifiée.")
            return redirect(url_for("absences_page"))

        elif form_type == "delete":
            if user["role"] != "admin":
                flash("Seul l'admin peut supprimer les absences.")
                return redirect(url_for("absences_page"))
            execute_db("DELETE FROM absences WHERE id = ?", (request.form.get("absence_id"),))
            flash("Absence supprimée.")
            return redirect(url_for("absences_page"))

    if user["role"] == "eleve":
        rows = query_all(
            "SELECT a.*, u.full_name AS teacher_name FROM absences a JOIN users u ON u.id=a.teacher_id WHERE student_id=? ORDER BY absence_date DESC",
            (user["id"],)
        )
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
        <h2 style='background:linear-gradient(135deg,#ef4444,#f97316);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>➕ Ajouter une absence</h2>
        <form method='post'>
          <input type='hidden' name='form_type' value='create'>
          <label>Élève</label>
          <select name='student_id' required>
            {% for s in students %}
              <option value='{{ s.id }}'>{{ s.full_name }}{% if s.class_name %} - {{ s.class_name }}{% endif %}</option>
            {% endfor %}
          </select>
          <label>Date début</label>
          <input type='date' name='absence_date' required>
          <label>Date fin</label>
          <input type='date' name='end_date'>
          <label>Motif</label>
          <textarea name='reason'></textarea>
          <label>Statut</label>
          <select name='status' required>
            <option>Non justifiée</option>
            <option>Justifiée</option>
          </select>
          <button type='submit'>Enregistrer</button>
        </form>
      </div>
      {% endif %}
      <div class='card'>
        <div style='display:flex;align-items:center;gap:10px;margin-bottom:16px;'>
          <span style='font-size:28px;'>📋</span>
          <h1 style='margin:0;background:linear-gradient(135deg,#ef4444,#f97316);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>Absences</h1>
        </div>
        <table>
          <thead>
            <tr>
              {% if user.role in ['admin','prof','parent'] %}<th>Élève</th><th>Classe</th>{% endif %}
              <th>Date début</th>
              <th>Date fin</th>
              <th>Motif</th>
              <th>Statut</th>
              <th>Déclarée par</th>
            </tr>
          </thead>
          <tbody>
            {% for r in rows %}
              <tr>
                {% if user.role in ['admin','prof','parent'] %}
                  <td>{{ r.student_name }}</td>
                  <td>{{ r.class_name or '-' }}</td>
                {% endif %}
                <td>{{ r.absence_date }}</td>
                <td>{{ r.end_date or r.absence_date }}</td>
                <td>{{ r.reason or '-' }}</td>
                <td>{{ r.status }}</td>
                <td>{{ r.teacher_name }}</td>
              </tr>
              {% if user.role == 'admin' %}
              <tr><td colspan='7'>
                <div class='admin-box'>
                  <form method='post'>
                    <input type='hidden' name='form_type' value='update'>
                    <input type='hidden' name='absence_id' value='{{ r.id }}'>
                    <label>Élève</label>
                    <select name='student_id' required>
                      {% for s in students %}
                        <option value='{{ s.id }}' {% if r.student_id == s.id %}selected{% endif %}>{{ s.full_name }}</option>
                      {% endfor %}
                    </select>
                    <label>Date début</label>
                    <input type='date' name='absence_date' value='{{ r.absence_date }}' required>
                    <label>Date fin</label>
                    <input type='date' name='end_date' value='{{ r.end_date or "" }}'>
                    <label>Motif</label>
                    <textarea name='reason'>{{ r.reason or "" }}</textarea>
                    <label>Statut</label>
                    <select name='status' required>
                      <option value='Non justifiée' {% if r.status == 'Non justifiée' %}selected{% endif %}>Non justifiée</option>
                      <option value='Justifiée' {% if r.status == 'Justifiée' %}selected{% endif %}>Justifiée</option>
                    </select>
                    <div class='actions-inline'><button type='submit'>Modifier</button></div>
                  </form>
                  <form method='post' onsubmit="return confirm('Supprimer cette absence ?');" style='margin-top:10px;'>
                    <input type='hidden' name='form_type' value='delete'>
                    <input type='hidden' name='absence_id' value='{{ r.id }}'>
                    <button type='submit' class='danger'>Supprimer</button>
                  </form>
                </div>
              </td></tr>
              {% endif %}
            {% else %}
              <tr><td colspan='7'>Aucune absence.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
    """
    return render_page(content, title="Absences", user=user, students=students, rows=rows)


# =========================
# Messagerie style WhatsApp (version Document 1)
# =========================

def init_chat_tables():
    """Create group chat tables if they don't exist."""
    if USE_POSTGRES:
        execute_db("""
            CREATE TABLE IF NOT EXISTS chat_groups (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            )
        """)
        execute_db("""
            CREATE TABLE IF NOT EXISTS chat_group_members (
                id SERIAL PRIMARY KEY,
                group_id INTEGER NOT NULL REFERENCES chat_groups(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                UNIQUE(group_id, user_id)
            )
        """)
        execute_db("""
            CREATE TABLE IF NOT EXISTS chat_group_messages (
                id SERIAL PRIMARY KEY,
                group_id INTEGER NOT NULL REFERENCES chat_groups(id),
                sender_id INTEGER NOT NULL REFERENCES users(id),
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
    else:
        execute_db("""
            CREATE TABLE IF NOT EXISTS chat_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(created_by) REFERENCES users(id)
            )
        """)
        execute_db("""
            CREATE TABLE IF NOT EXISTS chat_group_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                UNIQUE(group_id, user_id),
                FOREIGN KEY(group_id) REFERENCES chat_groups(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        execute_db("""
            CREATE TABLE IF NOT EXISTS chat_group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(group_id) REFERENCES chat_groups(id),
                FOREIGN KEY(sender_id) REFERENCES users(id)
            )
        """)


@app.route("/messages", methods=["GET", "POST"])
@login_required
def messages_page():
    init_chat_tables()
    user = g.user

    # Marquer les messages comme vus
    try:
        max_msg = query_one("SELECT MAX(id) AS max_id FROM messages WHERE receiver_id = ?", (user["id"],))
        max_id = max_msg["max_id"] if max_msg and max_msg["max_id"] else 0
        existing = query_one("SELECT id FROM notif_seen WHERE user_id = ?", (user["id"],))
        if existing:
            execute_db("UPDATE notif_seen SET last_seen_message_id = MAX(last_seen_message_id, ?) WHERE user_id = ?", (max_id, user["id"]))
        else:
            execute_db("INSERT INTO notif_seen (user_id, last_seen_message_id, last_seen_grade_id) VALUES (?, ?, 0)", (user["id"], max_id))
    except Exception:
        pass

    # Contacts selon le rôle
    if user["role"] == "eleve":
        contacts = query_all(
            "SELECT id, full_name, role, profile_picture_url FROM users WHERE id != ? ORDER BY full_name",
            (user["id"],)
        )
    elif user["role"] == "parent":
        children = get_parent_children(user)
        child_ids = [child["id"] for child in children]
        if child_ids:
            placeholders = ",".join(["?"] * len(child_ids))
            contacts = query_all(
                f"SELECT id, full_name, role, profile_picture_url FROM users WHERE (role IN ('prof', 'admin') OR id IN ({placeholders})) AND id != ? ORDER BY full_name",
                tuple(child_ids) + (user["id"],),
            )
        else:
            contacts = query_all(
                "SELECT id, full_name, role, profile_picture_url FROM users WHERE role IN ('prof', 'admin') ORDER BY full_name"
            )
    else:
        contacts = query_all(
            "SELECT id, full_name, role, profile_picture_url FROM users WHERE id != ? ORDER BY full_name",
            (user["id"],)
        )

    # Groupes dont l'utilisateur est membre
    my_groups = query_all(
        """
        SELECT cg.id, cg.name, cg.created_by, cg.created_at
        FROM chat_groups cg
        JOIN chat_group_members cgm ON cgm.group_id = cg.id
        WHERE cgm.user_id = ?
        ORDER BY cg.id DESC
        """,
        (user["id"],)
    )

    # Traitement POST
    if request.method == "POST":
        action = request.form.get("action", "")

        # Envoyer message privé
        if action == "send_dm":
            receiver_id = request.form.get("receiver_id")
            body = request.form.get("body", "").strip()
            if not receiver_id or not body:
                return redirect(url_for("messages_page", chat=receiver_id))
            execute_db(
                "INSERT INTO messages (sender_id, receiver_id, subject, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (user["id"], receiver_id, "DM", body, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            log_event("Message envoyé", user=user, details="DM", entity_type="message")
            return redirect(url_for("messages_page", chat=receiver_id))

        # Envoyer message de groupe
        elif action == "send_group":
            group_id = request.form.get("group_id")
            body = request.form.get("body", "").strip()
            if not group_id or not body:
                return redirect(url_for("messages_page", group=group_id))
            # Vérifier que l'user est bien membre
            member = query_one(
                "SELECT id FROM chat_group_members WHERE group_id = ? AND user_id = ?",
                (group_id, user["id"])
            )
            if member:
                execute_db(
                    "INSERT INTO chat_group_messages (group_id, sender_id, body, created_at) VALUES (?, ?, ?, ?)",
                    (group_id, user["id"], body, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
            return redirect(url_for("messages_page", group=group_id))

        # Créer un groupe (admin/prof seulement)
        elif action == "create_group":
            if user["role"] not in ["admin", "prof"]:
                flash("Accès refusé.")
                return redirect(url_for("messages_page"))
            group_name = request.form.get("group_name", "").strip()
            member_ids = request.form.getlist("member_ids")
            if not group_name:
                flash("Nom de groupe requis.")
                return redirect(url_for("messages_page"))
            # Créer le groupe
            conn = get_conn()
            try:
                if USE_POSTGRES:
                    with conn.cursor() as cur:
                        cur.execute(
                            adapt_sql("INSERT INTO chat_groups (name, created_by, created_at) VALUES (?, ?, ?)"),
                            (group_name, user["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        )
                        cur.execute("SELECT lastval()")
                        new_group_id = cur.fetchone()[0]
                    conn.commit()
                else:
                    cur = conn.execute(
                        "INSERT INTO chat_groups (name, created_by, created_at) VALUES (?, ?, ?)",
                        (group_name, user["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    )
                    new_group_id = cur.lastrowid
                    conn.commit()
            finally:
                conn.close()
            # Ajouter le créateur comme membre
            execute_db(
                "INSERT OR IGNORE INTO chat_group_members (group_id, user_id) VALUES (?, ?)",
                (new_group_id, user["id"])
            )
            # Ajouter les membres sélectionnés
            for mid in member_ids:
                try:
                    execute_db(
                        "INSERT OR IGNORE INTO chat_group_members (group_id, user_id) VALUES (?, ?)",
                        (new_group_id, int(mid))
                    )
                except Exception:
                    pass
            flash(f"Groupe « {group_name} » créé.")
            return redirect(url_for("messages_page", group=new_group_id))

        return redirect(url_for("messages_page"))

    # Conversation active
    active_chat_user = None
    active_chat_messages = []
    active_group = None
    active_group_messages = []
    active_group_members = []

    chat_with = request.args.get("chat")
    group_with = request.args.get("group")

    if chat_with:
        active_chat_user = query_one(
            "SELECT id, full_name, role, profile_picture_url FROM users WHERE id = ?",
            (chat_with,)
        )
        if active_chat_user:
            active_chat_messages = query_all(
                """
                SELECT m.*, u.full_name AS sender_name, u.profile_picture_url AS sender_pic
                FROM messages m
                JOIN users u ON u.id = m.sender_id
                WHERE (m.sender_id = ? AND m.receiver_id = ?)
                   OR (m.sender_id = ? AND m.receiver_id = ?)
                ORDER BY m.id ASC
                """,
                (user["id"], int(chat_with), int(chat_with), user["id"])
            )

    if group_with:
        active_group = query_one("SELECT * FROM chat_groups WHERE id = ?", (group_with,))
        if active_group:
            # Vérifier membre
            is_member = query_one(
                "SELECT id FROM chat_group_members WHERE group_id = ? AND user_id = ?",
                (group_with, user["id"])
            )
            if is_member:
                active_group_messages = query_all(
                    """
                    SELECT cgm.*, u.full_name AS sender_name, u.profile_picture_url AS sender_pic
                    FROM chat_group_messages cgm
                    JOIN users u ON u.id = cgm.sender_id
                    WHERE cgm.group_id = ?
                    ORDER BY cgm.id ASC
                    """,
                    (group_with,)
                )
                active_group_members = query_all(
                    """
                    SELECT u.id, u.full_name, u.role, u.profile_picture_url
                    FROM chat_group_members cgm
                    JOIN users u ON u.id = cgm.user_id
                    WHERE cgm.group_id = ?
                    ORDER BY u.full_name
                    """,
                    (group_with,)
                )
            else:
                active_group = None

    # Derniers messages par contact pour la sidebar
    contact_last_msg = {}
    all_dms = query_all(
        """
        SELECT m.sender_id, m.receiver_id, m.body, m.created_at
        FROM messages m
        WHERE m.sender_id = ? OR m.receiver_id = ?
        ORDER BY m.id DESC
        """,
        (user["id"], user["id"])
    )
    for msg in all_dms:
        other_id = msg["receiver_id"] if msg["sender_id"] == user["id"] else msg["sender_id"]
        if other_id not in contact_last_msg:
            contact_last_msg[other_id] = msg["body"][:40]

    # Dernier message de groupe
    group_last_msg = {}
    for grp in my_groups:
        last = query_one(
            "SELECT body FROM chat_group_messages WHERE group_id = ? ORDER BY id DESC LIMIT 1",
            (grp["id"],)
        )
        group_last_msg[grp["id"]] = last["body"][:40] if last else "Aucun message"

    # Template WhatsApp-style
    page_template = BASE_TOP + NAV + """
<style>
  .wa-wrap {
    display: flex;
    height: calc(100vh - 62px);
    overflow: hidden;
    background: #f0f2f5;
  }
  /* Sidebar */
  .wa-sidebar {
    width: 360px;
    min-width: 280px;
    background: #fff;
    border-right: 1px solid #e9edef;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .wa-sidebar-header {
    background: #f0f2f5;
    padding: 12px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid #e9edef;
  }
  .wa-sidebar-header h2 { margin: 0; font-size: 19px; font-weight: 700; color: #1f2937; }
  .wa-sidebar-tabs {
    display: flex;
    background: #fff;
    border-bottom: 1px solid #e9edef;
  }
  .wa-tab {
    flex: 1;
    padding: 10px;
    text-align: center;
    font-size: 13px;
    font-weight: 600;
    color: #8696a0;
    cursor: pointer;
    border-bottom: 3px solid transparent;
    transition: all 0.2s;
  }
  .wa-tab.active { color: #1d4ed8; border-bottom-color: #1d4ed8; }
  .wa-list { flex: 1; overflow-y: auto; }
  .wa-contact-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    cursor: pointer;
    border-bottom: 1px solid #f0f2f5;
    transition: background 0.15s;
    text-decoration: none;
    color: inherit;
  }
  .wa-contact-item:hover, .wa-contact-item.active { background: #f0f2f5; }
  .wa-avatar {
    width: 48px; height: 48px; border-radius: 50%;
    object-fit: cover; flex-shrink: 0;
    background: #dbeafe;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; font-weight: 700; color: #1d4ed8;
    overflow: hidden;
  }
  .wa-avatar img { width: 100%; height: 100%; object-fit: cover; border-radius: 50%; }
  .wa-contact-info { flex: 1; min-width: 0; }
  .wa-contact-name { font-weight: 600; font-size: 15px; color: #1f2937; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .wa-contact-preview { font-size: 13px; color: #8696a0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
  .wa-group-badge { font-size: 11px; background: #e0f2fe; color: #0369a1; border-radius: 999px; padding: 2px 7px; font-weight: 700; }
  /* Main chat */
  .wa-main {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .wa-empty {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    color: #8696a0;
    background: #f8fafc;
  }
  .wa-empty-icon { font-size: 72px; margin-bottom: 16px; opacity: 0.5; }
  .wa-chat-header {
    background: #f0f2f5;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
    border-bottom: 1px solid #e9edef;
  }
  .wa-chat-header-info { flex: 1; }
  .wa-chat-header-name { font-weight: 700; font-size: 16px; color: #1f2937; }
  .wa-chat-header-sub { font-size: 12px; color: #8696a0; }
  .wa-messages-area {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    background: #efeae2;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='60' height='60'%3E%3Cpath d='M0 0h60v60H0z' fill='%23e5ddd5'/%3E%3Cpath d='M30 0v60M0 30h60' stroke='%23d4c5b2' stroke-width='0.5'/%3E%3C/svg%3E");
  }
  .wa-msg {
    display: flex;
    margin-bottom: 6px;
  }
  .wa-msg.mine { justify-content: flex-end; }
  .wa-msg.theirs { justify-content: flex-start; }
  .wa-bubble {
    max-width: 65%;
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 14px;
    line-height: 1.5;
    word-break: break-word;
    box-shadow: 0 1px 2px rgba(0,0,0,0.12);
  }
  .wa-msg.mine .wa-bubble { background: #d9fdd3; border-top-right-radius: 2px; }
  .wa-msg.theirs .wa-bubble { background: #fff; border-top-left-radius: 2px; }
  .wa-bubble-sender { font-size: 12px; font-weight: 700; color: #1d4ed8; margin-bottom: 3px; }
  .wa-bubble-time { font-size: 11px; color: #8696a0; margin-top: 4px; text-align: right; }
  .wa-input-bar {
    background: #f0f2f5;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 10px;
    border-top: 1px solid #e9edef;
  }
  .wa-input-bar input, .wa-input-bar textarea {
    flex: 1;
    border: none;
    border-radius: 24px;
    padding: 10px 16px;
    font-size: 15px;
    outline: none;
    background: #fff;
    resize: none;
    max-height: 120px;
    margin: 0;
    box-shadow: none;
  }
  .wa-send-btn {
    width: 44px; height: 44px;
    border-radius: 50%;
    background: #1d4ed8;
    border: none;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
    box-shadow: none;
    padding: 0;
  }
  .wa-send-btn:hover { background: #1e40af; transform: none; }
  .wa-send-btn svg { width: 22px; height: 22px; fill: white; }
  /* Modal groupe */
  .wa-modal-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.5); z-index: 200;
    align-items: center; justify-content: center;
  }
  .wa-modal-overlay.show { display: flex; }
  .wa-modal {
    background: #fff; border-radius: 20px;
    padding: 28px; width: 500px; max-width: 95vw;
    max-height: 80vh; overflow-y: auto;
    box-shadow: 0 20px 60px rgba(0,0,0,0.25);
  }
  .wa-modal h3 { margin-top: 0; }
  .wa-modal input, .wa-modal select { margin-bottom: 12px; }
  .wa-new-group-btn {
    background: linear-gradient(90deg, #1d4ed8, #2563eb);
    color: white; border: none; padding: 8px 14px;
    border-radius: 20px; font-weight: 700; cursor: pointer;
    font-size: 13px; display: flex; align-items: center; gap: 6px;
    box-shadow: none;
  }
  .wa-new-group-btn:hover { transform: none; background: #1e40af; }
  .member-check-list { max-height: 200px; overflow-y: auto; border: 1px solid #e5e7eb; border-radius: 12px; padding: 8px; margin-bottom: 14px; }
  .member-check-item { display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 8px; cursor: pointer; }
  .member-check-item:hover { background: #f0f2f5; }
  .member-check-item input[type=checkbox] { width: 18px; height: 18px; margin: 0; cursor: pointer; }
  .group-members-list { font-size: 13px; color: #8696a0; }
  @media (max-width: 700px) {
    .wa-sidebar { width: 100%; min-width: unset; display: {% if active_chat_user or active_group %}none{% else %}flex{% endif %}; }
    .wa-main { display: {% if active_chat_user or active_group %}flex{% else %}none{% endif %}; }
    .wa-wrap { height: calc(100vh - 56px); }
  }
</style>

<div class='wa-wrap'>
  <!-- SIDEBAR -->
  <div class='wa-sidebar'>
    <div class='wa-sidebar-header'>
      <h2 style='margin:0;background:linear-gradient(135deg,#1d4ed8,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>💬 Messagerie</h2>
      {% if user.role in ['admin', 'prof'] %}
      <button class='wa-new-group-btn' onclick="document.getElementById('groupModal').classList.add('show')">
        <svg viewBox='0 0 24 24' width='16' height='16' fill='white'><path d='M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z'/></svg>
        Nouveau groupe
      </button>
      {% endif %}
    </div>
    <div class='wa-sidebar-tabs'>
      <div class='wa-tab {% if not active_group %}active{% endif %}' onclick="showTab('contacts')">Contacts</div>
      <div class='wa-tab {% if active_group %}active{% endif %}' onclick="showTab('groups')">Groupes</div>
    </div>
    <div class='wa-list' id='tab-contacts' style='display:{% if active_group %}none{% else %}block{% endif %}'>
      {% for c in contacts %}
      <a href='{{ url_for("messages_page") }}?chat={{ c.id }}' class='wa-contact-item {% if active_chat_user and active_chat_user.id == c.id %}active{% endif %}'>
        <div class='wa-avatar'>
          {% if c.profile_picture_url %}
            <img src='{{ c.profile_picture_url }}' alt=''>
          {% else %}
            {{ c.full_name[:1].upper() }}
          {% endif %}
        </div>
        <div class='wa-contact-info'>
          <div class='wa-contact-name'>{{ c.full_name }}</div>
          <div class='wa-contact-preview'>
            {% if contact_last_msg.get(c.id) %}
              {{ contact_last_msg[c.id] }}
            {% else %}
              <span style='color:#c7d2fe;font-style:italic;'>{{ c.role }}</span>
            {% endif %}
          </div>
        </div>
      </a>
      {% else %}
        <p style='padding:16px; color:#8696a0; font-size:14px;'>Aucun contact disponible.</p>
      {% endfor %}
    </div>
    <div class='wa-list' id='tab-groups' style='display:{% if active_group %}block{% else %}none{% endif %}'>
      {% for grp in my_groups %}
      <a href='{{ url_for("messages_page") }}?group={{ grp.id }}' class='wa-contact-item {% if active_group and active_group.id == grp.id %}active{% endif %}'>
        <div class='wa-avatar' style='background:#ede9fe; color:#7c3aed;'>👥</div>
        <div class='wa-contact-info'>
          <div class='wa-contact-name'>
            {{ grp.name }}
            <span class='wa-group-badge'>groupe</span>
          </div>
          <div class='wa-contact-preview'>{{ group_last_msg.get(grp.id, 'Aucun message') }}</div>
        </div>
      </a>
      {% else %}
        <p style='padding:16px; color:#8696a0; font-size:14px;'>Aucun groupe pour le moment.</p>
      {% endfor %}
    </div>
  </div>

  <!-- MAIN CHAT -->
  <div class='wa-main'>
    {% if active_chat_user %}
      <!-- Conversation privée -->
      <div class='wa-chat-header'>
        <div class='wa-avatar' style='width:40px;height:40px;font-size:16px;'>
          {% if active_chat_user.profile_picture_url %}
            <img src='{{ active_chat_user.profile_picture_url }}' alt=''>
          {% else %}
            {{ active_chat_user.full_name[:1].upper() }}
          {% endif %}
        </div>
        <div class='wa-chat-header-info'>
          <div class='wa-chat-header-name'>{{ active_chat_user.full_name }}</div>
          <div class='wa-chat-header-sub'>{{ active_chat_user.role }}</div>
        </div>
      </div>
      <div class='wa-messages-area' id='msgArea'>
        {% for m in active_chat_messages %}
        <div class='wa-msg {% if m.sender_id == user.id %}mine{% else %}theirs{% endif %}'>
          <div class='wa-bubble'>
            {% if m.sender_id != user.id %}
              <div class='wa-bubble-sender'>{{ m.sender_name }}</div>
            {% endif %}
            {{ m.body }}
            <div class='wa-bubble-time'>{{ m.created_at[11:16] if m.created_at|length > 10 else m.created_at }}</div>
          </div>
        </div>
        {% else %}
        <div style='text-align:center; color:#8696a0; margin-top:40px; font-size:14px;'>
          Début de la conversation avec {{ active_chat_user.full_name }} 👋
        </div>
        {% endfor %}
      </div>
      <form class='wa-input-bar' method='post'>
        <input type='hidden' name='action' value='send_dm'>
        <input type='hidden' name='receiver_id' value='{{ active_chat_user.id }}'>
        <input name='body' placeholder='Écris un message...' required autocomplete='off' id='dmInput'>
        <button type='submit' class='wa-send-btn'>
          <svg viewBox='0 0 24 24'><path d='M2.01 21L23 12 2.01 3 2 10l15 2-15 2z'/></svg>
        </button>
      </form>

    {% elif active_group %}
      <!-- Conversation de groupe -->
      <div class='wa-chat-header'>
        <div class='wa-avatar' style='width:40px;height:40px;font-size:18px;background:#ede9fe;color:#7c3aed;'>👥</div>
        <div class='wa-chat-header-info'>
          <div class='wa-chat-header-name'>{{ active_group.name }}</div>
          <div class='wa-chat-header-sub group-members-list'>
            {{ active_group_members | map(attribute='full_name') | join(', ') }}
          </div>
        </div>
      </div>
      <div class='wa-messages-area' id='msgArea'>
        {% for m in active_group_messages %}
        <div class='wa-msg {% if m.sender_id == user.id %}mine{% else %}theirs{% endif %}'>
          <div class='wa-bubble'>
            {% if m.sender_id != user.id %}
              <div class='wa-bubble-sender'>{{ m.sender_name }}</div>
            {% endif %}
            {{ m.body }}
            <div class='wa-bubble-time'>{{ m.created_at[11:16] if m.created_at|length > 10 else m.created_at }}</div>
          </div>
        </div>
        {% else %}
        <div style='text-align:center; color:#8696a0; margin-top:40px; font-size:14px;'>
          Début du groupe « {{ active_group.name }} » 🎉
        </div>
        {% endfor %}
      </div>
      <form class='wa-input-bar' method='post'>
        <input type='hidden' name='action' value='send_group'>
        <input type='hidden' name='group_id' value='{{ active_group.id }}'>
        <input name='body' placeholder='Écris un message dans le groupe...' required autocomplete='off'>
        <button type='submit' class='wa-send-btn'>
          <svg viewBox='0 0 24 24' fill='white'><path d='M2.01 21L23 12 2.01 3 2 10l15 2-15 2z'/></svg>
        </button>
      </form>

    {% else %}
      <!-- Écran vide -->
      <div class='wa-empty'>
        <div class='wa-empty-icon'>💬</div>
        <h2 style='color:#3d4043; font-size:22px; margin-bottom:8px;'>Renote Messagerie</h2>
        <p style='font-size:15px;'>Sélectionne un contact ou un groupe pour commencer à discuter</p>
      </div>
    {% endif %}
  </div>
</div>

{% if user.role in ['admin', 'prof'] %}
<!-- Modal création de groupe -->
<div class='wa-modal-overlay' id='groupModal'>
  <div class='wa-modal'>
    <h3>🟣 Créer un groupe</h3>
    <form method='post'>
      <input type='hidden' name='action' value='create_group'>
      <label>Nom du groupe</label>
      <input name='group_name' placeholder='Ex: Classe 6A Maths' required>
      <label>Membres à ajouter</label>
      <div class='member-check-list'>
        {% for c in contacts %}
        <label class='member-check-item'>
          <input type='checkbox' name='member_ids' value='{{ c.id }}'>
          <div class='wa-avatar' style='width:32px;height:32px;font-size:13px;'>
            {% if c.profile_picture_url %}
              <img src='{{ c.profile_picture_url }}' alt=''>
            {% else %}
              {{ c.full_name[:1].upper() }}
            {% endif %}
          </div>
          <span>{{ c.full_name }} <span style='color:#8696a0;font-size:12px;'>({{ c.role }})</span></span>
        </label>
        {% endfor %}
      </div>
      <div style='display:flex;gap:10px;'>
        <button type='submit' style='flex:1;'>Créer le groupe</button>
        <button type='button' class='secondary' onclick="document.getElementById('groupModal').classList.remove('show')" style='flex:1;'>Annuler</button>
      </div>
    </form>
  </div>
</div>
{% endif %}

<script>
function showTab(tab) {
  document.getElementById('tab-contacts').style.display = tab === 'contacts' ? 'block' : 'none';
  document.getElementById('tab-groups').style.display = tab === 'groups' ? 'block' : 'none';
  document.querySelectorAll('.wa-tab').forEach((el, i) => {
    el.classList.toggle('active', (tab === 'contacts' && i === 0) || (tab === 'groups' && i === 1));
  });
}
// Scroll to bottom of messages
const msgArea = document.getElementById('msgArea');
if (msgArea) msgArea.scrollTop = msgArea.scrollHeight;
// Enter to send
const dmInput = document.getElementById('dmInput');
if (dmInput) {
  dmInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.form.submit(); }
  });
}
</script>
</body></html>
"""
    return render_template_string(
        page_template,
        title="Messagerie",
        user=user,
        contacts=contacts,
        my_groups=my_groups,
        active_chat_user=active_chat_user,
        active_chat_messages=active_chat_messages,
        active_group=active_group,
        active_group_messages=active_group_messages,
        active_group_members=active_group_members,
        contact_last_msg=contact_last_msg,
        group_last_msg=group_last_msg,
        session=session,
        url_for=url_for,
    )


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
        form_type = request.form.get("form_type", "").strip()

        if form_type == "create":
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
                    "INSERT INTO users (username, password, role, full_name, class_id, child_id, child_id_2, profile_picture, profile_picture_url, created_at, last_login_at, login_count) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, 0)",
                    (username, generate_password_hash(password), role, full_name, class_id, child_id, child_id_2, current_timestamp()),
                )
                created_user = query_one("SELECT id, username, role FROM users WHERE username = ?", (username,))
                log_event("Utilisateur ajouté", user=user, details=f"Création du compte {username} ({role})", entity_type="user", entity_id=created_user['id'] if created_user else None)
                flash("Utilisateur ajouté.")
            except Exception:
                flash("Nom d'utilisateur déjà utilisé.")
            return redirect(url_for("manage_users"))

        elif form_type == "update":
            target_user_id = request.form.get("user_id")
            target_user = query_one("SELECT * FROM users WHERE id = ?", (target_user_id,))
            if not target_user:
                flash("Utilisateur introuvable.")
                return redirect(url_for("manage_users"))
            if target_user["role"] == "admin" and user["role"] != "admin":
                flash("Seul l'admin peut modifier un compte admin.")
                return redirect(url_for("manage_users"))

            new_username = request.form.get("edit_username", "").strip()
            new_full_name = request.form.get("edit_full_name", "").strip()
            new_role = request.form.get("edit_role", "").strip()
            new_class_id = request.form.get("edit_class_id") or None
            new_child_id = request.form.get("edit_child_id") or None
            new_child_id_2 = request.form.get("edit_child_id_2") or None
            new_password = request.form.get("reset_password", "").strip()

            if not new_username or not new_full_name or new_role not in ["admin", "prof", "eleve", "parent"]:
                flash("Champs invalides.")
                return redirect(url_for("manage_users"))
            if user["role"] == "prof" and new_role == "admin":
                flash("Un professeur ne peut pas promouvoir en admin.")
                return redirect(url_for("manage_users"))
            if new_role == "parent":
                if not new_child_id and not new_child_id_2:
                    flash("Un parent doit être lié à au moins un élève.")
                    return redirect(url_for("manage_users"))
                if new_child_id and new_child_id_2 and new_child_id == new_child_id_2:
                    flash("Tu ne peux pas choisir deux fois le même enfant.")
                    return redirect(url_for("manage_users"))
                new_class_id = None
            else:
                new_child_id = None
                new_child_id_2 = None

            try:
                execute_db(
                    "UPDATE users SET username = ?, full_name = ?, role = ?, class_id = ?, child_id = ?, child_id_2 = ? WHERE id = ?",
                    (new_username, new_full_name, new_role, new_class_id, new_child_id, new_child_id_2, target_user_id),
                )
                if new_password:
                    execute_db("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(new_password), target_user_id))
                log_event("Utilisateur modifié", user=user, details=f"Modification du compte {new_username} ({new_role})", entity_type="user", entity_id=target_user_id)
                flash("Utilisateur modifié.")
            except Exception:
                flash("Nom d'utilisateur déjà utilisé ou modification impossible.")
            return redirect(url_for("manage_users"))

        elif form_type == "delete":
            target_user_id = request.form.get("user_id")
            target_user = query_one("SELECT * FROM users WHERE id = ?", (target_user_id,))
            if not target_user:
                flash("Utilisateur introuvable.")
                return redirect(url_for("manage_users"))
            if str(target_user["username"]) == "admin":
                flash("Impossible de supprimer le compte admin.")
                return redirect(url_for("manage_users"))
            if str(target_user["id"]) == str(user["id"]):
                flash("Tu ne peux pas supprimer ton propre compte.")
                return redirect(url_for("manage_users"))
            if target_user["role"] == "admin" and user["role"] != "admin":
                flash("Seul l'admin peut supprimer un compte admin.")
                return redirect(url_for("manage_users"))
            if target_user.get("profile_picture"):
                delete_from_cloudinary(target_user["profile_picture"], resource_type="image")
            execute_db("DELETE FROM users WHERE id = ?", (target_user_id,))
            log_event("Utilisateur supprimé", user=user, details=f"Suppression du compte {target_user['username']}", entity_type="user", entity_id=target_user_id)
            flash("Utilisateur supprimé.")
            return redirect(url_for("manage_users"))

    users = query_all(
        """
        SELECT u.id, u.username, u.full_name, u.role, u.profile_picture, u.profile_picture_url, u.class_id, u.child_id, u.child_id_2,
               c.name AS class_name, child.full_name AS child_name, child2.full_name AS child_name_2
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
        <div style='display:flex;align-items:center;gap:10px;margin-bottom:16px;'>
          <span style='font-size:28px;'>👥</span>
          <h1 style='margin:0;background:linear-gradient(135deg,#6366f1,#8b5cf6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>Créer un compte</h1>
        </div>
        <form method='post' autocomplete='off'>
          <input type='hidden' name='form_type' value='create'>
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
          <thead><tr><th>ID</th><th>Photo</th><th>Nom</th><th>Utilisateur</th><th>Rôle</th><th>Classe</th><th>Enfant 1</th><th>Enfant 2</th></tr></thead>
          <tbody>
            {% for u in users %}
            <tr>
              <td>{{ u.id }}</td>
              <td>
                {% if u.profile_picture_url %}
                  <img src='{{ u.profile_picture_url }}' class='avatar' alt='Photo'>
                {% else %}
                  <div class='avatar' style='display:flex; align-items:center; justify-content:center; color:#1d4ed8; font-size:18px; font-weight:800;'>{{ u.full_name[:1] }}</div>
                {% endif %}
              </td>
              <td>{{ u.full_name }}</td><td>{{ u.username }}</td><td>{{ u.role }}</td>
              <td>{{ u.class_name or '-' }}</td><td>{{ u.child_name or '-' }}</td><td>{{ u.child_name_2 or '-' }}</td>
            </tr>
            <tr><td colspan='8'>
              <div class='admin-box'>
                <form method='post'>
                  <input type='hidden' name='form_type' value='update'>
                  <input type='hidden' name='user_id' value='{{ u.id }}'>
                  <label>Nom complet</label><input name='edit_full_name' value='{{ u.full_name }}' required>
                  <label>Nom d'utilisateur</label><input name='edit_username' value='{{ u.username }}' required>
                  <label>Rôle</label>
                  <select name='edit_role' required>
                    <option value='eleve' {% if u.role == 'eleve' %}selected{% endif %}>Élève</option>
                    <option value='prof' {% if u.role == 'prof' %}selected{% endif %}>Professeur</option>
                    <option value='parent' {% if u.role == 'parent' %}selected{% endif %}>Parent</option>
                    {% if user.role == 'admin' %}<option value='admin' {% if u.role == 'admin' %}selected{% endif %}>Admin</option>{% endif %}
                  </select>
                  <label>Classe</label>
                  <select name='edit_class_id'><option value=''>Aucune</option>{% for c in classes %}<option value='{{ c.id }}' {% if u.class_id == c.id %}selected{% endif %}>{{ c.name }}</option>{% endfor %}</select>
                  <label>Enfant lié 1</label>
                  <select name='edit_child_id'><option value=''>Aucun</option>{% for s in students %}<option value='{{ s.id }}' {% if u.child_id == s.id %}selected{% endif %}>{{ s.full_name }}</option>{% endfor %}</select>
                  <label>Enfant lié 2</label>
                  <select name='edit_child_id_2'><option value=''>Aucun</option>{% for s in students %}<option value='{{ s.id }}' {% if u.child_id_2 == s.id %}selected{% endif %}>{{ s.full_name }}</option>{% endfor %}</select>
                  <label>Réinitialiser le mot de passe</label>
                  <input name='reset_password' placeholder='Laisse vide pour ne pas changer'>
                  <div class='actions-inline'><button type='submit'>Modifier</button></div>
                </form>
                <form method='post' onsubmit="return confirm('Supprimer ce compte ?');" style='margin-top:10px;'>
                  <input type='hidden' name='form_type' value='delete'>
                  <input type='hidden' name='user_id' value='{{ u.id }}'>
                  <button type='submit' class='danger'>Supprimer</button>
                </form>
              </div>
            </td></tr>
            {% endfor %}
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
                execute_db(
                    "INSERT INTO classes (name) VALUES (?)" if form_type == "class" else "INSERT INTO subjects (name) VALUES (?)",
                    (name,),
                )
                flash("Classe ajoutée." if form_type == "class" else "Matière ajoutée.")
            except Exception:
                flash("Ce nom existe déjà.")
            return redirect(url_for("manage_school"))

        if form_type == "delete_class":
            class_id = request.form.get("class_id")
            if (query_one("SELECT COUNT(*) AS t FROM users WHERE class_id = ?", (class_id,))["t"] or
                query_one("SELECT COUNT(*) AS t FROM homework WHERE class_id = ?", (class_id,))["t"] or
                query_one("SELECT COUNT(*) AS t FROM schedules WHERE class_id = ?", (class_id,))["t"]):
                flash("Impossible de supprimer cette classe : elle est encore utilisée.")
                return redirect(url_for("manage_school"))
            execute_db("DELETE FROM classes WHERE id = ?", (class_id,))
            flash("Classe supprimée.")
            return redirect(url_for("manage_school"))

        if form_type == "delete_subject":
            subject_id = request.form.get("subject_id")
            if (query_one("SELECT COUNT(*) AS t FROM grades WHERE subject_id = ?", (subject_id,))["t"] or
                query_one("SELECT COUNT(*) AS t FROM homework WHERE subject_id = ?", (subject_id,))["t"] or
                query_one("SELECT COUNT(*) AS t FROM schedules WHERE subject_id = ?", (subject_id,))["t"]):
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
        <form method='post'>
          <input type='hidden' name='form_type' value='class'>
          <label>Nom de la classe</label><input name='name' placeholder='6A' required>
          <button type='submit'>Ajouter la classe</button>
        </form>
      </div>
      <div class='card'>
        <h2>Ajouter une matière</h2>
        <form method='post'>
          <input type='hidden' name='form_type' value='subject'>
          <label>Nom de la matière</label><input name='name' placeholder='Physique' required>
          <button type='submit'>Ajouter la matière</button>
        </form>
      </div>
    </div>
    <div class='grid' style='margin-top:18px;'>
      <div class='card'>
        <h2>Classes</h2>
        {% for c in classes %}
          <div style='display:flex; justify-content:space-between; align-items:center; gap:12px; padding:12px 0; border-bottom:1px solid #eef3fb;'>
            <strong>{{ c.name }}</strong>
            <form method='post' style='margin:0;'>
              <input type='hidden' name='form_type' value='delete_class'>
              <input type='hidden' name='class_id' value='{{ c.id }}'>
              <button type='submit' class='danger'>Supprimer</button>
            </form>
          </div>
        {% else %}<p class='muted'>Aucune classe.</p>{% endfor %}
      </div>
      <div class='card'>
        <h2>Matières</h2>
        {% for s in subjects %}
          <div style='display:flex; justify-content:space-between; align-items:center; gap:12px; padding:12px 0; border-bottom:1px solid #eef3fb;'>
            <strong>{{ s.name }}</strong>
            <form method='post' style='margin:0;'>
              <input type='hidden' name='form_type' value='delete_subject'>
              <input type='hidden' name='subject_id' value='{{ s.id }}'>
              <button type='submit' class='danger'>Supprimer</button>
            </form>
          </div>
        {% else %}<p class='muted'>Aucune matière.</p>{% endfor %}
      </div>
    </div>
    """
    return render_page(content, title="École", classes=classes, subjects=subjects)


# =========================
# Signalements
# =========================
@app.route("/signalement", methods=["GET", "POST"])
@login_required
def signalement_page():
    user = g.user

    if request.method == "POST":
        message = request.form.get("message", "").strip()
        if not message:
            flash("Écris le problème rencontré.")
            return redirect(url_for("signalement_page"))

        execute_db(
            "INSERT INTO reports (user_id, username, role, message, status, admin_note, created_at, updated_at, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"], user["username"], user["role"], message, "Nouveau", "", current_timestamp(), current_timestamp(), None),
        )
        log_event("Signalement envoyé", user=user, details=message[:120], entity_type="report")
        flash("Ton signalement a bien été envoyé.")
        return redirect(url_for("signalement_page"))

    my_report_count = scalar("SELECT COUNT(*) AS total FROM reports WHERE user_id = ?", (user["id"],), 0)
    content = """
    <div class='grid'>
      <div class='card'>
        <div style='display:flex;align-items:center;gap:10px;margin-bottom:16px;'>
          <span style='font-size:28px;'>🚨</span>
          <h1 style='margin:0;background:linear-gradient(135deg,#ef4444,#dc2626);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>Signaler un problème</h1>
        </div>
        <p class='muted'>Explique le bug, le problème ou l'amélioration que tu veux proposer. Les autres utilisateurs ne voient pas les signalements.</p>
        <form method='post'>
          <label>Décris le problème</label>
          <textarea name='message' required placeholder='Exemple : la page notes ne charge pas sur téléphone, un bouton ne fonctionne pas, etc.'></textarea>
          <button type='submit'>Envoyer le signalement</button>
        </form>
      </div>
      <div class='card'>
        <h2>Infos</h2>
        <p><span class='badge'>Privé</span> ton signalement est visible seulement par l'administration.</p>
        <p><span class='badge'>Utile</span> plus tu expliques précisément, plus ce sera facile à corriger.</p>
        <p><span class='badge'>Total</span> tu as envoyé {{ my_report_count }} signalement(s) au total.</p>
        {% if user.role == 'admin' %}
          <p style='margin-top:14px;'><a href='{{ url_for("admin_panel") }}'>Ouvrir l'espace administration</a></p>
        {% endif %}
      </div>
    </div>
    """
    return render_page(content, title="Signalement", user=user, my_report_count=my_report_count)


# =========================
# Admin Panel
# =========================
@app.route("/admin-panel", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_panel():
    user = g.user

    if request.method == "POST":
        form_type = request.form.get("form_type", "").strip()

        if form_type == "update_report":
            report_id = request.form.get("report_id")
            status = request.form.get("status", "Nouveau").strip() or "Nouveau"
            admin_note = request.form.get("admin_note", "").strip()
            resolved_at = current_timestamp() if status == "Résolu" else None
            execute_db(
                "UPDATE reports SET status = ?, admin_note = ?, updated_at = ?, resolved_at = ? WHERE id = ?",
                (status, admin_note, current_timestamp(), resolved_at, report_id),
            )
            log_event("Signalement mis à jour", user=user, details=f"Signalement #{report_id} -> {status}", entity_type="report", entity_id=report_id)
            flash("Signalement mis à jour.")
            return redirect(url_for("admin_panel"))

    totals = {
        "users": scalar("SELECT COUNT(*) AS total FROM users"),
        "admins": scalar("SELECT COUNT(*) AS total FROM users WHERE role = 'admin'"),
        "profs": scalar("SELECT COUNT(*) AS total FROM users WHERE role = 'prof'"),
        "eleves": scalar("SELECT COUNT(*) AS total FROM users WHERE role = 'eleve'"),
        "parents": scalar("SELECT COUNT(*) AS total FROM users WHERE role = 'parent'"),
        "grades": scalar("SELECT COUNT(*) AS total FROM grades"),
        "homework": scalar("SELECT COUNT(*) AS total FROM homework"),
        "schedules": scalar("SELECT COUNT(*) AS total FROM schedules"),
        "absences": scalar("SELECT COUNT(*) AS total FROM absences"),
        "messages": scalar("SELECT COUNT(*) AS total FROM messages"),
        "general_info": scalar("SELECT COUNT(*) AS total FROM general_info"),
        "reports": scalar("SELECT COUNT(*) AS total FROM reports"),
        "reports_open": scalar("SELECT COUNT(*) AS total FROM reports WHERE status IS NULL OR status != 'Résolu'"),
        "logs": scalar("SELECT COUNT(*) AS total FROM activity_logs"),
        "logins_total": scalar("SELECT COALESCE(SUM(login_count), 0) AS total FROM users"),
    }

    recent_users = query_all(
        "SELECT id, full_name, username, role, class_id, created_at, last_login_at, login_count FROM users ORDER BY COALESCE(created_at, '0000-00-00 00:00:00') DESC, id DESC LIMIT 12"
    )
    role_stats = query_all("SELECT role, COUNT(*) AS total FROM users GROUP BY role ORDER BY total DESC, role ASC")
    class_stats = query_all(
        """
        SELECT COALESCE(c.name, 'Sans classe') AS class_name, COUNT(*) AS total
        FROM users u
        LEFT JOIN classes c ON c.id = u.class_id
        WHERE u.role = 'eleve'
        GROUP BY COALESCE(c.name, 'Sans classe')
        ORDER BY total DESC, class_name ASC
        """
    )
    recent_logs = query_all("SELECT * FROM activity_logs ORDER BY id DESC LIMIT 80")
    recent_reports = query_all("SELECT * FROM reports ORDER BY id DESC LIMIT 30")
    recent_messages = query_all(
        """
        SELECT m.subject, m.created_at, s.full_name AS sender_name, r.full_name AS receiver_name
        FROM messages m
        JOIN users s ON s.id = m.sender_id
        JOIN users r ON r.id = m.receiver_id
        ORDER BY m.id DESC
        LIMIT 10
        """
    )
    recent_homework = query_all(
        """
        SELECT h.title, h.due_date, h.created_at, u.full_name AS teacher_name
        FROM homework h
        JOIN users u ON u.id = h.teacher_id
        ORDER BY h.id DESC
        LIMIT 10
        """
    )

    content = """
    <div class='hero'>
      <span class='badge'>ADMIN</span>
      <h1 style='margin-top:12px;'>Espace administration</h1>
      <p class='muted'>Vue globale du site, statistiques, activité récente, nouveaux comptes, signalements et suivi de l'utilisation.</p>
    </div>

    <div class='grid' style='margin-top:18px;'>
      <div class='card' style='border-top:4px solid #1d4ed8;'><div class='muted small'>👤 Utilisateurs</div><div class='metric' style='color:#1d4ed8;'>{{ totals.users }}</div></div>
      <div class='card' style='border-top:4px solid #10b981;'><div class='muted small'>🎒 Élèves</div><div class='metric' style='color:#10b981;'>{{ totals.eleves }}</div></div>
      <div class='card' style='border-top:4px solid #6366f1;'><div class='muted small'>👨‍🏫 Profs</div><div class='metric' style='color:#6366f1;'>{{ totals.profs }}</div></div>
      <div class='card' style='border-top:4px solid #f59e0b;'><div class='muted small'>👨‍👩‍👧 Parents</div><div class='metric' style='color:#f59e0b;'>{{ totals.parents }}</div></div>
      <div class='card' style='border-top:4px solid #ef4444;'><div class='muted small'>🚨 Signalements</div><div class='metric' style='color:#ef4444;'>{{ totals.reports }}</div></div>
      <div class='card' style='border-top:4px solid #dc2626;'><div class='muted small'>🔴 Ouverts</div><div class='metric' style='color:#dc2626;'>{{ totals.reports_open }}</div></div>
      <div class='card' style='border-top:4px solid #06b6d4;'><div class='muted small'>💬 Messages</div><div class='metric' style='color:#06b6d4;'>{{ totals.messages }}</div></div>
      <div class='card' style='border-top:4px solid #8b5cf6;'><div class='muted small'>🔑 Connexions</div><div class='metric' style='color:#8b5cf6;'>{{ totals.logins_total }}</div></div>
    </div>

    <div class='grid' style='margin-top:18px;'>
      <div class='card'>
        <h2>Contenu du site</h2>
        <table>
          <tbody>
            <tr><td>Notes</td><td><strong>{{ totals.grades }}</strong></td></tr>
            <tr><td>Devoirs</td><td><strong>{{ totals.homework }}</strong></td></tr>
            <tr><td>Cours EDT</td><td><strong>{{ totals.schedules }}</strong></td></tr>
            <tr><td>Absences</td><td><strong>{{ totals.absences }}</strong></td></tr>
            <tr><td>Infos générales</td><td><strong>{{ totals.general_info }}</strong></td></tr>
            <tr><td>Logs d'activité</td><td><strong>{{ totals.logs }}</strong></td></tr>
            <tr><td>Admins</td><td><strong>{{ totals.admins }}</strong></td></tr>
          </tbody>
        </table>
      </div>
      <div class='card'>
        <h2>Répartition par rôle</h2>
        <table>
          <thead><tr><th>Rôle</th><th>Total</th></tr></thead>
          <tbody>
            {% for row in role_stats %}
              <tr><td>{{ row.role }}</td><td><strong>{{ row.total }}</strong></td></tr>
            {% else %}
              <tr><td colspan='2'>Aucune donnée.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      <div class='card'>
        <h2>Élèves par classe</h2>
        <table>
          <thead><tr><th>Classe</th><th>Total</th></tr></thead>
          <tbody>
            {% for row in class_stats %}
              <tr><td>{{ row.class_name }}</td><td><strong>{{ row.total }}</strong></td></tr>
            {% else %}
              <tr><td colspan='2'>Aucune donnée.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <div class='grid' style='margin-top:18px;'>
      <div class='card'>
        <h2>Nouveaux comptes / comptes récents</h2>
        <table>
          <thead><tr><th>ID</th><th>Nom</th><th>Rôle</th><th>Créé le</th><th>Dernière connexion</th><th>Connexions</th></tr></thead>
          <tbody>
            {% for u in recent_users %}
              <tr>
                <td>{{ u.id }}</td>
                <td>{{ u.full_name }}<br><span class='muted small'>@{{ u.username }}</span></td>
                <td>{{ u.role }}</td>
                <td>{{ u.created_at or '-' }}</td>
                <td>{{ u.last_login_at or '-' }}</td>
                <td><strong>{{ u.login_count or 0 }}</strong></td>
              </tr>
            {% else %}
              <tr><td colspan='6'>Aucun compte.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      <div class='card'>
        <h2 style='background:linear-gradient(135deg,#6366f1,#8b5cf6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>⚡ Activité récente</h2>
        <div style='max-height:480px; overflow-y:auto; padding-right:6px;'>
          {% for log in recent_logs %}
            <div style='border:1px solid var(--table-border); border-radius:12px; padding:10px 12px; margin-bottom:8px; background:var(--table-bg);'>
              <div style='display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap;'>
                <strong style='font-size:14px;'>{{ log.action }}</strong>
                <span class='muted small'>{{ log.created_at }}</span>
              </div>
              <div class='muted small' style='margin-top:4px;'>{{ log.username or 'Système' }}{% if log.role %} · {{ log.role }}{% endif %}</div>
              {% if log.details %}<p style='margin:6px 0 0; font-size:13px; color:var(--text-muted);'>{{ log.details }}</p>{% endif %}
            </div>
          {% else %}
            <p class='muted'>Aucune activité enregistrée.</p>
          {% endfor %}
        </div>
      </div>
    </div>

    <div class='grid' style='margin-top:18px;'>
      <div class='card'>
        <h2>Derniers messages</h2>
        {% for item in recent_messages %}
          <div style='border-bottom:1px solid #eef3fb; padding:10px 0;'>
            <strong>{{ item.subject }}</strong>
            <div class='muted small'>{{ item.sender_name }} → {{ item.receiver_name }} · {{ item.created_at }}</div>
          </div>
        {% else %}
          <p class='muted'>Aucun message.</p>
        {% endfor %}
      </div>
      <div class='card'>
        <h2>Derniers devoirs publiés</h2>
        {% for item in recent_homework %}
          <div style='border-bottom:1px solid #eef3fb; padding:10px 0;'>
            <strong>{{ item.title }}</strong>
            <div class='muted small'>Par {{ item.teacher_name }} · créé le {{ item.created_at }} · rendu pour {{ item.due_date }}</div>
          </div>
        {% else %}
          <p class='muted'>Aucun devoir.</p>
        {% endfor %}
      </div>
    </div>

    <div class='card' style='margin-top:18px;'>
      <h2 style='background:linear-gradient(135deg,#ef4444,#f97316);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>🚨 Signalements reçus</h2>
      {% for report in recent_reports %}
        <div style='border:1px solid #e6edf8; border-radius:16px; padding:16px; margin-bottom:14px;'>
          <div style='display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap;'>
            <strong>#{{ report.id }} · {{ report.username or 'Utilisateur inconnu' }} ({{ report.role or '-' }})</strong>
            <span class='badge'>{{ report.status or 'Nouveau' }}</span>
          </div>
          <p style='margin:10px 0;'>{{ report.message }}</p>
          <p class='muted small'>Envoyé le {{ report.created_at }}{% if report.updated_at %} · maj {{ report.updated_at }}{% endif %}{% if report.resolved_at %} · résolu le {{ report.resolved_at }}{% endif %}</p>
          <div class='admin-box'>
            <form method='post'>
              <input type='hidden' name='form_type' value='update_report'>
              <input type='hidden' name='report_id' value='{{ report.id }}'>
              <label>Statut</label>
              <select name='status' required>
                {% for status in ['Nouveau', 'En cours', 'Résolu'] %}
                  <option value='{{ status }}' {% if (report.status or 'Nouveau') == status %}selected{% endif %}>{{ status }}</option>
                {% endfor %}
              </select>
              <label>Note admin</label>
              <textarea name='admin_note' placeholder='Réponse ou suivi admin'>{{ report.admin_note or '' }}</textarea>
              <div class='actions-inline'><button type='submit'>Mettre à jour</button></div>
            </form>
          </div>
        </div>
      {% else %}
        <p class='muted'>Aucun signalement pour le moment.</p>
      {% endfor %}
    </div>
    """
    return render_page(
        content,
        title="Administration",
        user=user,
        totals=totals,
        role_stats=role_stats,
        class_stats=class_stats,
        recent_users=recent_users,
        recent_logs=recent_logs,
        recent_reports=recent_reports,
        recent_messages=recent_messages,
        recent_homework=recent_homework,
    )


@app.route("/bulletin-pdf")
@login_required
def bulletin_pdf():
    from flask import make_response
    import io
    user = g.user

    # Récupérer les données selon le rôle
    if user["role"] == "eleve":
        students_data = [user]
    elif user["role"] == "parent":
        students_data = get_parent_children(user)
        if not students_data:
            flash("Aucun enfant lié à ce compte.")
            return redirect(url_for("settings_page"))
    else:
        flash("Le bulletin PDF est disponible uniquement pour les élèves et parents.")
        return redirect(url_for("settings_page"))

    # Générer le HTML du bulletin
    html_parts = []
    html_parts.append("""<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>
    <style>
      body { font-family: Arial, sans-serif; color: #1a1a2e; margin: 0; padding: 0; }
      .page { max-width: 800px; margin: 0 auto; padding: 40px 36px; }
      .header { text-align:center; border-bottom: 3px solid #1d4ed8; padding-bottom: 20px; margin-bottom: 28px; }
      .header h1 { color: #1d4ed8; font-size: 26px; margin: 0 0 6px; }
      .header p { color: #555; margin: 4px 0; font-size: 14px; }
      .student-name { font-size: 20px; font-weight: 800; color: #0f172a; margin: 24px 0 4px; }
      .student-meta { color: #555; font-size: 13px; margin-bottom: 18px; }
      table { width: 100%; border-collapse: collapse; margin-bottom: 28px; }
      th { background: #1d4ed8; color: white; padding: 10px 12px; text-align: left; font-size: 13px; }
      td { padding: 9px 12px; border-bottom: 1px solid #e2e8f0; font-size: 13px; }
      tr:nth-child(even) td { background: #f8faff; }
      .avg-row td { font-weight: 800; background: #eef4ff !important; color: #1d4ed8; }
      .section-title { font-size: 15px; font-weight: 700; color: #1d4ed8; margin: 20px 0 10px; border-left: 4px solid #1d4ed8; padding-left: 10px; }
      .absence-badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:700; }
      .badge-ok { background:#dcfce7; color:#166534; }
      .badge-ko { background:#fee2e2; color:#991b1b; }
      .footer { text-align:center; color:#888; font-size:11px; margin-top:40px; border-top:1px solid #e2e8f0; padding-top:12px; }
      .general-avg { font-size:28px; font-weight:900; color:#1d4ed8; }
      .avg-card { background:#eef4ff; border-radius:12px; padding:16px 20px; margin-bottom:20px; display:flex; align-items:center; gap:16px; }
    </style></head><body><div class='page'>""")

    html_parts.append(f"""
    <div class='header'>
      <h1>📋 Bulletin scolaire</h1>
      <p>Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}</p>
      <p>Renote — Renote</p>
    </div>""")

    for student in students_data:
        # Notes
        grades_rows = query_all(
            """
            SELECT g.value, g.comment, g.created_at, s.name AS subject_name, u.full_name AS teacher_name
            FROM grades g JOIN subjects s ON s.id = g.subject_id JOIN users u ON u.id = g.teacher_id
            WHERE g.student_id = ? ORDER BY s.name, g.created_at DESC
            """,
            (student["id"],)
        )
        # Moyennes par matière
        averages = query_all(
            """
            SELECT s.name AS subject_name, ROUND(CAST(AVG(g.value) AS NUMERIC), 2) AS avg_value, COUNT(*) AS nb
            FROM grades g JOIN subjects s ON s.id = g.subject_id
            WHERE g.student_id = ? GROUP BY s.name ORDER BY s.name
            """,
            (student["id"],)
        )
        # Absences
        absences = query_all(
            "SELECT * FROM absences WHERE student_id = ? ORDER BY absence_date DESC",
            (student["id"],)
        )
        # Moyenne générale
        all_values = [r["value"] for r in grades_rows]
        gen_avg = round(sum(all_values) / len(all_values), 2) if all_values else None

        class_name = student.get("class_name") or "-"
        html_parts.append(f"""
        <div class='student-name'>{student['full_name']}</div>
        <div class='student-meta'>Classe : {class_name} &nbsp;|&nbsp; {len(grades_rows)} note(s) enregistrée(s) &nbsp;|&nbsp; {len(absences)} absence(s)</div>""")

        if gen_avg is not None:
            color = "#166534" if gen_avg >= 10 else "#991b1b"
            html_parts.append(f"""
            <div class='avg-card'>
              <div>
                <div style='font-size:13px;color:#555;'>Moyenne générale</div>
                <div class='general-avg' style='color:{color};'>{gen_avg}/20</div>
              </div>
            </div>""")

        # Tableau moyennes par matière
        if averages:
            html_parts.append("<div class='section-title'>Moyennes par matière</div>")
            html_parts.append("<table><tr><th>Matière</th><th>Moyenne</th><th>Notes</th></tr>")
            for avg in averages:
                color = "#166534" if avg["avg_value"] >= 10 else "#991b1b"
                html_parts.append(f"<tr class='avg-row'><td>{avg['subject_name']}</td><td style='color:{color};font-weight:800;'>{avg['avg_value']}/20</td><td>{avg['nb']} note(s)</td></tr>")
            html_parts.append("</table>")

        # Tableau détail des notes
        if grades_rows:
            html_parts.append("<div class='section-title'>Détail des notes</div>")
            html_parts.append("<table><tr><th>Matière</th><th>Note</th><th>Professeur</th><th>Commentaire</th><th>Date</th></tr>")
            for g_row in grades_rows:
                color = "#166534" if g_row["value"] >= 10 else "#991b1b"
                html_parts.append(f"<tr><td>{g_row['subject_name']}</td><td style='color:{color};font-weight:700;'>{g_row['value']}/20</td><td>{g_row['teacher_name']}</td><td>{g_row['comment'] or '-'}</td><td>{g_row['created_at'][:10]}</td></tr>")
            html_parts.append("</table>")
        else:
            html_parts.append("<p style='color:#888;'>Aucune note enregistrée.</p>")

        # Absences
        html_parts.append("<div class='section-title'>Absences</div>")
        if absences:
            html_parts.append("<table><tr><th>Date</th><th>Motif</th><th>Statut</th></tr>")
            for ab in absences:
                badge_class = "badge-ok" if ab["status"] == "Justifiée" else "badge-ko"
                html_parts.append(f"<tr><td>{ab['absence_date']}</td><td>{ab.get('reason') or '-'}</td><td><span class='absence-badge {badge_class}'>{ab['status']}</span></td></tr>")
            html_parts.append("</table>")
        else:
            html_parts.append("<p style='color:#166534;font-weight:700;'>✅ Aucune absence enregistrée.</p>")

    html_parts.append(f"""
    <div class='footer'>Document généré automatiquement par Renote · {datetime.now().strftime('%d/%m/%Y')}</div>
    </div></body></html>""")

    full_html = "".join(html_parts)

    # Retourner en HTML (téléchargeable comme fichier)
    response = make_response(full_html)
    student_name = students_data[0]["full_name"].replace(" ", "_") if students_data else "bulletin"
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=bulletin_{student_name}_{datetime.now().strftime('%Y%m%d')}.html"
    return response


# =========================
# Vie de classe
# =========================
@app.route("/vie-de-classe", methods=["GET", "POST"])
@login_required
def vie_de_classe():
    user = g.user
    EMOJIS = ["❤️", "😂", "😮", "👏", "🔥", "🌟"]

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "post":
            if user["role"] not in ["prof", "admin"]:
                flash("Seul un prof ou admin peut publier.")
                return redirect(url_for("vie_de_classe"))
            body = request.form.get("body", "").strip()
            uploaded = request.files.get("image")
            image_url = None
            image_public_id = None
            if uploaded and uploaded.filename and allowed_profile_image(uploaded.filename):
                image_public_id, image_url = upload_to_cloudinary(uploaded, folder="renote_vie", resource_type="image")
            if not body and not image_url:
                flash("Ajoute du texte ou une image.")
                return redirect(url_for("vie_de_classe"))
            execute_db(
                "INSERT INTO vie_posts (author_id, body, image_url, image_public_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (user["id"], body or None, image_url, image_public_id, current_timestamp())
            )
            log_event("Post vie de classe", user=user, entity_type="vie_post")
            return redirect(url_for("vie_de_classe"))

        elif action == "react":
            post_id = request.form.get("post_id")
            emoji = request.form.get("emoji")
            if emoji not in EMOJIS:
                return redirect(url_for("vie_de_classe"))
            existing = query_one("SELECT id, emoji FROM vie_reactions WHERE post_id = ? AND user_id = ?", (post_id, user["id"]))
            if existing:
                if existing["emoji"] == emoji:
                    execute_db("DELETE FROM vie_reactions WHERE post_id = ? AND user_id = ?", (post_id, user["id"]))
                else:
                    execute_db("UPDATE vie_reactions SET emoji = ? WHERE post_id = ? AND user_id = ?", (emoji, post_id, user["id"]))
            else:
                try:
                    execute_db("INSERT INTO vie_reactions (post_id, user_id, emoji) VALUES (?, ?, ?)", (post_id, user["id"], emoji))
                except Exception:
                    pass
            return redirect(url_for("vie_de_classe"))

        elif action == "delete":
            if user["role"] not in ["prof", "admin"]:
                return redirect(url_for("vie_de_classe"))
            post_id = request.form.get("post_id")
            post = query_one("SELECT * FROM vie_posts WHERE id = ?", (post_id,))
            if post:
                if user["role"] == "admin" or str(post["author_id"]) == str(user["id"]):
                    if post.get("image_public_id"):
                        delete_from_cloudinary(post["image_public_id"], resource_type="image")
                    execute_db("DELETE FROM vie_posts WHERE id = ?", (post_id,))
            return redirect(url_for("vie_de_classe"))

    posts = query_all(
        """
        SELECT vp.*, u.full_name AS author_name, u.profile_picture_url AS author_pic, u.role AS author_role
        FROM vie_posts vp JOIN users u ON u.id = vp.author_id
        ORDER BY vp.id DESC
        """
    )

    # Load reactions for each post
    all_reactions = query_all("SELECT post_id, emoji, COUNT(*) AS cnt FROM vie_reactions GROUP BY post_id, emoji")
    my_reactions = query_all("SELECT post_id, emoji FROM vie_reactions WHERE user_id = ?", (user["id"],))
    my_react_map = {r["post_id"]: r["emoji"] for r in my_reactions}
    react_map = {}
    for r in all_reactions:
        pid = r["post_id"]
        if pid not in react_map:
            react_map[pid] = {}
        react_map[pid][r["emoji"]] = r["cnt"]

    import json
    react_map_json = json.dumps(react_map)
    my_react_json = json.dumps(my_react_map)

    page_html = BASE_TOP + NAV + """
<style>
  .vie-wrap {
    max-width: 680px;
    margin: 32px auto;
    padding: 0 18px;
  }
  .vie-header {
    text-align: center;
    margin-bottom: 28px;
  }
  .vie-header h1 {
    font-size: 28px;
    font-weight: 900;
    background: linear-gradient(135deg, #f59e0b, #ec4899, #8b5cf6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 6px;
  }
  .vie-header p { color: var(--text-muted); font-size: 14px; }
  .vie-post-form {
    background: var(--card);
    border-radius: 22px;
    padding: 20px;
    margin-bottom: 24px;
    box-shadow: 0 8px 24px rgba(139,92,246,0.10);
    border: 1px solid rgba(139,92,246,0.15);
  }
  .vie-post-form textarea {
    border-color: rgba(139,92,246,0.25);
    border-radius: 14px;
    resize: none;
    min-height: 80px;
    font-size: 15px;
  }
  .vie-post-form textarea:focus { border-color: #8b5cf6; box-shadow: 0 0 0 4px rgba(139,92,246,0.12); }
  .vie-post-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .vie-post-btn {
    background: linear-gradient(135deg, #8b5cf6, #ec4899);
    color: white; border: none; padding: 10px 20px;
    border-radius: 12px; font-weight: 700; cursor: pointer;
    font-size: 14px; box-shadow: 0 6px 16px rgba(139,92,246,0.25);
  }
  .vie-post-btn:hover { transform: translateY(-1px); }
  .vie-file-label {
    display: inline-flex; align-items: center; gap: 6px;
    background: var(--admin-box); border: 1px solid var(--admin-box-border);
    padding: 9px 14px; border-radius: 12px; cursor: pointer;
    font-size: 13px; font-weight: 600; color: var(--text-muted);
  }
  .vie-file-label:hover { background: var(--table-th); }
  .vie-post-card {
    background: var(--card);
    border-radius: 22px;
    margin-bottom: 20px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.06);
    border: 1px solid var(--card-border);
    overflow: hidden;
  }
  .vie-post-head {
    display: flex; align-items: center; gap: 12px;
    padding: 16px 18px 12px;
  }
  .vie-post-avatar {
    width: 42px; height: 42px; border-radius: 50%;
    object-fit: cover; flex-shrink: 0;
    background: linear-gradient(135deg, #8b5cf6, #ec4899);
    display: flex; align-items: center; justify-content: center;
    font-size: 17px; font-weight: 800; color: white;
  }
  .vie-post-author { font-weight: 700; font-size: 15px; color: var(--text); }
  .vie-post-time { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
  .vie-post-body { padding: 0 18px 14px; font-size: 15px; line-height: 1.65; color: var(--text); white-space: pre-wrap; }
  .vie-post-img { width: 100%; max-height: 480px; object-fit: cover; display: block; }
  .vie-post-footer { padding: 12px 18px 16px; border-top: 1px solid var(--table-border); }
  .vie-emojis { display: flex; gap: 8px; flex-wrap: wrap; }
  .vie-emoji-btn {
    display: inline-flex; align-items: center; gap: 5px;
    background: var(--admin-box); border: 1px solid var(--admin-box-border);
    border-radius: 999px; padding: 6px 12px; cursor: pointer;
    font-size: 15px; font-weight: 700; color: var(--text);
    transition: all 0.15s; box-shadow: none;
  }
  .vie-emoji-btn:hover { transform: scale(1.08); }
  .vie-emoji-btn.active {
    background: linear-gradient(135deg, #8b5cf6, #ec4899);
    border-color: transparent; color: white;
    box-shadow: 0 4px 12px rgba(139,92,246,0.25);
  }
  .vie-emoji-count { font-size: 12px; }
  .vie-delete-btn {
    background: none; border: none; color: var(--text-muted);
    cursor: pointer; font-size: 13px; padding: 4px 8px;
    border-radius: 8px; margin-left: auto; box-shadow: none;
  }
  .vie-delete-btn:hover { background: #fee2e2; color: #dc2626; transform: none; }
  [data-theme='dark'] .vie-post-card { box-shadow: 0 8px 24px rgba(0,0,0,0.25); }
  [data-theme='dark'] .vie-emoji-btn { background: #1e293b; border-color: #334155; }
  [data-theme='dark'] .vie-delete-btn:hover { background: #3b1010; color: #f87171; }
</style>

<div class='vie-wrap'>
  <div class='vie-header'>
    <h1>🌸 Vie de classe</h1>
    <p>Photos, moments et nouvelles de la classe</p>
  </div>

  {% if user.role in ['prof', 'admin'] %}
  <div class='vie-post-form'>
    <form method='post' enctype='multipart/form-data'>
      <input type='hidden' name='action' value='post'>
      <textarea name='body' placeholder='Partage un moment de classe, une nouvelle, une photo...' style='width:100%;margin-bottom:12px;'></textarea>
      <div class='vie-post-actions'>
        <label class='vie-file-label'>
          📷 Photo
          <input type='file' name='image' accept='image/*' style='display:none;' onchange="this.parentNode.querySelector('span') && (this.parentNode.querySelector('span').textContent = this.files[0]?.name || '')">
        </label>
        <button type='submit' class='vie-post-btn'>✨ Publier</button>
      </div>
    </form>
  </div>
  {% endif %}

  {% if not posts %}
    <div style='text-align:center; padding:60px 20px; color:var(--text-muted);'>
      <div style='font-size:56px; margin-bottom:16px;'>📷</div>
      <p>Aucune publication pour le moment.<br>Les profs peuvent partager des photos et moments ici.</p>
    </div>
  {% endif %}

  {% for post in posts %}
  <div class='vie-post-card'>
    <div class='vie-post-head'>
      {% if post.author_pic %}
        <img src='{{ post.author_pic }}' class='vie-post-avatar'>
      {% else %}
        <div class='vie-post-avatar'>{{ post.author_name[:1] }}</div>
      {% endif %}
      <div style='flex:1;'>
        <div class='vie-post-author'>{{ post.author_name }}</div>
        <div class='vie-post-time'>{{ post.created_at }}</div>
      </div>
      {% if user.role == 'admin' or post.author_id == user.id %}
      <form method='post' style='margin:0;' onsubmit="return confirm('Supprimer ce post ?');">
        <input type='hidden' name='action' value='delete'>
        <input type='hidden' name='post_id' value='{{ post.id }}'>
        <button type='submit' class='vie-delete-btn'>🗑️</button>
      </form>
      {% endif %}
    </div>
    {% if post.image_url %}
      <img src='{{ post.image_url }}' class='vie-post-img' alt='Photo'>
    {% endif %}
    {% if post.body %}
      <div class='vie-post-body'>{{ post.body }}</div>
    {% endif %}
    <div class='vie-post-footer'>
      <div class='vie-emojis' id='emojis-{{ post.id }}'>
        {% for emoji in emojis %}
          {% set count = react_map.get(post.id, {}).get(emoji, 0) %}
          {% set is_mine = my_react_map.get(post.id) == emoji %}
          <form method='post' style='margin:0;display:inline;'>
            <input type='hidden' name='action' value='react'>
            <input type='hidden' name='post_id' value='{{ post.id }}'>
            <input type='hidden' name='emoji' value='{{ emoji }}'>
            <button type='submit' class='vie-emoji-btn {% if is_mine %}active{% endif %}'>
              {{ emoji }}{% if count > 0 %}<span class='vie-emoji-count'>{{ count }}</span>{% endif %}
            </button>
          </form>
        {% endfor %}
      </div>
    </div>
  </div>
  {% endfor %}
</div>
</body></html>
"""
    return render_template_string(
        page_html,
        title="Vie de classe",
        user=user,
        posts=posts,
        emojis=EMOJIS,
        react_map={int(k): v for k,v in __import__('json').loads(react_map_json).items()},
        my_react_map={int(k): v for k,v in __import__('json').loads(my_react_json).items()},
        session=session,
        url_for=url_for,
        g=g,
    )


# =========================
# Mot de passe oublié
# =========================
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if not session.get("site_unlocked"):
        return redirect(url_for("site_access"))

    step = request.args.get("step", "1")
    error = None

    if request.method == "POST":
        step = request.form.get("step", "1")

        # Étape 1 : vérifier le nom d'utilisateur
        if step == "1":
            username = request.form.get("username", "").strip()
            user = query_one("SELECT id, username, full_name, secret_question, secret_answer FROM users WHERE username = ?", (username,))
            if not user:
                error = "Nom d'utilisateur introuvable."
            elif not user.get("secret_question"):
                error = "Aucune question secrète définie pour ce compte. Contacte l'administrateur."
            else:
                return render_page(
                    _forgot_step2_content(),
                    title="Mot de passe oublié",
                    username=username,
                    question=user["secret_question"],
                    error=None,
                )

        # Étape 2 : vérifier la réponse
        elif step == "2":
            username = request.form.get("username", "").strip()
            answer = request.form.get("answer", "").strip().lower()
            user = query_one("SELECT id, username, full_name, secret_answer FROM users WHERE username = ?", (username,))
            if not user or user.get("secret_answer") != answer:
                user_q = query_one("SELECT secret_question FROM users WHERE username = ?", (username,))
                return render_page(
                    _forgot_step2_content(),
                    title="Mot de passe oublié",
                    username=username,
                    question=user_q["secret_question"] if user_q else "",
                    error="Réponse incorrecte. Réessaie.",
                )
            return render_page(
                _forgot_step3_content(),
                title="Mot de passe oublié",
                username=username,
                error=None,
            )

        # Étape 3 : nouveau mot de passe
        elif step == "3":
            username = request.form.get("username", "").strip()
            new_password = request.form.get("new_password", "").strip()
            confirm = request.form.get("confirm_password", "").strip()
            user = query_one("SELECT id FROM users WHERE username = ?", (username,))
            if not user:
                flash("Erreur, recommence.")
                return redirect(url_for("forgot_password"))
            if len(new_password) < 6:
                return render_page(_forgot_step3_content(), title="Mot de passe oublié", username=username, error="Minimum 6 caractères.")
            if new_password != confirm:
                return render_page(_forgot_step3_content(), title="Mot de passe oublié", username=username, error="Les mots de passe ne correspondent pas.")
            execute_db("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(new_password), user["id"]))
            log_event("Mot de passe réinitialisé via question secrète", details=f"Utilisateur: {username}", entity_type="user", entity_id=user["id"])
            flash("✅ Mot de passe changé avec succès ! Tu peux te connecter.")
            return redirect(url_for("login"))

    return render_page(_forgot_step1_content(), title="Mot de passe oublié", error=error)


def _forgot_step1_content():
    return """
    <div class='card' style='max-width:480px;margin:40px auto;border-top:4px solid #6366f1;'>
      <div style='text-align:center;margin-bottom:20px;'>
        <span style='font-size:44px;'>🔑</span>
        <h1 style='background:linear-gradient(135deg,#6366f1,#8b5cf6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin:10px 0 4px;'>Mot de passe oublié</h1>
        <p class='muted'>Étape 1 sur 3 — Entre ton nom d'utilisateur</p>
      </div>
      {% if error %}<div class='flash' style='background:#fee2e2;border-color:#fca5a5;color:#dc2626;'>{{ error }}</div>{% endif %}
      <form method='post'>
        <input type='hidden' name='step' value='1'>
        <label>Nom d'utilisateur</label>
        <input name='username' required autocomplete='off' placeholder='Ton identifiant'>
        <button type='submit' style='width:100%;background:linear-gradient(90deg,#6366f1,#8b5cf6);'>Continuer →</button>
      </form>
      <p style='text-align:center;margin-top:14px;'><a href='{{ url_for("login") }}' style='color:var(--text-muted);font-size:13px;'>← Retour à la connexion</a></p>
    </div>"""


def _forgot_step2_content():
    return """
    <div class='card' style='max-width:480px;margin:40px auto;border-top:4px solid #6366f1;'>
      <div style='text-align:center;margin-bottom:20px;'>
        <span style='font-size:44px;'>🤫</span>
        <h1 style='background:linear-gradient(135deg,#6366f1,#8b5cf6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin:10px 0 4px;'>Question secrète</h1>
        <p class='muted'>Étape 2 sur 3 — Réponds à ta question secrète</p>
      </div>
      {% if error %}<div class='flash' style='background:#fee2e2;border-color:#fca5a5;color:#dc2626;'>{{ error }}</div>{% endif %}
      <div style='background:var(--admin-box);border:1px solid var(--admin-box-border);border-radius:14px;padding:14px 16px;margin-bottom:16px;'>
        <p style='margin:0;font-weight:700;color:var(--text);'>{{ question }}</p>
      </div>
      <form method='post'>
        <input type='hidden' name='step' value='2'>
        <input type='hidden' name='username' value='{{ username }}'>
        <label>Ta réponse <span class='muted small'>(en minuscules)</span></label>
        <input name='answer' required autocomplete='off' placeholder='Ta réponse...'>
        <button type='submit' style='width:100%;background:linear-gradient(90deg,#6366f1,#8b5cf6);'>Vérifier →</button>
      </form>
    </div>"""


def _forgot_step3_content():
    return """
    <div class='card' style='max-width:480px;margin:40px auto;border-top:4px solid #10b981;'>
      <div style='text-align:center;margin-bottom:20px;'>
        <span style='font-size:44px;'>🔐</span>
        <h1 style='background:linear-gradient(135deg,#10b981,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin:10px 0 4px;'>Nouveau mot de passe</h1>
        <p class='muted'>Étape 3 sur 3 — Choisis un nouveau mot de passe</p>
      </div>
      {% if error %}<div class='flash' style='background:#fee2e2;border-color:#fca5a5;color:#dc2626;'>{{ error }}</div>{% endif %}
      <form method='post'>
        <input type='hidden' name='step' value='3'>
        <input type='hidden' name='username' value='{{ username }}'>
        <label>Nouveau mot de passe</label>
        <input type='password' name='new_password' required placeholder='Minimum 6 caractères' autocomplete='new-password'>
        <label>Confirmer le mot de passe</label>
        <input type='password' name='confirm_password' required autocomplete='new-password'>
        <button type='submit' style='width:100%;background:linear-gradient(90deg,#10b981,#06b6d4);'>Changer mon mot de passe ✅</button>
      </form>
    </div>"""


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


with app.app_context():
    init_db()
    log_event("Application démarrée", user=None, details=f"Démarrage du site sur le port {os.environ.get('PORT', '5000')}", entity_type="system")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
