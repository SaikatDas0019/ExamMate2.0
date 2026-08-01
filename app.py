from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
import sqlite3
from datetime import timedelta
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# .env ফাইল লোড করা
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "exam_mate_super_secret_key_2026")

# 🎯 পারমানেন্ট সেশন (৩০ দিন)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True

# ফাইল আপলোড ফোল্ডার কনফিগারেশন
UPLOAD_FOLDER = 'static/uploads/resources'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 🐘 PostgreSQL Database Connection Helper
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if DATABASE_URL:
        # Render PostgreSQL
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn, 'postgres'
    else:
        # Fallback to local SQLite for testing
        conn = sqlite3.connect('ExamMate.db')
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'

# 🐘 অটোমেটিক টেবিল ক্রিয়েশন
def init_db():
    conn, db_type = get_db_connection()
    cursor = conn.cursor()
    
    if db_type == 'postgres':
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                email VARCHAR(255) PRIMARY KEY, 
                name VARCHAR(255) NOT NULL, 
                category VARCHAR(50) NOT NULL,
                last_notif_read TIMESTAMP DEFAULT '1970-01-01 00:00:00'
            );
            CREATE TABLE IF NOT EXISTS results (
                id SERIAL PRIMARY KEY,
                student_email VARCHAR(255) NOT NULL,
                exam_code VARCHAR(100) NOT NULL,
                exam_name VARCHAR(255) NOT NULL,
                score INT NOT NULL,
                total_questions INT NOT NULL,
                date_taken TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS exams (
                exam_code VARCHAR(100) PRIMARY KEY, 
                exam_name VARCHAR(255) NOT NULL, 
                teacher_email VARCHAR(255) NOT NULL, 
                timer_minutes INT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS questions (
                id SERIAL PRIMARY KEY, 
                exam_code VARCHAR(100) REFERENCES exams(exam_code) ON DELETE CASCADE, 
                question_text TEXT NOT NULL, 
                option_a TEXT NOT NULL, 
                option_b TEXT NOT NULL, 
                option_c TEXT NOT NULL, 
                option_d TEXT NOT NULL, 
                correct_option VARCHAR(10) NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                message TEXT NOT NULL,
                target_role VARCHAR(50) NOT NULL,
                date_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS drive_folders (
                id SERIAL PRIMARY KEY,
                folder_name VARCHAR(255) NOT NULL,
                parent_id INT DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS drive_files (
                id SERIAL PRIMARY KEY,
                folder_id INT NOT NULL,
                title VARCHAR(255) NOT NULL,
                file_url TEXT NOT NULL,
                resource_type VARCHAR(50) DEFAULT 'Notes',
                date_uploaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, last_notif_read TIMESTAMP DEFAULT '1970-01-01 00:00:00');
            CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, student_email TEXT NOT NULL, exam_code TEXT NOT NULL, exam_name TEXT NOT NULL, score INTEGER NOT NULL, total_questions INTEGER NOT NULL, date_taken TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS exams (exam_code TEXT PRIMARY KEY, exam_name TEXT NOT NULL, teacher_email TEXT NOT NULL, timer_minutes INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY AUTOINCREMENT, exam_code TEXT, question_text TEXT NOT NULL, option_a TEXT NOT NULL, option_b TEXT NOT NULL, option_c TEXT NOT NULL, option_d TEXT NOT NULL, correct_option TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL, target_role TEXT NOT NULL, date_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS drive_folders (id INTEGER PRIMARY KEY AUTOINCREMENT, folder_name TEXT NOT NULL, parent_id INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS drive_files (id INTEGER PRIMARY KEY AUTOINCREMENT, folder_id INTEGER NOT NULL, title TEXT NOT NULL, file_url TEXT NOT NULL, resource_type TEXT DEFAULT 'Notes', date_uploaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        ''')
    conn.commit()
    conn.close()

# ইনিশিয়ালাইজ ডাটাবেস
try:
    init_db()
except Exception as e:
    print("DB Init Error:", e)

# ==========================================
# ১. HTML পেজের রুট
# ==========================================
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/signup.html')
def signup_page():
    return render_template('signup.html')

@app.route('/signin.html')
def signin_page():
    return render_template('signin.html')
    
@app.route('/auth.html')
@app.route('/login.html')
def auth_page():
    return render_template('auth.html')

@app.route('/student_dashboard.html')
def student_dashboard():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role') != 'Student':
        return redirect(url_for('auth_page'))
    return render_template('student_dashboard.html')

@app.route('/teacher_dashboard.html')
def teacher_dashboard():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role') != 'Teacher':
        return redirect(url_for('auth_page'))
    return render_template('teacher_dashboard.html')

@app.route('/teacher_profile.html')
def teacher_profile():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role') != 'Teacher':
        return redirect(url_for('auth_page'))
    return render_template('teacher_profile.html')

@app.route('/student_exam.html')
def student_exam():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role') != 'Student':
        return redirect(url_for('auth_page'))
    return render_template('student_exam.html')

@app.route('/student_profile.html')
def student_profile():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role') != 'Student':
        return redirect(url_for('auth_page'))
    return render_template('student_profile.html')

@app.route('/create_exam.html')
def create_exam():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role') != 'Teacher':
        return redirect(url_for('auth_page'))
    return render_template('create_exam.html')

@app.route('/teacher_analytics.html')
def teacher_analytics():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role') != 'Teacher':
        return redirect(url_for('auth_page'))
    return render_template('teacher_analytics.html')

@app.route('/notification.html')
def notification():
    if 'user' not in session or not isinstance(session['user'], dict):
        return redirect(url_for('auth_page'))
    return render_template('notification.html')

@app.route('/admin.html')
def admin_page():
    return render_template('admin.html')

@app.route('/student_analytics.html')
def student_analytics():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role') != 'Student':
        return redirect(url_for('auth_page'))
    return render_template('student_analytics.html')

@app.route('/student_resources.html')
def student_resources():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role') != 'Student':
        return redirect(url_for('auth_page'))
    return render_template('student_resources.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth_page'))

# ==========================================
# ২. Google Auth Sync API
# ==========================================
@app.route('/api/google-auth-sync', methods=['POST'])
def google_auth_sync():
    data = request.get_json()
    email = data.get('email')
    name = data.get('name')
    role = data.get('role', 'Student')

    if not email or not name:
        return jsonify({"success": False, "error": "Missing user details!"}), 400

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT category FROM users WHERE email = {ph}", (email,))
        existing_user = cursor.fetchone()

        if existing_user:
            user_role = existing_user['category'] if isinstance(existing_user, dict) else existing_user[0]
        else:
            user_role = role
            cursor.execute(f"INSERT INTO users (email, name, category) VALUES ({ph}, {ph}, {ph})", (email, name, user_role))
            conn.commit()

        conn.close()

        session.permanent = True
        session['user'] = {'email': email, 'name': name, 'role': user_role}

        return jsonify({
            "success": True, 
            "role": user_role, 
            "redirect_url": "/student_dashboard.html" if user_role == "Student" else "/teacher_dashboard.html"
        })

    except Exception as e:
        print(f"Auth Sync Error: {e}")
        return jsonify({"success": False, "error": "Database error occurred."}), 500

# ==========================================
# ৩. Google Drive Style Nested Folder & File APIs
# ==========================================
@app.route('/api/admin/get-drive-contents', methods=['POST'])
@app.route('/api/get-student-drive-contents', methods=['POST'])
def get_drive_contents_api():
    data = request.get_json() if request.is_json else {}
    parent_id = data.get('parent_id', 0)

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"

        cursor.execute(f"SELECT id, folder_name FROM drive_folders WHERE parent_id = {ph} ORDER BY id DESC", (parent_id,))
        folders = [{"id": r["id"], "name": r["folder_name"]} for r in cursor.fetchall()]

        cursor.execute(f"SELECT id, title, file_url, resource_type, date_uploaded FROM drive_files WHERE folder_id = {ph} ORDER BY id DESC", (parent_id,))
        files = [{
            "id": r["id"],
            "title": r["title"],
            "file_url": r["file_url"],
            "type": r["resource_type"],
            "date": str(r["date_uploaded"]).split(' ')[0]
        } for r in cursor.fetchall()]

        conn.close()
        return jsonify({"success": True, "folders": folders, "files": files})
    except Exception as e:
        print("Drive Fetch Error:", e)
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/admin/create-drive-folder', methods=['POST'])
def create_drive_folder():
    data = request.get_json()
    folder_name = data.get('folder_name')
    parent_id = data.get('parent_id', 0)

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"INSERT INTO drive_folders (folder_name, parent_id) VALUES ({ph}, {ph})", (folder_name, parent_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Folder created!"})
    except Exception as e:
        print("Folder Create Error:", e)
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/admin/upload-drive-file', methods=['POST'])
def upload_drive_file():
    try:
        folder_id = request.form.get('folder_id', 0)
        title = request.form.get('title')
        resource_type = request.form.get('resource_type', 'Notes')
        file = request.files.get('file')

        if not file:
            return jsonify({"success": False, "error": "No file chosen!"}), 400

        filename = secure_filename(file.filename)
        unique_filename = f"{resource_type}_{folder_id}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(file_path)
        file_url = f"/static/uploads/resources/{unique_filename}"

        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"INSERT INTO drive_files (folder_id, title, file_url, resource_type) VALUES ({ph}, {ph}, {ph}, {ph})",
                       (folder_id, title, file_url, resource_type))
        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": "File uploaded!"})
    except Exception as e:
        print("Upload Error:", e)
        return jsonify({"success": False, "error": "Upload Failed"}), 500

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
        else:
            cursor.execute(f"DELETE FROM drive_files WHERE id = {ph}", (item_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Deleted!"})
    except Exception as e:
        print("Delete Error:", e)
        return jsonify({"success": False, "error": "Delete Failed"}), 500

# ==========================================
# ৪. Notifications APIs
# ==========================================
@app.route('/api/admin/send-notification', methods=['POST'])
def admin_send_notification():
    data = request.get_json()
    message = data.get('message')
    target_role = data.get('target_role')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"INSERT INTO notifications (message, target_role) VALUES ({ph}, {ph})", (message, target_role))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Broadcasted!"})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/admin/get-notifications', methods=['GET'])
def admin_get_notifications():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, message, target_role, date_sent FROM notifications ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()

        notifs = [{"id": r["id"], "message": r["message"], "target_role": r["target_role"], "date": str(r["date_sent"])} for r in rows]
        return jsonify({"success": True, "notifications": notifs})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
