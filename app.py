from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
import sqlite3
from datetime import timedelta
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# .env ফাইল লোড করা
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "exam_mate_super_secret_key_2026")

# 🎯 পারমানেন্ট সেশন (৩০ দিন পর্যন্ত সেশন থাকবে)
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
        # Fallback to local SQLite
        conn = sqlite3.connect('ExamMate.db')
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'

# 🐘 অটোমেটিক টেবিল ক্রিয়েশন
def init_db():
    try:
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
        
        # Profile Picture Column
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN photo_url TEXT;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()
            
        # 🆕 Folder ID Column for Exams (To link exams inside folders)
        try:
            cursor.execute("ALTER TABLE exams ADD COLUMN folder_id INT DEFAULT 0;")
            conn.commit()
        except Exception:
            if db_type == 'postgres': conn.rollback()
            
        conn.close()
    except Exception as e:
        print("DB Init Exception:", e)

init_db()

# ==========================================
# ১. HTML পেজের রুট (Page Routing)
# ==========================================
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/setup_profile.html')
def setup_profile_page():
    return render_template('setup_profile.html')

@app.route('/student_dashboard.html')
def student_dashboard():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role', '').lower() != 'student':
        return redirect('/')
    return render_template('student_dashboard.html')

@app.route('/teacher_dashboard.html')
def teacher_dashboard():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role', '').lower() != 'teacher':
        return redirect('/')
    return render_template('teacher_dashboard.html')

@app.route('/teacher_profile.html')
def teacher_profile():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role', '').lower() != 'teacher':
        return redirect('/')
    return render_template('teacher_profile.html')

@app.route('/student_exam.html')
def student_exam():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role', '').lower() != 'student':
        return redirect('/')
    return render_template('student_exam.html')

@app.route('/student_profile.html')
def student_profile():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role', '').lower() != 'student':
        return redirect('/')
    return render_template('student_profile.html')

@app.route('/create_exam.html')
def create_exam():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role', '').lower() != 'teacher':
        return redirect('/')
    return render_template('create_exam.html')

@app.route('/teacher_analytics.html')
def teacher_analytics():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role', '').lower() != 'teacher':
        return redirect('/')
    return render_template('teacher_analytics.html')

@app.route('/notification.html')
def notification():
    if 'user' not in session or not isinstance(session['user'], dict):
        return redirect('/')
    return render_template('notification.html')

@app.route('/admin.html')
def admin_page():
    return render_template('admin.html')

@app.route('/student_analytics.html')
def student_analytics():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role', '').lower() != 'student':
        return redirect('/')
    return render_template('student_analytics.html')

@app.route('/student_resources.html')
def student_resources():
    if 'user' not in session or not isinstance(session['user'], dict) or session['user'].get('role', '').lower() != 'student':
        return redirect('/')
    return render_template('student_resources.html')

# 🎯 গ্লোবাল এরর হ্যান্ডলার
@app.errorhandler(500)
def internal_error(e):
    session.pop('user', None)
    return redirect('/')

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e
    session.pop('user', None)
    return redirect('/')

# 🎯 নিরাপদ লগআউট
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/')

# ==========================================
# ২. Google Login & Signup APIs
# ==========================================
@app.route('/api/google-login', methods=['POST'])
def google_login():
    data = request.get_json()
    email = data.get('email')
    
    if not email:
        return jsonify({"success": False, "error": "Email missing!"}), 400

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(f"SELECT name, category, photo_url FROM users WHERE email = {ph}", (email,))
        user = cursor.fetchone()
        
        if user:
            user_role = user['category'] if isinstance(user, dict) else user[1]
            user_name = user['name'] if isinstance(user, dict) else user[0]
            
            session.permanent = True
            session['user'] = {'email': email, 'name': user_name, 'role': user_role}
            conn.close()
            
            return jsonify({
                "success": True, 
                "is_new": False,
                "name": user_name,
                "role": user_role,
                "redirect_url": "/student_dashboard.html" if user_role.lower() == "student" else "/teacher_dashboard.html"
            })
        else:
            conn.close()
            return jsonify({"success": True, "is_new": True})
            
    except Exception as e:
        return jsonify({"success": False, "error": "Database error occurred."}), 500

