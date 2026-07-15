from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import random
import os
import sqlite3
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

# .env ফাইল লোড করা
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "exam_mate_super_secret_key_2026")

# ==========================================
# ০. MailerSend ইমেইল ফাংশন
# ==========================================
def send_email_mailersend(to_email, subject, html):
    api_key = os.getenv("MAILERSEND_API_KEY")
    from_email = os.getenv("MAILERSEND_FROM_EMAIL")
    from_name = os.getenv("MAILERSEND_FROM_NAME", "ExamMate Team")

    if not api_key or not from_email:
        raise Exception("MailerSend configuration missing in environment variables.")

    url = "https://api.mailersend.com/v1/email"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": application/json"
    }
    
    payload = {
        "from": {
            "email": from_email,
            "name": from_name
        },
        "to": [
            {
                "email": to_email
            }
        ],
        "subject": subject,
        "html": html
    }

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code not in [200, 201, 202]:
        raise Exception(f"MailerSend API error: Status {response.status_code} - {response.text}")

# ==========================================
# ১. HTML পেজের রুট (Routes - লগইন প্রোটেকশন সহ)
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

@app.route('/student_dashboard.html')
def student_dashboard():
    if 'user' not in session or session['user']['role'] != 'Student':
        return redirect(url_for('signin_page'))
    return render_template('student_dashboard.html')

@app.route('/teacher_dashboard.html')
def teacher_dashboard():
    if 'user' not in session or session['user']['role'] != 'Teacher':
        return redirect(url_for('signin_page'))
    return render_template('teacher_dashboard.html')

@app.route('/student_exam.html')
def student_exam():
    if 'user' not in session or session['user']['role'] != 'Student':
        return redirect(url_for('signin_page'))
    return render_template('student_exam.html')

@app.route('/student_profile.html')
def student_profile():
    if 'user' not in session or session['user']['role'] != 'Student':
        return redirect(url_for('signin_page'))
    return render_template('student_profile.html')

@app.route('/create_exam.html')
def create_exam():
    if 'user' not in session or session['user']['role'] != 'Teacher':
        return redirect(url_for('signin_page'))
    return render_template('create_exam.html')

@app.route('/teacher_analytics.html')
def teacher_analytics():
    if 'user' not in session or session['user']['role'] != 'Teacher':
        return redirect(url_for('signin_page'))
    return render_template('teacher_analytics.html')

@app.route('/notification.html')
def notification():
    if 'user' not in session:
        return redirect(url_for('signin_page'))
    return render_template('notification.html')

