import os
import secrets
import datetime
from io import BytesIO

from flask import Flask, request, render_template_string, abort, send_file
from flask_sqlalchemy import SQLAlchemy

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

# =========================
# APP CONFIG
# =========================
app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB_PATH = os.path.join(BASE_DIR, "grades.db")

# Use DATABASE_URL (cloud) if available, else SQLite (local)
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url or f"sqlite:///{LOCAL_DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# =========================
# SETTINGS
# =========================
SCHOOL_NAME = "New Challenge Institute"  # <-- change here if needed

MAX_POINTS = {
    "Participation": 30,
    "Homework": 10,
    "Oral Test": 40,
    "Attendance": 10,
    "Questions": 10,
}

# =========================
# DATABASE MODELS
# =========================
class Link(db.Model):
    __tablename__ = "links"
    token = db.Column(db.String(120), primary_key=True)
    teacher_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.String(40), nullable=False)

class Grade(db.Model):
    __tablename__ = "grades"
    id = db.Column(db.Integer, primary_key=True)

    token = db.Column(db.String(120), nullable=False)

    student_name = db.Column(db.String(120), nullable=False)
    student_id = db.Column(db.String(50), nullable=False)
    level = db.Column(db.String(50), nullable=False)

    participation = db.Column(db.Float, nullable=False)
    homework = db.Column(db.Float, nullable=False)
    oral_test = db.Column(db.Float, nullable=False)
    attendance = db.Column(db.Float, nullable=False)
    questions = db.Column(db.Float, nullable=False)

    total_points = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.String(40), nullable=False)

# =========================
# HTML TEMPLATES
# =========================
HOME = """
<h2>Admin – Create Teacher Link</h2>
<form method="post" action="/create-link">
  Teacher name: <input name="teacher_name" required>
  <button type="submit">Create Link</button>
</form>
<p><a href="/report">View Report</a></p>
"""

ENTRY_FORM = """
<h2>Enter Grades</h2>
<p><b>Teacher:</b> {{teacher}}</p>

<form method="post">
  Student name: <input name="student_name" required><br>
  Student ID: <input name="student_id" required><br>
  Level: <input name="level" required><br><br>

  Participation (0–30):
  <input type="number" name="participation" min="0" max="30" step="0.01" required><br>

  Homework (0–10):
  <input type="number" name="homework" min="0" max="10" step="0.01" required><br>

  Oral Test (0–40):
  <input type="number" name="oral_test" min="0" max="40" step="0.01" required><br>

  Attendance (0–10):
  <input type="number" name="attendance" min="0" max="10" step="0.01" required><br>

  Questions (0–10):
  <input type="number" name="questions" min="0" max="10" step="0.01" required><br><br>

  <button type="submit">Save</button>
</form>
"""

SUCCESS = """
<h3>Saved ✅</h3>
<p><b>{{student}}</b> (ID: {{sid}} | Level: {{level}})</p>
<p>Total Points: <b>{{total}}</b> / 100</p>
<a href="">Add another</a>
"""

# =========================
# HELPERS
# =========================
def safe_float(v: str, field: str, min_v: float, max_v: float) -> float:
    try:
        n = float(v)
    except ValueError:
        raise ValueError(f"{field} must be a number.")
    if n < min_v or n > max_v:
        raise ValueError(f"{field} must be between {min_v} and {max_v}.")
    return n

# =========================
# ROUTES
# =========================
@app.get("/")
def index():
    return render_template_string(HOME)

@app.post("/create-link")
def create_link():
    teacher = request.form["teacher_name"].strip()
    if not teacher:
        return "<h3 style='color:red'>Teacher name is required.</h3><p><a href='/'>Back</a></p>"

    token = secrets.token_urlsafe(16)
    now = datetime.datetime.utcnow().isoformat()

    db.session.add(Link(token=token, teacher_name=teacher, created_at=now))
    db.session.commit()

    # IMPORTANT: Use host_url so it works locally and online
    link = f"{request.host_url}entry/{token}"

    return f"""
    <h3>Link created ✅</h3>
    <p>Send this link to the teacher:</p>
    <code>{link}</code>
    <p><a href="/">Back</a></p>
    """