@app.route('/api/complete-signup', methods=['POST'])
def complete_signup():
    data = request.get_json()
    email = data.get('email')
    name = data.get('name')
    role = data.get('role', 'Student')
    photo_url = data.get('photo_url')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(
            f"INSERT INTO users (email, name, category, photo_url) VALUES ({ph}, {ph}, {ph}, {ph})", 
            (email, name, role, photo_url)
        )
        conn.commit()
        conn.close()

        session.permanent = True
        session['user'] = {'email': email, 'name': name, 'role': role}

        return jsonify({
            "success": True, 
            "redirect_url": "/student_dashboard.html" if role.lower() == "student" else "/teacher_dashboard.html"
        })
    except Exception as e:
        return jsonify({"success": False, "error": "Failed to create account."}), 500

# ==========================================
# ৩. স্টুডেন্ট ড্যাশবোর্ড - প্রগ্রেস ও রেজাল্ট আনা
# ==========================================
@app.route('/api/get-student-progress', methods=['POST'])
def get_student_progress():
    data = request.get_json()
    email = data.get('email')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"

        cursor.execute(f'''
            SELECT COUNT(id) as count_id, MAX(score) as max_score, MIN(score) as min_score, SUM(score) as sum_score 
            FROM results WHERE student_email = {ph}
        ''', (email,))
        
        stats = cursor.fetchone()
        conn.close()

        if isinstance(stats, dict):
            c_id = stats['count_id'] or 0
            m_sc = stats['max_score'] or 0
            mn_sc = stats['min_score'] or 0
            s_sc = stats['sum_score'] or 0
        else:
            c_id = stats[0] if stats and stats[0] else 0
            m_sc = stats[1] if stats and stats[1] else 0
            mn_sc = stats[2] if stats and stats[2] else 0
            s_sc = stats[3] if stats and stats[3] else 0

        return jsonify({
            "success": True,
            "total_exams": f"{c_id:02d}",
            "highest": f"{m_sc:02d}",
            "lowest": f"{mn_sc:02d}",
            "score": f"{s_sc:02d}"
        })
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

# ==========================================
# ৪. এক্সাম সার্চ ও প্রশ্ন আনা
# ==========================================
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

        if exam:
            e_name = exam['exam_name'] if isinstance(exam, dict) else exam[0]
            return jsonify({"success": True, "exam_name": e_name})
        else:
            return jsonify({"success": False, "error": f"No Exam Found with code: {exam_code}"})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/get-exam-questions', methods=['POST'])
def get_exam_questions():
    data = request.get_json()
    exam_code = data.get('exam_code')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"

        cursor.execute(f"SELECT exam_name, timer_minutes FROM exams WHERE exam_code = {ph}", (exam_code,))
        exam_info = cursor.fetchone()

        if not exam_info:
            conn.close()
            return jsonify({"success": False, "error": "Exam not found in database!"}), 404

        cursor.execute(f"""
            SELECT id, question_text, option_a, option_b, option_c, option_d, correct_option 
            FROM questions WHERE exam_code = {ph}
        """, (exam_code,))
        questions_rows = cursor.fetchall()
        conn.close()

        questions_list = []
        for q in questions_rows:
            questions_list.append({
                "id": q["id"], "q_text": q["question_text"], 
                "opt_a": q["option_a"], "opt_b": q["option_b"], 
                "opt_c": q["option_c"], "opt_d": q["option_d"], 
                "correct": q["correct_option"]
            })

        e_name = exam_info['exam_name'] if isinstance(exam_info, dict) else exam_info[0]
        t_min = exam_info['timer_minutes'] if isinstance(exam_info, dict) else exam_info[1]

        return jsonify({
            "success": True,
            "exam_name": e_name,
            "timer_minutes": t_min,
            "questions": questions_list
        })
    except Exception as e:
        return jsonify({"success": False, "error": "Database error occurred."}), 500

