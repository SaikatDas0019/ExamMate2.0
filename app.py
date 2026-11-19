from flask import Flask, render_template, request, jsonify, session, redirect, url_for, make_response
import os
import sqlite3
from datetime import timedelta, datetime
from zoneinfo import ZoneInfo
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import PyPDF2
import requests
import smtplib
from email.message import EmailMessage
import random
import secrets
import hashlib
import hmac
import re
from werkzeug.security import generate_password_hash, check_password_hash


load_dotenv()

app = Flask(__name__)
_secret_key = os.getenv("FLASK_SECRET_KEY")
if not _secret_key:
    # A random fallback is safer than a published hard-coded key, but set
    # FLASK_SECRET_KEY in deployment so sessions survive worker restarts.
    _secret_key = secrets.token_hex(32)
    if os.getenv("FLASK_ENV", "").lower() == "production":
        app.logger.warning("FLASK_SECRET_KEY is not configured; sessions will not survive restarts.")
app.secret_key = _secret_key
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    SESSION_REFRESH_EACH_REQUEST=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() in ("1", "true", "yes"),
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    JSON_SORT_KEYS=False,
)

UPLOAD_FOLDER = 'static/uploads/resources'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_EMAILS = {
    item.strip().lower()
    for item in os.getenv("ADMIN_EMAILS", "").split(",")
    if item.strip()
}
SPECIAL_EXAM_OWNER_EMAIL = os.getenv("SPECIAL_EXAM_OWNER_EMAIL", "exammate.official@gmail.com").strip().lower()
OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60


def _normalise_email(value):
    return value.strip().lower() if isinstance(value, str) else ""


def _request_data():
    return request.get_json(silent=True) or {}


def _current_user():
    user = session.get("user")
    return user if isinstance(user, dict) else None


def _is_admin():
    user = _current_user()
    return bool(user and _normalise_email(user.get("email")) in ADMIN_EMAILS)


def _is_teacher_or_admin():
    user = _current_user()
    return bool(user and (str(user.get("role", "")).lower() == "teacher" or _is_admin()))


def _verify_google_id_token(id_token):
    """Verify a Google OAuth ID token server-side; never trust a posted email."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not client_id or not isinstance(id_token, str) or len(id_token) > 10000:
        return None
    try:
        response = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token}, timeout=8
        )
        if response.status_code != 200:
            return None
        claims = response.json()
        if claims.get("aud") != client_id:
            return None
        if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
            return None
        if str(claims.get("email_verified", "")).lower() != "true":
            return None
        email = _normalise_email(claims.get("email"))
        if not email or "@" not in email:
            return None
        return {
            "email": email,
            "name": str(claims.get("name") or claims.get("given_name") or email.split("@")[0])[:255],
            "picture": str(claims.get("picture") or "")[:2048],
        }
    except requests.RequestException:
        app.logger.exception("Google token verification request failed")
        return None
    except (ValueError, TypeError):
        return None


def _set_login_session(email, name, role):
    session.clear()
    session.permanent = True
    session["user"] = {
        "email": _normalise_email(email),
        "name": str(name or "User")[:255],
        "role": str(role or "Student")[:50],
    }


def _db_user_by_email(email):
    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if db_type == "postgres" else "?"
        cursor.execute(
            f"SELECT email, name, category, password, photo_url FROM users WHERE email = {ph}",
            (email,),
        )
        return cursor.fetchone()
    finally:
        conn.close()

def send_otp_email(receiver_email, otp):
    subject = "ExamMate Login OTP"
    body_text = (
        f"Welcome to ExamMate! Your OTP is: {otp}\n\n"
        "This code expires in 5 minutes. Do not share it with anyone."
    )

    # Render-এ সেট করা SMTP_FROM_EMAIL (বা RESEND_FROM_EMAIL) এবং SMTP_PASSWORD (বা MAIL_APP_PASSWORD) ব্যবহার করবে
    smtp_user = (os.getenv("SMTP_FROM_EMAIL") or os.getenv("RESEND_FROM_EMAIL") or "").strip()
    smtp_pass = (os.getenv("SMTP_PASSWORD") or os.getenv("MAIL_APP_PASSWORD") or "").strip().replace(" ", "")

    if smtp_user and smtp_pass:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"ExamMate <{smtp_user}>"
        msg["To"] = receiver_email
        msg.set_content(body_text)

        # প্রথমে Port 465 (SSL) এবং কাজ না করলে Port 587 (TLS) দিয়ে চেষ্টা করবে
        for port, use_ssl in ((465, True), (587, False)):
            try:
                if use_ssl:
                    with smtplib.SMTP_SSL("smtp.gmail.com", port, timeout=10) as server:
                        server.login(smtp_user, smtp_pass)
                        server.send_message(msg)
                else:
                    with smtplib.SMTP("smtp.gmail.com", port, timeout=10) as server:
                        server.ehlo()
                        server.starttls()
                        server.ehlo()
                        server.login(smtp_user, smtp_pass)
                        server.send_message(msg)
                return True
            except Exception as exc:
                app.logger.error("SMTP failed on port %s: %s", port, exc)

    # ব্যাকআপ হিসেবে Resend API রাখা হলো (যদি ভবিষ্যতে ডোমেইন ভেরিফাই করা হয়)
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    sender = (os.getenv("RESEND_FROM_EMAIL") or "").strip()
    if api_key and sender and not sender.lower().endswith("@gmail.com"):
        try:
            response = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": sender,
                    "to": [receiver_email],
                    "subject": subject,
                    "text": body_text,
                },
                timeout=15,
            )
            if response.status_code in (200, 201):
                return True
        except requests.RequestException:
            app.logger.exception("Resend email request failed")

    return False

@app.before_request
def enforce_route_permissions():
    path = request.path
    data = _request_data() if request.is_json else {}
    user = _current_user()

    # The old public GET migration endpoint is intentionally disabled.
    if path == "/run-fix-db":
        return jsonify({"success": False, "error": "This maintenance endpoint is disabled."}), 404

    if path == "/admin.html" or path.startswith("/api/admin/"):
        if not user:
            return jsonify({"success": False, "error": "Authentication required."}), 401
        if not _is_admin():
            return jsonify({"success": False, "error": "Administrator access required."}), 403

    teacher_paths = (
        "/api/create-exam", "/api/teacher-dashboard", "/api/teacher-analysis",
        "/api/teacher-full-analytics", "/api/teacher-delete-exam",
        "/api/teacher-update-exam", "/api/teacher-pending-evaluations",
        "/api/teacher-submit-evaluation",
    )
    if path in teacher_paths:
        if not user:
            return jsonify({"success": False, "error": "Login required."}), 401
        if not _is_teacher_or_admin():
            return jsonify({"success": False, "error": "Teacher access required."}), 403
        supplied_email = _normalise_email(data.get("email") or data.get("teacher_email"))
        if supplied_email and supplied_email != _normalise_email(user.get("email")) and not _is_admin():
            return jsonify({"success": False, "error": "You cannot access another account."}), 403

        # A teacher may only manage exams that belong to their own account.
        exam_code = data.get("exam_code")
        if exam_code and not _is_admin():
            try:
                conn, db_type = get_db_connection()
                cursor = conn.cursor()
                ph = "%s" if db_type == "postgres" else "?"
                cursor.execute(f"SELECT teacher_email FROM exams WHERE exam_code = {ph}", (exam_code,))
                owner = cursor.fetchone()
                conn.close()
                owner_email = owner["teacher_email"] if owner else None
                if not owner_email or _normalise_email(owner_email) != _normalise_email(user.get("email")):
                    return jsonify({"success": False, "error": "You do not own this exam."}), 403
            except Exception:
                app.logger.exception("Exam ownership check failed")
                return jsonify({"success": False, "error": "Could not verify exam ownership."}), 500

    identity_paths = {
        "/api/get-student-progress", "/api/get-student-history", "/api/get-profile-data",
        "/api/update-profile", "/api/get-notifications-page", "/api/check-unread-notifications",
        "/api/mark-notifications-read", "/api/search-feed", "/api/toggle-social",
        "/api/get-season-analytics", "/api/get-student-exam-review",
        "/api/get-special-exams", "/api/get-special-playlist-exams",
        "/api/get-student-drive-contents",
    }
    if path in identity_paths:
        if not user:
            return jsonify({"success": False, "error": "Login required."}), 401
        supplied_email = _normalise_email(data.get("email"))
        if supplied_email and supplied_email != _normalise_email(user.get("email")):
            return jsonify({"success": False, "error": "You cannot access another account's data."}), 403
        if not supplied_email and path != "/api/get-student-drive-contents":
            data["email"] = _normalise_email(user.get("email"))

    # Logged-in users cannot submit results or social actions under another email.
    if user and path in ("/api/submit-exam-result", "/api/toggle-social"):
        supplied_email = _normalise_email(data.get("email"))
        if supplied_email and supplied_email != _normalise_email(user.get("email")):
            return jsonify({"success": False, "error": "Account mismatch."}), 403

    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


def get_db_connection():
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn, 'postgres'
    else:
        conn = sqlite3.connect('ExamMate.db')
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'

def _row_value(row, key, index=0):
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[index]


STREAK_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _streak_now():
    """Naive local timestamp in the application's agreed IST timezone."""
    return datetime.now(STREAK_TIMEZONE).replace(tzinfo=None)


def _record_streak_activity(cursor, db_type, email, exam_code, exam_name,
                            score=None, total_marks=None, source_key=None,
                            completed_at=None):
    """Append one immutable completion event; repeated attempts remain separate."""
    email = _normalise_email(email)
    if not email or not exam_code:
        return False
    completed_at = completed_at or _streak_now().strftime("%Y-%m-%d %H:%M:%S")
    source_key = source_key or f"event:{email}:{exam_code}:{secrets.token_hex(12)}"
    if db_type == "postgres":
        cursor.execute("""INSERT INTO streak_activity
            (student_email, exam_code, exam_name, score, total_marks, completed_at, source_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (source_key) DO NOTHING""",
            (email, str(exam_code), str(exam_name or "Completed Exam"), score, total_marks, completed_at, source_key))
    else:
        cursor.execute("""INSERT OR IGNORE INTO streak_activity
            (student_email, exam_code, exam_name, score, total_marks, completed_at, source_key)
            VALUES (?,?,?,?,?,?,?)""",
            (email, str(exam_code), str(exam_name or "Completed Exam"), score, total_marks, completed_at, source_key))
    return True