@app.route('/admin.html')
def admin_page():
    return render_template('admin.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('signin_page'))

# ==========================================
# ২. API রুটস (Sign Up - Send OTP)
# ==========================================
@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    data = request.get_json()
    email = data.get('email')
    name = data.get('name')
    password = data.get('password')
    role = data.get('role')

    if not email or not password:
        return jsonify({"success": False, "error": "Email and Password are required!"}), 400

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY, name TEXT NOT NULL, password TEXT NOT NULL, category TEXT NOT NULL, gender TEXT
            )
        ''')
        cursor.execute("SELECT email FROM users WHERE email = ?", (email,))
        existing_user = cursor.fetchone()
        conn.close()

        if existing_user:
            return jsonify({"success": False, "error": "This email is already registered! Please go to Sign In page."}), 400

    except Exception as e:
        print(f"\n❌ [DB ERROR IN SEND_OTP CHECK]: {e}\n")
        return jsonify({"success": False, "error": "Database error while checking email."}), 500

    hashed_password = generate_password_hash(password)
    otp_code = str(random.randint(1000, 9999))
    session['temp_user'] = {'name': name, 'email': email, 'password': hashed_password, 'role': role, 'otp': otp_code}

    subject = "ExamMate OTP Verification"
    html_content = f"""
    <h2>ExamMate Verification Code</h2>
    <p>Your OTP is:</p>
    <h1>{otp_code}</h1>
    <p>This OTP expires after verification.</p>
    <p>If you didn't request this email, simply ignore it.</p>
    """

    try:
        send_email_mailersend(email, subject, html_content)
        return jsonify({"success": True, "message": "OTP sent successfully!"})
    except Exception as e:
        print(f"\n❌ [MAILERSEND ERROR IN SEND_OTP]: {e}\n")
        return jsonify({"success": False, "error": "Failed to send OTP. Check terminal for details."}), 500

# ==========================================
# ৩. API রুটস (Sign Up - Verify OTP)
# ==========================================
@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    data = request.get_json()
    user_otp = data.get('otp')

    if 'temp_user' not in session:
        return jsonify({"success": False, "error": "Session expired. Please fill form again."}), 400

    stored_data = session['temp_user']

    if user_otp == stored_data['otp']:
        try:
            conn = sqlite3.connect('ExamMate.db')
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO users (email, name, password, category) VALUES (?, ?, ?, ?)",
                (stored_data['email'], stored_data['name'], stored_data['password'], stored_data['role'])
            )
            conn.commit()
            conn.close()
            
            session['user'] = {'email': stored_data['email'], 'name': stored_data['name'], 'role': stored_data['role']}
            session.pop('temp_user', None)
            return jsonify({"success": True})
        except Exception as e:
            print(f"\n❌ [DB ERROR IN VERIFY_OTP]: {e}\n")
            return jsonify({"success": False, "error": "Database error occurred."}), 500
    else:
        return jsonify({"success": False, "error": "Incorrect OTP! Please check your inbox and try again."}), 400

# ==========================================
# ৪. API রুটস (Sign In)
# ==========================================
@app.route('/api/signin', methods=['POST'])
def signin():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    
    try:
        conn = sqlite3.connect('ExamMate.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name, password, category FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user["password"], password):
            session['user'] = {'email': email, 'name': user["name"], 'role': user["category"]}
            return jsonify({"success": True, "name": user["name"], "role": user["category"]})
        else:
            return jsonify({"success": False, "error": "Invalid email or password. Try again!"}), 401
    except Exception as e:
        print(f"\n❌ [DB ERROR IN SIGNIN]: {e}\n")
        return jsonify({"success": False, "error": "Database connection error."}), 500

# ==========================================
# ৫. API রুটস (Forgot Password - Send OTP)
# ==========================================
@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json()
    email = data.get('email')

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.close()

        if not user:
            return jsonify({"success": False, "error": "This email is not registered with us!"}), 404

        otp_code = str(random.randint(1000, 9999))
        session['reset_user'] = {'email': email, 'otp': otp_code}

        subject = "ExamMate Password Reset OTP"
        html_content = f"""
        <h2>Password Reset</h2>
        <p>Your OTP is:</p>
        <h1>{otp_code}</h1>
        <p>Use this OTP to reset your password.</p>
        """

        send_email_mailersend(email, subject, html_content)
        return jsonify({"success": True})
    except Exception as e:
        print(f"\n❌ [MAILERSEND ERROR IN FORGOT_PASSWORD]: {e}\n")
        return jsonify({"success": False, "error": "Error sending reset OTP."}), 500

# ==========================================
# ৬. API রুটস (Forgot Password - Reset)
# ==========================================
@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json()
    user_otp = data.get('otp')
    new_password = data.get('new_password')

    if 'reset_user' not in session:
        return jsonify({"success": False, "error": "Session timed out. Please try again."}), 400

    stored_data = session['reset_user']

    if user_otp == stored_data['otp']:
        try:
            secure_password = generate_password_hash(new_password)
            conn = sqlite3.connect('ExamMate.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET password = ? WHERE email = ?", (secure_password, stored_data['email']))
            conn.commit()
            conn.close()
            session.pop('reset_user', None)
            return jsonify({"success": True})
        except Exception as e:
            print(f"\n❌ [DB ERROR IN RESET_PASSWORD]: {e}\n")
            return jsonify({"success": False, "error": "Database error."}), 500
    else:
        return jsonify({"success": False, "error": "Incorrect OTP! Try again."}), 400

# ==========================================
# ৭. স্টুডেন্ট ড্যাশবোর্ড - প্রগ্রেস ও রেজাল্ট আনা
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
# ৮. এক্সাম সার্চ চেক করা
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
# ৯. এক্সাম পেজ - পরীক্ষার প্রশ্ন ও সময় আনা
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
# ১০. এক্সাম পেজ - পরীক্ষার রেজাল্ট সেভ করা
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
# ১১. প্রোফাইল পেজ - স্টুডেন্টের এক্সাম হিস্ট্রি আনা
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
# ১২. Create Exam (Teacher) - প্রশ্ন ও পরীক্ষা সেভ করা
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
# ১৩. Teacher Dashboard - ম্যাট্রিক্স ও এক্সাম লিস্ট
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
# ১৪. Teacher Profile - অ্যানালাইসিস উইন্ডো
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
# ১৫. Notifications - নোটিফিকেশন রিকভার করা
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
# ১৬. Send Notification (Admin Only)
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

if __name__ == '__main__':
    app.run(debug=True)
@app.route('/notification.html')
def notification():
    if 'user' not in session:
        return redirect(url_for('signin_page'))
    return render_template('notification.html')

@app.route('/admin.html')
def admin_page():
    return render_template('admin.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('signin_page'))

# ==========================================
# ২. API রুটস (Sign Up - Send OTP)
# ==========================================
@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    data = request.get_json()
    email = data.get('email')
    name = data.get('name')
    password = data.get('password')
    role = data.get('role')

    if not email or not password:
        return jsonify({"success": False, "error": "Email and Password are required!"}), 400

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY, name TEXT NOT NULL, password TEXT NOT NULL, category TEXT NOT NULL, gender TEXT
            )
        ''')
        cursor.execute("SELECT email FROM users WHERE email = ?", (email,))
        existing_user = cursor.fetchone()
        conn.close()

        if existing_user:
            return jsonify({"success": False, "error": "This email is already registered! Please go to Sign In page."}), 400

    except Exception as e:
        print(f"\n❌ [DB ERROR IN SEND_OTP CHECK]: {e}\n")
        return jsonify({"success": False, "error": "Database error while checking email."}), 500

    hashed_password = generate_password_hash(password)
    otp_code = str(random.randint(1000, 9999))
    session['temp_user'] = {'name': name, 'email': email, 'password': hashed_password, 'role': role, 'otp': otp_code}

    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 465)) 
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("FROM_EMAIL", smtp_username)
    from_name = os.getenv("FROM_NAME", "ExamMate Team")

    msg = MIMEText(f"<html><body><h2>ExamMate Verification Code</h2><p>Your OTP is:</p><h1>{otp_code}</h1></body></html>", "html")
    msg["From"] = f"{from_name} <{from_email}>"
    msg["Subject"] = "ExamMate OTP Verification"
    msg["To"] = email

    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(smtp_username, smtp_password)
            server.sendmail(from_email, email, msg.as_string())
        return jsonify({"success": True, "message": "OTP sent successfully!"})
    except Exception as e:
        print(f"\n❌ [SMTP ERROR IN SEND_OTP]: {e}\n")
        return jsonify({"success": False, "error": "Failed to send OTP. Check terminal for details."}), 500