# ==========================================
# ৫. পরীক্ষার রেজাল্ট ও হিস্ট্রি API
# ==========================================
@app.route('/api/submit-exam-result', methods=['POST'])
def submit_exam_result():
    data = request.get_json()
    email = data.get('email')
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    score = data.get('score')
    total_q = data.get('total_questions')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f'''
            INSERT INTO results (student_email, exam_code, exam_name, score, total_questions) 
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
        ''', (email, exam_code, exam_name, score, total_q))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Result saved successfully!"})
    except Exception as e:
        return jsonify({"success": False, "error": "Failed to save result."}), 500

@app.route('/api/get-student-history', methods=['POST'])
def get_student_history():
    data = request.get_json()
    email = data.get('email')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"""
            SELECT exam_name, score, total_questions, date_taken 
            FROM results WHERE student_email = {ph} ORDER BY date_taken DESC
        """, (email,))
        rows = cursor.fetchall()
        conn.close()

        history_list = [{"exam_name": r["exam_name"], "score": r["score"], "total": r["total_questions"], "date": str(r["date_taken"]).split(' ')[0]} for r in rows]
        return jsonify({"success": True, "history": history_list})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

# ==========================================
# ৬. Teacher Create Exam & Dashboard
# ==========================================
@app.route('/api/create-exam', methods=['POST'])
def create_exam_api():
    data = request.get_json()
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    timer = data.get('timer')
    teacher_email = data.get('teacher_email', 'dasbabu938207@gmail.com') # 🆕 Default Teacher Email
    folder_id = data.get('folder_id', 0) # 🆕 Folder ID for exams inside folders
    questions = data.get('questions')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"

        cursor.execute(f"INSERT INTO exams (exam_code, exam_name, teacher_email, timer_minutes, folder_id) VALUES ({ph}, {ph}, {ph}, {ph}, {ph})", 
                       (exam_code, exam_name, teacher_email, timer, folder_id))
        
        for q in questions:
            cursor.execute(f"INSERT INTO questions (exam_code, question_text, option_a, option_b, option_c, option_d, correct_option) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})", 
                           (exam_code, q['question_text'], q['option_a'], q['option_b'], q['option_c'], q['option_d'], q['correct_option']))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Exam published successfully!"})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error or Code already exists."}), 500

@app.route('/api/teacher-dashboard', methods=['POST'])
def get_teacher_dashboard():
    data = request.get_json()
    email = data.get('email')
    
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(f"SELECT COUNT(*) as count FROM exams WHERE teacher_email = {ph}", (email,))
        row = cursor.fetchone()
        total_exams = (row['count'] if isinstance(row, dict) else row[0]) or 0
        
        cursor.execute(f"SELECT COUNT(DISTINCT student_email) as std_count, AVG(CAST(score AS FLOAT) / total_questions * 100) as avg_sc FROM results WHERE exam_code IN (SELECT exam_code FROM exams WHERE teacher_email = {ph})", (email,))
        stats = cursor.fetchone()
        
        if isinstance(stats, dict):
            total_students = stats['std_count'] or 0
            avg_score = round(stats['avg_sc'], 1) if stats['avg_sc'] else 0.0
        else:
            total_students = stats[0] or 0
            avg_score = round(stats[1], 1) if stats[1] else 0.0
        
        cursor.execute(f"SELECT exam_name, exam_code FROM exams WHERE teacher_email = {ph} ORDER BY exam_code DESC", (email,))
        all_exams = cursor.fetchall()
        conn.close()
        
        exams_list = [{"name": r["exam_name"], "code": r["exam_code"]} for r in all_exams]
        return jsonify({"success": True, "total_exams": total_exams, "total_students": total_students, "avg_score": avg_score, "all_exams": exams_list})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

