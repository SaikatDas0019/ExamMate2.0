from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
import sqlite3
from datetime import timedelta
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import PyPDF2
import requests

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "exam_mate_super_secret_key_2026")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True

UPLOAD_FOLDER = 'static/uploads/resources'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn, 'postgres'
    else:
        conn = sqlite3.connect('ExamMate.db')
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'

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
                    id SERIAL PRIMARY KEY,
                    reviewer_name VARCHAR(255) NOT NULL,
                    role VARCHAR(100) NOT NULL,
                    rating INT NOT NULL,
                    review_text TEXT NOT NULL,
                    date_submitted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reviewer_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    review_text TEXT NOT NULL,
                    date_submitted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
        conn.commit()
        if db_type == 'postgres':
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notifications (id SERIAL PRIMARY KEY, message TEXT NOT NULL, target_role VARCHAR(50) NOT NULL, link_url TEXT, date_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT NOT NULL, target_role TEXT NOT NULL, link_url TEXT, date_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            ''')
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

        cursor.execute('''CREATE TABLE IF NOT EXISTS likes (
                            email VARCHAR(255), 
                            exam_code VARCHAR(50), 
                            PRIMARY KEY(email, exam_code)
                        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS saves (
                            email VARCHAR(255), 
                            exam_code VARCHAR(50), 
                            PRIMARY KEY(email, exam_code)
                        )''')
        conn.commit()
        
        conn.close()
    except Exception as e:
        print("DB Init Exception:", e)

init_db()

@app.route('/')
def home(): return render_template('index.html')
@app.route('/setup_profile.html')
def setup_profile_page(): return render_template('setup_profile.html')
@app.route('/student_dashboard.html')
def student_dashboard(): return render_template('student_dashboard.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')
@app.route('/teacher_dashboard.html')
def teacher_dashboard(): return render_template('teacher_dashboard.html') if session.get('user', {}).get('role', '').lower() == 'teacher' else redirect('/')
@app.route('/teacher_profile.html')
def teacher_profile(): return render_template('teacher_profile.html') if session.get('user', {}).get('role', '').lower() == 'teacher' else redirect('/')
@app.route('/student_exam.html')
def student_exam(): return render_template('student_exam.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')
@app.route('/student_profile.html')
def student_profile(): return render_template('student_profile.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')
@app.route('/create_exam.html')
def create_exam(): return render_template('create_exam.html') if session.get('user', {}).get('role', '').lower() == 'teacher' else redirect('/')
@app.route('/teacher_analytics.html')
def teacher_analytics(): return render_template('teacher_analytics.html') if session.get('user', {}).get('role', '').lower() == 'teacher' else redirect('/')
@app.route('/notification.html')
def notification(): return render_template('notification.html') if session.get('user') else redirect('/')
@app.route('/admin.html')
def admin_page(): return render_template('admin.html')
@app.route('/student_analytics.html')
def student_analytics(): return render_template('student_analytics.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')
@app.route('/student_resources.html')
def student_resources(): return render_template('student_resources.html') if session.get('user', {}).get('role', '').lower() == 'student' else redirect('/')

@app.errorhandler(500)
def internal_error(e): session.pop('user', None); return redirect('/')
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException): return e
    session.pop('user', None); return redirect('/')

@app.route('/api/google-login', methods=['POST'])
def google_login():
    data = request.get_json()
    email = data.get('email')
    if not email: return jsonify({"success": False, "error": "Email missing!"}), 400
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT name, category, photo_url FROM users WHERE email = {ph}", (email,))
        user = cursor.fetchone()
        if user:
            user_role = user['category'] if isinstance(user, dict) else user[1]
            user_name = user['name'] if isinstance(user, dict) else user[0]
            
            cursor.execute(f"UPDATE users SET is_logged_in = TRUE WHERE email = {ph}", (email,))
            conn.commit()

            session.permanent = True
            session['user'] = {'email': email, 'name': user_name, 'role': user_role}
            conn.close()
            return jsonify({"success": True, "is_new": False, "name": user_name, "role": user_role, "redirect_url": "/student_dashboard.html" if user_role.lower() == "student" else "/teacher_dashboard.html"})
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
        cursor.execute(f"INSERT INTO users (email, name, category, photo_url, is_logged_in) VALUES ({ph}, {ph}, {ph}, {ph}, TRUE)", (email, name, role, photo_url))
        conn.commit()
        conn.close()
        session.permanent = True
        session['user'] = {'email': email, 'name': name, 'role': role}
        return jsonify({"success": True, "redirect_url": "/student_dashboard.html" if role.lower() == "student" else "/teacher_dashboard.html"})
    except Exception as e:
        return jsonify({"success": False, "error": "Failed to create account."}), 500

@app.route('/api/get-student-progress', methods=['POST'])
def get_student_progress():
    data = request.get_json()
    email = data.get('email')
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT COUNT(id) as count_id, MAX(score) as max_score, MIN(score) as min_score, SUM(score) as sum_score FROM results WHERE student_email = {ph}", (email,))
        stats = cursor.fetchone()
        conn.close()
        c_id = (stats['count_id'] if isinstance(stats, dict) else stats[0]) or 0
        m_sc = (stats['max_score'] if isinstance(stats, dict) else stats[1]) or 0
        mn_sc = (stats['min_score'] if isinstance(stats, dict) else stats[2]) or 0
        s_sc = (stats['sum_score'] if isinstance(stats, dict) else stats[3]) or 0
        return jsonify({ "success": True, "total_exams": f"{c_id:02d}", "highest": f"{m_sc:.2f}", "lowest": f"{mn_sc:.2f}", "score": f"{s_sc:.2f}" })
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

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
        cursor.execute(f"SELECT exam_name, timer_minutes, negative_marks FROM exams WHERE exam_code = {ph}", (exam_code,))
        exam_info = cursor.fetchone()
        if not exam_info:
            conn.close()
            return jsonify({"success": False, "error": "Exam not found!"}), 404

        cursor.execute(f"SELECT id, question_text, option_a, option_b, option_c, option_d, correct_option FROM questions WHERE exam_code = {ph}", (exam_code,))
        questions_rows = cursor.fetchall()
        conn.close()

        questions_list = [{"id": q["id"], "q_text": q["question_text"], "opt_a": q["option_a"], "opt_b": q["option_b"], "opt_c": q["option_c"], "opt_d": q["option_d"], "correct": q["correct_option"]} for q in questions_rows]
        
        if isinstance(exam_info, dict):
            e_name = exam_info['exam_name']
            e_time = exam_info['timer_minutes']
            e_neg = exam_info.get('negative_marks', 0.0)
        else:
            e_name = exam_info[0]
            e_time = exam_info[1]
            e_neg = exam_info[2] if len(exam_info) > 2 else 0.0

        return jsonify({ "success": True, "exam_name": e_name, "timer_minutes": e_time, "negative_marks": float(e_neg) if e_neg else 0.0, "questions": questions_list })
    except Exception as e:
        return jsonify({"success": False, "error": "Database error occurred."}), 500

@app.route('/api/submit-exam-result', methods=['POST'])
def submit_exam_result():
    data = request.get_json()
    email = data.get('email')
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    score = float(data.get('score', 0))
    total_q = data.get('total_questions')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"INSERT INTO results (student_email, exam_code, exam_name, score, total_questions) VALUES ({ph}, {ph}, {ph}, {ph}, {ph})", (email, exam_code, exam_name, score, total_q))
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
        cursor.execute(f"SELECT exam_name, score, total_questions, date_taken FROM results WHERE student_email = {ph} ORDER BY date_taken DESC", (email,))
        rows = cursor.fetchall()
        conn.close()
        history_list = [{"exam_name": r["exam_name"], "score": float(r["score"]), "total": r["total_questions"], "date": str(r["date_taken"]).split(' ')[0]} for r in rows]
        return jsonify({"success": True, "history": history_list})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/create-exam', methods=['POST'])
def create_exam_api():
    data = request.get_json()
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    timer = data.get('timer')
    negative_marks = float(data.get('negative_marks', 0.0))
    teacher_email = data.get('teacher_email', 'teacher@email.com')
    class_name = data.get('class_name', 'General')
    subject = data.get('subject', 'General')
    is_private = data.get('is_private', False)
    questions = data.get('questions')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(f"INSERT INTO exams (exam_code, exam_name, timer_minutes, negative_marks, teacher_email, class_name, subject, is_private) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})", 
                       (exam_code, exam_name, timer, negative_marks, teacher_email, class_name, subject, is_private))
     
        for q in questions:
            cursor.execute(f"INSERT INTO questions (exam_code, question_text, option_a, option_b, option_c, option_d, correct_option) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})", 
                           (exam_code, q['question_text'], q['option_a'], q['option_b'], q['option_c'], q['option_d'], q['correct_option']))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Exam published successfully!"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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
        
        total_students = (stats['std_count'] if isinstance(stats, dict) else stats[0]) or 0
        avg_sc_val = (stats['avg_sc'] if isinstance(stats, dict) else stats[1])
        avg_score = round(avg_sc_val, 1) if avg_sc_val else 0.0
        
        cursor.execute(f"SELECT exam_name, exam_code FROM exams WHERE teacher_email = {ph} ORDER BY exam_code DESC", (email,))
        all_exams = cursor.fetchall()
        conn.close()
        
        exams_list = [{"name": r["exam_name"], "code": r["exam_code"]} for r in all_exams]
        return jsonify({"success": True, "total_exams": total_exams, "total_students": total_students, "avg_score": avg_score, "all_exams": exams_list})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

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
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/teacher-full-analytics', methods=['POST'])
def teacher_full_analytics():
    data = request.get_json()
    email = data.get('email')
    if not email: return jsonify({"success": False, "error": "Email is required!"}), 400

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
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

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
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

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
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

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
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"})

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
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

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
    except Exception as e:
        return jsonify({"success": False, "has_unread": False})

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
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

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
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

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
    except Exception as e:
        return jsonify({"success": False, "error": "Save Failed"}), 500

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
    except Exception as e:
        return jsonify({"success": False, "error": "Delete Failed"}), 500

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
    except Exception as e:
        return jsonify({"success": False})

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
        Extract all the multiple-choice questions from the following text. 
        You must respond ONLY with a valid JSON array of objects. Do not include markdown formatting like ```json or ```.
        Each object must strictly follow this exact structure:
        [
            {
                "question_text": "The question here?",
                "option_a": "Option A text",
                "option_b": "Option B text",
                "option_c": "Option C text",
                "option_d": "Option D text",
                "correct_option": "A" 
            }
        ]
        Make sure 'correct_option' only contains A, B, C, or D.
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
    except Exception as e: return jsonify({"success": False, "error": str(e)}), 500
        
@app.route('/api/update-profile', methods=['POST'])
def update_profile():
    data = request.get_json()
    email = data.get('email')
    name = data.get('name')
    role = data.get('role')
    if not email or not name or not role: return jsonify({"success": False, "error": "Missing required data!"}), 400
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"UPDATE users SET name = {ph}, category = {ph} WHERE email = {ph}", (name, role, email))
        conn.commit()
        conn.close()
        if 'user' in session and session['user']['email'] == email:
            session['user']['name'] = name
            session['user']['role'] = role
            session.modified = True
        redirect_url = "/teacher_dashboard.html" if role.lower() == 'teacher' else "/student_dashboard.html"
        return jsonify({"success": True, "redirect_url": redirect_url})
    except Exception as e:
        return jsonify({"success": False, "error": "Database error occurred."}), 500
        
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/?logout=1')

@app.route('/api/check-login-status', methods=['POST'])
def check_login_status():
    data = request.get_json() or {}
    email = data.get('email')
    
    if not email and 'user' in session:
        email = session['user'].get('email')

    if not email:
        return jsonify({"logged_in": False})
        
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"SELECT category, name FROM users WHERE email = {ph}", (email,))
        user = cursor.fetchone()
        conn.close()
        
        if user:
            role = user['category'] if isinstance(user, dict) else user[0]
            name = user['name'] if isinstance(user, dict) else user[1]
            
            session.permanent = True
            session['user'] = {'email': email, 'role': role, 'name': name}
            
            return jsonify({
                "logged_in": True,
                "email": email,
                "role": role,
                "name": name,
                "redirect_url": "/teacher_dashboard.html" if role.lower() == 'teacher' else "/student_dashboard.html"
            })
            
        return jsonify({"logged_in": False})
    except Exception as e:
        return jsonify({"logged_in": False})

@app.route('/download')
def download_page(): 
    return render_template('download.html')

@app.route('/api/submit-review', methods=['POST'])
def submit_review():
    data = request.get_json()
    name = data.get('name')
    role = data.get('role', 'Student')
    rating = int(data.get('rating', 5))
    text = data.get('text')
    
    if not name or not text:
        return jsonify({"success": False, "error": "Name and Review text are required!"}), 400
        
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        cursor.execute(f"INSERT INTO reviews (reviewer_name, role, rating, review_text) VALUES ({ph}, {ph}, {ph}, {ph})", 
                       (name, role, rating, text))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Review submitted successfully!"})
    except Exception as e:
        return jsonify({"success": False, "error": "Failed to submit review."}), 500

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
            reviews_list.append({
                "name": r["reviewer_name"],
                "role": r["role"],
                "rating": r["rating"],
                "text": r["review_text"],
                "date": str(r["date_submitted"]).split(' ')[0]
            })
            total_rating += r["rating"]
            
        avg_rating = round(total_rating / len(reviews_list), 1) if reviews_list else 5.0
        
        return jsonify({
            "success": True, 
            "reviews": reviews_list,
            "avg_rating": avg_rating,
            "total_reviews": len(reviews_list)
        })
    except Exception as e:
        return jsonify({"success": False, "error": "Failed to fetch reviews."}), 500

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
        
        if item_type == 'folder':
            cursor.execute(f"UPDATE drive_folders SET parent_id = {ph} WHERE id = {ph}", (new_folder_id, item_id))
        elif item_type == 'exam':
            cursor.execute(f"UPDATE exams SET folder_id = {ph} WHERE exam_code = {ph}", (new_folder_id, item_id))
        else:
            cursor.execute(f"UPDATE drive_files SET folder_id = {ph} WHERE id = {ph}", (new_folder_id, item_id))
            
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Moved successfully!"})
    except Exception as e:
        return jsonify({"success": False, "error": "Failed to move item."}), 500

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
            
            if it_type == 'folder':
                cursor.execute(f"UPDATE drive_folders SET position = {ph} WHERE id = {ph}", (pos, it_id))
            elif it_type == 'exam':
                cursor.execute(f"UPDATE exams SET position = {ph} WHERE exam_code = {ph}", (pos, it_id))
            elif it_type == 'file':
                cursor.execute(f"UPDATE drive_files SET position = {ph} WHERE id = {ph}", (pos, it_id))
                
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": "Failed to update order."}), 500

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
    except Exception as e:
        return jsonify({"success": False, "error": "Database error"}), 500

@app.route('/api/teacher-update-exam', methods=['POST'])
def teacher_update_exam():
    data = request.get_json()
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    timer = data.get('timer')
    negative_marks = float(data.get('negative_marks', 0.0))
    questions = data.get('questions')

    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if db_type == 'postgres' else "?"
        
        cursor.execute(f"UPDATE exams SET exam_name = {ph}, timer_minutes = {ph}, negative_marks = {ph} WHERE exam_code = {ph}", 
                       (exam_name, timer, negative_marks, exam_code))
        
        cursor.execute(f"DELETE FROM questions WHERE exam_code = {ph}", (exam_code,))
        for q in questions:
            cursor.execute(f"INSERT INTO questions (exam_code, question_text, option_a, option_b, option_c, option_d, correct_option) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})", 
                           (exam_code, q['question_text'], q['option_a'], q['option_b'], q['option_c'], q['option_d'], q['correct_option']))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Exam updated successfully!"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

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
        
        search_term = f"%{query}%"
        sql = f"""SELECT e.*, u.name as teacher_name, 
                 (SELECT COUNT(*) FROM likes WHERE exam_code = e.exam_code) as total_likes,
                 (SELECT COUNT(*) FROM likes WHERE exam_code = e.exam_code AND email = {ph}) as is_liked,
                 (SELECT COUNT(*) FROM saves WHERE exam_code = e.exam_code AND email = {ph}) as is_saved
                 FROM exams e 
                 LEFT JOIN users u ON e.teacher_email = u.email 
                 WHERE (e.exam_name ILIKE {ph} OR e.exam_code ILIKE {ph} OR e.subject ILIKE {ph} OR e.class_name ILIKE {ph} OR u.name ILIKE {ph})"""
                 
        if db_type == 'sqlite':
            sql = sql.replace("ILIKE", "LIKE")
            
        params = [email, email, search_term, search_term, search_term, search_term, search_term]
        
        if selected_class != 'All':
            sql += f" AND e.class_name = {ph}"
            params.append(selected_class)
            
        if selected_subject != 'All':
            sql += f" AND e.subject = {ph}"
            params.append(selected_subject)
            
        if status_filter == 'Private':
            sql += " AND e.is_private = TRUE"
        elif status_filter == 'Public':
            sql += " AND e.is_private = FALSE"
            
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
                "code": code,
                "name": ex['exam_name'],
                "teacher_name": ex['teacher_name'] or "Teacher",
                "class_name": ex.get('class_name', 'General'),
                "subject": ex.get('subject', 'General'),
                "timer": ex['timer_minutes'],
                "is_private": ex.get('is_private', False),
                "date": str(ex['created_at']).split()[0] if ex['created_at'] else "Recently",
                "likes": ex['total_likes'],
                "shares": ex.get('shares', 0),
                "is_liked": ex['is_liked'] > 0,
                "is_saved": ex['is_saved'] > 0,
                "is_attempted": is_attempted,
                "score": history[code]['score'] if is_attempted else 0,
                "total": history[code]['total_questions'] if is_attempted else 0
            })
        conn.close()
        return jsonify({"success": True, "feed": feed})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

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
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
@app.route('/run-fix-db', methods=['GET'])
def run_fix_db():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        
        # টেবিলের মধ্যে is_private কলামটি যুক্ত করা হচ্ছে
        cursor.execute("ALTER TABLE exams ADD COLUMN is_private BOOLEAN DEFAULT FALSE;")
        conn.commit()
        conn.close()
        return "<h3>🎉 Database successfully fixed! The 'is_private' column has been added. You can now go back and publish your exam.</h3>"
    except Exception as e:
        return f"<h3>⚠️ Note:</h3> <p>{str(e)}</p><p>(If it says column already exists, that means it's already fixed and ready to use!)</p>"
        
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
