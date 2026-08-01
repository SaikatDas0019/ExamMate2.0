from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
import sqlite3
from dotenv import load_dotenv

# .env ফাইল লোড করা
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "exam_mate_super_secret_key_2026")

# ==========================================
# ১. HTML পেজের রুট (Single Auth Page & Dashboard Protection)
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
    if 'user' not in session or session['user']['role'] != 'Student':
        return redirect(url_for('auth_page'))
    return render_template('student_dashboard.html')

@app.route('/teacher_dashboard.html')
def teacher_dashboard():
    if 'user' not in session or session['user']['role'] != 'Teacher':
        return redirect(url_for('auth_page'))
    return render_template('teacher_dashboard.html')

@app.route('/teacher_profile.html')
def teacher_profile():
    if 'user' not in session or session['user']['role'] != 'Teacher':
        return redirect(url_for('auth_page'))
    return render_template('teacher_profile.html')

@app.route('/student_exam.html')
def student_exam():
    if 'user' not in session or session['user']['role'] != 'Student':
        return redirect(url_for('auth_page'))
    return render_template('student_exam.html')

@app.route('/student_profile.html')
def student_profile():
    if 'user' not in session or session['user']['role'] != 'Student':
        return redirect(url_for('auth_page'))
    return render_template('student_profile.html')

@app.route('/create_exam.html')
def create_exam():
    if 'user' not in session or session['user']['role'] != 'Teacher':
        return redirect(url_for('auth_page'))
    return render_template('create_exam.html')

@app.route('/teacher_analytics.html')
def teacher_analytics():
    if 'user' not in session or session['user']['role'] != 'Teacher':
        return redirect(url_for('auth_page'))
    return render_template('teacher_analytics.html')

@app.route('/notification.html')
def notification():
    if 'user' not in session:
        return redirect(url_for('auth_page'))
    return render_template('notification.html')

@app.route('/admin.html')
def admin_page():
    return render_template('admin.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('auth_page'))

# ==========================================
# ২. Google Auth Sync API (ফায়ারবেস লগইনের পর ব্যাকএন্ডে ডেটা সেভ রাখা)
# ==========================================
@app.route('/api/google-auth-sync', methods=['POST'])
def google_auth_sync():
    data = request.get_json()
    email = data.get('email')
    name = data.get('name')
    role = data.get('role', 'Student') # ডিফল্ট রোল Student

    if not email or not name:
        return jsonify({"success": False, "error": "Missing user details!"}), 400

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        
        # ইউজার টেবিল চেক
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL
            )
        ''')
        
        cursor.execute("SELECT category FROM users WHERE email = ?", (email,))
        existing_user = cursor.fetchone()

        if existing_user:
            # আগে থেকে থাকলে বিদ্যমান Role ব্যবহার করা হবে
            user_role = existing_user[0]
        else:
            # নতুন ইউজার হলে সিস্টেমে সেভ হবে
            user_role = role
            cursor.execute(
                "INSERT INTO users (email, name, category) VALUES (?, ?, ?)",
                (email, name, user_role)
            )
            conn.commit()

        conn.close()

        # ফ্ল্যাঙ্ক সেসন সেট করা
        session['user'] = {'email': email, 'name': name, 'role': user_role}

        return jsonify({
            "success": True, 
            "role": user_role, 
            "redirect_url": "/student_dashboard.html" if user_role == "Student" else "/teacher_dashboard.html"
        })

    except Exception as e:
        print(f"\n❌ [DB ERROR IN GOOGLE_AUTH_SYNC]: {e}\n")
        return jsonify({"success": False, "error": "Database error occurred."}), 500

# ==========================================
# ৩. স্টুডেন্ট ড্যাশবোর্ড - প্রগ্রেস ও রেজাল্ট আনা
# ==========================================
@app.route('/api/get-student-progress', methods=['POST'])
def get_student_progress():
    data = request.get_json()
    email = data.get('email')

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_email TEXT NOT NULL,
                exam_code TEXT NOT NULL,
                exam_name TEXT NOT NULL,
                score INTEGER NOT NULL,
                total_questions INTEGER NOT NULL,
                date_taken TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            SELECT COUNT(id), MAX(score), MIN(score), SUM(score) 
            FROM results WHERE student_email = ?
        ''', (email,))
        
        stats = cursor.fetchone()
        conn.close()

        return jsonify({
            "success": True,
            "total_exams": f"{stats[0]:02d}" if stats[0] else "00",
            "highest": f"{stats[1]:02d}" if stats[1] else "00",
            "lowest": f"{stats[2]:02d}" if stats[2] else "00",
            "score": f"{stats[3]:02d}" if stats[3] else "00"
        })
    except Exception as e:
        print("DB Error (Student Progress):", e)
        return jsonify({"success": False, "error": "Database error"}), 500