@app.route("/entry/<token>", methods=["GET", "POST"])
def entry(token):
    link = Link.query.get(token)
    if not link:
        abort(404)

    if request.method == "POST":
        try:
            student_name = request.form["student_name"].strip()
            student_id = request.form["student_id"].strip()
            level = request.form["level"].strip()

            if not student_name or not student_id or not level:
                raise ValueError("Student name, Student ID, and Level are required.")

            p = safe_float(request.form["participation"], "Participation", 0, MAX_POINTS["Participation"])
            h = safe_float(request.form["homework"], "Homework", 0, MAX_POINTS["Homework"])
            o = safe_float(request.form["oral_test"], "Oral Test", 0, MAX_POINTS["Oral Test"])
            a = safe_float(request.form["attendance"], "Attendance", 0, MAX_POINTS["Attendance"])
            q = safe_float(request.form["questions"], "Questions", 0, MAX_POINTS["Questions"])

            total = p + h + o + a + q
            now = datetime.datetime.utcnow().isoformat()

            db.session.add(Grade(
                token=token,
                student_name=student_name,
                student_id=student_id,
                level=level,
                participation=p,
                homework=h,
                oral_test=o,
                attendance=a,
                questions=q,
                total_points=total,
                created_at=now
            ))
            db.session.commit()

            return render_template_string(
                SUCCESS,
                student=student_name,
                sid=student_id,
                level=level,
                total=f"{total:.2f}"
            )

        except ValueError as e:
            return f"<h3 style='color:red'>Error: {e}</h3><p><a href=''>Go back</a></p>"

    return render_template_string(ENTRY_FORM, teacher=link.teacher_name)

@app.get("/report")
def report():
    rows = Grade.query.order_by(Grade.id.desc()).all()

    html = "<h2>Grades Report</h2>"
    html += "<p><a href='/'>Back</a></p>"

    if not rows:
        return html + "<p>No grades yet.</p>"

    html += "<table border='1' cellpadding='6'>"
    html += "<tr><th>Record ID</th><th>Student</th><th>Student ID</th><th>Level</th><th>Total</th><th>PDF</th><th>Date</th></tr>"

    for r in rows:
        html += (
            f"<tr>"
            f"<td>{r.id}</td>"
            f"<td>{r.student_name}</td>"
            f"<td>{r.student_id}</td>"
            f"<td>{r.level}</td>"
            f"<td>{r.total_points:.2f}</td>"
            f"<td><a href='/bulletin/{r.id}'>PDF</a></td>"
            f"<td>{r.created_at[:10]}</td>"
            f"</tr>"
        )

    html += "</table>"
    return html

@app.get("/bulletin/<int:grade_id>")
def bulletin(grade_id):
    g = Grade.query.get_or_404(grade_id)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Logo (assets/logo.png)
    logo_path = os.path.join(BASE_DIR, "assets", "logo.png")
    if os.path.exists(logo_path):
        logo = ImageReader(logo_path)
        c.drawImage(logo, 50, height - 110, width=70, height=70, mask="auto")

    # Header
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width / 2, height - 55, "STUDENT REPORT CARD")

    c.setFont("Helvetica", 12)
    c.drawCentredString(width / 2, height - 80, SCHOOL_NAME)

    c.line(50, height - 115, width - 50, height - 115)

    # Student info
    y = height - 150
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Student Information")
    y -= 20

    c.setFont("Helvetica", 11)
    c.drawString(50, y, f"Student Name: {g.student_name}")
    y -= 18
    c.drawString(50, y, f"Student ID: {g.student_id}")
    y -= 18
    c.drawString(50, y, f"Level: {g.level}")
    y -= 18
    c.drawString(50, y, f"Date: {g.created_at[:10]}")
    y -= 30

    # Grades
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Grades (Points System)")
    y -= 20

    c.setFont("Helvetica", 11)
    c.drawString(60, y, f"Participation (0–30): {g.participation}")
    y -= 16
    c.drawString(60, y, f"Homework (0–10): {g.homework}")
    y -= 16
    c.drawString(60, y, f"Oral Test (0–40): {g.oral_test}")
    y -= 16
    c.drawString(60, y, f"Attendance (0–10): {g.attendance}")
    y -= 16
    c.drawString(60, y, f"Questions (0–10): {g.questions}")
    y -= 30

    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, y, f"TOTAL SCORE: {g.total_points:.2f} / 100")
    y -= 40

    # Signatures
    c.line(50, y, 250, y)
    c.setFont("Helvetica", 10)
    c.drawString(50, y - 15, "Teacher Signature")

    c.line(330, y, 530, y)
    c.drawString(330, y - 15, "Principal Signature")

    c.setFont("Helvetica", 9)
    c.drawCentredString(width / 2, 40, "Generated by Grade Portal System")

    c.showPage()
    c.save()

    buffer.seek(0)
    filename = f"boletin_{g.student_name.replace(' ', '_')}_{g.id}.pdf"

    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )

# =========================
# START APP
# =========================
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
    @app.route("/delete/<int:record_id>")
def delete_record(record_id):
    record = Grade.query.get_or_404(record_id)
    db.session.delete(record)
    db.session.commit()
    return redirect(url_for("report"))