def _ensure_streak_history_schema(cursor, db_type):
    """Additive migration for account dates and immutable exam-completion history."""
    if db_type == "postgres":
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP")
        cursor.execute("ALTER TABLE users ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP")
        cursor.execute("""UPDATE users u SET created_at = COALESCE(
            u.created_at, (SELECT MIN(r.date_taken) FROM results r WHERE r.student_email=u.email), CURRENT_TIMESTAMP)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS streak_activity (
            id BIGSERIAL PRIMARY KEY,
            student_email VARCHAR(255) NOT NULL,
            exam_code VARCHAR(100) NOT NULL,
            exam_name VARCHAR(255) NOT NULL,
            score DOUBLE PRECISION NULL,
            total_marks DOUBLE PRECISION NULL,
            completed_at TIMESTAMP NOT NULL,
            source_key VARCHAR(400) NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_streak_activity_student_date ON streak_activity (student_email, completed_at)")
        # Import every attempt whose timestamp was already preserved by the V2 submission ledger.
        cursor.execute("""SELECT m.student_email, m.exam_code, COALESCE(e.exam_name,m.exam_code) AS exam_name_value,
            m.created_at::text AS completed_value, m.attempt_id
            FROM exam_submission_metadata m LEFT JOIN exams e ON e.exam_code=m.exam_code""")
        for row in cursor.fetchall():
            completed = str(_row_value(row, 'completed_value', 3) or _streak_now().strftime('%Y-%m-%d %H:%M:%S')).replace('T',' ')[:19]
            key = f"legacy-meta:{_row_value(row,'student_email',0)}:{_row_value(row,'exam_code',1)}:{_row_value(row,'attempt_id',4)}"
            _record_streak_activity(cursor, db_type, _row_value(row,'student_email',0), _row_value(row,'exam_code',1), _row_value(row,'exam_name_value',2), source_key=key, completed_at=completed)
        cursor.execute("""SELECT r.id,r.student_email,r.exam_code,r.exam_name,r.score,r.total_questions,r.date_taken,
            COALESCE(NULLIF((SELECT SUM(q.max_marks) FROM questions q WHERE q.exam_code=r.exam_code),0),r.total_questions) AS max_marks
            FROM results r WHERE NOT EXISTS (SELECT 1 FROM exam_submission_metadata m
            WHERE m.student_email=r.student_email AND m.exam_code=r.exam_code)""")
        for row in cursor.fetchall():
            _record_streak_activity(cursor, db_type, _row_value(row,'student_email',1), _row_value(row,'exam_code',2),
                _row_value(row,'exam_name',3), _row_value(row,'score',4), _row_value(row,'max_marks',7),
                source_key=f"legacy-result:{_row_value(row,'id',0)}",
                completed_at=str(_row_value(row,'date_taken',6) or _streak_now())[:19].replace('T',' '))
    else:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
        except sqlite3.OperationalError:
            pass
        cursor.execute("""UPDATE users SET created_at = COALESCE(created_at,
            (SELECT MIN(r.date_taken) FROM results r WHERE r.student_email=users.email), datetime('now'))""")
        cursor.execute("""CREATE TRIGGER IF NOT EXISTS trg_users_created_at
            AFTER INSERT ON users WHEN NEW.created_at IS NULL
            BEGIN UPDATE users SET created_at=datetime('now') WHERE email=NEW.email; END""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS streak_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_email TEXT NOT NULL,
            exam_code TEXT NOT NULL,
            exam_name TEXT NOT NULL,
            score REAL NULL,
            total_marks REAL NULL,
            completed_at TEXT NOT NULL,
            source_key TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_streak_activity_student_date ON streak_activity (student_email, completed_at)")
        cursor.execute("""SELECT m.student_email,m.exam_code,COALESCE(e.exam_name,m.exam_code) AS exam_name_value,
            m.created_at AS completed_value,m.attempt_id
            FROM exam_submission_metadata m LEFT JOIN exams e ON e.exam_code=m.exam_code""")
        for row in cursor.fetchall():
            completed = str(_row_value(row,'completed_value',3) or _streak_now().strftime('%Y-%m-%d %H:%M:%S')).replace('T',' ')[:19]
            key = f"legacy-meta:{_row_value(row,'student_email',0)}:{_row_value(row,'exam_code',1)}:{_row_value(row,'attempt_id',4)}"
            _record_streak_activity(cursor, db_type, _row_value(row,'student_email',0), _row_value(row,'exam_code',1), _row_value(row,'exam_name_value',2), source_key=key, completed_at=completed)
        cursor.execute("""SELECT r.id,r.student_email,r.exam_code,r.exam_name,r.score,r.total_questions,r.date_taken,
            COALESCE(NULLIF((SELECT SUM(q.max_marks) FROM questions q WHERE q.exam_code=r.exam_code),0),r.total_questions) AS max_marks
            FROM results r WHERE NOT EXISTS (SELECT 1 FROM exam_submission_metadata m
            WHERE m.student_email=r.student_email AND m.exam_code=r.exam_code)""")
        for row in cursor.fetchall():
            _record_streak_activity(cursor, db_type, _row_value(row,'student_email',1), _row_value(row,'exam_code',2),
                _row_value(row,'exam_name',3), _row_value(row,'score',4), _row_value(row,'max_marks',7),
                source_key=f"legacy-result:{_row_value(row,'id',0)}",
                completed_at=str(_row_value(row,'date_taken',6) or _streak_now())[:19].replace('T',' '))


def _streak_day_sets(active_dates, today):
    """One missed day may bridge two active days; only active days add to the count."""
    dates = sorted(active_dates)
    grace_dates = set()
    for previous, current in zip(dates, dates[1:]):
        if (current - previous).days == 2:
            grace_dates.add(previous + timedelta(days=1))

    longest = run = 0
    previous = None
    latest_chain = []
    chain = []
    for day in dates:
        if previous is None or (day - previous).days <= 2:
            chain.append(day)
        else:
            longest = max(longest, len(chain))
            chain = [day]
        run = len(chain)
        previous = day
    longest = max(longest, len(chain))

    current_streak = 0
    if dates and (today - dates[-1]).days <= 2:
        latest_chain = [dates[-1]]
        for day in reversed(dates[:-1]):
            if (latest_chain[0] - day).days <= 2:
                latest_chain.insert(0, day)
            else:
                break
        current_streak = len(latest_chain)
    return grace_dates, current_streak, longest



def init_db():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        
        if db_type == 'postgres':
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (email VARCHAR(255) PRIMARY KEY, name VARCHAR(255) NOT NULL, category VARCHAR(50) NOT NULL, last_notif_read TIMESTAMP DEFAULT '1970-01-01 00:00:00');
                CREATE TABLE IF NOT EXISTS results (id SERIAL PRIMARY KEY, student_email VARCHAR(255) NOT NULL, exam_code VARCHAR(100) NOT NULL, exam_name VARCHAR(255) NOT NULL, score FLOAT NOT NULL, total_questions INT NOT NULL, date_taken TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS exams (exam_code VARCHAR(100) PRIMARY KEY, exam_name VARCHAR(255) NOT NULL, teacher_email VARCHAR(255) NOT NULL, timer_minutes INT NOT NULL, folder_id INT DEFAULT 0, negative_marks FLOAT DEFAULT 0.0, position INT DEFAULT 0);
                CREATE TABLE IF NOT EXISTS questions (id SERIAL PRIMARY KEY, exam_code VARCHAR(100) REFERENCES exams(exam_code) ON DELETE CASCADE, question_text TEXT NOT NULL, option_a TEXT NOT NULL, option_b TEXT NOT NULL, option_c TEXT NOT NULL, option_d TEXT NOT NULL, correct_option VARCHAR(10) NOT NULL);
                CREATE TABLE IF NOT EXISTS notifications (id SERIAL PRIMARY KEY, message TEXT NOT NULL, target_role VARCHAR(50) NOT NULL, date_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS drive_folders (id SERIAL PRIMARY KEY, folder_name VARCHAR(255) NOT NULL, parent_id INT DEFAULT 0, folder_type VARCHAR(50) DEFAULT 'content', position INT DEFAULT 0);
                CREATE TABLE IF NOT EXISTS drive_files (id SERIAL PRIMARY KEY, folder_id INT NOT NULL, title VARCHAR(255) NOT NULL, file_url TEXT NOT NULL, resource_type VARCHAR(50) DEFAULT 'Notes', date_uploaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP, position INT DEFAULT 0);
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, last_notif_read TIMESTAMP DEFAULT '1970-01-01 00:00:00');
                CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, student_email TEXT NOT NULL, exam_code TEXT NOT NULL, exam_name TEXT NOT NULL, score REAL NOT NULL, total_questions INTEGER NOT NULL, date_taken TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS exams (exam_code TEXT PRIMARY KEY, exam_name TEXT NOT NULL, teacher_email TEXT NOT NULL, timer_minutes INTEGER NOT NULL, folder_id INTEGER DEFAULT 0, negative_marks REAL DEFAULT 0.0, position INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY AUTOINCREMENT, exam_code TEXT, question_text TEXT NOT NULL, option_a TEXT NOT NULL, option_b TEXT NOT NULL, option_c TEXT NOT NULL, option_d TEXT NOT NULL, correct_option TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL, target_role TEXT NOT NULL, date_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS drive_folders (id INTEGER PRIMARY KEY AUTOINCREMENT, folder_name TEXT NOT NULL, parent_id INTEGER DEFAULT 0, folder_type TEXT DEFAULT 'content', position INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS drive_files (id INTEGER PRIMARY KEY AUTOINCREMENT, folder_id INTEGER NOT NULL, title TEXT NOT NULL, file_url TEXT NOT NULL, resource_type TEXT DEFAULT 'Notes', date_uploaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP, position INTEGER DEFAULT 0);
            ''')
        conn.commit()
        
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN password TEXT;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()
            
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN photo_url TEXT;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()
            
        try:
            cursor.execute("ALTER TABLE exams ADD COLUMN folder_id INT DEFAULT 0;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()

        try:
            cursor.execute("ALTER TABLE drive_folders ADD COLUMN folder_type VARCHAR(50) DEFAULT 'content';")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()
            
        try:
            cursor.execute("ALTER TABLE exams ADD COLUMN negative_marks FLOAT DEFAULT 0.0;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()
            
        try:
            cursor.execute("ALTER TABLE exams ADD COLUMN position INT DEFAULT 0;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()

        try:
            cursor.execute("ALTER TABLE drive_folders ADD COLUMN position INT DEFAULT 0;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()

        try:
            cursor.execute("ALTER TABLE drive_files ADD COLUMN position INT DEFAULT 0;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()
            
        try:
            if db_type == 'postgres':
                cursor.execute("ALTER TABLE results ALTER COLUMN score TYPE FLOAT;")
                conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN is_logged_in BOOLEAN DEFAULT FALSE;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()

        if db_type == 'postgres':
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS reviews (
                    id SERIAL PRIMARY KEY, reviewer_name VARCHAR(255) NOT NULL, role VARCHAR(100) NOT NULL, rating INT NOT NULL, review_text TEXT NOT NULL, date_submitted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, reviewer_name TEXT NOT NULL, role TEXT NOT NULL, rating INTEGER NOT NULL, review_text TEXT NOT NULL, date_submitted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
        conn.commit()
        
        if db_type == 'postgres':
            cursor.execute('''CREATE TABLE IF NOT EXISTS notifications (id SERIAL PRIMARY KEY, message TEXT NOT NULL, target_role VARCHAR(50) NOT NULL, link_url TEXT, date_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP);''')
        else:
            cursor.execute('''CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL, target_role TEXT NOT NULL, link_url TEXT, date_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP);''')
        conn.commit()

        try:
            cursor.execute("ALTER TABLE notifications ADD COLUMN link_url TEXT;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()
            
        try:
            cursor.execute("ALTER TABLE exams ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()

        try:
            cursor.execute("ALTER TABLE exams ADD COLUMN class_name VARCHAR(50) DEFAULT 'General';")
            cursor.execute("ALTER TABLE exams ADD COLUMN subject VARCHAR(100) DEFAULT 'General';")
            cursor.execute("ALTER TABLE exams ADD COLUMN shares INT DEFAULT 0;")
            cursor.execute("ALTER TABLE exams ADD COLUMN is_private BOOLEAN DEFAULT FALSE;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()

        cursor.execute('''CREATE TABLE IF NOT EXISTS likes (email VARCHAR(255), exam_code VARCHAR(50), PRIMARY KEY(email, exam_code))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS saves (email VARCHAR(255), exam_code VARCHAR(50), PRIMARY KEY(email, exam_code))''')
        conn.commit()
        
        try:
            cursor.execute("ALTER TABLE questions ADD COLUMN question_type VARCHAR(20) DEFAULT 'mcq';")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()
            
        try:
            cursor.execute("ALTER TABLE questions ADD COLUMN max_marks FLOAT DEFAULT 1.0;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()
            
        if db_type == 'postgres':
            cursor.execute('''CREATE TABLE IF NOT EXISTS subjective_answers (id SERIAL PRIMARY KEY, exam_code VARCHAR(100), student_email VARCHAR(255), question_id INT, image_url TEXT, marks FLOAT DEFAULT 0.0, is_checked BOOLEAN DEFAULT FALSE);''')
        else:
            cursor.execute('''CREATE TABLE IF NOT EXISTS subjective_answers (id INTEGER PRIMARY KEY AUTOINCREMENT, exam_code TEXT, student_email TEXT, question_id INTEGER, image_url TEXT, marks REAL DEFAULT 0.0, is_checked BOOLEAN DEFAULT FALSE);''')
        conn.commit()

        try:
            cursor.execute("ALTER TABLE exams ADD COLUMN expiry_time TIMESTAMP DEFAULT NULL;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()

        try:
            cursor.execute("ALTER TABLE exams ADD COLUMN season VARCHAR(100) DEFAULT 'General';")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()

        # Gamification foundation: additive-only schema; existing student and exam data is untouched.
        if db_type == 'postgres':
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS student_gamification (
                    student_email VARCHAR(255) PRIMARY KEY,
                    xp_total BIGINT NOT NULL DEFAULT 0,
                    level INTEGER NOT NULL DEFAULT 1,
                    current_streak INTEGER NOT NULL DEFAULT 0,
                    longest_streak INTEGER NOT NULL DEFAULT 0,
                    last_activity_date DATE DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS xp_transactions (
                    id BIGSERIAL PRIMARY KEY,
                    student_email VARCHAR(255) NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    reference_key VARCHAR(255) NOT NULL,
                    xp_delta INTEGER NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (student_email, event_type, reference_key)
                );
                CREATE TABLE IF NOT EXISTS achievement_catalog (
                    achievement_code VARCHAR(100) PRIMARY KEY,
                    name VARCHAR(120) NOT NULL,
                    description TEXT NOT NULL,
                    icon VARCHAR(255) DEFAULT NULL,
                    xp_reward INTEGER NOT NULL DEFAULT 0,
                    requirement_type VARCHAR(50) NOT NULL,
                    requirement_value INTEGER NOT NULL DEFAULT 1,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS student_achievements (
                    id BIGSERIAL PRIMARY KEY,
                    student_email VARCHAR(255) NOT NULL,
                    achievement_code VARCHAR(100) NOT NULL,
                    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (student_email, achievement_code)
                );
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS student_gamification (
                    student_email TEXT PRIMARY KEY,
                    xp_total INTEGER NOT NULL DEFAULT 0,
                    level INTEGER NOT NULL DEFAULT 1,
                    current_streak INTEGER NOT NULL DEFAULT 0,
                    longest_streak INTEGER NOT NULL DEFAULT 0,
                    last_activity_date DATE DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS xp_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_email TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    reference_key TEXT NOT NULL,
                    xp_delta INTEGER NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (student_email, event_type, reference_key)
                );
                CREATE TABLE IF NOT EXISTS achievement_catalog (
                    achievement_code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    icon TEXT DEFAULT NULL,
                    xp_reward INTEGER NOT NULL DEFAULT 0,
                    requirement_type TEXT NOT NULL,
                    requirement_value INTEGER NOT NULL DEFAULT 1,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS student_achievements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_email TEXT NOT NULL,
                    achievement_code TEXT NOT NULL,
                    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (student_email, achievement_code)
                );
            ''')

        # Permanent first-attempt ledger: results rows are overwritten on re-attempt,
        # so XP eligibility must live in a separate table.
        if db_type == 'postgres':
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS exam_xp_attempts (
                    student_email VARCHAR(255) NOT NULL,
                    exam_code VARCHAR(100) NOT NULL,
                    mcq_accuracy DOUBLE PRECISION NOT NULL DEFAULT 0,
                    correct_count INTEGER NOT NULL DEFAULT 0,
                    wrong_count INTEGER NOT NULL DEFAULT 0,
                    mcq_count INTEGER NOT NULL DEFAULT 0,
                    max_consecutive_correct INTEGER NOT NULL DEFAULT 0,
                    xp_net INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (student_email, exam_code)
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS exam_xp_attempts (
                    student_email TEXT NOT NULL,
                    exam_code TEXT NOT NULL,
                    mcq_accuracy REAL NOT NULL DEFAULT 0,
                    correct_count INTEGER NOT NULL DEFAULT 0,
                    wrong_count INTEGER NOT NULL DEFAULT 0,
                    mcq_count INTEGER NOT NULL DEFAULT 0,
                    max_consecutive_correct INTEGER NOT NULL DEFAULT 0,
                    xp_net INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (student_email, exam_code)
                )
            """)

        # Per-attempt immutable MCQ selections: one answer per question per exam attempt.
        if db_type == 'postgres':
            cursor.execute("""CREATE TABLE IF NOT EXISTS exam_attempt_answers (
                student_email VARCHAR(255) NOT NULL,
                exam_code VARCHAR(100) NOT NULL,
                attempt_id VARCHAR(100) NOT NULL,
                question_id INTEGER NOT NULL,
                selected_option VARCHAR(10) NOT NULL,
                is_correct BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (student_email, exam_code, attempt_id, question_id)
            )""")
        else:
            cursor.execute("""CREATE TABLE IF NOT EXISTS exam_attempt_answers (
                student_email TEXT NOT NULL,
                exam_code TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                question_id INTEGER NOT NULL,
                selected_option TEXT NOT NULL,
                is_correct INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (student_email, exam_code, attempt_id, question_id)
            )""")

        # Persist timing and anti-cheat metadata for every submitted attempt.
        if db_type == 'postgres':
            cursor.execute("""CREATE TABLE IF NOT EXISTS exam_submission_metadata (
                id BIGSERIAL PRIMARY KEY, student_email VARCHAR(255) NOT NULL, exam_code VARCHAR(100) NOT NULL,
                attempt_id VARCHAR(100) NOT NULL, warnings INTEGER NOT NULL DEFAULT 0, submit_reason VARCHAR(50),
                is_reattempt BOOLEAN NOT NULL DEFAULT FALSE, started_at TEXT, submitted_at TEXT,
                elapsed_seconds INTEGER NOT NULL DEFAULT 0, time_remaining_seconds INTEGER NOT NULL DEFAULT 0,
                client_question_count INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_email, exam_code, attempt_id))""")
        else:
            cursor.execute("""CREATE TABLE IF NOT EXISTS exam_submission_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT, student_email TEXT NOT NULL, exam_code TEXT NOT NULL,
                attempt_id TEXT NOT NULL, warnings INTEGER NOT NULL DEFAULT 0, submit_reason TEXT,
                is_reattempt INTEGER NOT NULL DEFAULT 0, started_at TEXT, submitted_at TEXT,
                elapsed_seconds INTEGER NOT NULL DEFAULT 0, time_remaining_seconds INTEGER NOT NULL DEFAULT 0,
                client_question_count INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_email, exam_code, attempt_id))""")

        _ensure_streak_history_schema(cursor, db_type)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_xp_transactions_student_date ON xp_transactions (student_email, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_student_achievements_student ON student_achievements (student_email)")
        conn.commit()

        conn.close()
    except Exception as e:
        print("DB Init Exception:", e)

init_db()

@app.route('/')
def home(): 
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp
    
@app.route('/setup_profile.html')
def setup_profile_page(): return render_template('setup_profile.html')

@app.route('/student_exam.html')
def student_exam(): 
    return render_template('student_exam.html') 

@app.route('/student_dashboard.html')
def student_dashboard(): return render_template('student_dashboard.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')
@app.route('/teacher_dashboard.html')
def teacher_dashboard(): return render_template('teacher_dashboard.html') if session.get('user', {}).get('role', '').lower() == 'teacher' else redirect('/')
@app.route('/teacher_profile.html')
def teacher_profile(): return render_template('teacher_profile.html') if session.get('user', {}).get('role', '').lower() == 'teacher' else redirect('/')
@app.route('/student_profile.html')
def student_profile(): return render_template('student_profile.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')

@app.route('/streak_history.html')
def streak_history_page():
    return render_template('streak_history.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')
@app.route('/create_exam.html')
def create_exam(): 
    return render_template('create_exam.html')
@app.route('/teacher_analytics.html')
def teacher_analytics(): return render_template('teacher_analytics.html') if session.get('user', {}).get('role', '').lower() == 'teacher' else redirect('/')
@app.route('/notification.html')
def notification(): return render_template('notification.html') if session.get('user') else redirect('/')
@app.route('/admin.html')
def admin_page():
    if not _is_admin():
        return redirect("/")
    return render_template("admin.html")

@app.route('/student_analytics.html')
def student_analytics(): return render_template('student_analytics.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')
@app.route('/student_resources.html')
def student_resources(): return render_template('student_resources.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')
@app.route('/special_exams.html')
def special_exams(): return render_template('special_exams.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')
    
@app.errorhandler(500)
def internal_error(error):
    app.logger.exception("Unhandled server error", exc_info=error)
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "Internal server error."}), 500
    return render_template("index.html"), 500


@app.errorhandler(Exception)
def handle_exception(error):
    if isinstance(error, HTTPException):
        return error
    app.logger.exception("Unhandled application exception")
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "An unexpected server error occurred."}), 500
    return render_template("index.html"), 500

@app.route('/api/google-login', methods=['POST'])
def google_login():
    data = _request_data()
    id_token = data.get("id_token")
    verified = _verify_google_id_token(id_token)
    if not verified:
        return jsonify({"success": False, "error": "Google authentication could not be verified. Please sign in again."}), 401

    email = verified["email"]
    try:
        user = _db_user_by_email(email)
        if user:
            role = user["category"]
            _set_login_session(email, user["name"], role)
            conn, db_type = get_db_connection()
            try:
                cursor = conn.cursor()
                ph = "%s" if db_type == "postgres" else "?"
                cursor.execute(f"UPDATE users SET is_logged_in = TRUE WHERE email = {ph}", (email,))
                conn.commit()
            finally:
                conn.close()
            return jsonify({"success": True, "is_new": False, "name": user["name"], "role": role,
                            "redirect_url": "/student_dashboard.html" if str(role).lower() == "student" else "/teacher_dashboard.html"})

        session.clear()
        session.permanent = True
        session["pending_google_email"] = email
        session["pending_google_name"] = verified["name"]
        session["pending_google_photo"] = verified["picture"]
        return jsonify({"success": True, "is_new": True, "name": verified["name"], "photo_url": verified["picture"]})
    except Exception:
        app.logger.exception("Google login database operation failed")
        return jsonify({"success": False, "error": "Unable to complete sign-in."}), 500

@app.route('/api/email-auth-step1', methods=['POST'])
def email_auth_step1():
    data = _request_data()
    email = _normalise_email(data.get("email"))
    forgot = bool(data.get("forgot", False))
    if not email or len(email) > 255 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return jsonify({"success": False, "error": "Enter a valid email address."}), 400

    now = int(datetime.utcnow().timestamp())
    if session.get("otp_email") == email and now - int(session.get("otp_sent_at", 0)) < OTP_RESEND_COOLDOWN_SECONDS:
        return jsonify({"success": False, "error": "Please wait before requesting another OTP."}), 429

    try:
        user = _db_user_by_email(email)
        if user and user["password"] and not forgot:
            return jsonify({"requires_password": True})

        otp = f"{secrets.randbelow(900000) + 100000:06d}"
        otp_hash = hashlib.sha256((email + ":" + otp + app.secret_key).encode("utf-8")).hexdigest()
        if not send_otp_email(email, otp):
            session.pop("otp_hash", None)
            return jsonify({"success": False, "error": "OTP email could not be sent. Please try again later."}), 503
        session["otp_email"] = email
        session["otp_hash"] = otp_hash
        session["otp_sent_at"] = now
        session["otp_expires_at"] = now + OTP_TTL_SECONDS
        session["otp_attempts"] = 0
        session.pop("otp_verified_email", None)
        session.pop("temp_password", None)
        session.pop("pending_google_email", None)
        return jsonify({"requires_otp": True})
    except Exception:
        app.logger.exception("Email authentication step failed")
        return jsonify({"success": False, "error": "Unable to start email authentication."}), 500

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    data = _request_data()
    email = _normalise_email(data.get("email"))
    otp = str(data.get("otp", "")).strip()
    now = int(datetime.utcnow().timestamp())
    if (email != session.get("otp_email") or not session.get("otp_hash")
            or now > int(session.get("otp_expires_at", 0))):
        session.pop("otp_hash", None)
        return jsonify({"success": False, "error": "OTP is invalid or expired. Request a new one."}), 400
    attempts = int(session.get("otp_attempts", 0))
    if attempts >= OTP_MAX_ATTEMPTS:
        session.pop("otp_hash", None)
        return jsonify({"success": False, "error": "Too many attempts. Request a new OTP."}), 429
    expected = hashlib.sha256((email + ":" + otp + app.secret_key).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(expected, session.get("otp_hash", "")):
        session["otp_attempts"] = attempts + 1
        return jsonify({"success": False, "error": "Invalid OTP."}), 400
    session["otp_verified_email"] = email
    session.pop("otp_hash", None)
    session.pop("otp_expires_at", None)
    session.pop("otp_attempts", None)
    return jsonify({"success": True})

@app.route('/api/set-password', methods=['POST'])
def set_password():
    data = _request_data()
    email = _normalise_email(data.get("email"))
    password = data.get("password")
    if email != session.get("otp_verified_email"):
        return jsonify({"success": False, "error": "Verify your OTP before setting a password."}), 401
    if not isinstance(password, str) or len(password) < 8 or len(password) > 128:
        return jsonify({"success": False, "error": "Password must be 8–128 characters long."}), 400

    hashed_pw = generate_password_hash(password)
    try:
        user = _db_user_by_email(email)
        if user:
            conn, db_type = get_db_connection()
            try:
                cursor = conn.cursor()
                ph = "%s" if db_type == "postgres" else "?"
                cursor.execute(f"UPDATE users SET password = {ph}, is_logged_in = TRUE WHERE email = {ph}", (hashed_pw, email))
                conn.commit()
            finally:
                conn.close()
            _set_login_session(email, user["name"], user["category"])
            return jsonify({"success": True, "is_new": False, "email": email, "name": user["name"],
                            "role": user["category"], "redirect_url": "/student_dashboard.html" if str(user["category"]).lower() == "student" else "/teacher_dashboard.html"})

        session["temp_password"] = hashed_pw
        return jsonify({"success": True, "is_new": True})
    except Exception:
        app.logger.exception("Password setup failed")
        return jsonify({"success": False, "error": "Unable to set password."}), 500

@app.route('/api/login-with-password', methods=['POST'])
def login_with_password():
    data = _request_data()
    email = _normalise_email(data.get("email"))
    password = data.get("password")
    if not email or not isinstance(password, str):
        return jsonify({"success": False, "error": "Email and password are required."}), 400
    try:
        user = _db_user_by_email(email)
        if not user or not user["password"] or not check_password_hash(user["password"], password):
            return jsonify({"success": False, "error": "Incorrect email or password."}), 401
        _set_login_session(email, user["name"], user["category"])
        conn, db_type = get_db_connection()
        try:
            cursor = conn.cursor()
            ph = "%s" if db_type == "postgres" else "?"
            cursor.execute(f"UPDATE users SET is_logged_in = TRUE WHERE email = {ph}", (email,))
            conn.commit()
        finally:
            conn.close()
        role = user["category"]
        return jsonify({"success": True, "email": email, "name": user["name"], "role": role,
                        "redirect_url": "/student_dashboard.html" if str(role).lower() == "student" else "/teacher_dashboard.html"})
    except Exception:
        app.logger.exception("Password login failed")
        return jsonify({"success": False, "error": "Unable to sign in."}), 500

@app.route('/api/complete-signup', methods=['POST'])
def complete_signup():
    data = _request_data()
    email = _normalise_email(data.get("email"))
    name = str(data.get("name") or "").strip()[:255]
    requested_role = str(data.get("role", "Student")).strip().title()
    otp_email = session.get("otp_verified_email")
    google_email = session.get("pending_google_email")
    if not email or email not in (otp_email, google_email):
        return jsonify({"success": False, "error": "Complete email or Google verification first."}), 401
    if not name:
        name = session.get("pending_google_name") or email.split("@")[0]
    if requested_role not in ("Student", "Teacher"):
        return jsonify({"success": False, "error": "Invalid account type."}), 400
    photo_url = session.get("pending_google_photo") if google_email == email else None
    password_hash = session.get("temp_password") if otp_email == email else None

    try:
        conn, db_type = get_db_connection()
        try:
            cursor = conn.cursor()
            ph = "%s" if db_type == "postgres" else "?"
            cursor.execute(
                f"INSERT INTO users (email, name, category, photo_url, password, is_logged_in) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, TRUE)",
                (email, name, requested_role, photo_url, password_hash),
            )
            conn.commit()
        finally:
            conn.close()
        _set_login_session(email, name, requested_role)
        return jsonify({"success": True, "redirect_url": "/student_dashboard.html" if requested_role == "Student" else "/teacher_dashboard.html"})
    except Exception:
        app.logger.exception("Account creation failed")
        return jsonify({"success": False, "error": "Account could not be created. It may already exist."}), 409

@app.route('/api/get-student-progress', methods=['POST'])
def get_student_progress():
    user = _current_user()
    if not user:
        return jsonify({"success": False, "error": "Login required."}), 401
    email = _normalise_email(user.get('email'))
    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"""SELECT r.id, r.exam_code, r.score, r.total_questions, r.date_taken,
                    COALESCE(NULLIF(SUM(q.max_marks), 0), r.total_questions) AS max_marks
                    FROM results r
                    LEFT JOIN questions q ON q.exam_code = r.exam_code
                    WHERE r.student_email = {ph}
                    GROUP BY r.id, r.exam_code, r.score, r.total_questions, r.date_taken
                    ORDER BY r.date_taken DESC, r.id DESC""", (email,))
        rows = cursor.fetchall()
        percentages = []
        total_score = 0.0
        for row in rows:
            score = float(_row_value(row, 'score', 2) or 0)
            denominator = float(_row_value(row, 'max_marks', 5) or _row_value(row, 'total_questions', 3) or 0)
            total_score += score
            if denominator > 0:
                percentages.append(max(0.0, min(100.0, score / denominator * 100.0)))
        recent = percentages[:10]
        average = sum(recent) / len(recent) if recent else 0.0
        highest = max(recent) if recent else 0.0
        lowest = min(recent) if recent else 0.0
        return jsonify({"success": True, "total_exams": len(rows),
                        "highest": round(highest, 2), "lowest": round(lowest, 2),
                        "average_percentage": round(average, 2),
                        "average_exam_count": len(recent), "score": round(total_score, 2)})
    except Exception:
        app.logger.exception("Student progress calculation failed")
        return jsonify({"success": False, "error": "Database error"}), 500
    finally:
        conn.close()

# ------------------------- Gamification backend -------------------------
XP_LEVEL_SIZE = 100
XP_CONSECUTIVE_THRESHOLDS = ((3, 5), (5, 10), (10, 20), (15, 30), (20, 40))



def _record_xp(cursor, ph, email, event_type, reference_key, amount, description):
    """Insert an auditable XP transaction once; return whether it was new."""
    if amount == 0:
        return False
    if ph == '%s':
        cursor.execute(
            "INSERT INTO xp_transactions (student_email, event_type, reference_key, xp_delta, description) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (student_email, event_type, reference_key) DO NOTHING",
            (email, event_type, reference_key, int(amount), description),
        )
    else:
        cursor.execute(
            "INSERT OR IGNORE INTO xp_transactions (student_email, event_type, reference_key, xp_delta, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, event_type, reference_key, int(amount), description),
        )
    return cursor.rowcount > 0


def _refresh_gamification_totals(cursor, ph, email):
    cursor.execute(f"SELECT COALESCE(SUM(xp_delta), 0) AS total FROM xp_transactions WHERE student_email = {ph}", (email,))
    total = int(_row_value(cursor.fetchone(), 'total', 0) or 0)
    # Level never falls below 1; XP itself is allowed to become negative.
    level = max(1, (total // XP_LEVEL_SIZE) + 1)
    cursor.execute(f"""INSERT INTO student_gamification
        (student_email, xp_total, level, updated_at) VALUES ({ph}, {ph}, {ph}, CURRENT_TIMESTAMP)
        ON CONFLICT (student_email) DO UPDATE SET xp_total = EXCLUDED.xp_total,
        level = EXCLUDED.level, updated_at = CURRENT_TIMESTAMP""" if ph == '%s' else
        f"""INSERT INTO student_gamification (student_email, xp_total, level, updated_at)
        VALUES ({ph}, {ph}, {ph}, CURRENT_TIMESTAMP) ON CONFLICT(student_email) DO UPDATE SET
        xp_total = excluded.xp_total, level = excluded.level, updated_at = CURRENT_TIMESTAMP""",
        (email, total, level),
    )
    return total, level


def _award_first_exam_xp(cursor, db_type, email, exam_code, exam_answers):
    """Award XP only after the unique first-attempt ledger row is inserted."""
    ph = '%s' if db_type == 'postgres' else '?'
    cursor.execute(f"SELECT id, correct_option, question_type, max_marks FROM questions WHERE exam_code = {ph} ORDER BY id", (exam_code,))
    questions = cursor.fetchall()
    mcq_questions = [q for q in questions if str(_row_value(q, 'question_type', 2) or 'mcq').lower() == 'mcq']

    correct_count = wrong_count = current_run = max_run = 0
    score = 0.0
    negative_marks = 0.0
    cursor.execute(f"SELECT negative_marks FROM exams WHERE exam_code = {ph}", (exam_code,))
    exam_row = cursor.fetchone()
    if exam_row:
        negative_marks = float(_row_value(exam_row, 'negative_marks', 0) or 0)

    for q in mcq_questions:
        qid = str(_row_value(q, 'id', 0))
        correct = str(_row_value(q, 'correct_option', 1) or '').strip().upper()
        selected = str(exam_answers.get(qid, '') or '').strip().upper()
        max_marks = float(_row_value(q, 'max_marks', 3) or 1.0)
        if selected and selected == correct:
            correct_count += 1
            current_run += 1
            max_run = max(max_run, current_run)
            score += max_marks
        else:
            current_run = 0
            if selected:
                wrong_count += 1
                score -= negative_marks

    mcq_count = len(mcq_questions)
    accuracy = (correct_count / mcq_count) * 100.0 if mcq_count else 0.0

    # This insert is the permanent idempotency lock. A re-attempt never earns or loses XP.
    if db_type == 'postgres':
        cursor.execute("""INSERT INTO exam_xp_attempts
            (student_email, exam_code, mcq_accuracy, correct_count, wrong_count, mcq_count, max_consecutive_correct)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (student_email, exam_code) DO NOTHING""",
            (email, exam_code, accuracy, correct_count, wrong_count, mcq_count, max_run))
    else:
        cursor.execute("""INSERT OR IGNORE INTO exam_xp_attempts
            (student_email, exam_code, mcq_accuracy, correct_count, wrong_count, mcq_count, max_consecutive_correct)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (email, exam_code, accuracy, correct_count, wrong_count, mcq_count, max_run))
    if cursor.rowcount == 0:
        return {"eligible": False, "xp_delta": 0, "reason": "XP was already processed for this exam."}

    awards = []
    def award(event, ref, amount, label):
        if _record_xp(cursor, ph, email, event, ref, amount, label):
            awards.append({"event": event, "xp": int(amount), "description": label})

    # First completion of this distinct exam.
    award('exam_first_completion', exam_code, 20, 'First completion of a distinct exam')

    # First exam completed on this local calendar day.
    cursor.execute(f"SELECT COUNT(*) AS n FROM xp_transactions WHERE student_email = {ph} AND event_type = 'daily_first_exam' AND reference_key = {ph}", (email, datetime.now(ZoneInfo('Asia/Kolkata')).date().isoformat()))
    if int(_row_value(cursor.fetchone(), 'n', 0) or 0) == 0:
        award('daily_first_exam', datetime.now(ZoneInfo('Asia/Kolkata')).date().isoformat(), 5, 'First exam completed today')

    # Streak is based on first-time completions, not re-attempts.
    today = datetime.now(ZoneInfo('Asia/Kolkata')).date()
    cursor.execute(f"SELECT current_streak, longest_streak, last_activity_date FROM student_gamification WHERE student_email = {ph}", (email,))
    streak_row = cursor.fetchone()
    old_streak = int(_row_value(streak_row, 'current_streak', 0) or 0) if streak_row else 0
    old_longest = int(_row_value(streak_row, 'longest_streak', 1) or 0) if streak_row else 0
    last_day = _row_value(streak_row, 'last_activity_date', 2) if streak_row else None
    if isinstance(last_day, str):
        try: last_day = datetime.fromisoformat(last_day).date()
        except ValueError: last_day = None
    if last_day == today:
        new_streak = old_streak
    elif last_day == today.fromordinal(today.toordinal() - 1):
        new_streak = old_streak + 1
    else:
        new_streak = 1
    longest = max(old_longest, new_streak)
    cursor.execute(f"""INSERT INTO student_gamification
        (student_email, current_streak, longest_streak, last_activity_date, updated_at)
        VALUES ({ph}, {ph}, {ph}, {ph}, CURRENT_TIMESTAMP)
        ON CONFLICT (student_email) DO UPDATE SET current_streak = EXCLUDED.current_streak,
        longest_streak = EXCLUDED.longest_streak, last_activity_date = EXCLUDED.last_activity_date,
        updated_at = CURRENT_TIMESTAMP""" if db_type == 'postgres' else
        f"""INSERT INTO student_gamification (student_email, current_streak, longest_streak, last_activity_date, updated_at)
        VALUES ({ph}, {ph}, {ph}, {ph}, CURRENT_TIMESTAMP) ON CONFLICT(student_email) DO UPDATE SET
        current_streak = excluded.current_streak, longest_streak = excluded.longest_streak,
        last_activity_date = excluded.last_activity_date, updated_at = CURRENT_TIMESTAMP""",
        (email, new_streak, longest, today.isoformat()))
    if new_streak >= 3:
        award('streak_milestone', 'streak_3', 15, 'Reached a 3-day completion streak')
    if new_streak >= 7:
        award('streak_milestone', 'streak_7', 40, 'Reached a 7-day completion streak')

    # Consecutive correct-answer rewards: one award per threshold per first attempt.
    for threshold, reward in XP_CONSECUTIVE_THRESHOLDS:
        if mcq_count and max_run >= threshold:
            award('answer_streak', f'{exam_code}:{threshold}', reward, f'{threshold} consecutive correct answers')

    # Wrong answers cost XP; a blank answer is not counted as wrong.
    if wrong_count:
        award('wrong_answers', exam_code, -2 * wrong_count, f'{wrong_count} incorrect MCQ answer(s)')

    # Perfect means every MCQ question in the exam was answered correctly.
    if mcq_count and correct_count == mcq_count:
        award('perfect_exam', exam_code, 50, 'All MCQ answers correct')

    # Accuracy adjustment compares against prior distinct first-attempt exams only.
    baseline = None
    if mcq_count:
        cursor.execute(f"SELECT AVG(mcq_accuracy) AS avg_accuracy FROM exam_xp_attempts WHERE student_email = {ph} AND exam_code <> {ph} AND mcq_count > 0", (email, exam_code))
        baseline_row = cursor.fetchone()
        baseline = _row_value(baseline_row, 'avg_accuracy', 0) if baseline_row else None
    if baseline is not None:
        adjustment = int(round((accuracy - float(baseline)) * 2))
        if adjustment:
            award('accuracy_adjustment', exam_code, adjustment, f'Accuracy change versus previous average ({float(baseline):.2f}% to {accuracy:.2f}%)')

    # 10 distinct exams completed: one-time milestone.
    cursor.execute(f"SELECT COUNT(*) AS n FROM exam_xp_attempts WHERE student_email = {ph}", (email,))
    completed_count = int(_row_value(cursor.fetchone(), 'n', 0) or 0)
    if completed_count >= 10:
        award('exam_milestone', '10_distinct_exams', 50, 'Completed 10 distinct exams')

    total, level = _refresh_gamification_totals(cursor, ph, email)
    net = sum(item['xp'] for item in awards)
    cursor.execute(f"UPDATE exam_xp_attempts SET xp_net = {ph} WHERE student_email = {ph} AND exam_code = {ph}", (net, email, exam_code))
    return {"eligible": True, "xp_delta": net, "xp_total": total, "level": level,
            "current_streak": new_streak, "longest_streak": longest,
            "mcq_accuracy": round(accuracy, 2), "awards": awards}