# ==========================================
# ৭. Teacher Profile & Full Analytics
# ==========================================
@app.route('/api/teacher-analysis', methods=['POST'])
def get_teacher_analysis():
    data = request.get_json()
    exam_code = data.get('exam_code')
    
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT users.name, results.score, results.total_questions FROM results JOIN users ON results.student_email = users.email WHERE results.exam_code = {ph}", (exam_code,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return jsonify({"success": False, "error": "No students have taken this exam yet."})
            
        total_students = len(rows)
        total_q = rows[0]["total_questions"]
        student_data = [{"name": r["name"], "score": r["score"], "perf": round((r["score"]/total_q)*100, 2)} for r in rows]
        avg_score = round(sum([r['score'] for r in student_data]) / total_students, 2)
        
        sorted_students = sorted(student_data, key=lambda x: x['score'], reverse=True)
        top_students = sorted_students[:10]
        bottom_students = sorted_students[-10:] if len(sorted_students) > 10 else sorted_students[::-1]
        
        chart_data = [0] * (total_q + 1)
        for s in student_data:
            chart_data[s['score']] += 1
        labels = [str(i) for i in range(total_q + 1)]
            
        return jsonify({
            "success": True, "total_students": total_students, "avg_score": avg_score, "total_q": total_q,
            "top": top_students, "bottom": bottom_students, "all_students": sorted_students,
            "chartLabels": labels, "chartData": chart_data
        })
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/teacher-full-analytics', methods=['POST'])
def teacher_full_analytics():
    data = request.get_json()
    email = data.get('email')

    if not email:
        return jsonify({"success": False, "error": "Email is required!"}), 400

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"

        cursor.execute(f"SELECT exam_code, exam_name FROM exams WHERE teacher_email = {ph}", (email,))
        teacher_exams = cursor.fetchall()

        if not teacher_exams:
            conn.close()
            return jsonify({
                "success": True,
                "overall": {"students": 0, "attempts": 0, "avg": "0.0", "high": "0.0", "exams": 0},
                "examStats": [], "leaderboard": [], "studentProgress": {}
            })

        exam_codes = [e['exam_code'] for e in teacher_exams]
        placeholders = ','.join([ph] * len(exam_codes))

        query = f"""
            SELECT r.student_email, u.name as student_name, r.exam_code, r.exam_name, r.score, r.total_questions, r.date_taken
            FROM results r
            LEFT JOIN users u ON r.student_email = u.email
            WHERE r.exam_code IN ({placeholders})
        """
        cursor.execute(query, exam_codes)
        results = cursor.fetchall()
        conn.close()

        if not results:
            return jsonify({
                "success": True,
                "overall": {"students": 0, "attempts": 0, "avg": "0.0", "high": "0.0", "exams": len(teacher_exams)},
                "examStats": [{"name": e['exam_name'], "avg": 0, "high": 0, "low": 0, "attempts": 0} for e in teacher_exams],
                "leaderboard": [], "studentProgress": {}
            })

        total_attempts = len(results)
        unique_students = len(set(r['student_email'] for r in results))
        all_percentages = [round((r['score'] / r['total_questions']) * 100, 1) for r in results if r['total_questions'] > 0]
        class_avg = round(sum(all_percentages) / len(all_percentages), 1) if all_percentages else 0.0
        highest_score = max(all_percentages) if all_percentages else 0.0

        exam_map = {}
        for r in results:
            code = r['exam_code']
            perf = round((r['score'] / r['total_questions']) * 100, 1) if r['total_questions'] > 0 else 0
            if code not in exam_map:
                exam_map[code] = {"name": r['exam_name'], "perfs": [], "attempts": 0}
            exam_map[code]["perfs"].append(perf)
            exam_map[code]["attempts"] += 1

        exam_stats = []
        for e in teacher_exams:
            code = e['exam_code']
            if code in exam_map:
                perfs = exam_map[code]["perfs"]
                exam_stats.append({
                    "name": exam_map[code]["name"],
                    "avg": round(sum(perfs) / len(perfs), 1),
                    "high": max(perfs),
                    "low": min(perfs),
                    "attempts": exam_map[code]["attempts"]
                })
            else:
                exam_stats.append({"name": e['exam_name'], "avg": 0, "high": 0, "low": 0, "attempts": 0})

        student_map = {}
        for r in results:
            s_email = r['student_email']
            s_name = r['student_name'] if r['student_name'] else s_email.split('@')[0]
            perf = round((r['score'] / r['total_questions']) * 100, 1) if r['total_questions'] > 0 else 0
            score = r['score']

            if s_email not in student_map:
                student_map[s_email] = {"name": s_name, "perfs": [], "scores": [], "exam_names": []}
            student_map[s_email]["perfs"].append(perf)
            student_map[s_email]["scores"].append(score)
            student_map[s_email]["exam_names"].append(r['exam_name'])

        leaderboard = []
        student_progress = {}

        for email_key, data in student_map.items():
            s_avg = round(sum(data["perfs"]) / len(data["perfs"]), 1)
            s_best = max(data["perfs"])
            s_taken = len(data["perfs"])
            s_total_score = sum(data["scores"])

            leaderboard.append({
                "name": data["name"],
                "avg": s_avg,
                "best": s_best,
                "taken": s_taken,
                "totalScore": s_total_score
            })

            student_progress[data["name"]] = {
                "avg": s_avg,
                "best": s_best,
                "taken": s_taken,
                "labels": data["exam_names"],
                "data": data["perfs"]
            }

        leaderboard = sorted(leaderboard, key=lambda x: x['avg'], reverse=True)

        return jsonify({
            "success": True,
            "overall": {
                "students": unique_students,
                "attempts": total_attempts,
                "avg": class_avg,
                "high": highest_score,
                "exams": len(teacher_exams)
            },
            "examStats": exam_stats,
            "leaderboard": leaderboard,
            "studentProgress": student_progress
        })

    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

