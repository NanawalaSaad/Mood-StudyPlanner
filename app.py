from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3, os, time, datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import cv2
import numpy as np
import face_recognition
import base64
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI

app = Flask(__name__)
app.secret_key = "secret123"

# ===== DB INIT (Added Gamification) =====
def init_db():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users(username TEXT, password TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS study(username TEXT, subject TEXT, hours REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS goals(username TEXT, goal TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS attendance(username TEXT, date TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS notes(username TEXT, note TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS user_stats(username TEXT UNIQUE, xp INTEGER DEFAULT 0, streak INTEGER DEFAULT 0)")
    conn.commit()
    conn.close()

init_db()

# XP Helper Function
def add_xp(username, points):
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO user_stats (username, xp, streak) VALUES (?, 0, 0)", (username,))
    c.execute("UPDATE user_stats SET xp = xp + ? WHERE username = ?", (points, username))
    conn.commit()
    conn.close()

# ===== LOGIN & SIGNUP =====
@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username")
        p = request.form.get("password")
        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute("SELECT password FROM users WHERE username=?", (u,))
        data = c.fetchone()
        conn.close()
        if data and check_password_hash(data[0], p):
            session["user"] = u
            return redirect("/dashboard")
        else:
            return render_template("login.html", error="Invalid Credentials!")
    return render_template("login.html")

@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        u = request.form.get("username")
        p = request.form.get("password")
        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=?", (u,))
        if c.fetchone():
            conn.close()
            return "User already exists!"
        hashed = generate_password_hash(p)
        c.execute("INSERT INTO users VALUES (?,?)", (u,hashed))
        c.execute("INSERT INTO user_stats (username, xp, streak) VALUES (?, 0, 0)", (u,))
        conn.commit()
        conn.close()
        return redirect("/")
    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ===== DASHBOARD (Added XP & Level) =====
@app.route("/dashboard")
def dashboard():
    if "user" not in session: return redirect("/")
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM attendance WHERE username=?", (session["user"],))
    att_count = c.fetchone()[0]
    
    c.execute("SELECT xp FROM user_stats WHERE username=?", (session["user"],))
    stats = c.fetchone()
    xp = stats[0] if stats else 0
    level = (xp // 100) + 1  # Level up every 100 XP
    conn.close()
    
    return render_template("dashboard.html", username=session["user"], att_count=att_count, xp=xp, level=level)

# ===== FACE LOGIC =====
@app.route("/register_face", methods=["POST"])
def register_face():
    if "user" not in session: return jsonify({"status":"unauthorized"})
    data = request.json
    if not data or 'image' not in data: return jsonify({"status":"camera error"})
    try:
        img_data = base64.b64decode(data['image'].split(',')[1])
        nparr = np.frombuffer(img_data, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        enc = face_recognition.face_encodings(rgb)
        if len(enc)==0: return jsonify({"status":"no face detected"})
        if not os.path.exists("faces"): os.makedirs("faces")
        np.save(f"faces/{session['user']}.npy", enc[0])
        add_xp(session["user"], 50) # Bonus 50 XP for setting up Face ID!
        return jsonify({"status":"face saved"})
    except Exception as e: return jsonify({"status": str(e)})

@app.route("/face_login", methods=["POST"])
def face_login():
    data = request.json
    if not data or 'image' not in data: return jsonify({"status":"camera error"})
    try:
        img_data = base64.b64decode(data['image'].split(',')[1])
        nparr = np.frombuffer(img_data, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        enc = face_recognition.face_encodings(rgb)
        if len(enc)==0: return jsonify({"status":"no face"})
        if not os.path.exists("faces"): return jsonify({"status":"no data"})
        for f in os.listdir("faces"):
            try:
                saved = np.load(f"faces/{f}", allow_pickle=True)
                match = face_recognition.compare_faces([saved], enc[0], tolerance=0.48)[0]
                if match:
                    session["user"] = f.replace(".npy","")
                    return jsonify({"status":"success"})
            except: continue
        return jsonify({"status":"fail"})
    except Exception as e: return jsonify({"status": str(e)})

# ===== ATTENDANCE =====
@app.route("/mark_attendance", methods=["POST"])
def mark_attendance():
    if "user" not in session: return redirect("/")
    today = str(datetime.date.today())
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("SELECT * FROM attendance WHERE username=? AND date=?", (session["user"], today))
    if not c.fetchone():
        c.execute("INSERT INTO attendance VALUES (?,?)",(session["user"], today))
        add_xp(session["user"], 10) # 10 XP daily
        conn.commit()
    conn.close()
    return jsonify({"status": "marked"})

# ===== PLANNER =====
@app.route("/planner", methods=["GET","POST"])
def planner():
    if "user" not in session: return redirect("/")
    plan=[]; chart_filename=None; chart_time=None
    if request.method=="POST":
        try: hrs = float(request.form.get("hours", 0))
        except ValueError: hrs = 0.0
        if hrs <= 0: return render_template("planner.html", plan=["⚠️ Enter valid hours (> 0)"])
        raw_subs = request.form.getlist("subject[]")
        raw_diffs = request.form.getlist("difficulty[]")
        valid_subs, valid_diffs = [], []
        for s, d in zip(raw_subs, raw_diffs):
            s = s.strip()
            if s:  
                valid_subs.append(s)
                valid_diffs.append(d)
        if not valid_subs: return render_template("planner.html", plan=["⚠️ Enter at least one subject"])
        w={"H":3,"M":2,"E":1}
        total = sum(w.get(d, 2) for d in valid_diffs) if valid_diffs else 1
        names, times = [], []
        conn=sqlite3.connect("database.db")
        c=conn.cursor()
        for s, d in zip(valid_subs, valid_diffs):
            t=round((w.get(d, 2)/total)*hrs,1)
            plan.append(f"{s} → {t} hrs")
            names.append(s); times.append(t)
            if t > 0: c.execute("INSERT INTO study VALUES (?,?,?)",(session["user"],s,t))
        conn.commit(); conn.close()
        if names:
            add_xp(session["user"], 20) # 20 XP for planning
            static_dir = os.path.join(app.root_path, 'static')
            if not os.path.exists(static_dir): os.makedirs(static_dir)
            chart_filename = f"chart_{session['user']}.png"
            filepath = os.path.join(static_dir, chart_filename)
            plt.figure(figsize=(4,4), facecolor='none')
            plt.pie(times, labels=names, autopct='%1.1f%%', textprops={'color':"white"})
            plt.savefig(filepath, transparent=True)
            plt.close()
            chart_time = int(time.time())
    return render_template("planner.html", plan=plan, chart=chart_filename, chart_time=chart_time)
# ===== PROGRESS & ANALYTICS =====
@app.route("/progress")
def progress():
    if "user" not in session: return redirect("/")
    conn=sqlite3.connect("database.db")
    c=conn.cursor()
    c.execute("SELECT subject,SUM(hours) FROM study WHERE username=? GROUP BY subject ORDER BY SUM(hours) DESC",(session["user"],))
    data=c.fetchall()
    conn.close()
    subjects = [d[0] for d in data]
    hours = [d[1] for d in data]
    # Data Science Analytics: Find Favorite Subject
    fav_subject = subjects[0] if subjects else "None"
    return render_template("progress.html", subjects=subjects, hours=hours, fav_subject=fav_subject)

# ===== GOALS & AI BREAKDOWN =====
@app.route("/goals",methods=["GET","POST"])
def goals():
    if "user" not in session: return redirect("/")
    conn=sqlite3.connect("database.db")
    c=conn.cursor()
    if request.method=="POST":
        g=request.form.get("goal")
        if g and g.strip():
            c.execute("INSERT INTO goals VALUES (?,?)",(session["user"],g.strip()))
            conn.commit()
    c.execute("SELECT goal FROM goals WHERE username=?",(session["user"],))
    data=c.fetchall()
    conn.close()
    return render_template("goals.html",data=data)

@app.route("/api/ai_goal", methods=["POST"])
def ai_goal():
    goal = request.json.get("goal")
    api_key = os.environ.get("OPENAI_API_KEY") 
    if not api_key or api_key == "YOUR_API_KEY":
        return jsonify({"plan": f"🤖 AI Strategy for '{goal}':\n1. Learn Basics\n2. Practice Coding\n3. Build Project\n(Add API key for detailed steps)"})
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"Break down this goal into 3 steps: {goal}"}]
        )
        return jsonify({"plan": response.choices[0].message.content})
    except Exception as e: return jsonify({"plan": f"Error: {str(e)}"})

# ===== NOTES & AI CHAT =====
@app.route("/notes", methods=["GET", "POST"])
def notes():
    if "user" not in session: return redirect("/")
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    if request.method == "POST":
        note = request.form.get("note")
        if note and note.strip():
            c.execute("INSERT INTO notes VALUES (?,?)", (session["user"], note.strip()))
            conn.commit()
    c.execute("SELECT note FROM notes WHERE username=?", (session["user"],))
    data = c.fetchall()
    conn.close()
    return render_template("notes.html", data=data)

@app.route("/api/chat", methods=["POST"])
def api_chat():
    user_msg = request.json.get("message")
    api_key = os.environ.get("OPENAI_API_KEY") 
    if not api_key or api_key == "YOUR_API_KEY":
        return jsonify({"reply": f"🤖 API Key missing! You said: '{user_msg}'"})
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "You are a helpful tutor."}, {"role": "user", "content": user_msg}]
        )
        return jsonify({"reply": response.choices[0].message.content})
    except Exception as e: return jsonify({"reply": f"Error: {str(e)}"})

# ===== DEEP FOCUS MODE =====
@app.route("/focus", methods=["GET", "POST"])
def focus():
    if "user" not in session: return redirect("/")
    if request.method == "POST":
        add_xp(session["user"], 30) # XP for completing focus
        return jsonify({"status": "success"})
    return render_template("focus.html")

# ===== EXPORT PDF REPORT =====
@app.route("/report")
def report():
    if "user" not in session: return redirect("/")
    conn=sqlite3.connect("database.db")
    c=conn.cursor()
    c.execute("SELECT subject, SUM(hours) FROM study WHERE username=? GROUP BY subject",(session["user"],))
    study_data = c.fetchall()
    c.execute("SELECT COUNT(*) FROM attendance WHERE username=?", (session["user"],))
    att_count = c.fetchone()[0]
    conn.close()
    return render_template("report.html", username=session["user"], study_data=study_data, att_count=att_count)

# ===== LEADERBOARD =====
@app.route("/leaderboard")
def leaderboard():
    if "user" not in session: return redirect("/")
    conn=sqlite3.connect("database.db")
    c=conn.cursor()
    # Now ranked by XP instead of hours!
    c.execute("SELECT username, xp FROM user_stats ORDER BY xp DESC")
    data=c.fetchall()
    conn.close()
    return render_template("leaderboard.html",data=data)

# ===== PREDICT =====
@app.route("/predict")
def predict():
    if "user" not in session: return redirect("/")
    conn=sqlite3.connect("database.db")
    c=conn.cursor()
    c.execute("SELECT COUNT(*) FROM attendance WHERE username=?",(session["user"],))
    att=c.fetchone()[0]
    conn.close()
    total_days = 30
    percent = round((att / total_days) * 100, 2) if att else 0
    if percent > 100: percent = 100.0
    marks = int(percent * 0.8)
    return render_template("predict.html",percent=percent,marks=marks)

if __name__ == "__main__":
    app.run(debug=True)