@app.route('/api/gamification/summary', methods=['GET'])
def get_gamification_summary():
    user = _current_user()
    if not user:
        return jsonify({"success": False, "error": "Login required."}), 401
    email = _normalise_email(user.get('email'))
    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = '%s' if db_type == 'postgres' else '?'
        cursor.execute(f"SELECT COALESCE(SUM(xp_delta), 0) AS xp_total FROM xp_transactions WHERE student_email = {ph}", (email,))
        xp_row = cursor.fetchone()
        xp_total = int(_row_value(xp_row, 'xp_total', 0) or 0) if xp_row else 0
        level = max(1, (xp_total // XP_LEVEL_SIZE) + 1)
        xp_in_level = xp_total - ((level - 1) * XP_LEVEL_SIZE)
        xp_to_next = max(0, XP_LEVEL_SIZE - xp_in_level)
        cursor.execute(f"SELECT current_streak, longest_streak FROM student_gamification WHERE student_email = {ph}", (email,))
        row = cursor.fetchone()
        current_streak = int(_row_value(row, 'current_streak', 0) or 0) if row else 0
        longest_streak = int(_row_value(row, 'longest_streak', 1) or 0) if row else 0
        return jsonify({"success": True, "xp_total": xp_total, "level": level,
                        "level_name": f"Level {level}", "xp_in_level": xp_in_level,
                        "xp_per_level": XP_LEVEL_SIZE, "xp_to_next_level": xp_to_next,
                        "level_progress_percent": round(max(0, min(100, xp_in_level / XP_LEVEL_SIZE * 100)), 2),
                        "current_streak": current_streak, "longest_streak": longest_streak})
    except Exception:
        app.logger.exception('Gamification summary failed')
        return jsonify({"success": False, "error": "Could not load gamification data."}), 500
    finally:
        conn.close()


@app.route('/api/profile/badges', methods=['GET'])
def get_profile_badges():
    """Return computed profile badges and streak calendar for the signed-in student."""
    user = _current_user()
    if not user:
        return jsonify({"success": False, "error": "Login required."}), 401
    email = _normalise_email(user.get('email'))
    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"""SELECT r.id, r.exam_code, r.score, r.total_questions, r.date_taken,
                    COALESCE(e.subject, 'General') AS subject,
                    COALESCE(NULLIF((SELECT SUM(q.max_marks) FROM questions q WHERE q.exam_code = r.exam_code), 0), r.total_questions) AS max_marks
                    FROM results r LEFT JOIN exams e ON e.exam_code = r.exam_code
                    WHERE r.student_email = {ph} ORDER BY r.date_taken DESC, r.id DESC""", (email,))
        result_rows = cursor.fetchall()
        exam_items = []
        active_dates = set()
        for row in result_rows:
            score = float(_row_value(row, 'score', 2) or 0)
            denominator = float(_row_value(row, 'max_marks', 6) or _row_value(row, 'total_questions', 3) or 0)
            pct = max(0.0, min(100.0, score / denominator * 100.0)) if denominator > 0 else 0.0
            raw_date = _row_value(row, 'date_taken', 4)
            date_text = str(raw_date)[:10] if raw_date else ''
            if date_text:
                active_dates.add(date_text)
            exam_items.append({"exam_code": str(_row_value(row, 'exam_code', 1) or ''),
                               "subject": str(_row_value(row, 'subject', 5) or 'General').strip(),
                               "percentage": pct, "date": date_text})

        # Achievement milestones are permanent: the total completed exam count only grows.
        exam_count = len(exam_items)
        achievement_defs = [
            (1, 'First Step', 'Complete your first exam', 'fa-flag-checkered', 'first'),
            (3, 'Getting Started', 'Complete 3 exams', 'fa-shoe-prints', 'starter'),
            (5, 'Curious Learner', 'Complete 5 exams', 'fa-book-open', 'curious'),
            (10, 'Regular Learner', 'Complete 10 exams', 'fa-calendar-check', 'regular'),
            (25, 'Dedicated Student', 'Complete 25 exams', 'fa-award', 'dedicated'),
            (50, 'Exam Master', 'Complete 50 exams', 'fa-trophy', 'master'),
            (100, 'Exam Legend', 'Complete 100 exams', 'fa-crown', 'legend')
        ]
        achievements = [{"code": f"exam_{n}", "name": name, "description": desc,
                         "icon": icon, "theme": theme, "threshold": n,
                         "current": exam_count, "progress": min(100, round(exam_count / n * 100)),
                         "unlocked": exam_count >= n, "permanent": True}
                        for n, name, desc, icon, theme in achievement_defs]

        # Performance badges use only the ten most recent completed result records.
        recent_pcts = [x['percentage'] for x in exam_items[:10]]
        recent_avg = sum(recent_pcts) / len(recent_pcts) if recent_pcts else 0.0
        performance_defs = [
            (20, 'First Spark', 'fa-seedling', 'spark'), (40, 'Rising Learner', 'fa-arrow-trend-up', 'rise'),
            (50, 'Good Performer', 'fa-star', 'star'), (60, 'Skilled Learner', 'fa-gem', 'gem'),
            (70, 'Excellent', 'fa-medal', 'excellent'), (80, 'Outstanding', 'fa-certificate', 'outstanding'),
            (90, 'Perfectionist', 'fa-crown', 'perfect'), (100, 'Flawless', 'fa-sun', 'flawless')
        ]
        performance = [{"code": f"performance_{n}", "name": name,
                       "description": f"Reach {n}% average across your latest 10 exams",
                       "icon": icon, "theme": theme, "threshold": n,
                       "current": round(recent_avg, 2),
                       "progress": min(100, round(recent_avg / n * 100)) if n else 0,
                       "unlocked": recent_avg >= n, "permanent": False}
                      for n, name, icon, theme in performance_defs]

        cursor.execute(f"SELECT COALESCE(SUM(xp_delta), 0) AS total FROM xp_transactions WHERE student_email = {ph}", (email,))
        xp_row = cursor.fetchone()
        xp_total = int(_row_value(xp_row, 'total', 0) or 0) if xp_row else 0
        xp_defs = [
            (100, 'XP Beginner', 'fa-bolt', 'xp_bronze'), (300, 'XP Explorer', 'fa-compass', 'xp_silver'),
            (600, 'XP Warrior', 'fa-shield-halved', 'xp_gold'), (1000, 'XP Champion', 'fa-medal', 'xp_champion'),
            (2000, 'XP Master', 'fa-gem', 'xp_master'), (5000, 'XP Legend', 'fa-crown', 'xp_legend')
        ]
        xp_badges = [{"code": f"xp_{n}", "name": name, "description": f"Reach {n} total XP",
                      "icon": icon, "theme": theme, "threshold": n, "current": xp_total,
                      "progress": min(100, round(max(0, xp_total) / n * 100)),
                      "unlocked": xp_total >= n, "permanent": False}
                     for n, name, icon, theme in xp_defs]

        # Subject mastery is based on all completed exams in that subject; minimum three.
        subject_stats = {}
        for item in exam_items:
            key = item['subject'].strip().casefold()
            if key not in ('bengali', 'english'):
                continue
            if key not in subject_stats:
                subject_stats[key] = {"subject": item['subject'].strip(), "percentages": []}
            subject_stats[key]['percentages'].append(item['percentage'])
        mastery = []
        for key, subject in [('bengali', 'Bengali'), ('english', 'English')]:
            values = subject_stats.get(key, {}).get('percentages', [])
            avg = sum(values) / len(values) if values else 0.0
            count = len(values)
            mastery.append({"code": f"mastery_{key}", "name": f"{subject} Master",
                            "description": f"Complete at least 3 {subject} exams and maintain a 90% subject average",
                            "icon": 'fa-language' if key == 'bengali' else 'fa-book-open',
                            "theme": 'mastery_bengali' if key == 'bengali' else 'mastery_english',
                            "threshold": 90, "current": round(avg, 2), "exam_count": count,
                            "required_exams": 3,
                            "progress": min(100, round(count / 3 * 100)) if count < 3 else min(100, round(avg / 90 * 100)),
                            "unlocked": count >= 3 and avg >= 90, "permanent": False})

        # Streak Journey reads the immutable attempt ledger, so retakes are not lost.
        today = datetime.now(STREAK_TIMEZONE).date()
        if db_type == 'postgres':
            cursor.execute(f"SELECT DISTINCT completed_at::date AS active_date FROM streak_activity WHERE student_email = {ph}", (email,))
        else:
            cursor.execute(f"SELECT DISTINCT substr(completed_at,1,10) AS active_date FROM streak_activity WHERE student_email = {ph}", (email,))
        active_dates = {str(_row_value(r, 'active_date', 0))[:10] for r in cursor.fetchall() if _row_value(r, 'active_date', 0)}
        active_date_objects = {datetime.strptime(value, '%Y-%m-%d').date() for value in active_dates}
        grace_dates, current_streak, longest = _streak_day_sets(active_date_objects, today)
        week_start = today - timedelta(days=(today.weekday() + 1) % 7)
        week_days = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            week_days.append({"date": day.isoformat(), "day": day.strftime('%a').upper(),
                              "active": day.isoformat() in active_dates, "is_today": day == today,
                              "grace": day in grace_dates})
        total_active_days = len(active_dates)
        return jsonify({"success": True, "exam_count": exam_count,
                        "achievements": achievements, "performance": performance,
                        "xp_badges": xp_badges, "mastery": mastery,
                        "performance_average": round(recent_avg, 2),
                        "performance_exam_count": len(recent_pcts), "xp_total": xp_total,
                        "streak": {"current": current_streak, "longest": longest,
                                   "total_active_days": total_active_days,
                                   "week": week_days}})
    except Exception:
        app.logger.exception("Profile badge summary failed")
        return jsonify({"success": False, "error": "Could not load profile badges."}), 500
    finally:
        conn.close()


@app.route('/api/gamification/transactions', methods=['GET'])
def get_gamification_transactions():
    user = _current_user()
    if not user:
        return jsonify({"success": False, "error": "Login required."}), 401
    email = _normalise_email(user.get('email'))
    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = '%s' if db_type == 'postgres' else '?'
        cursor.execute(f"SELECT event_type, xp_delta, description, created_at FROM xp_transactions WHERE student_email = {ph} ORDER BY created_at DESC LIMIT 100", (email,))
        rows = cursor.fetchall()
        return jsonify({"success": True, "transactions": [
            {"event_type": _row_value(r, 'event_type', 0), "xp_delta": int(_row_value(r, 'xp_delta', 1)),
             "description": _row_value(r, 'description', 2), "created_at": str(_row_value(r, 'created_at', 3))}
            for r in rows]})
    except Exception:
        app.logger.exception('Gamification transactions failed')
        return jsonify({"success": False, "error": "Could not load XP history."}), 500
    finally:
        conn.close()


@app.route('/api/get-exam-questions-v2', methods=['POST'])
def get_exam_questions_v2():
    """Frontend integration endpoint: returns questions but never the answer key."""
    data = _request_data()
    exam_code = str(data.get('exam_code') or '').strip()
    if not exam_code:
        return jsonify({"success": False, "error": "Exam code is required."}), 400
    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = '%s' if db_type == 'postgres' else '?'
        cursor.execute(f"SELECT exam_name, timer_minutes, negative_marks, class_name, subject, is_private, expiry_time, season FROM exams WHERE exam_code = {ph}", (exam_code,))
        exam = cursor.fetchone()
        if not exam:
            return jsonify({"success": False, "error": "Exam not found."}), 404
        cursor.execute(f"SELECT id, question_text, option_a, option_b, option_c, option_d, question_type, max_marks FROM questions WHERE exam_code = {ph} ORDER BY id", (exam_code,))
        question_rows = cursor.fetchall()
        questions = []
        for q in question_rows:
            q_type = str(_row_value(q, 'question_type', 6) or 'mcq')
            max_marks = _row_value(q, 'max_marks', 7)
            questions.append({
                "id": _row_value(q, 'id', 0),
                "q_text": _row_value(q, 'question_text', 1),
                "opt_a": _row_value(q, 'option_a', 2),
                "opt_b": _row_value(q, 'option_b', 3),
                "opt_c": _row_value(q, 'option_c', 4),
                "opt_d": _row_value(q, 'option_d', 5),
                "question_type": q_type,
                "max_marks": float(max_marks if max_marks is not None else (1.0 if q_type == 'mcq' else 5.0)),
            })
        return jsonify({
            "success": True,
            "exam_name": _row_value(exam, 'exam_name', 0),
            "timer_minutes": _row_value(exam, 'timer_minutes', 1),
            "negative_marks": float(_row_value(exam, 'negative_marks', 2) or 0),
            "class_name": _row_value(exam, 'class_name', 3) if 'class_name' in (exam.keys() if hasattr(exam, 'keys') else []) else 'General',
            "subject": _row_value(exam, 'subject', 4) if 'subject' in (exam.keys() if hasattr(exam, 'keys') else []) else 'General',
            "is_private": _row_value(exam, 'is_private', 5) if 'is_private' in (exam.keys() if hasattr(exam, 'keys') else []) else False,
            "expiry_time": str(_row_value(exam, 'expiry_time', 6) or ''),
            "season": _row_value(exam, 'season', 7) if 'season' in (exam.keys() if hasattr(exam, 'keys') else []) else 'General',
            "questions": questions,
        })
    except Exception:
        app.logger.exception('V2 exam question fetch failed')
        return jsonify({"success": False, "error": "Could not load exam questions."}), 500
    finally:
        conn.close()



@app.route('/api/answer-exam-question-v2', methods=['POST'])
def answer_exam_question_v2():
    """Grade one MCQ answer and permanently lock the first selection for this attempt."""

    user = _current_user()
    if not user:
        return jsonify({"success": False, "error": "Login required."}), 401

    if str(user.get('role', '')).lower() != 'student':
        return jsonify({
            "success": False,
            "error": "Only student accounts can answer this exam."
        }), 403

    data = _request_data()
    email = _normalise_email(user.get('email'))
    exam_code = str(data.get('exam_code') or '').strip()
    attempt_id = str(data.get('attempt_id') or '').strip()
    selected = str(data.get('selected_option') or '').strip().upper()

    try:
        question_id = int(data.get('question_id'))
    except (TypeError, ValueError):
        question_id = 0

    if (
        not exam_code
        or not attempt_id
        or len(attempt_id) > 100
        or question_id <= 0
        or selected not in {'A', 'B', 'C', 'D'}
    ):
        return jsonify({
            "success": False,
            "error": "Invalid answer submission."
        }), 400

    conn, db_type = get_db_connection()

    try:
        cursor = conn.cursor()
        ph = '%s' if db_type == 'postgres' else '?'

        # 1. Find the question and its correct answer.
        cursor.execute(
            f"""
            SELECT correct_option, question_type
            FROM questions
            WHERE id = {ph} AND exam_code = {ph}
            """,
            (question_id, exam_code)
        )

        question = cursor.fetchone()

        if (
            not question
            or str(_row_value(question, 'question_type', 1) or 'mcq').lower() != 'mcq'
        ):
            return jsonify({
                "success": False,
                "error": "MCQ question not found."
            }), 404

        correct = str(
            _row_value(question, 'correct_option', 0) or ''
        ).strip().upper()

        is_correct = selected == correct

        # 2. Save only the first selected answer.
        if db_type == 'postgres':
            cursor.execute(
                """
                INSERT INTO exam_attempt_answers
                (student_email, exam_code, attempt_id, question_id,
                 selected_option, is_correct)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT
                (student_email, exam_code, attempt_id, question_id)
                DO NOTHING
                """,
                (
                    email, exam_code, attempt_id,
                    question_id, selected, is_correct
                )
            )
        else:
            cursor.execute(
                """
                INSERT OR IGNORE INTO exam_attempt_answers
                (student_email, exam_code, attempt_id, question_id,
                 selected_option, is_correct)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    email, exam_code, attempt_id,
                    question_id, selected, int(is_correct)
                )
            )

        # Whether this request actually inserted a new answer.
        was_new_answer = cursor.rowcount > 0

        # 3. Retrieve the permanently locked answer.
        cursor.execute(
            f"""
            SELECT selected_option, is_correct
            FROM exam_attempt_answers
            WHERE student_email = {ph}
              AND exam_code = {ph}
              AND attempt_id = {ph}
              AND question_id = {ph}
            """,
            (email, exam_code, attempt_id, question_id)
        )

        saved = cursor.fetchone()

        if not saved:
            conn.rollback()
            return jsonify({
                "success": False,
                "error": "Could not retrieve the saved answer."
            }), 500

        saved_option = str(
            _row_value(saved, 'selected_option', 0) or ''
        ).upper()

        saved_correct = bool(_row_value(saved, 'is_correct', 1))

        # 4. Get all MCQ question IDs in exam order.
        cursor.execute(
            f"""
            SELECT id
            FROM questions
            WHERE exam_code = {ph}
              AND LOWER(COALESCE(question_type, 'mcq')) = 'mcq'
            ORDER BY id
            """,
            (exam_code,)
        )

        ordered_mcq_ids = [
            str(_row_value(row, 'id', 0))
            for row in cursor.fetchall()
        ]

        # 5. Get all locked answers for this attempt.
        cursor.execute(
            f"""
            SELECT question_id, selected_option, is_correct
            FROM exam_attempt_answers
            WHERE student_email = {ph}
              AND exam_code = {ph}
              AND attempt_id = {ph}
            """,
            (email, exam_code, attempt_id)
        )

        locked_rows = {
            str(_row_value(row, 'question_id', 0)): row
            for row in cursor.fetchall()
        }

        # 6. Calculate each separate consecutive-correct streak.
        current_run = 0
        current_streak = 0
        wrong_count = 0
        answered_count = 0
        correct_count = 0
        xp_preview = 0
        milestones = []

        for ordered_id in ordered_mcq_ids:
            row = locked_rows.get(ordered_id)

            if not row:
                # An unanswered question breaks the current streak.
                if current_run > 0:
                    xp_preview += sum(
                        reward
                        for threshold, reward in XP_CONSECUTIVE_THRESHOLDS
                        if current_run >= threshold
                    )

                current_run = 0
                continue

            answered_count += 1

            if bool(_row_value(row, 'is_correct', 2)):
                correct_count += 1
                current_run += 1

                # Detect a newly achieved milestone.
                if (
                    was_new_answer
                    and ordered_id == str(question_id)
                    and saved_correct
                ):
                    for threshold, reward in XP_CONSECUTIVE_THRESHOLDS:
                        if current_run == threshold:
                            milestones.append({
                                "threshold": threshold,
                                "xp": reward
                            })

            else:
                wrong_count += 1

                # Award the previous streak before resetting it.
                if current_run > 0:
                    xp_preview += sum(
                        reward
                        for threshold, reward in XP_CONSECUTIVE_THRESHOLDS
                        if current_run >= threshold
                    )

                current_run = 0

        # 7. Include the final streak if the exam ends with correct answers.
        if current_run > 0:
            xp_preview += sum(
                reward
                for threshold, reward in XP_CONSECUTIVE_THRESHOLDS
                if current_run >= threshold
            )

        # The trailing consecutive-correct count.
        current_streak = current_run

        # 8. Apply wrong-answer penalty.
        xp_preview -= (2 * wrong_count)

        # 9. Perfect MCQ bonus.
        if (
            ordered_mcq_ids
            and answered_count == len(ordered_mcq_ids)
            and correct_count == len(ordered_mcq_ids)
        ):
            xp_preview += 50

        conn.commit()

        # 10. Send the result to the frontend.
        return jsonify({
            "success": True,
            "selected_option": saved_option,
            "is_correct": saved_correct,
            "correct_option": correct,
            "locked": True,
            "xp_preview": xp_preview,
            "current_streak": current_streak,
            "milestones": milestones
        }), 200

    except Exception:
        conn.rollback()
        app.logger.exception('V2 answer lock failed')

        return jsonify({
            "success": False,
            "error": "Could not save this answer."
        }), 500

    finally:
        conn.close()



@app.route('/api/get-exam-review-answers-v2', methods=['POST'])
def get_exam_review_answers_v2():
    """Reveal the answer key only after this authenticated student has a saved result."""
    user = _current_user()
    if not user or str(user.get('role', '')).lower() != 'student':
        return jsonify({"success": False, "error": "Login required."}), 401
    data = _request_data()
    email = _normalise_email(user.get('email'))
    exam_code = str(data.get('exam_code') or '').strip()
    if not exam_code:
        return jsonify({"success": False, "error": "Exam code is required."}), 400
    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = '%s' if db_type == 'postgres' else '?'
        cursor.execute(f"SELECT id FROM results WHERE student_email = {ph} AND exam_code = {ph} LIMIT 1", (email, exam_code))
        if not cursor.fetchone():
            return jsonify({"success": False, "error": "Submit this exam before viewing the answer key."}), 403
        cursor.execute(f"SELECT id, correct_option FROM questions WHERE exam_code = {ph} ORDER BY id", (exam_code,))
        rows = cursor.fetchall()
        correct_answers = {str(_row_value(row, 'id', 0)): str(_row_value(row, 'correct_option', 1) or '').upper() for row in rows}
        submitted_answers = {}
        cursor.execute(f"SELECT attempt_id FROM exam_submission_metadata WHERE student_email = {ph} AND exam_code = {ph} ORDER BY created_at DESC LIMIT 1", (email, exam_code))
        attempt_row = cursor.fetchone()
        if attempt_row:
            saved_attempt_id = str(_row_value(attempt_row, 'attempt_id', 0) or '')
            cursor.execute(f"SELECT question_id, selected_option FROM exam_attempt_answers WHERE student_email = {ph} AND exam_code = {ph} AND attempt_id = {ph}", (email, exam_code, saved_attempt_id))
            submitted_answers = {str(_row_value(row, 'question_id', 0)): str(_row_value(row, 'selected_option', 1) or '').upper() for row in cursor.fetchall()}
        return jsonify({"success": True, "answers": correct_answers, "student_answers": submitted_answers})
    except Exception:
        app.logger.exception('V2 review answer fetch failed')
        return jsonify({"success": False, "error": "Could not load answer review."}), 500
    finally:
        conn.close()


@app.route('/api/upload-exam-answer-image', methods=['POST'])
def upload_exam_answer_image():
    """Upload a cropped subjective answer image using a server-side ImgBB key."""
    user = _current_user()
    if not user or str(user.get('role', '')).lower() != 'student':
        return jsonify({"success": False, "error": "Student login required."}), 401
    image_file = request.files.get('image')
    api_key = os.getenv('IMGBB_API_KEY', '').strip()
    if not image_file:
        return jsonify({"success": False, "error": "No image received."}), 400
    if not api_key:
        return jsonify({"success": False, "error": "Image upload is not configured. Set IMGBB_API_KEY on the server."}), 503
    try:
        response = requests.post('https://api.imgbb.com/1/upload', data={'key': api_key}, files={'image': (secure_filename(image_file.filename or 'answer.jpg'), image_file.stream, image_file.mimetype or 'image/jpeg')}, timeout=25)
        result = response.json()
        if response.ok and result.get('success') and result.get('data', {}).get('url'):
            return jsonify({"success": True, "image_url": result['data']['url']})
        app.logger.warning('ImgBB upload rejected: %s', result.get('error', {}).get('message', 'unknown error'))
        return jsonify({"success": False, "error": "Image hosting rejected the upload."}), 502
    except requests.RequestException:
        app.logger.exception('ImgBB upload request failed')
        return jsonify({"success": False, "error": "Image upload network error."}), 502


@app.route('/api/submit-exam-result-v2', methods=['POST'])
def submit_exam_result_v2():
    """New contract for the later frontend integration; never trusts client score/correctness."""
    user = _current_user()
    if not user:
        return jsonify({"success": False, "error": "Login required to submit this version of the exam result."}), 401
    if str(user.get('role', '')).lower() != 'student':
        return jsonify({"success": False, "error": "Only student accounts can submit exam results."}), 403
    data = _request_data()
    email = _normalise_email(user.get('email'))
    exam_code = str(data.get('exam_code') or '').strip()
    attempt_id = str(data.get('attempt_id') or '').strip()
    client_answers = data.get('answers')
    subj_answers = data.get('subjective_answers', [])
    if not exam_code or not attempt_id or len(attempt_id) > 100 or not isinstance(client_answers, dict) or not isinstance(subj_answers, list):
        return jsonify({"success": False, "error": "exam_code, attempt_id, answers object and subjective_answers array are required."}), 400

    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = '%s' if db_type == 'postgres' else '?'
        cursor.execute(f"SELECT exam_name, negative_marks FROM exams WHERE exam_code = {ph}", (exam_code,))
        exam = cursor.fetchone()
        if not exam:
            return jsonify({"success": False, "error": "Exam not found."}), 404
        exam_name = _row_value(exam, 'exam_name', 0)
        negative_marks = float(_row_value(exam, 'negative_marks', 1) or 0)
        cursor.execute(f"SELECT id, question_type, max_marks FROM questions WHERE exam_code = {ph} ORDER BY id", (exam_code,))
        all_questions = cursor.fetchall()
        valid_ids = {str(_row_value(q, 'id', 0)) for q in all_questions}
        if any(str(k) not in valid_ids for k in client_answers.keys()):
            return jsonify({"success": False, "error": "One or more submitted question IDs do not belong to this exam."}), 400
        cursor.execute(f"SELECT question_id, selected_option FROM exam_attempt_answers WHERE student_email = {ph} AND exam_code = {ph} AND attempt_id = {ph}", (email, exam_code, attempt_id))
        locked_answers = {str(_row_value(row, 'question_id', 0)): str(_row_value(row, 'selected_option', 1) or '').upper() for row in cursor.fetchall()}
        # The server-side immutable selections are authoritative; client-supplied answer values are ignored.
        answers = locked_answers
        # Calculate the displayed objective score on the server; client score is ignored.
        objective_score = 0.0
        total_marks = 0.0
        for q in all_questions:
            qid = str(_row_value(q, 'id', 0))
            qtype = str(_row_value(q, 'question_type', 1) or 'mcq').lower()
            max_marks = float(_row_value(q, 'max_marks', 2) or (1.0 if qtype == 'mcq' else 5.0))
            total_marks += max_marks
            if qtype == 'mcq':
                selected = str(answers.get(qid, '') or '').strip().upper()
                cursor.execute(f"SELECT correct_option FROM questions WHERE id = {ph} AND exam_code = {ph}", (int(qid), exam_code))
                correct_row = cursor.fetchone()
                correct = str(_row_value(correct_row, 'correct_option', 0) or '').strip().upper() if correct_row else ''
                if selected and selected == correct:
                    objective_score += max_marks
                elif selected:
                    objective_score -= negative_marks

        # Submission telemetry is stored separately so legacy results schema remains compatible.
        if db_type == 'postgres':
            cursor.execute("""CREATE TABLE IF NOT EXISTS exam_submission_metadata (
                id BIGSERIAL PRIMARY KEY, student_email VARCHAR(255) NOT NULL, exam_code VARCHAR(100) NOT NULL,
                attempt_id VARCHAR(100) NOT NULL, warnings INTEGER NOT NULL DEFAULT 0, submit_reason VARCHAR(50),
                is_reattempt BOOLEAN NOT NULL DEFAULT FALSE, started_at TEXT, submitted_at TEXT,
                elapsed_seconds INTEGER NOT NULL DEFAULT 0, time_remaining_seconds INTEGER NOT NULL DEFAULT 0,
                client_question_count INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_email, exam_code, attempt_id))""")
        else:
            cursor.execute("""CREATE TABLE IF NOT EXISTS exam_submission_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT, student_email TEXT NOT NULL, exam_code TEXT NOT NULL,
                attempt_id TEXT NOT NULL, warnings INTEGER NOT NULL DEFAULT 0, submit_reason TEXT,
                is_reattempt INTEGER NOT NULL DEFAULT 0, started_at TEXT, submitted_at TEXT,
                elapsed_seconds INTEGER NOT NULL DEFAULT 0, time_remaining_seconds INTEGER NOT NULL DEFAULT 0,
                client_question_count INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_email, exam_code, attempt_id))""")
        cursor.execute(f"""INSERT INTO exam_submission_metadata
            (student_email, exam_code, attempt_id, warnings, submit_reason, is_reattempt, started_at, submitted_at,
             elapsed_seconds, time_remaining_seconds, client_question_count)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})
            ON CONFLICT(student_email, exam_code, attempt_id) DO UPDATE SET warnings=excluded.warnings,
            submit_reason=excluded.submit_reason, is_reattempt=excluded.is_reattempt, started_at=excluded.started_at,
            submitted_at=excluded.submitted_at, elapsed_seconds=excluded.elapsed_seconds,
            time_remaining_seconds=excluded.time_remaining_seconds, client_question_count=excluded.client_question_count""",
            (email, exam_code, attempt_id, max(0, int(data.get('warnings') or 0)), str(data.get('submit_reason') or 'manual')[:50],
             bool(data.get('is_reattempt')), str(data.get('started_at') or '')[:80], str(data.get('submitted_at') or '')[:80],
             max(0, int(data.get('elapsed_seconds') or 0)), max(0, int(data.get('time_remaining_seconds') or 0)),
             max(0, int(data.get('client_question_count') or 0))))

        # Keep the existing result-history behaviour; the XP ledger remains permanent.
        cursor.execute(f"DELETE FROM results WHERE student_email = {ph} AND exam_code = {ph}", (email, exam_code))
        cursor.execute(f"DELETE FROM subjective_answers WHERE student_email = {ph} AND exam_code = {ph}", (email, exam_code))
        cursor.execute(f"INSERT INTO results (student_email, exam_code, exam_name, score, total_questions) VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
                       (email, exam_code, exam_name, objective_score, int(round(total_marks))))
        _record_streak_activity(cursor, db_type, email, exam_code, exam_name,
            score=round(objective_score, 2), total_marks=round(total_marks, 2),
            source_key=f"v2:{email}:{exam_code}:{attempt_id}")
        for sa in subj_answers:
            try:
                qid = int(sa.get('question_id'))
            except (TypeError, ValueError, AttributeError):
                return jsonify({"success": False, "error": "Invalid subjective question ID."}), 400
            if str(qid) not in valid_ids or not isinstance(sa.get('image_url'), str) or not sa.get('image_url', '').startswith(('http://', 'https://')):
                return jsonify({"success": False, "error": "Invalid subjective answer data."}), 400
            cursor.execute(f"INSERT INTO subjective_answers (exam_code, student_email, question_id, image_url) VALUES ({ph}, {ph}, {ph}, {ph})",
                           (exam_code, email, qid, sa['image_url']))

        xp_result = _award_first_exam_xp(cursor, db_type, email, exam_code, {str(k): v for k, v in answers.items()})
        conn.commit()
        return jsonify({"success": True, "message": "Result saved successfully.", "score": round(objective_score, 2),
                        "total_marks": round(total_marks, 2), "xp": xp_result})
    except Exception:
        conn.rollback()
        app.logger.exception('V2 exam result submission failed')
        return jsonify({"success": False, "error": "Failed to save exam result."}), 500
    finally:
        conn.close()


@app.route('/api/check-exam', methods=['POST'])
def check_exam():
    data = request.get_json()
    exam_code = data.get('exam_code')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT exam_name FROM exams WHERE exam_code = {ph}", (exam_code,))
        exam = cursor.fetchone()
        conn.close()
        if exam: return jsonify({"success": True, "exam_name": exam['exam_name'] if isinstance(exam, dict) else exam[0]})
        return jsonify({"success": False, "error": "No Exam Found!"})
    except Exception as e: return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/get-exam-questions', methods=['POST'])
def get_exam_questions():
    data = request.get_json()
    exam_code = data.get('exam_code')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(f"SELECT exam_name, timer_minutes, negative_marks, class_name, subject, is_private, expiry_time, season FROM exams WHERE exam_code = {ph}", (exam_code,))
        exam_info = cursor.fetchone()
        if not exam_info:
            conn.close()
            return jsonify({"success": False, "error": "Exam not found!"}), 404

        cursor.execute(f"SELECT id, question_text, option_a, option_b, option_c, option_d, correct_option, question_type, max_marks FROM questions WHERE exam_code = {ph}", (exam_code,))
        questions_rows = cursor.fetchall()
        conn.close()

        questions_list = []
        for q in questions_rows:
            q_type = q["question_type"] if "question_type" in q.keys() else "mcq"
            m_marks = q["max_marks"] if "max_marks" in q.keys() and q["max_marks"] is not None else (1.0 if q_type == 'mcq' else 5.0)
            questions_list.append({
                "id": q["id"], "q_text": q["question_text"], 
                "opt_a": q["option_a"], "opt_b": q["option_b"], 
                "opt_c": q["option_c"], "opt_d": q["option_d"],
                # Never expose the answer key in the student-facing question endpoint.
                "question_type": q_type,
                "max_marks": float(m_marks)
            })
        
        if isinstance(exam_info, dict):
            e_name = exam_info['exam_name']
            e_time = exam_info['timer_minutes']
            e_neg = exam_info.get('negative_marks', 0.0)
            c_name = exam_info.get('class_name', 'General')
            subj = exam_info.get('subject', 'General')
            is_priv = exam_info.get('is_private', False)
            exp_time = str(exam_info.get('expiry_time', '')) if exam_info.get('expiry_time') else ''
            seas = exam_info.get('season', 'General')
        else:
            e_name = exam_info[0]
            e_time = exam_info[1]
            e_neg = exam_info[2] if len(exam_info) > 2 else 0.0
            c_name = exam_info[3] if len(exam_info) > 3 else 'General'
            subj = exam_info[4] if len(exam_info) > 4 else 'General'
            is_priv = exam_info[5] if len(exam_info) > 5 else False
            exp_time = str(exam_info[6]) if len(exam_info) > 6 and exam_info[6] else ''
            seas = exam_info[7] if len(exam_info) > 7 and exam_info[7] else 'General'

        return jsonify({ 
            "success": True, 
            "exam_name": e_name, 
            "timer_minutes": e_time, 
            "negative_marks": float(e_neg) if e_neg else 0.0, 
            "class_name": c_name,
            "subject": subj,
            "is_private": is_priv,
            "expiry_time": exp_time.split('.')[0].replace(' ', 'T') if exp_time else '',
            "season": seas,
            "questions": questions_list 
        })
    except Exception:
        app.logger.exception("Request failed")
        return jsonify({"success": False, "error": "Request could not be completed."}), 500

@app.route('/api/submit-exam-result', methods=['POST'])
def submit_exam_result():
    data = _request_data()
    logged_user = _current_user()
    # An anonymous visitor cannot submit a result under an existing user's email.
    email = _normalise_email(logged_user.get('email')) if logged_user else f"guest_{secrets.token_hex(8)}@guest.com"
    name = logged_user.get('name') if logged_user else str(data.get('name') or 'Guest Student')[:255]
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    try:
        score = float(data.get('score', 0))
        total_q = int(data.get('total_questions'))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid score or question count."}), 400
    subj_answers = data.get('subjective_answers', [])
    if not exam_code or not exam_name or total_q < 1 or score < 0 or not isinstance(subj_answers, list):
        return jsonify({"success": False, "error": "Required exam result data is invalid."}), 400

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        # ১. নিশ্চিত করছি যে এই গেস্ট ইউজারটি `users` টেবিলে রেজিস্টার্ড আছে, 
        # যাতে লিডারবোর্ডের JOIN কুয়েরি কোনো এরর না দেয়।
        cursor.execute(f"SELECT email FROM users WHERE email = {ph}", (email,))
        user_exists = cursor.fetchone()
        if not user_exists:
            cursor.execute(f"INSERT INTO users (email, name, category, is_logged_in) VALUES ({ph}, {ph}, {ph}, {ph})", (email, name, 'Student', False))
        
        # ২. পুরনো ফলাফল ডিলিট করে নতুন ফলাফল সেভ করা
        cursor.execute(f"DELETE FROM results WHERE student_email = {ph} AND exam_code = {ph}", (email, exam_code))
        cursor.execute(f"DELETE FROM subjective_answers WHERE student_email = {ph} AND exam_code = {ph}", (email, exam_code))
        
        cursor.execute(f"INSERT INTO results (student_email, exam_code, exam_name, score, total_questions) VALUES ({ph}, {ph}, {ph}, {ph}, {ph})", (email, exam_code, exam_name, score, total_q))
        _record_streak_activity(cursor, db_type, email, exam_code, exam_name,
            score=score, total_marks=float(total_q), source_key=f"legacy-submit:{email}:{exam_code}:{secrets.token_hex(12)}")
        
        for sa in subj_answers:
            cursor.execute(f"INSERT INTO subjective_answers (exam_code, student_email, question_id, image_url) VALUES ({ph}, {ph}, {ph}, {ph})", (exam_code, email, sa['question_id'], sa['image_url']))

        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Result saved successfully!"})
    except Exception as e: 
        print("Submit Error:", e)
        return jsonify({"success": False, "error": "Failed to save result."}), 500

@app.route('/api/streak-history', methods=['GET'])
def get_streak_history():
    user = _current_user()
    if not user or str(user.get('role', '')).lower() != 'student':
        return jsonify({"success": False, "error": "Student login required."}), 401
    email = _normalise_email(user.get('email'))
    now = datetime.now(STREAK_TIMEZONE)
    today = now.date()
    try:
        year = int(request.args.get('year', today.year))
        month = int(request.args.get('month', today.month))
        if year < 2000 or year > today.year or month < 1 or month > 12:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid month or year."}), 400

    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = '%s' if db_type == 'postgres' else '?'
        cursor.execute(f"""SELECT id, exam_code, exam_name, score, total_marks, completed_at
            FROM streak_activity WHERE student_email={ph} ORDER BY completed_at ASC, id ASC""", (email,))
        rows = cursor.fetchall()
        activities_by_date = {}
        active_dates = set()
        all_activities = []
        for row in rows:
            raw = str(_row_value(row, 'completed_at', 5) or '')
            day_text = raw[:10]
            try:
                day = datetime.strptime(day_text, '%Y-%m-%d').date()
            except ValueError:
                continue
            if day > today:
                continue
            score_value = _row_value(row, 'score', 3)
            total_value = _row_value(row, 'total_marks', 4)
            score = float(score_value) if score_value is not None else None
            total = float(total_value) if total_value is not None else None
            pct = round(max(0.0, min(100.0, score / total * 100.0)), 2) if score is not None and total and total > 0 else None
            item = {
                'id': _row_value(row, 'id', 0),
                'exam_code': str(_row_value(row, 'exam_code', 1) or ''),
                'exam_name': str(_row_value(row, 'exam_name', 2) or 'Completed Exam'),
                'score': score, 'total_marks': total, 'percentage': pct,
                'completed_at': raw,
            }
            activities_by_date.setdefault(day_text, []).append(item)
            all_activities.append(item)
            active_dates.add(day)

        grace_dates, current_streak, longest_streak = _streak_day_sets(active_dates, today)
        total_active_days = len(active_dates)
        first_day = datetime(year, month, 1).date()
        if month == 12:
            next_month = datetime(year + 1, 1, 1).date()
        else:
            next_month = datetime(year, month + 1, 1).date()
        last_day = next_month - timedelta(days=1)
        month_days = []
        for _ in range((first_day.weekday() + 1) % 7):
            month_days.append({'date': None, 'status': 'blank', 'exam_count': 0, 'exams': []})
        day = first_day
        while day <= last_day:
            date_text = day.isoformat()
            status = 'future' if day > today else ('active' if day in active_dates else ('grace' if day in grace_dates else 'inactive'))
            day_items = activities_by_date.get(date_text, [])
            month_days.append({'date': date_text, 'day': day.day, 'status': status,
                               'exam_count': len(day_items), 'exams': day_items})
            day += timedelta(days=1)

        year_start = datetime(year, 1, 1).date()
        year_end = datetime(year + 1, 1, 1).date() - timedelta(days=1)
        year_days = []
        if year_start <= year_end:
            for offset in range((year_end - year_start).days + 1):
                day = year_start + timedelta(days=offset)
                date_text = day.isoformat()
                count = len(activities_by_date.get(date_text, []))
                status = 'future' if day > today else ('active' if day in active_dates else ('grace' if day in grace_dates else 'inactive'))
                intensity = 0 if count == 0 else (1 if count == 1 else (2 if count == 2 else (3 if count <= 4 else 4)))
                year_days.append({'date': date_text, 'status': status, 'exam_count': count, 'intensity': intensity,
                                  'exams': [x['exam_name'] for x in activities_by_date.get(date_text, [])]})

        month_exam_count = sum(len(v) for k, v in activities_by_date.items() if k.startswith(f'{year:04d}-{month:02d}-'))
        account_created = None
        cursor.execute(f"SELECT created_at FROM users WHERE email={ph}", (email,))
        user_row = cursor.fetchone()
        if user_row:
            account_created = str(_row_value(user_row, 'created_at', 0) or '')
        return jsonify({
            'success': True,
            'timezone': 'Asia/Kolkata',
            'today': today.isoformat(),
            'selected_year': year, 'selected_month': month,
            'account_created_at': account_created,
            'stats': {'current_streak': current_streak, 'longest_streak': longest_streak,
                      'total_active_days': total_active_days, 'total_exams': len(all_activities)},
            'month': {'year': year, 'month': month, 'days': month_days, 'exam_count': month_exam_count},
            'year': {'year': year, 'days': year_days},
            'activities_by_date': activities_by_date,
        })
    except Exception:
        app.logger.exception('Streak history request failed')
        return jsonify({"success": False, "error": "Could not load streak history."}), 500
    finally:
        conn.close()


@app.route('/api/get-student-history', methods=['POST'])
def get_student_history():
    user = _current_user()
    if not user:
        return jsonify({"success": False, "error": "Login required."}), 401
    email = _normalise_email(user.get('email'))
    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"""SELECT r.id, r.exam_code, r.exam_name, r.score, r.total_questions,
                    r.date_taken, COALESCE(e.subject, 'General') AS subject,
                    COALESCE(NULLIF((SELECT SUM(q.max_marks) FROM questions q WHERE q.exam_code = r.exam_code), 0), r.total_questions) AS max_marks
                    FROM results r LEFT JOIN exams e ON e.exam_code = r.exam_code
                    WHERE r.student_email = {ph}
                    ORDER BY r.date_taken DESC, r.id DESC""", (email,))
        rows = cursor.fetchall()
        history_list = []
        for row in rows:
            score = float(_row_value(row, 'score', 3) or 0)
            total = float(_row_value(row, 'max_marks', 7) or _row_value(row, 'total_questions', 4) or 0)
            percentage = round(max(0.0, min(100.0, score / total * 100.0)), 2) if total > 0 else 0.0
            history_list.append({
                "exam_code": _row_value(row, 'exam_code', 1),
                "exam_name": _row_value(row, 'exam_name', 2),
                "score": score, "total": total, "percentage": percentage,
                "subject": str(_row_value(row, 'subject', 6) or 'General'),
                "date": str(_row_value(row, 'date_taken', 5)).split(' ')[0]
            })
        return jsonify({"success": True, "history": history_list})
    except Exception:
        app.logger.exception("Student history calculation failed")
        return jsonify({"success": False, "error": "Database error"}), 500
    finally:
        conn.close()

@app.route('/api/get-student-analytics', methods=['GET'])
def get_student_analytics():
    """Return immutable attempt history for the signed-in student only."""
    user = _current_user()
    if not user or str(user.get('role', '')).lower() != 'student':
        return jsonify({"success": False, "error": "Student login required."}), 401

    email = _normalise_email(user.get('email'))
    selected_exam = str(request.args.get('exam_code') or '').strip()
    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = '%s' if db_type == 'postgres' else '?'
        cursor.execute(f"""SELECT id, exam_code, exam_name, score, total_marks, completed_at
            FROM streak_activity WHERE student_email={ph}
            ORDER BY completed_at ASC, id ASC""", (email,))
        raw_rows = cursor.fetchall()

        all_attempts = []
        for row in raw_rows:
            score_raw = _row_value(row, 'score', 3)
            total_raw = _row_value(row, 'total_marks', 4)
            try:
                score = round(float(score_raw), 2) if score_raw is not None else None
            except (TypeError, ValueError):
                score = None
            try:
                total = round(float(total_raw), 2) if total_raw is not None else None
            except (TypeError, ValueError):
                total = None
            percentage = round((score / total) * 100, 2) if score is not None and total is not None and total > 0 else None
            completed_at = str(_row_value(row, 'completed_at', 5) or '')
            exam_code = str(_row_value(row, 'exam_code', 1) or '')
            all_attempts.append({
                'id': _row_value(row, 'id', 0),
                'exam_code': exam_code,
                'exam_name': str(_row_value(row, 'exam_name', 2) or exam_code or 'Completed Exam'),
                'score': score,
                'total': total,
                'percentage': percentage,
                'date': completed_at[:10],
                'completed_at': completed_at,
            })

        # Build one filter option per unique exam_code. Attempt count includes
        # older attempts whose score was not retained by the previous schema.
        grouped = {}
        for item in all_attempts:
            key = item['exam_code']
            if not key:
                continue
            grouped.setdefault(key, []).append(item)
        exam_options = []
        for code, items in grouped.items():
            latest = items[-1]
            exam_options.append({
                'exam_code': code,
                'exam_name': latest['exam_name'],
                'attempt_count': len(items),
                'latest_score': latest['score'],
                'latest_total': latest['total'],
                'latest_percentage': latest['percentage'],
                'latest_date': latest['date'],
            })
        exam_options.sort(key=lambda item: item.get('latest_date') or '', reverse=True)

        if selected_exam:
            attempts = [item for item in all_attempts if item['exam_code'] == selected_exam]
            # Do not disclose whether an arbitrary code belongs to another student.
            if not attempts:
                return jsonify({"success": False, "error": "No history found for this exam."}), 404
        else:
            attempts = all_attempts

        return jsonify({
            'success': True,
            'selected_exam': selected_exam or None,
            'exam_options': exam_options,
            'attempts': list(reversed(attempts)),
            'all_attempt_count': len(all_attempts),
            'timezone': 'Asia/Kolkata',
        })
    except Exception:
        app.logger.exception('Student analytics request failed')
        return jsonify({"success": False, "error": "Could not load student analytics."}), 500
    finally:
        conn.close()


@app.route('/api/create-exam', methods=['POST'])
def create_exam_api():
    data = request.get_json()
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    timer = data.get('timer')
    teacher_email = _normalise_email((_current_user() or {}).get('email'))
    class_name = data.get('class_name', 'General')
    subject = data.get('subject', 'General')
    is_private = data.get('is_private', False)
    questions = data.get('questions')
    if not exam_code or not exam_name or not isinstance(questions, list) or not questions:
        return jsonify({"success": False, "error": "Exam code, name and at least one question are required."}), 400
    try:
        timer = int(timer)
        negative_marks = float(data.get('negative_marks', 0.0))
        if timer < 1 or negative_marks < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Timer and negative marks are invalid."}), 400

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"INSERT INTO exams (exam_code, exam_name, timer_minutes, negative_marks, teacher_email, class_name, subject, is_private) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})", 
                       (exam_code, exam_name, timer, negative_marks, teacher_email, class_name, subject, is_private))
        for q in questions:
            q_type = q.get('question_type', 'mcq')
            max_marks = float(q.get('max_marks', 1.0))
            cursor.execute(f"INSERT INTO questions (exam_code, question_text, option_a, option_b, option_c, option_d, correct_option, question_type, max_marks) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})", 
                           (exam_code, q['question_text'], q.get('option_a', ''), q.get('option_b', ''), q.get('option_c', ''), q.get('option_d', ''), q.get('correct_option', ''), q_type, max_marks))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Exam published successfully!"})
    except Exception:
        app.logger.exception("Request failed")
        return jsonify({"success": False, "error": "Request could not be completed."}), 500

@app.route('/api/teacher-dashboard', methods=['POST'])
def get_teacher_dashboard():
    data = request.get_json()
    email = _normalise_email((_current_user() or {}).get('email'))
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT COUNT(*) as count FROM exams WHERE teacher_email = {ph}", (email,))
        row = cursor.fetchone()
        total_exams = (row['count'] if isinstance(row, dict) else row[0]) or 0
        cursor.execute(f"SELECT COUNT(DISTINCT student_email) as std_count, AVG(CAST(score AS FLOAT) / total_questions * 100) as avg_sc FROM results WHERE exam_code IN (SELECT exam_code FROM exams WHERE teacher_email = {ph})", (email,))
        stats = cursor.fetchone()
        total_students = (stats['std_count'] if isinstance(stats, dict) else stats[0]) or 0
        avg_sc_val = (stats['avg_sc'] if isinstance(stats, dict) else stats[1])
        avg_score = round(avg_sc_val, 1) if avg_sc_val else 0.0
        cursor.execute(f"SELECT exam_name, exam_code FROM exams WHERE teacher_email = {ph} ORDER BY exam_code DESC", (email,))
        all_exams = cursor.fetchall()
        conn.close()
        exams_list = [{"name": r["exam_name"], "code": r["exam_code"]} for r in all_exams]
        return jsonify({"success": True, "total_exams": total_exams, "total_students": total_students, "avg_score": avg_score, "all_exams": exams_list})
    except Exception as e: return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/teacher-analysis', methods=['POST'])
def get_teacher_analysis():
    data = request.get_json()
    exam_code = data.get('exam_code')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT users.name, results.student_email, results.score, results.total_questions FROM results JOIN users ON results.student_email = users.email WHERE results.exam_code = {ph}", (exam_code,))
        rows = cursor.fetchall()
        conn.close()
        if not rows: return jsonify({"success": False, "error": "No students have taken this exam yet."})
        total_students = len(rows)
        total_q = rows[0]["total_questions"]
        student_data = [{"name": r["name"], "email": r["student_email"], "score": float(r["score"]), "perf": round((float(r["score"])/total_q)*100, 2)} for r in rows]
        avg_score = round(sum([r['score'] for r in student_data]) / total_students, 2)
        sorted_students = sorted(student_data, key=lambda x: x['score'], reverse=True)
        top_students = sorted_students[:10]
        bottom_students = sorted_students[-10:] if len(sorted_students) > 10 else sorted_students[::-1]
        chart_data = [0] * (total_q + 1)
        for s in student_data:
            int_score = int(max(0, s['score']))
            if int_score <= total_q: chart_data[int_score] += 1
        labels = [str(i) for i in range(total_q + 1)]
        return jsonify({ "success": True, "total_students": total_students, "avg_score": avg_score, "total_q": total_q, "top": top_students, "bottom": bottom_students, "all_students": sorted_students, "chartLabels": labels, "chartData": chart_data })
    except Exception as e: return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/teacher-full-analytics', methods=['POST'])
def teacher_full_analytics():
    data = request.get_json()
    email = _normalise_email((_current_user() or {}).get('email'))
    if not email: return jsonify({"success": False, "error": "Login required."}), 401
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT exam_code, exam_name, created_at FROM exams WHERE teacher_email = {ph} ORDER BY created_at DESC, exam_code DESC", (email,))
        teacher_exams = cursor.fetchall()
        if not teacher_exams:
            conn.close()
            return jsonify({"success": True, "overall": {"students": 0, "attempts": 0, "avg": "0.0", "high": "0.0", "exams": 0}, "examStats": [], "leaderboard": [], "studentProgress": {}})
        exam_codes = [e['exam_code'] for e in teacher_exams]
        placeholders = ','.join([ph] * len(exam_codes))
        query = f"SELECT r.student_email, u.name as student_name, r.exam_code, r.exam_name, r.score, r.total_questions, r.date_taken FROM results r LEFT JOIN users u ON r.student_email = u.email WHERE r.exam_code IN ({placeholders})"
        cursor.execute(query, exam_codes)
        results = cursor.fetchall()
        conn.close()
        total_attempts = len(results)
        unique_students = len(set(r['student_email'] for r in results))
        all_percentages = [round((float(r['score']) / r['total_questions']) * 100, 1) for r in results if r['total_questions'] > 0]
        class_avg = round(sum(all_percentages) / len(all_percentages), 1) if all_percentages else 0.0
        highest_score = max(all_percentages) if all_percentages else 0.0
        exam_map = {}
        for r in results:
            code = r['exam_code']
            perf = round((float(r['score']) / r['total_questions']) * 100, 1) if r['total_questions'] > 0 else 0
            if code not in exam_map: exam_map[code] = {"name": r['exam_name'], "perfs": [], "attempts": 0}
            exam_map[code]["perfs"].append(perf)
            exam_map[code]["attempts"] += 1
        exam_stats = []
        for e in teacher_exams:
            code = e['exam_code']
            dt_str = str(e['created_at']).split(' ') if e['created_at'] else ["N/A"]
            date_str = dt_str[0]
            time_str = dt_str[1].split('.')[0] if len(dt_str) > 1 else "N/A"
            if code in exam_map:
                perfs = exam_map[code]["perfs"]
                exam_stats.append({"code": code, "name": exam_map[code]["name"], "avg": round(sum(perfs) / len(perfs), 1), "high": max(perfs), "low": min(perfs), "attempts": exam_map[code]["attempts"], "date": date_str, "time": time_str})
            else:
                exam_stats.append({"code": code, "name": e['exam_name'], "avg": 0, "high": 0, "low": 0, "attempts": 0, "date": date_str, "time": time_str})
        student_map = {}
        for r in results:
            s_email = r['student_email']
            s_name = r['student_name'] if r['student_name'] else s_email.split('@')[0]
            perf = round((float(r['score']) / r['total_questions']) * 100, 1) if r['total_questions'] > 0 else 0
            score = float(r['score'])
            if s_email not in student_map: student_map[s_email] = {"name": s_name, "perfs": [], "scores": [], "exam_names": []}
            student_map[s_email]["perfs"].append(perf)
            student_map[s_email]["scores"].append(score)
            student_map[s_email]["exam_names"].append(r['exam_name'])
        leaderboard = []
        student_progress = {}
        for email_key, data in student_map.items():
            s_avg = round(sum(data["perfs"]) / len(data["perfs"]), 1)
            leaderboard.append({"name": data["name"], "email": email_key, "avg": s_avg, "best": max(data["perfs"]), "taken": len(data["perfs"]), "totalScore": sum(data["scores"])})
            student_progress[data["name"]] = {"avg": s_avg, "best": max(data["perfs"]), "taken": len(data["perfs"]), "labels": data["exam_names"], "data": data["perfs"]}
        leaderboard = sorted(leaderboard, key=lambda x: x['best'], reverse=True)
        return jsonify({"success": True, "overall": {"students": unique_students, "attempts": total_attempts, "avg": class_avg, "high": highest_score, "exams": len(teacher_exams)}, "examStats": exam_stats, "leaderboard": leaderboard, "studentProgress": student_progress})
    except Exception as e: return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/admin/send-notification', methods=['POST'])
def admin_send_notification():
    data = request.get_json()
    message = data.get('message')
    target_role = data.get('target_role')
    link_url = data.get('link_url', '')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"INSERT INTO notifications (message, target_role, link_url) VALUES ({ph}, {ph}, {ph})", (message, target_role, link_url))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Broadcasted!"})
    except Exception as e: return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/admin/get-notifications', methods=['GET'])
def admin_get_notifications():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, message, target_role, link_url, date_sent FROM notifications ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        notifs = [{"id": r["id"], "message": r["message"], "target_role": r["target_role"], "link_url": r["link_url"] or '', "date": str(r["date_sent"])} for r in rows]
        return jsonify({"success": True, "notifications": notifs})
    except Exception as e: return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/admin/edit-notification', methods=['POST'])
def edit_notification():
    data = request.get_json()
    notif_id = data.get('id')
    new_message = data.get('message')
    link_url = data.get('link_url', '')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"UPDATE notifications SET message = {ph}, link_url = {ph} WHERE id = {ph}", (new_message, link_url, notif_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False, "error": "Database error"})

@app.route('/api/get-notifications-page', methods=['POST'])
def get_notifications_page():
    data = request.get_json()
    email = data.get('email')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT category, last_notif_read FROM users WHERE email = {ph}", (email,))
        user = cursor.fetchone()
        role = user["category"] if user else 'All'
        last_read = str(user["last_notif_read"]) if (user and user["last_notif_read"]) else '1970-01-01 00:00:00'
        cursor.execute(f"SELECT id, message, target_role, link_url, date_sent FROM notifications WHERE target_role = 'All' OR target_role = {ph} ORDER BY id DESC", (role,))
        rows = cursor.fetchall()
        conn.close()
        notifs = [{"id": r["id"], "message": r["message"], "target_role": r["target_role"], "link_url": r["link_url"] or '', "date": str(r["date_sent"]).split('.')[0], "is_unread": str(r["date_sent"]) > last_read} for r in rows]
        return jsonify({"success": True, "notifications": notifs})
    except Exception as e: return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/admin/delete-notification', methods=['POST'])
def delete_notification():
    data = request.get_json()
    notif_id = data.get('id')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"DELETE FROM notifications WHERE id = {ph}", (notif_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False, "error": "Database error"})

@app.route('/api/check-unread-notifications', methods=['POST'])
def check_unread_notifications():
    data = request.get_json()
    email = data.get('email')
    if not email: return jsonify({"success": False, "has_unread": False})
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT category, last_notif_read FROM users WHERE email = {ph}", (email,))
        user = cursor.fetchone()
        role = user["category"] if user else 'All'
        last_read = user["last_notif_read"] if (user and user["last_notif_read"]) else '1970-01-01 00:00:00'
        cursor.execute(f"SELECT COUNT(*) as count FROM notifications WHERE (target_role = 'All' OR target_role = {ph}) AND date_sent > {ph}", (role, last_read))
        row = cursor.fetchone()
        conn.close()
        unread_count = row['count'] if isinstance(row, dict) else row[0]
        return jsonify({"success": True, "has_unread": unread_count > 0})
    except Exception as e: return jsonify({"success": False, "has_unread": False})

@app.route('/api/mark-notifications-read', methods=['POST'])
def mark_notifications_read():
    data = request.get_json()
    email = data.get('email')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"UPDATE users SET last_notif_read = CURRENT_TIMESTAMP WHERE email = {ph}", (email,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False})

@app.route('/api/admin/get-drive-contents', methods=['POST'])
@app.route('/api/get-student-drive-contents', methods=['POST'])
def get_drive_contents_api():
    data = request.get_json() if request.is_json else {}
    parent_id = data.get('parent_id', 0)
    folder_type = data.get('folder_type', 'content') 
    is_student_req = request.path == '/api/get-student-drive-contents'
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT id, folder_name FROM drive_folders WHERE parent_id = {ph} AND folder_type = {ph} ORDER BY position ASC, id DESC", (parent_id, folder_type))
        folders = [{"id": r["id"], "name": r["folder_name"]} for r in cursor.fetchall()]
        if folder_type == 'content':
            cursor.execute(f"SELECT id, title, file_url, resource_type, date_uploaded FROM drive_files WHERE folder_id = {ph} ORDER BY position ASC, id DESC", (parent_id,))
            files = [{"id": r["id"], "title": r["title"], "file_url": r["file_url"], "type": r["resource_type"], "date": str(r["date_uploaded"]).split(' ')[0]} for r in cursor.fetchall()]
            exams = []
        else:
            files = []
            if is_student_req:
                query = f"SELECT exam_code, exam_name, timer_minutes FROM exams WHERE folder_id = {ph} AND teacher_email = 'dasbabu938207@gmail.com' ORDER BY position ASC, exam_code DESC"
                cursor.execute(query, (parent_id,))
            else:
                cursor.execute(f"SELECT exam_code, exam_name, timer_minutes FROM exams WHERE folder_id = {ph} ORDER BY position ASC, exam_code DESC", (parent_id,))
            exams = [{"code": r["exam_code"], "name": r["exam_name"], "timer": r["timer_minutes"]} for r in cursor.fetchall()]
        conn.close()
        return jsonify({"success": True, "folders": folders, "files": files, "exams": exams})
    except Exception as e: return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/admin/create-drive-folder', methods=['POST'])
def create_drive_folder():
    data = request.get_json()
    folder_name = data.get('folder_name')
    parent_id = data.get('parent_id', 0)
    folder_type = data.get('folder_type', 'content') 
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"INSERT INTO drive_folders (folder_name, parent_id, folder_type) VALUES ({ph}, {ph}, {ph})", (folder_name, parent_id, folder_type))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Folder created!"})
    except Exception as e: return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/admin/upload-drive-file', methods=['POST'])
def upload_drive_file():
    data = request.get_json()
    folder_id = data.get('folder_id', 0)
    title = data.get('title')
    resource_type = data.get('resource_type', 'Notes')
    file_url = data.get('file_url')
    if not title or not file_url: return jsonify({"success": False, "error": "Title and Link are required!"}), 400
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"INSERT INTO drive_files (folder_id, title, file_url, resource_type) VALUES ({ph}, {ph}, {ph}, {ph})", (folder_id, title, file_url, resource_type))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Link saved successfully!"})
    except Exception as e: return jsonify({"success": False, "error": "Save Failed"}), 500

@app.route('/api/admin/delete-drive-item', methods=['POST'])
def delete_drive_item():
    data = request.get_json()
    item_type = data.get('type')
    item_id = data.get('id')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        if item_type == 'folder':
            cursor.execute(f"DELETE FROM drive_folders WHERE id = {ph}", (item_id,))
            cursor.execute(f"DELETE FROM drive_files WHERE folder_id = {ph}", (item_id,))
            cursor.execute(f"DELETE FROM exams WHERE folder_id = {ph}", (item_id,))
        elif item_type == 'exam':
            cursor.execute(f"DELETE FROM exams WHERE exam_code = {ph}", (item_id,))
        else:
            cursor.execute(f"DELETE FROM drive_files WHERE id = {ph}", (item_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Deleted!"})
    except Exception as e: return jsonify({"success": False, "error": "Delete Failed"}), 500

@app.route('/api/upload-profile-pic', methods=['POST'])
def upload_profile_pic():
    return jsonify({"success": True})
        
@app.route('/api/get-profile-data', methods=['POST'])
def get_profile_data():
    data = request.get_json()
    email = data.get('email')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT name FROM users WHERE email = {ph}", (email,))
        user = cursor.fetchone()
        conn.close()
        if user: return jsonify({"success": True, "name": user['name']})
        return jsonify({"success": False})
    except Exception as e: return jsonify({"success": False})

@app.route('/api/extract-pdf-gemini', methods=['POST'])
def extract_pdf_gemini():
    if 'file' not in request.files: return jsonify({"success": False, "error": "No file uploaded!"}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({"success": False, "error": "No selected file!"}), 400
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key: return jsonify({"success": False, "error": "Gemini API Key is missing on the server!"}), 500
    try:
        reader = PyPDF2.PdfReader(file)
        extracted_text = ""
        for page in reader.pages:
            text = page.extract_text()
            if text: extracted_text += text
        if not extracted_text.strip(): return jsonify({"success": False, "error": "No readable text found in the PDF!"}), 400
        prompt = """
        Extract all the questions from the following text. Categorize each question as either 'mcq' or 'subjective' (long descriptive question).
        You must respond ONLY with a valid JSON array of objects. Do not include markdown formatting like ```json or ```.
        Each object must strictly follow this exact structure:
        [
            {
                "question_type": "mcq", /* use "mcq" if it has options, or "subjective" if it is a long question without options */
                "question_text": "The question here?",
                "option_a": "Option A text", /* leave empty string "" if subjective */
                "option_b": "Option B text", /* leave empty string "" if subjective */
                "option_c": "Option C text", /* leave empty string "" if subjective */
                "option_d": "Option D text", /* leave empty string "" if subjective */
                "correct_option": "A" /* leave empty string "" if subjective */
            }
        ]
        Text to analyze:
        """ + extracted_text
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.0-pro:generateContent?key={api_key}"
        headers = {'Content-Type': 'application/json'}
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        api_response = requests.post(url, headers=headers, json=payload)
        response_data = api_response.json()
        if api_response.status_code != 200: return jsonify({"success": False, "error": f"Google API Error: {response_data}"}), 500
        response_text = response_data['candidates'][0]['content']['parts'][0]['text'].strip()
        if response_text.startswith("```json"): response_text = response_text[7:-3].strip()
        elif response_text.startswith("```"): response_text = response_text[3:-3].strip()
        questions_json = json.loads(response_text)
        return jsonify({"success": True, "questions": questions_json})
    except json.JSONDecodeError: return jsonify({"success": False, "error": "AI could not generate valid questions. Please try a clearer PDF."}), 500
    except Exception:
        app.logger.exception("Request failed")
        return jsonify({"success": False, "error": "Request could not be completed."}), 500
        
@app.route('/api/update-profile', methods=['POST'])
def update_profile():
    data = _request_data()
    user = _current_user()
    if not user:
        return jsonify({"success": False, "error": "Login required."}), 401
    email = _normalise_email(user.get("email"))
    name = str(data.get("name") or "").strip()[:255]
    role = str(data.get("role") or user.get("role") or "Student").strip().title()
    if not name:
        return jsonify({"success": False, "error": "Name is required."}), 400
    if role not in ("Student", "Teacher"):
        return jsonify({"success": False, "error": "Invalid account type."}), 400
    try:
        conn, db_type = get_db_connection()
        try:
            cursor = conn.cursor()
            ph = "%s" if db_type == "postgres" else "?"
            cursor.execute(f"UPDATE users SET name = {ph}, category = {ph} WHERE email = {ph}", (name, role, email))
            conn.commit()
        finally:
            conn.close()
        session["user"]["name"] = name
        session["user"]["role"] = role
        session.modified = True
        return jsonify({"success": True, "redirect_url": "/student_dashboard.html" if role == "Student" else "/teacher_dashboard.html"})
    except Exception:
        app.logger.exception("Profile update failed")
        return jsonify({"success": False, "error": "Profile could not be updated."}), 500

@app.route('/logout')
def logout():
    user = _current_user()
    if user and user.get("email"):
        try:
            conn, db_type = get_db_connection()
            try:
                cursor = conn.cursor()
                ph = "%s" if db_type == "postgres" else "?"
                cursor.execute(f"UPDATE users SET is_logged_in = FALSE WHERE email = {ph}", (_normalise_email(user["email"]),))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            app.logger.exception("Could not update sign-out status")
    session.clear()
    return redirect("/?logout=1")

@app.route('/api/check-login-status', methods=['POST'])
def check_login_status():
    user_session = _current_user()
    if not user_session:
        return jsonify({"logged_in": False})
    email = _normalise_email(user_session.get("email"))
    try:
        user = _db_user_by_email(email)
        if not user:
            session.clear()
            return jsonify({"logged_in": False})
        role = user["category"]
        name = user["name"]
        session["user"] = {"email": email, "role": role, "name": name}
        return jsonify({"logged_in": True, "email": email, "role": role, "name": name,
                        "redirect_url": "/student_dashboard.html" if str(role).lower() == "student" else "/teacher_dashboard.html"})
    except Exception:
        app.logger.exception("Login status check failed")
        return jsonify({"logged_in": False}), 500

@app.route('/download')
def download_page(): return render_template('download.html')

@app.route('/api/submit-review', methods=['POST'])
def submit_review():
    data = request.get_json()
    name = data.get('name')
    role = data.get('role', 'Student')
    rating = int(data.get('rating', 5))
    text = data.get('text')
    if not name or not text: return jsonify({"success": False, "error": "Name and Review text are required!"}), 400
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"INSERT INTO reviews (reviewer_name, role, rating, review_text) VALUES ({ph}, {ph}, {ph}, {ph})", (name, role, rating, text))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Review submitted successfully!"})
    except Exception as e: return jsonify({"success": False, "error": "Failed to submit review."}), 500

@app.route('/api/get-reviews', methods=['GET'])
def get_reviews():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT reviewer_name, role, rating, review_text, date_submitted FROM reviews ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        reviews_list = []
        total_rating = 0
        for r in rows:
            reviews_list.append({"name": r["reviewer_name"], "role": r["role"], "rating": r["rating"], "text": r["review_text"], "date": str(r["date_submitted"]).split(' ')[0]})
            total_rating += r["rating"]
        avg_rating = round(total_rating / len(reviews_list), 1) if reviews_list else 5.0
        return jsonify({"success": True, "reviews": reviews_list, "avg_rating": avg_rating, "total_reviews": len(reviews_list)})
    except Exception as e: return jsonify({"success": False, "error": "Failed to fetch reviews."}), 500

@app.route('/api/admin/move-item', methods=['POST'])
def move_drive_item():
    data = request.get_json()
    item_type = data.get('type')
    item_id = data.get('id')
    new_folder_id = data.get('new_folder_id', 0)
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        if item_type == 'folder': cursor.execute(f"UPDATE drive_folders SET parent_id = {ph} WHERE id = {ph}", (new_folder_id, item_id))
        elif item_type == 'exam': cursor.execute(f"UPDATE exams SET folder_id = {ph} WHERE exam_code = {ph}", (new_folder_id, item_id))
        else: cursor.execute(f"UPDATE drive_files SET folder_id = {ph} WHERE id = {ph}", (new_folder_id, item_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Moved successfully!"})
    except Exception as e: return jsonify({"success": False, "error": "Failed to move item."}), 500

@app.route('/api/admin/update-position', methods=['POST'])
def update_item_position():
    data = request.get_json()
    items = data.get('items', [])
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        for item in items:
            it_type = item.get('type')
            it_id = item.get('id')
            pos = item.get('position', 0)
            if it_type == 'folder': cursor.execute(f"UPDATE drive_folders SET position = {ph} WHERE id = {ph}", (pos, it_id))
            elif it_type == 'exam': cursor.execute(f"UPDATE exams SET position = {ph} WHERE exam_code = {ph}", (pos, it_id))
            elif it_type == 'file': cursor.execute(f"UPDATE drive_files SET position = {ph} WHERE id = {ph}", (pos, it_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False, "error": "Failed to update order."}), 500

@app.route('/api/teacher-delete-exam', methods=['POST'])
def teacher_delete_exam():
    data = request.get_json()
    exam_code = data.get('exam_code')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"DELETE FROM questions WHERE exam_code = {ph}", (exam_code,))
        cursor.execute(f"DELETE FROM results WHERE exam_code = {ph}", (exam_code,))
        cursor.execute(f"DELETE FROM exams WHERE exam_code = {ph}", (exam_code,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Exam deleted successfully!"})
    except Exception as e: return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/teacher-update-exam', methods=['POST'])
def teacher_update_exam():
    data = request.get_json()
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    timer = data.get('timer')
    negative_marks = float(data.get('negative_marks', 0.0))
    class_name = data.get('class_name', 'General')
    subject = data.get('subject', 'General')
    is_private = data.get('is_private', False)
    questions = data.get('questions')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"UPDATE exams SET exam_name = {ph}, timer_minutes = {ph}, negative_marks = {ph}, class_name = {ph}, subject = {ph}, is_private = {ph} WHERE exam_code = {ph}", (exam_name, timer, negative_marks, class_name, subject, is_private, exam_code))
        cursor.execute(f"DELETE FROM questions WHERE exam_code = {ph}", (exam_code,))
        for q in questions:
            q_type = q.get('question_type', 'mcq')
            max_marks = float(q.get('max_marks', 1.0))
            cursor.execute(f"INSERT INTO questions (exam_code, question_text, option_a, option_b, option_c, option_d, correct_option, question_type, max_marks) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})", 
                           (exam_code, q['question_text'], q.get('option_a', ''), q.get('option_b', ''), q.get('option_c', ''), q.get('option_d', ''), q.get('correct_option', ''), q_type, max_marks))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Exam updated successfully!"})
    except Exception:
        app.logger.exception("Request failed")
        return jsonify({"success": False, "error": "Request could not be completed."}), 500

@app.route('/api/get-filter-options', methods=['GET'])
def get_filter_options():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT class_name FROM exams WHERE class_name IS NOT NULL")
        classes = [r['class_name'] if isinstance(r, dict) else r[0] for r in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT subject FROM exams WHERE subject IS NOT NULL")
        subjects = [r['subject'] if isinstance(r, dict) else r[0] for r in cursor.fetchall()]
        conn.close()
        return jsonify({"success": True, "classes": classes, "subjects": subjects})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route('/api/search-feed', methods=['POST'])
def search_feed():
    data = request.get_json()
    email = data.get('email')
    query = data.get('query', '').strip()
    selected_class = data.get('class_name', 'All')
    selected_subject = data.get('subject', 'All')
    status_filter = data.get('status', 'All')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT category FROM users WHERE email = {ph}", (email,))
        user_row = cursor.fetchone()
        user_role = (user_row['category'] if isinstance(user_row, dict) else user_row[0]) if user_row else 'Student'
        search_term = f"%{query}%"
        sql = f"SELECT e.*, u.name as teacher_name, (SELECT COUNT(*) FROM likes WHERE exam_code = e.exam_code) as total_likes, (SELECT COUNT(*) FROM likes WHERE exam_code = e.exam_code AND email = {ph}) as is_liked, (SELECT COUNT(*) FROM saves WHERE exam_code = e.exam_code AND email = {ph}) as is_saved FROM exams e LEFT JOIN users u ON e.teacher_email = u.email WHERE (e.exam_name ILIKE {ph} OR e.exam_code ILIKE {ph} OR e.subject ILIKE {ph} OR e.class_name ILIKE {ph} OR u.name ILIKE {ph})"
        if db_type == 'sqlite': sql = sql.replace("ILIKE", "LIKE")
        params = [email, email, search_term, search_term, search_term, search_term, search_term]
        
        if user_role.lower() == 'teacher':
            sql += f" AND (e.is_private = FALSE OR e.teacher_email = {ph})"
            params.append(email)
        if selected_class != 'All':
            sql += f" AND e.class_name = {ph}"
            params.append(selected_class)
        if selected_subject != 'All':
            sql += f" AND e.subject = {ph}"
            params.append(selected_subject)
        if status_filter == 'Private': sql += " AND e.is_private = TRUE"
        elif status_filter == 'Public': sql += " AND e.is_private = FALSE"
        
        sql += " ORDER BY e.created_at DESC"
        cursor.execute(sql, tuple(params))
        results = cursor.fetchall()
        
        cursor.execute(f"SELECT exam_code, score, total_questions FROM results WHERE student_email = {ph}", (email,))
        history = {r['exam_code']: r for r in cursor.fetchall()}
        
        feed = []
        for ex in results:
            code = ex['exam_code']
            is_attempted = code in history
            if status_filter == 'Attempted' and not is_attempted: continue
            if status_filter == 'Unattempted' and is_attempted: continue
            feed.append({
                "code": code, "name": ex['exam_name'], "teacher_name": ex['teacher_name'] or "Teacher",
                "class_name": ex.get('class_name', 'General'), "subject": ex.get('subject', 'General'),
                "timer": ex['timer_minutes'], "is_private": ex.get('is_private', False),
                "date": str(ex['created_at']).split()[0] if ex['created_at'] else "Recently",
                "likes": ex['total_likes'], "shares": ex.get('shares', 0),
                "is_liked": ex['is_liked'] > 0, "is_saved": ex['is_saved'] > 0,
                "is_attempted": is_attempted, "score": history[code]['score'] if is_attempted else 0,
                "total": history[code]['total_questions'] if is_attempted else 0
            })
        conn.close()
        return jsonify({"success": True, "feed": feed})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route('/api/toggle-social', methods=['POST'])
def toggle_social():
    data = request.get_json()
    email = data.get('email')
    exam_code = data.get('exam_code')
    action_type = data.get('type')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        table_name = "likes" if action_type == 'like' else "saves"
        cursor.execute(f"SELECT * FROM {table_name} WHERE email = {ph} AND exam_code = {ph}", (email, exam_code))
        exists = cursor.fetchone()
        if exists:
            cursor.execute(f"DELETE FROM {table_name} WHERE email = {ph} AND exam_code = {ph}", (email, exam_code))
            status = False
        else:
            cursor.execute(f"INSERT INTO {table_name} (email, exam_code) VALUES ({ph}, {ph})", (email, exam_code))
            status = True
        conn.commit()
        conn.close()
        return jsonify({"success": True, "status": status})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route('/run-fix-db', methods=['GET'])
def run_fix_db():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        
        try: 
            cursor.execute("ALTER TABLE exams ADD COLUMN is_private BOOLEAN DEFAULT FALSE;")
            conn.commit()
        except: 
            if db_type == 'postgres': conn.rollback()
            
        try: 
            cursor.execute("ALTER TABLE questions ADD COLUMN question_type VARCHAR(20) DEFAULT 'mcq';")
            conn.commit()
        except: 
            if db_type == 'postgres': conn.rollback()
            
        try: 
            cursor.execute("ALTER TABLE questions ADD COLUMN max_marks FLOAT DEFAULT 1.0;")
            conn.commit()
        except: 
            if db_type == 'postgres': conn.rollback()
            
        if db_type == 'postgres':
            cursor.execute('''CREATE TABLE IF NOT EXISTS subjective_answers (id SERIAL PRIMARY KEY, exam_code VARCHAR(100), student_email VARCHAR(255), question_id INT, image_url TEXT, marks FLOAT DEFAULT 0.0, is_checked BOOLEAN DEFAULT FALSE);''')
        else:
            cursor.execute('''CREATE TABLE IF NOT EXISTS subjective_answers (id INTEGER PRIMARY KEY AUTOINCREMENT, exam_code TEXT, student_email TEXT, question_id INTEGER, image_url TEXT, marks REAL DEFAULT 0.0, is_checked BOOLEAN DEFAULT FALSE);''')
        
        conn.commit()
        conn.close()
        return "<h3>🎉 Database successfully fixed! Version 2.0 tables and columns are ready!</h3>"
    except Exception as e: return f"<h3>⚠️ Error:</h3> <p>{str(e)}</p>"

@app.route('/api/teacher-pending-evaluations', methods=['POST'])
def get_pending_evaluations():
    data = request.get_json()
    exam_code = data.get('exam_code')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT s.id, s.student_email, s.image_url, q.question_text, q.max_marks FROM subjective_answers s JOIN questions q ON s.question_id = q.id WHERE s.exam_code = {ph} AND s.is_checked = FALSE", (exam_code,))
        rows = cursor.fetchall()
        conn.close()
        evaluations = [{"id": r["id"], "student_email": r["student_email"], "image_url": r["image_url"], "question_text": r["question_text"], "max_marks": r["max_marks"] or 1.0} for r in rows]
        return jsonify({"success": True, "evaluations": evaluations})
    except Exception:
        app.logger.exception("Request failed")
        return jsonify({"success": False, "error": "Request could not be completed."}), 500

@app.route('/api/teacher-submit-evaluation', methods=['POST'])
def submit_evaluation():
    data = _request_data()
    ans_id = data.get('answer_id')
    try:
        marks = float(data.get('marks', 0.0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid marks."}), 400
    if not ans_id or marks < 0:
        return jsonify({"success": False, "error": "Answer ID and non-negative marks are required."}), 400

    conn, db_type = get_db_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(
            f"SELECT s.student_email, s.exam_code, s.marks, q.max_marks "
            f"FROM subjective_answers s JOIN questions q ON s.question_id = q.id WHERE s.id = {ph}",
            (ans_id,),
        )
        answer = cursor.fetchone()
        if not answer:
            return jsonify({"success": False, "error": "Answer not found."}), 404
        student_email = answer['student_email']
        exam_code = answer['exam_code']
        old_marks = float(answer['marks'] or 0.0)
        max_marks = float(answer['max_marks'] or 0.0)
        if marks > max_marks:
            return jsonify({"success": False, "error": f"Marks cannot exceed {max_marks}."}), 400

        # Replace the previous marks instead of adding marks repeatedly on retries.
        cursor.execute(f"UPDATE subjective_answers SET marks = {ph}, is_checked = TRUE WHERE id = {ph}", (marks, ans_id))
        cursor.execute(
            f"UPDATE results SET score = score + {ph} WHERE student_email = {ph} AND exam_code = {ph}",
            (marks - old_marks, student_email, exam_code),
        )
        conn.commit()
        return jsonify({"success": True})
    except Exception:
        conn.rollback()
        app.logger.exception("Subjective evaluation failed")
        return jsonify({"success": False, "error": "Evaluation could not be saved."}), 500
    finally:
        conn.close()

@app.route('/api/get-student-exam-review', methods=['POST'])
def get_student_exam_review():
    data = request.get_json()
    email = data.get('email')
    exam_code = data.get('exam_code')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT score FROM results WHERE student_email = {ph} AND exam_code = {ph}", (email, exam_code))
        res = cursor.fetchone()
        if not res:
            conn.close()
            return jsonify({"success": True, "taken": False})
        
        total_score = res['score'] if isinstance(res, dict) else res[0]
        cursor.execute(f"SELECT question_id, marks, is_checked FROM subjective_answers WHERE student_email = {ph} AND exam_code = {ph}", (email, exam_code))
        subj_rows = cursor.fetchall()
        subj_data = {}
        for r in subj_rows:
            qid = r['question_id'] if isinstance(r, dict) else r[0]
            mks = r['marks'] if isinstance(r, dict) else r[1]
            chk = r['is_checked'] if isinstance(r, dict) else r[2]
            subj_data[str(qid)] = {"marks": mks, "is_checked": chk}
            
        conn.close()
        return jsonify({"success": True, "taken": True, "total_score": total_score, "subjective_data": subj_data})
    except Exception as e:
        app.logger.exception("Request failed")
        return jsonify({"success": False, "error": "Request could not be completed."}), 500
        
from flask import send_from_directory

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('.', 'sitemap.xml')

@app.route('/api/get-special-exams', methods=['POST'])
def get_special_exams():
    data = request.get_json() or {}
    email = data.get('email', '')
    
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute("""
            SELECT e.exam_code, e.exam_name, e.class_name, e.subject, e.season, e.expiry_time, e.folder_id, e.created_at
            FROM exams e
            LEFT JOIN drive_folders f ON e.folder_id = f.id
            WHERE e.teacher_email = %s
            AND (e.folder_id = 0 OR f.folder_type = 'live_session')
        """ if db_type == 'postgres' else """
            SELECT e.exam_code, e.exam_name, e.class_name, e.subject, e.season, e.expiry_time, e.folder_id, e.created_at
            FROM exams e
            LEFT JOIN drive_folders f ON e.folder_id = f.id
            WHERE e.teacher_email = ?
            AND (e.folder_id = 0 OR f.folder_type = 'live_session')
        """, (SPECIAL_EXAM_OWNER_EMAIL,))
        live_exams_rows = cursor.fetchall()
        
        cursor.execute(f"SELECT exam_code, score, total_questions FROM results WHERE student_email = {ph}", (email,))
        history_rows = cursor.fetchall()
        history_map = {r["exam_code"] if isinstance(r, dict) else r[0]: {"score": r["score"] if isinstance(r, dict) else r[1], "total": r["total_questions"] if isinstance(r, dict) else r[2]} for r in history_rows}
        
        live_exams = []
        for r in live_exams_rows:
            code = r["exam_code"] if isinstance(r, dict) else r[0]
            name = r["exam_name"] if isinstance(r, dict) else r[1]
            cls_name = r["class_name"] if isinstance(r, dict) else r[2]
            subj = r["subject"] if isinstance(r, dict) else r[3]
            season_name = r["season"] if isinstance(r, dict) else (r[4] if len(r) > 4 else "General")
            created_at_val = str(r["created_at"]) if isinstance(r, dict) and r.get("created_at") else (str(r[7]) if len(r) > 7 and r[7] else "")
            
            if isinstance(r, dict):
                exp_time_str = str(r.get("expiry_time")) if r.get("expiry_time") else None
            else:
                exp_time_str = str(r[5]) if len(r) > 5 and r[5] else None

            if exp_time_str == "None": exp_time_str = None
            
            is_attempted = code in history_map
            live_exams.append({
                "code": code, "name": name, "class_name": cls_name, "subject": subj, "season": season_name or "General", "expiry_time": exp_time_str,
                "created_at": created_at_val,
                "is_attempted": is_attempted,
                "score": history_map[code]["score"] if is_attempted else 0,
                "total": history_map[code]["total"] if is_attempted else 0
            })
        
        cursor.execute("SELECT id, folder_name, folder_type FROM drive_folders WHERE parent_id = 0 AND folder_type IN ('category', 'playlist')")
        all_folders = cursor.fetchall()
        
        cursor.execute(f"SELECT folder_id, MAX(created_at) as last_updated FROM exams WHERE teacher_email = {ph} GROUP BY folder_id", (SPECIAL_EXAM_OWNER_EMAIL,))
        latest_exams = {}
        for r in cursor.fetchall():
            fid = r["folder_id"] if isinstance(r, dict) else r[0]
            l_upd = str(r["last_updated"]) if isinstance(r, dict) else str(r[1])
            latest_exams[fid] = l_upd
        
        folders_list = []
        for f in all_folders:
            fid = f["id"] if isinstance(f, dict) else f[0]
            fname = f["folder_name"] if isinstance(f, dict) else f[1]
            ftype = f["folder_type"] if isinstance(f, dict) else f[2]
            folders_list.append({
                "id": fid, "name": fname, "type": ftype,
                "last_updated": latest_exams.get(fid, "")
            })
        
        folders_list.sort(key=lambda x: x["last_updated"], reverse=True)
        categories = [{"id": f["id"], "name": f["name"]} for f in folders_list if f["type"] == 'category']
        playlists = [{"id": f["id"], "name": f["name"]} for f in folders_list if f["type"] == 'playlist']
        
        conn.close()
        return jsonify({"success": True, "live_exams": live_exams, "categories": categories, "playlists": playlists})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/get-special-playlist-exams', methods=['POST'])
def get_special_playlist_exams():
    data = request.get_json()
    folder_id = data.get('folder_id')
    email = data.get('email', '')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(f"SELECT exam_code, exam_name, timer_minutes, class_name, subject FROM exams WHERE folder_id = {ph} AND teacher_email = {ph} ORDER BY position ASC, exam_code DESC", (folder_id, SPECIAL_EXAM_OWNER_EMAIL))
        rows = cursor.fetchall()
        
        cursor.execute(f"SELECT exam_code, score, total_questions FROM results WHERE student_email = {ph}", (email,))
        history_rows = cursor.fetchall()
        history_map = {r["exam_code"] if isinstance(r, dict) else r[0]: {"score": r["score"] if isinstance(r, dict) else r[1], "total": r["total_questions"] if isinstance(r, dict) else r[2]} for r in history_rows}
        
        exams = []
        for r in rows:
            code = r["exam_code"] if isinstance(r, dict) else r[0]
            name = r["exam_name"] if isinstance(r, dict) else r[1]
            timer = r["timer_minutes"] if isinstance(r, dict) else r[2]
            cls_name = r["class_name"] if isinstance(r, dict) else r[3]
            subj = r["subject"] if isinstance(r, dict) else r[4]
            
            is_attempted = code in history_map
            exams.append({
                "code": code, "name": name, "timer": timer, "class_name": cls_name, "subject": subj,
                "is_attempted": is_attempted,
                "score": history_map[code]["score"] if is_attempted else 0,
                "total": history_map[code]["total"] if is_attempted else 0
            })
        
        conn.close()
        return jsonify({"success": True, "exams": exams})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
        
@app.route('/api/admin/get-special-dashboard', methods=['POST'])
def admin_get_special_dashboard():
    data = request.get_json()
    parent_id = data.get('parent_id', 0)
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(f"SELECT id, folder_name, folder_type FROM drive_folders WHERE parent_id = {ph} ORDER BY id DESC", (parent_id,))
        folders = [{"id": r["id"], "name": r["folder_name"], "type": r["folder_type"]} for r in cursor.fetchall()]
        
        cursor.execute(f"SELECT exam_code, exam_name, timer_minutes, expiry_time FROM exams WHERE folder_id = {ph} AND teacher_email = {ph} ORDER BY created_at DESC", (parent_id, SPECIAL_EXAM_OWNER_EMAIL))
        exams = [{"code": r["exam_code"], "name": r["exam_name"], "timer": r["timer_minutes"], "expiry_time": str(r["expiry_time"]) if r.get("expiry_time") else ""} for r in cursor.fetchall()]
        
        conn.close()
        return jsonify({"success": True, "folders": folders, "exams": exams})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/admin/create-special-exam', methods=['POST'])
def create_special_exam_api():
    data = request.get_json()
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    timer = data.get('timer')
    negative_marks = float(data.get('negative_marks', 0.0))
    teacher_email = SPECIAL_EXAM_OWNER_EMAIL 
    
    class_name = data.get('class_name', 'General')
    subject = data.get('subject', 'General')
    season = data.get('season', 'General')
    is_private = False
    
    folder_id = data.get('folder_id', 0)
    expiry_time = data.get('expiry_time') 
    if not expiry_time: 
        expiry_time = None
        
    questions = data.get('questions')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(f"INSERT INTO exams (exam_code, exam_name, timer_minutes, negative_marks, teacher_email, class_name, subject, season, is_private, folder_id, expiry_time) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})", 
                       (exam_code, exam_name, timer, negative_marks, teacher_email, class_name, subject, season, is_private, folder_id, expiry_time))
        
        for q in questions:
            q_type = q.get('question_type', 'mcq')
            max_marks = float(q.get('max_marks', 1.0))
            cursor.execute(f"INSERT INTO questions (exam_code, question_text, option_a, option_b, option_c, option_d, correct_option, question_type, max_marks) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})", 
                           (exam_code, q['question_text'], q.get('option_a', ''), q.get('option_b', ''), q.get('option_c', ''), q.get('option_d', ''), q.get('correct_option', ''), q_type, max_marks))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Special Exam published successfully!"})
    except Exception as e: 
        app.logger.exception("Request failed")
        return jsonify({"success": False, "error": "Request could not be completed."}), 500

@app.route('/season_analytics.html')
def season_analytics_page():
    return render_template('season_analytics.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')

@app.route('/api/get-season-analytics', methods=['POST'])
def get_season_analytics():
    data = request.get_json()
    email = data.get('email')
    season_name = data.get('season')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(f"SELECT exam_code, exam_name, created_at FROM exams WHERE season = {ph} AND teacher_email = {ph} ORDER BY created_at ASC", (season_name, SPECIAL_EXAM_OWNER_EMAIL))
        season_exams = cursor.fetchall()
        
        if not season_exams:
            conn.close()
            return jsonify({"success": False, "error": "No exams found for this season."})
            
        exam_codes = [e['exam_code'] if isinstance(e, dict) else e[0] for e in season_exams]
        placeholders = ','.join([ph] * len(exam_codes))
        
        query = f"SELECT exam_code, score, total_questions FROM results WHERE student_email = {ph} AND exam_code IN ({placeholders})"
        params = [email]
        params.extend(exam_codes)
        cursor.execute(query, params)
        
        student_results = {r['exam_code'] if isinstance(r, dict) else r[0]: r for r in cursor.fetchall()}
        
        daily_progress = []
        total_student_score = 0
        total_student_q = 0
        
        for idx, ex in enumerate(season_exams):
            code = ex['exam_code'] if isinstance(ex, dict) else ex[0]
            name = ex['exam_name'] if isinstance(ex, dict) else ex[1]
            date_str = str(ex['created_at']).split()[0] if (isinstance(ex, dict) and ex.get('created_at')) else f"Day {idx+1}"
            
            if code in student_results:
                res = student_results[code]
                sc = res['score'] if isinstance(res, dict) else res[1]
                tq = res['total_questions'] if isinstance(res, dict) else res[2]
                daily_progress.append({"day": idx+1, "code": code, "name": name, "date": date_str, "score": sc, "total": tq, "attempted": True})
                total_student_score += sc
                total_student_q += tq
            else:
                daily_progress.append({"day": idx+1, "code": code, "name": name, "date": date_str, "score": 0, "total": 0, "attempted": False})
                
        query_lb = f"SELECT r.student_email, u.name, SUM(r.score) as total_score, SUM(r.total_questions) as total_q FROM results r JOIN users u ON r.student_email = u.email WHERE r.exam_code IN ({placeholders}) GROUP BY r.student_email, u.name ORDER BY total_score DESC"
        cursor.execute(query_lb, exam_codes)
        lb_rows = cursor.fetchall()
        
        leaderboard = []
        student_rank = 0
        for idx, r in enumerate(lb_rows):
            s_email = r['student_email'] if isinstance(r, dict) else r[0]
            s_name = r['name'] if isinstance(r, dict) else r[1]
            tot_sc = r['total_score'] if isinstance(r, dict) else r[2]
            tot_q = r['total_q'] if isinstance(r, dict) else r[3]
            
            leaderboard.append({"rank": idx+1, "name": s_name, "score": tot_sc, "total": tot_q})
            if s_email == email:
                student_rank = idx + 1
                
        conn.close()
        return jsonify({
            "success": True, 
            "season_name": season_name,
            "student_stat": {"total_score": total_student_score, "total_q": total_student_q, "rank": student_rank},
            "daily_progress": daily_progress,
            "leaderboard": leaderboard
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/get-daily-leaderboard', methods=['POST'])
def get_daily_leaderboard():
    data = request.get_json()
    exam_code = data.get('exam_code')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT u.name, r.score, r.total_questions FROM results r JOIN users u ON r.student_email = u.email WHERE r.exam_code = {ph} ORDER BY r.score DESC LIMIT 10", (exam_code,))
        rows = cursor.fetchall()
        lb = [{"name": r["name"] if isinstance(r, dict) else r[0], "score": r["score"] if isinstance(r, dict) else r[1], "total": r["total_questions"] if isinstance(r, dict) else r[2]} for r in rows]
        conn.close()
        return jsonify({"success": True, "leaderboard": lb})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/admin/update-special-exam', methods=['POST'])
def update_special_exam_api():
    data = request.get_json()
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    timer = data.get('timer')
    negative_marks = float(data.get('negative_marks', 0.0))
    class_name = data.get('class_name', 'General')
    subject = data.get('subject', 'General')
    season = data.get('season', 'General')
    expiry_time = data.get('expiry_time')
    if not expiry_time: expiry_time = None
    questions = data.get('questions', [])

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(f"UPDATE exams SET exam_name = {ph}, timer_minutes = {ph}, negative_marks = {ph}, class_name = {ph}, subject = {ph}, season = {ph}, expiry_time = {ph} WHERE exam_code = {ph}", 
                       (exam_name, timer, negative_marks, class_name, subject, season, expiry_time, exam_code))
        
        cursor.execute(f"DELETE FROM questions WHERE exam_code = {ph}", (exam_code,))
        for q in questions:
            q_type = q.get('question_type', 'mcq')
            max_marks = float(q.get('max_marks', 1.0))
            cursor.execute(f"INSERT INTO questions (exam_code, question_text, option_a, option_b, option_c, option_d, correct_option, question_type, max_marks) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})", 
                           (exam_code, q['question_text'] or q.get('q_text', ''), q.get('option_a', q.get('opt_a', '')), q.get('option_b', q.get('opt_b', '')), q.get('option_c', q.get('opt_c', '')), q.get('option_d', q.get('opt_d', '')), q.get('correct_option', q.get('correct', '')), q_type, max_marks))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Exam updated successfully!"})
    except Exception as e:
        app.logger.exception("Request failed")
        return jsonify({"success": False, "error": "Request could not be completed."}), 500

@app.route('/api/admin/get-users-list', methods=['GET'])
def get_users_list():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name, email, category FROM users ORDER BY name ASC")
        rows = cursor.fetchall()
        conn.close()
        users_list = [{"name": r["name"] if isinstance(r, dict) else r[0], "email": r["email"] if isinstance(r, dict) else r[1], "role": r["category"] if isinstance(r, dict) else r[2]} for r in rows]
        return jsonify({"success": True, "users": users_list})
    except Exception as e:
        app.logger.exception("Request failed")
        return jsonify({"success": False, "error": "Request could not be completed."}), 500

@app.route('/api/admin/get-all-exams', methods=['GET'])
def admin_get_all_exams():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT e.*, u.name as teacher_name 
            FROM exams e 
            LEFT JOIN users u ON e.teacher_email = u.email 
            ORDER BY e.created_at DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        exams_list = []
        for r in rows:
            exams_list.append({
                "code": r["exam_code"],
                "name": r["exam_name"],
                "teacher_name": r["teacher_name"] or "Teacher",
                "class_name": r.get("class_name", "General"),
                "subject": r.get("subject", "General"),
                "timer": r["timer_minutes"],
                "is_private": r.get("is_private", False),
                "date": str(r["created_at"]).split()[0] if r.get("created_at") else "Recently"
            })
        return jsonify({"success": True, "exams": exams_list})
    except Exception as e:
        app.logger.exception("Request failed")
        return jsonify({"success": False, "error": "Request could not be completed."}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