# ==========================================
# ৮. Notifications & Profile APIs
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

@app.route('/api/check-unread-notifications', methods=['POST'])
def check_unread_notifications():
    data = request.get_json()
    email = data.get('email')

    if not email:
        return jsonify({"success": False, "has_unread": False})

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"

        cursor.execute(f"SELECT category, last_notif_read FROM users WHERE email = {ph}", (email,))
        user = cursor.fetchone()
        role = user["category"] if user else 'All'
        last_read = user["last_notif_read"] if (user and user["last_notif_read"]) else '1970-01-01 00:00:00'

        cursor.execute(f"""
            SELECT COUNT(*) as count FROM notifications 
            WHERE (target_role = 'All' OR target_role = {ph}) 
            AND date_sent > {ph}
        """, (role, last_read))
        
        row = cursor.fetchone()
        conn.close()

        unread_count = row['count'] if isinstance(row, dict) else row[0]
        return jsonify({"success": True, "has_unread": unread_count > 0})
    except Exception as e:
        return jsonify({"success": False, "has_unread": False})

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

        cursor.execute(f"""
            SELECT id, message, target_role, date_sent 
            FROM notifications 
            WHERE target_role = 'All' OR target_role = {ph} 
            ORDER BY id DESC
        """, (role,))
        rows = cursor.fetchall()
        conn.close()

        notifs = []
        for r in rows:
            date_sent = str(r["date_sent"])
            is_unread = date_sent > last_read
            notifs.append({
                "id": r["id"], "message": r["message"], 
                "target_role": r["target_role"], 
                "date": date_sent.split('.')[0], 
                "is_unread": is_unread
            })

        return jsonify({"success": True, "notifications": notifs})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

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
    except Exception as e:
        return jsonify({"success": False})

