from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "change-this-secret-key"
DATABASE = "ecoconnect.db"
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'user',
        points INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        location TEXT NOT NULL,
        latitude TEXT,
        longitude TEXT,
        image TEXT,
        status TEXT DEFAULT 'Pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        points INTEGER DEFAULT 10,
        date TEXT,
        location TEXT
    );

    CREATE TABLE IF NOT EXISTS participations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        activity_id INTEGER NOT NULL,
        UNIQUE(user_id, activity_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(activity_id) REFERENCES activities(id)
    );
    """)

    # Create demo admin account
    admin = conn.execute("SELECT id FROM users WHERE email=?", ("admin@ecoconnect.com",)).fetchone()
    if not admin:
        conn.execute(
            "INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
            ("EcoConnect Admin", "admin@ecoconnect.com",
             generate_password_hash("admin786787"), "admin")
        )

    # Create sample activities
    count = conn.execute("SELECT COUNT(*) AS c FROM activities").fetchone()["c"]
    if count == 0:
        conn.executemany(
            "INSERT INTO activities(title,description,points,date,location) VALUES(?,?,?,?,?)",
            [
                ("Tree Plantation Drive", "Plant and care for native trees in the community.", 50, "2026-09-05", "College Campus"),
                ("Cleanliness Drive", "Help clean a local public area and segregate waste.", 30, "2026-09-10", "Kamshet"),
                ("Recycling Campaign", "Collect recyclable materials and submit them for recycling.", 25, "2026-09-15", "Community Hall"),
                ("Water Conservation Awareness", "Participate in an awareness activity about saving water.", 20, "2026-09-20", "College Campus")
            ]
        )
    conn.commit()
    conn.close()

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def current_user():
    if "user_id" not in session:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()
    return user

@app.context_processor
def inject_user():
    return {"current_user": current_user()}

@app.route("/")
def home():
    conn = get_db()
    activities = conn.execute("SELECT * FROM activities ORDER BY id DESC LIMIT 4").fetchall()
    leaderboard = conn.execute(
        "SELECT name, points FROM users WHERE role='user' ORDER BY points DESC, name LIMIT 5"
    ).fetchall()
    issue_count = conn.execute("SELECT COUNT(*) AS c FROM issues").fetchone()["c"]
    resolved_count = conn.execute(
        "SELECT COUNT(*) AS c FROM issues WHERE status='Resolved'"
    ).fetchone()["c"]
    conn.close()
    return render_template("index.html", activities=activities,
                           leaderboard=leaderboard,
                           issue_count=issue_count,
                           resolved_count=resolved_count)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if len(password) < 6:
            flash("Password must contain at least 6 characters.", "danger")
            return redirect(url_for("register"))

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users(name,email,password) VALUES(?,?,?)",
                (name, email, generate_password_hash(password))
            )
            conn.commit()
            flash("Registration successful. Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email is already registered.", "danger")
        finally:
            conn.close()
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            if user["role"] == "admin":
                return redirect(url_for("admin"))
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/dashboard")
def dashboard():
    if not current_user():
        return redirect(url_for("login"))
    conn = get_db()
    issues = conn.execute(
        "SELECT * FROM issues WHERE user_id=? ORDER BY id DESC",
        (session["user_id"],)
    ).fetchall()
    activities = conn.execute("SELECT * FROM activities ORDER BY date").fetchall()
    joined = {
        row["activity_id"] for row in conn.execute(
            "SELECT activity_id FROM participations WHERE user_id=?",
            (session["user_id"],)
        ).fetchall()
    }
    conn.close()
    return render_template("dashboard.html", issues=issues,
                           activities=activities, joined=joined)

@app.route("/report", methods=["GET", "POST"])
def report():
    if not current_user():
        return redirect(url_for("login"))

    if request.method == "POST":
        title = request.form["title"].strip()
        description = request.form["description"].strip()
        location = request.form["location"].strip()
        latitude = request.form.get("latitude", "").strip()
        longitude = request.form.get("longitude", "").strip()

        image_name = None
        image = request.files.get("image")
        if image and image.filename:
            if not allowed_file(image.filename):
                flash("Only image files are allowed.", "danger")
                return redirect(url_for("report"))
            image_name = secure_filename(image.filename)
            image.save(os.path.join(app.config["UPLOAD_FOLDER"], image_name))

        conn = get_db()
        conn.execute("""
            INSERT INTO issues(user_id,title,description,location,latitude,longitude,image)
            VALUES(?,?,?,?,?,?,?)
        """, (session["user_id"], title, description, location,
              latitude, longitude, image_name))
        conn.commit()
        conn.close()
        flash("Environmental issue submitted successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("report.html")

@app.route("/activity/<int:activity_id>/join", methods=["POST"])
def join_activity(activity_id):
    if not current_user():
        return redirect(url_for("login"))

    conn = get_db()
    activity = conn.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
    if activity:
        try:
            conn.execute(
                "INSERT INTO participations(user_id,activity_id) VALUES(?,?)",
                (session["user_id"], activity_id)
            )
            conn.execute(
                "UPDATE users SET points=points+? WHERE id=?",
                (activity["points"], session["user_id"])
            )
            conn.commit()
            flash(f"You joined the activity and earned {activity['points']} Green Points!", "success")
        except sqlite3.IntegrityError:
            flash("You have already joined this activity.", "info")
    conn.close()
    return redirect(url_for("dashboard"))

@app.route("/leaderboard")
def leaderboard():
    conn = get_db()
    users = conn.execute(
        "SELECT name, points FROM users WHERE role='user' ORDER BY points DESC, name"
    ).fetchall()
    conn.close()
    return render_template("leaderboard.html", users=users)

@app.route("/admin", methods=["GET"])
def admin():
    user = current_user()
    if not user or user["role"] != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("login"))

    conn = get_db()
    issues = conn.execute("""
        SELECT issues.*, users.name AS reporter
        FROM issues JOIN users ON issues.user_id=users.id
        ORDER BY issues.id DESC
    """).fetchall()
    users = conn.execute(
        "SELECT id,name,email,points,created_at FROM users WHERE role='user' ORDER BY id DESC"
    ).fetchall()
    activities = conn.execute("SELECT * FROM activities ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin.html", issues=issues, users=users, activities=activities)

@app.route("/admin/issue/<int:issue_id>/status", methods=["POST"])
def update_status(issue_id):
    user = current_user()
    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    status = request.form["status"]
    if status not in {"Pending", "In Progress", "Resolved"}:
        status = "Pending"

    conn = get_db()
    conn.execute("UPDATE issues SET status=? WHERE id=?", (status, issue_id))
    conn.commit()
    conn.close()
    flash("Issue status updated.", "success")
    return redirect(url_for("admin"))

@app.route("/admin/activity/add", methods=["POST"])
def add_activity():
    user = current_user()
    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    title = request.form["title"].strip()
    description = request.form["description"].strip()
    points = int(request.form["points"])
    date = request.form["date"]
    location = request.form["location"].strip()

    conn = get_db()
    conn.execute(
        "INSERT INTO activities(title,description,points,date,location) VALUES(?,?,?,?,?)",
        (title, description, points, date, location)
    )
    conn.commit()
    conn.close()
    flash("Activity added.", "success")
    return redirect(url_for("admin"))

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
