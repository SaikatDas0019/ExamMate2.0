# ExamMate v2.0 🚀

ExamMate is a secure, feature-rich online examination platform designed to provide a seamless assessment experience for both teachers and students. Version 2.0 introduces advanced subjective evaluation, robust anti-cheat mechanisms, gamified student progress tracking, and a dedicated Android application.

## ✨ Key Features

### 👨‍🏫 For Teachers
* **Comprehensive Question Types**: Create exams with both Objective (MCQ) and Subjective/Long-form questions.
* **Custom Marking System**: Assign specific `max_marks` to individual questions.
* **Manual Evaluation Dashboard**: Easily review student uploads, evaluate subjective answers manually, and assign final grades.
* **Exam Analytics**: Track student performance and overall class statistics.

### 🎓 For Students
* **Smart Image Cropper**: Capture, crop, and upload handwritten answer sheets seamlessly using the integrated Cropper.js and ImgBB API.
* **Gamification & Leaderboard**: Earn points for taking exams, unlock dynamic badges (from *Novice* to *Platinum Legend*), and compete on the global leaderboard.
* **Social Hub**: Save favorite exams and view liked exams in personalized dashboard panels.
* **Smart Deep Linking**: Open shared exam links directly in the ExamMate native Android app using Intent URIs for a distraction-free experience.

### 🛡️ Security & Infrastructure
* **Advanced Anti-Cheat System**: Strict monitoring of tab-switching and window resizing.
* **Smart Upload Overlay**: Automatically suspends anti-cheat warnings during legitimate camera usage or file uploads to prevent false positives.
* **Seamless Distribution**: Direct, fast APK downloads securely hosted via GitHub Releases.

## 🛠️ Tech Stack

* **Backend**: Python, Flask
* **Database**: PostgreSQL (Production) / SQLite (Local)
* **Frontend**: HTML5, CSS3, Vanilla JavaScript
* **APIs & Tools**: ImgBB API (Image Hosting), Cropper.js (Image processing)
* **App Framework**: WebIntoApp (Android WebView with Native Intents)
* **Hosting**: Render (Web Platform), GitHub Releases (APK Distribution)

## 🚀 Live Demo & App Download

* **Web Platform**: https://exammate-sscn.onrender.com
* **Android App**: Download the latest optimized APK from our [GitHub Releases](https://github.com/SaikatDas0019/ExamMate2.0/releases) section.

## 💻 Local Setup & Installation

If you want to run this project locally, follow these steps:

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/SaikatDas0019/ExamMate2.0.git](https://github.com/SaikatDas0019/ExamMate2.0.git)
   cd ExamMate2.0
   
2. **Install the required dependencies:**
   Make sure you have Python installed, then run:
   ```bash
   pip install -r requirements.txt

4. **Run the Flask application:**
   ```bash
   python app.py

6. Access the web app: Open `http://127.0.0.1:5000` in your web browser.

## 🌐 Deployment Notes (Render)
When deploying new updates to the Render live server, especially after modifying the database schema (like adding `max_marks` or subjective answer tables), a manual database synchronization is required:
1. Push the latest code to the `main` branch to trigger Render deployment.
2. Once deployed, visit `https://<your-render-url>/run-fix-db` in your browser. This endpoint safely updates the live database tables without losing existing data.

### 👨‍💻 Developed By
***Saikat Das***
* **GitHub:** @SaikatDas0019
* **App Package:** `com.saikat.exammate`
*If you find this project helpful, don't forget to give it a ⭐ on GitHub!*