# ==========================================
# ৪. এক্সাম সার্চ চেক করা
# ==========================================
@app.route('/api/check-exam', methods=['POST'])
def check_exam():
    data = request.get_json()
    exam_code = data.get('exam_code')

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        cursor.execute("SELECT exam_name FROM exams WHERE exam_code = ?", (exam_code,))
        exam = cursor.fetchone()
        conn.close()

        if exam:
            return jsonify({"success": True, "exam_name": exam[0]})
        else:
            return jsonify({"success": False, "error": f"No Exam Found with code: {exam_code}"})
    except Exception as e:
        print("DB Error (Check Exam):", e)
        return jsonify({"success": False, "error": "Database error"}), 500

# ==========================================
# ৫. এক্সাম পেজ - পরীক্ষার প্রশ্ন ও সময় আনা
# ==========================================
@app.route('/api/get-exam-questions', methods=['POST'])
def get_exam_questions():
    data = request.get_json()
    exam_code = data.get('exam_code')

    try:
        conn = sqlite3.connect('ExamMate.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT exam_name, timer_minutes FROM exams WHERE exam_code = ?", (exam_code,))
        exam_info = cursor.fetchone()

        if not exam_info:
            conn.close()
            return jsonify({"success": False, "error": "Exam not found in database!"}), 404

        cursor.execute("""
            SELECT id, question_text, option_a, option_b, option_c, option_d, correct_option 
            FROM questions WHERE exam_code = ?
        """, (exam_code,))
        questions_rows = cursor.fetchall()
        conn.close()

        questions_list = [{"id": q["id"], "q_text": q["question_text"], "opt_a": q["option_a"], "opt_b": q["option_b"], "opt_c": q["option_c"], "opt_d": q["option_d"], "correct": q["correct_option"]} for q in questions_rows]

        return jsonify({
            "success": True,
            "exam_name": exam_info["exam_name"],
            "timer_minutes": exam_info["timer_minutes"],
            "questions": questions_list
        })
    except Exception as e:
        print("DB Error (Get Questions):", e)
        return jsonify({"success": False, "error": "Database error occurred."}), 500

# ==========================================
# ৬. এক্সাম পেজ - পরীক্ষার রেজাল্ট সেভ করা
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
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO results (student_email, exam_code, exam_name, score, total_questions) 
            VALUES (?, ?, ?, ?, ?)
        ''', (email, exam_code, exam_name, score, total_q))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Result saved successfully!"})
    except Exception as e:
        print("DB Error (Submit Result):", e)
        return jsonify({"success": False, "error": "Failed to save result."}), 500

# ==========================================
# ৭. প্রোফাইল পেজ - স্টুডেন্টের এক্সাম হিস্ট্রি আনা
# ==========================================
@app.route('/api/get-student-history', methods=['POST'])
def get_student_history():
    data = request.get_json()
    email = data.get('email')

    try:
        conn = sqlite3.connect('ExamMate.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT exam_name, score, total_questions, date_taken 
            FROM results WHERE student_email = ? ORDER BY date_taken DESC
        """, (email,))
        rows = cursor.fetchall()
        conn.close()

        history_list = [{"exam_name": r["exam_name"], "score": r["score"], "total": r["total_questions"], "date": r["date_taken"].split(' ')[0]} for r in rows]
        return jsonify({"success": True, "history": history_list})
    except Exception as e:
        print("DB Error (Student History):", e)
        return jsonify({"success": False, "error": "Database error"}), 500

# ==========================================
# ৮. Create Exam (Teacher) - প্রশ্ন ও পরীক্ষা সেভ করা
# ==========================================
@app.route('/api/create-exam', methods=['POST'])
def create_exam_api():
    data = request.get_json()
    exam_code = data.get('exam_code')
    exam_name = data.get('exam_name')
    timer = data.get('timer')
    teacher_email = data.get('teacher_email')
    questions = data.get('questions')

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS exams (exam_code TEXT PRIMARY KEY, exam_name TEXT NOT NULL, teacher_email TEXT NOT NULL, timer_minutes INTEGER NOT NULL)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY AUTOINCREMENT, exam_code TEXT, question_text TEXT NOT NULL, option_a TEXT NOT NULL, option_b TEXT NOT NULL, option_c TEXT NOT NULL, option_d TEXT NOT NULL, correct_option TEXT NOT NULL, FOREIGN KEY (exam_code) REFERENCES exams (exam_code))''')

        cursor.execute("INSERT INTO exams (exam_code, exam_name, teacher_email, timer_minutes) VALUES (?, ?, ?, ?)", (exam_code, exam_name, teacher_email, timer))
        
        for q in questions:
            cursor.execute("INSERT INTO questions (exam_code, question_text, option_a, option_b, option_c, option_d, correct_option) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                           (exam_code, q['question_text'], q['option_a'], q['option_b'], q['option_c'], q['option_d'], q['correct_option']))
        
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Exam published successfully!"})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "This Exam Code is already used! Please choose a different code."}), 400
    except Exception as e:
        print("DB Error (Create Exam):", e)
        return jsonify({"success": False, "error": "Database error occurred."}), 500

# ==========================================
# ৯. Teacher Dashboard - ম্যাট্রিক্স ও এক্সাম লিস্ট
# ==========================================
@app.route('/api/teacher-dashboard', methods=['POST'])
def get_teacher_dashboard():
    data = request.get_json()
    email = data.get('email')
    
    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM exams WHERE teacher_email = ?", (email,))
        total_exams = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(DISTINCT student_email), AVG(CAST(score AS FLOAT) / total_questions * 100) FROM results WHERE exam_code IN (SELECT exam_code FROM exams WHERE teacher_email = ?)", (email,))
        stats = cursor.fetchone()
        total_students = stats[0] or 0
        avg_score = round(stats[1], 1) if stats[1] else 0.0
        
        cursor.execute("SELECT exam_name, exam_code FROM exams WHERE teacher_email = ? ORDER BY rowid DESC", (email,))
        all_exams = cursor.fetchall()
        conn.close()
        
        exams_list = [{"name": row[0], "code": row[1]} for row in all_exams]
        return jsonify({"success": True, "total_exams": total_exams, "total_students": total_students, "avg_score": avg_score, "all_exams": exams_list})
    except Exception as e:
        print("DB Error (Teacher Dashboard):", e)
        return jsonify({"success": False, "error": "Database error"}), 500

# ==========================================
# ১০. Teacher Profile - অ্যানালাইসিস উইন্ডো
# ==========================================
@app.route('/api/teacher-analysis', methods=['POST'])
def get_teacher_analysis():
    data = request.get_json()
    exam_code = data.get('exam_code')
    
    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        cursor.execute("SELECT users.name, results.score, results.total_questions FROM results JOIN users ON results.student_email = users.email WHERE results.exam_code = ?", (exam_code,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return jsonify({"success": False, "error": "No students have taken this exam yet."})
            
        total_students = len(rows)
        total_q = rows[0][2]
        student_data = [{"name": r[0], "score": r[1], "perf": round((r[1]/total_q)*100, 2)} for r in rows]
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
        print("DB Error (Teacher Analysis):", e)
        return jsonify({"success": False, "error": "Database error"}), 500

# ==========================================
# ১১. Notifications - নোটিফিকেশন রিকভার করা
# ==========================================
@app.route('/api/notifications', methods=['POST'])
def get_notifications():
    data = request.get_json()
    email = data.get('email')

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT category FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        role = user[0] if user else 'All'

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                target_role TEXT NOT NULL,
                date_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute("SELECT COUNT(*) FROM notifications")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO notifications (message, target_role) VALUES ('Welcome to ExamMate! Ensure your profile is updated.', 'All')")
            conn.commit()

        cursor.execute("""
            SELECT message, date_sent FROM notifications 
            WHERE target_role = 'All' OR target_role = ? 
            ORDER BY id DESC
        """, (role,))
        
        notifs = cursor.fetchall()
        conn.close()

        notifs_list = [{"msg": n[0], "date": n[1].split(' ')[0]} for n in notifs]
        return jsonify({"success": True, "notifications": notifs_list})
    except Exception as e:
        print("DB Error (Notifications):", e)
        return jsonify({"success": False, "error": "Database error"}), 500

# ==========================================
# ১২. Send Notification (Admin Only)
# ==========================================
@app.route('/api/admin/send-notification', methods=['POST'])
def admin_send_notification():
    data = request.get_json()
    message = data.get('message')
    target_role = data.get('target_role')

    if not message or not target_role:
        return jsonify({"success": False, "error": "Message and Target Role are required!"}), 400

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO notifications (message, target_role) VALUES (?, ?)", (message, target_role))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Notification broadcasted successfully! 🚀"})
    except Exception as e:
        print("Admin Notification Error:", e)
        return jsonify({"success": False, "error": "Database error occurred."}), 500

# ==========================================
# প্রোফাইল আপডেট ও রোল পরিবর্তনের API
# ==========================================
@app.route('/api/update-profile', methods=['POST'])
def update_profile():
    data = request.get_json()
    email = data.get('email')
    new_name = data.get('name')
    new_role = data.get('role')

    if not email or not new_name or not new_role:
        return jsonify({"success": False, "error": "Incomplete data!"}), 400

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        
        # ডেটাবেসে নাম ও ক্যাটাগরি/রোল আপডেট করা
        cursor.execute("UPDATE users SET name = ?, category = ? WHERE email = ?", (new_name, new_role, email))
        conn.commit()
        conn.close()

        # ফ্ল্যাঙ্ক সেশন আপডেট করা
        session['user'] = {'email': email, 'name': new_name, 'role': new_role}

        redirect_url = "/student_dashboard.html" if new_role == "Student" else "/teacher_profile.html"

        return jsonify({
            "success": True, 
            "message": "Profile updated successfully!",
            "new_role": new_role,
            "redirect_url": redirect_url
        })
    except Exception as e:
        print(f"Profile Update Error: {e}")
        return jsonify({"success": False, "error": "Database update failed!"}), 500
# ==========================================
# Teacher Full Analytics Real Data API
# ==========================================
@app.route('/api/teacher-full-analytics', methods=['POST'])
def teacher_full_analytics():
    data = request.get_json()
    email = data.get('email')

    if not email:
        return jsonify({"success": False, "error": "Email is required!"}), 400

    try:
        conn = sqlite3.connect('ExamMate.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # ১. শিক্ষকের তৈরি সমস্ত এক্সাম আনা
        cursor.execute("SELECT exam_code, exam_name FROM exams WHERE teacher_email = ?", (email,))
        teacher_exams = cursor.fetchall()

        if not teacher_exams:
            conn.close()
            return jsonify({
                "success": True,
                "overall": {"students": 0, "attempts": 0, "avg": "0.0", "high": "0.0", "exams": 0},
                "examStats": [], "leaderboard": [], "studentProgress": {}
            })

        exam_codes = [e['exam_code'] for e in teacher_exams]
        placeholders = ','.join(['?'] * len(exam_codes))

        # ২. ওই পরীক্ষাগুলোর সমস্ত রেজাল্ট আনা (Users টেবিল জয়েন করে স্টুডেন্টের নামসহ)
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

        # ৩. ডেটা প্রসেসিং (Calculations)
        total_attempts = len(results)
        unique_students = len(set(r['student_email'] for r in results))
        
        all_percentages = [round((r['score'] / r['total_questions']) * 100, 1) for r in results if r['total_questions'] > 0]
        class_avg = round(sum(all_percentages) / len(all_percentages), 1) if all_percentages else 0.0
        highest_score = max(all_percentages) if all_percentages else 0.0

        # ৪. Exam-wise Performance
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

        # ৫. Student Leaderboard & Progress Data
        student_map = {}
        for r in results:
            s_email = r['student_email']
            s_name = r['student_name'] if r['student_name'] else s_email.split('@')[0]
            perf = round((r['score'] / r['total_questions']) * 100, 1) if r['total_questions'] > 0 else 0
            score = r['score']

            if s_email not in student_map:
                student_map[s_email] = {
                    "name": s_name,
                    "perfs": [],
                    "scores": [],
                    "exams_taken": [],
                    "exam_names": []
                }
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

        # টপ স্কোর অনুযায়ী লিডারবোর্ড সর্ট করা
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
        print("DB Error (Teacher Full Analytics):", e)
        return jsonify({"success": False, "error": "Database error occurred."}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