# ==========================================
# ৩. API রুটস (Sign Up - Verify OTP)
# ==========================================
@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    data = request.get_json()
    user_otp = data.get('otp')

    if 'temp_user' not in session:
        return jsonify({"success": False, "error": "Session expired. Please fill form again."}), 400

    stored_data = session['temp_user']

    if user_otp == stored_data['otp']:
        try:
            conn = sqlite3.connect('ExamMate.db')
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO users (email, name, password, category) VALUES (?, ?, ?, ?)",
                (stored_data['email'], stored_data['name'], stored_data['password'], stored_data['role'])
            )
            conn.commit()
            conn.close()
            
            session['user'] = {'email': stored_data['email'], 'name': stored_data['name'], 'role': stored_data['role']}
            session.pop('temp_user', None)
            return jsonify({"success": True})
        except Exception as e:
            print(f"\n❌ [DB ERROR IN VERIFY_OTP]: {e}\n")
            return jsonify({"success": False, "error": "Database error occurred."}), 500
    else:
        return jsonify({"success": False, "error": "Incorrect OTP! Please check your inbox and try again."}), 400

# ==========================================
# ৪. API রুটস (Sign In)
# ==========================================
@app.route('/api/signin', methods=['POST'])
def signin():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    
    try:
        conn = sqlite3.connect('ExamMate.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name, password, category FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user["password"], password):
            session['user'] = {'email': email, 'name': user["name"], 'role': user["category"]}
            return jsonify({"success": True, "name": user["name"], "role": user["category"]})
        else:
            return jsonify({"success": False, "error": "Invalid email or password. Try again!"}), 401
    except Exception as e:
        print(f"\n❌ [DB ERROR IN SIGNIN]: {e}\n")
        return jsonify({"success": False, "error": "Database connection error."}), 500

# ==========================================
# ৫. API রুটস (Forgot Password - Send OTP)
# ==========================================
@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json()
    email = data.get('email')

    try:
        conn = sqlite3.connect('ExamMate.db')
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.close()

        if not user:
            return jsonify({"success": False, "error": "This email is not registered with us!"}), 404

        otp_code = str(random.randint(1000, 9999))
        session['reset_user'] = {'email': email, 'otp': otp_code}

        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 465)) 
        smtp_username = os.getenv("SMTP_USERNAME")
        smtp_password = os.getenv("SMTP_PASSWORD")
        from_email = os.getenv("FROM_EMAIL", smtp_username)
        
        msg = MIMEText(f"<html><body><h2>Reset Password OTP</h2><p>Your reset code is:</p><h1>{otp_code}</h1></body></html>", "html")
        msg["From"] = f"ExamMate <{from_email}>"
        msg["Subject"] = "ExamMate Password Reset OTP"
        msg["To"] = email

        with smtplib.SMTP_SSL(smtp_server,smtp_port,timeout=1) as server:
            server.login(smtp_username, smtp_password)
            server.sendmail(from_email, email, msg.as_string())
        return jsonify({"success": True})
    except Exception as e:
        print(f"\n❌ [SMTP ERROR IN FORGOT_PASSWORD]: {e}\n")
        return jsonify({"success": False, "error": "Error sending reset OTP."}), 500