@app.route('/api/update-profile', methods=['POST'])
def update_profile():
    data = request.get_json()
    email = data.get('email')
    new_name = data.get('name')
    new_role = data.get('role')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"UPDATE users SET name = {ph}, category = {ph} WHERE email = {ph}", (new_name, new_role, email))
        conn.commit()
        conn.close()

        session['user'] = {'email': email, 'name': new_name, 'role': new_role}
        redirect_url = "/student_dashboard.html" if new_role.lower() == "student" else "/teacher_profile.html"

        return jsonify({
            "success": True, 
            "message": "Profile updated successfully!",
            "new_role": new_role,
            "redirect_url": redirect_url
        })
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/admin/edit-notification', methods=['POST'])
def edit_notification():
    data = request.get_json()
    notif_id = data.get('id')
    new_message = data.get('message')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"UPDATE notifications SET message = {ph} WHERE id = {ph}", (new_message, notif_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"})

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
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"})
        
# ==========================================
# ৯. 📂 Google Drive Style APIs + Exam Support
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

        # 🆕 Fetch exams linked to this folder
        cursor.execute(f"SELECT exam_code, exam_name, timer_minutes FROM exams WHERE folder_id = {ph} ORDER BY exam_code DESC", (parent_id,))
        exams = [{
            "code": r["exam_code"],
            "name": r["exam_name"],
            "timer": r["timer_minutes"]
        } for r in cursor.fetchall()]

        conn.close()
        return jsonify({"success": True, "folders": folders, "files": files, "exams": exams})
    except Exception as e:
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
            cursor.execute(f"DELETE FROM exams WHERE folder_id = {ph}", (item_id,)) # Delete exams inside folder
        elif item_type == 'exam':
            cursor.execute(f"DELETE FROM exams WHERE exam_code = {ph}", (item_id,))
        else:
            cursor.execute(f"DELETE FROM drive_files WHERE id = {ph}", (item_id,))
            
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Deleted!"})
    except Exception as e:
        return jsonify({"success": False, "error": "Delete Failed"}), 500

# ==========================================
# ১০. Profile Picture APIs
# ==========================================
@app.route('/api/get-profile-data', methods=['POST'])
def get_profile_data():
    data = request.get_json()
    email = data.get('email')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT name, photo_url FROM users WHERE email = {ph}", (email,))
        user = cursor.fetchone()
        conn.close()
        if user:
            return jsonify({"success": True, "name": user['name'], "photo_url": user['photo_url']})
        return jsonify({"success": False})
    except Exception as e:
        return jsonify({"success": False})

@app.route('/api/upload-profile-pic', methods=['POST'])
def upload_profile_pic():
    email = request.form.get('email')
    action = request.form.get('action')
    file = request.files.get('file')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"

        if action == 'remove':
            cursor.execute(f"UPDATE users SET photo_url = NULL WHERE email = {ph}", (email,))
            conn.commit()
            conn.close()
            return jsonify({"success": True})

        if file:
            PROFILE_PICS_FOLDER = 'static/uploads/profiles'
            os.makedirs(PROFILE_PICS_FOLDER, exist_ok=True)
            filename = secure_filename(file.filename)
            unique_filename = f"profile_{email.replace('@','_').replace('.','_')}_{filename}"
            file_path = os.path.join(PROFILE_PICS_FOLDER, unique_filename)
            file.save(file_path)
            
            file_url = f"/static/uploads/profiles/{unique_filename}"
            cursor.execute(f"UPDATE users SET photo_url = {ph} WHERE email = {ph}", (file_url, email))
            conn.commit()
            conn.close()
            return jsonify({"success": True, "photo_url": file_url})
        
        return jsonify({"success": False, "error": "No file uploaded"})
    except Exception as e:
        return jsonify({"success": False})
        
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