# ==========================================
# ৬. API রুটস (Forgot Password - Reset)
# ==========================================
@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json()
    user_otp = data.get('otp')
    new_password = data.get('new_password')

    if 'reset_user' not in session:
        return jsonify({"success": False, "error": "Session timed out. Please try again."}), 400

    stored_data = session['reset_user']

    if user_otp == stored_data['otp']:
        try:
            secure_password = generate_password_hash(new_password)
            conn = sqlite3.connect('ExamMate.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET password = ? WHERE email = ?", (secure_password, stored_data['email']))
            conn.commit()
            conn.close()
            session.pop('reset_user', None)
            return jsonify({"success": True})
        except Exception as e:
            print(f"\n❌ [DB ERROR IN RESET_PASSWORD]: {e}\n")
            return jsonify({"success": False, "error": "Database error."}), 500
    else:
        return jsonify({"success": False, "error": "Incorrect OTP! Try again."}), 400

# ==========================================
# ৭. স্টুডেন্ট ড্যাশবোর্ড - প্রগ্রেস ও রেজাল্ট আনা
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
# ৮. এক্সাম সার্চ চেক করা
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
# ৯. এক্সাম পেজ - পরীক্ষার প্রশ্ন ও সময় আনা
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
# ১০. এক্সাম পেজ - পরীক্ষার রেজাল্ট সেভ করা
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
# ১১. প্রোফাইল পেজ - স্টুডেন্টের এক্সাম হিস্ট্রি আনা
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
# ১২. Create Exam (Teacher) - প্রশ্ন ও পরীক্ষা সেভ করা
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
# ১৩. Teacher Dashboard - ম্যাট্রিক্স ও এক্সাম লিস্ট
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
# ১৪. Teacher Profile - অ্যানালাইসিস উইন্ডো
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
# ১৫. Notifications - নোটিফিকেশন রিকভার করা
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
# ১৬. Send Notification (Admin Only)
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

if __name__ == '__main__':
    app.run(debug=True)
