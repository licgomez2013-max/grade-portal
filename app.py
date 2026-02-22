import os
import secrets
import datetime
from io import BytesIO

from flask import (
    Flask, request, render_template_string, abort,
    send_file, redirect, url_for, session
)
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

# DB: Render uses DATABASE_URL, local uses SQLite
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url or f"sqlite:///{LOCAL_DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ✅ Admin password fixed in app.py
ADMIN_PASSWORD = "NewChallenge2026"
app.secret_key = "new-challenge-secret-key-2026"

SCHOOL_NAME = "New Challenge Institute"
LOGO_URL = "/static/logo.png"  # put your logo at static/logo.png

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
    group_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.String(40), nullable=False)


class Grade(db.Model):
    __tablename__ = "grades"
    id = db.Column(db.Integer, primary_key=True)

    token = db.Column(db.String(120), nullable=False)

    teacher_name = db.Column(db.String(120), nullable=False)
    group_name = db.Column(db.String(120), nullable=False)

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


with app.app_context():
    db.create_all()


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


def is_admin() -> bool:
    return bool(session.get("is_admin"))


def require_admin():
    if not is_admin():
        return redirect(url_for("login_page"))
    return None


# =========================
# UI (NICE)
# =========================
BASE_STYLE = """
<style>
  :root{
    --bg:#0b1220; --card:#111827; --line:#243244;
    --text:#e5e7eb; --muted:#9ca3af; --accent:#7c3aed; --accent2:#a78bfa;
    --danger:#ef4444; --danger2:#b91c1c;
  }
  *{box-sizing:border-box;}
  body{font-family:Arial, sans-serif; background:var(--bg); color:var(--text); margin:0; padding:28px;}
  a{color:var(--accent2); text-decoration:none;}
  a:hover{text-decoration:underline;}
  .wrap{max-width:1100px; margin:0 auto;}
  .card{
    background:linear-gradient(180deg, #0f172a 0%, #111827 100%);
    border:1px solid var(--line); border-radius:18px; padding:22px;
    box-shadow:0 12px 34px rgba(0,0,0,.35);
  }
  .title{display:flex; align-items:flex-start; justify-content:space-between; gap:14px; flex-wrap:wrap;}
  h1{margin:0; font-size:30px; letter-spacing:.2px;}
  .sub{color:var(--muted); margin-top:7px; font-size:14px;}
  .row{display:grid; grid-template-columns:1fr 1fr; gap:12px;}
  @media (max-width:800px){ .row{grid-template-columns:1fr;} }
  label{display:block; margin-top:12px; font-weight:800;}
  input, select{
    width:100%; padding:12px 12px; margin-top:6px;
    border-radius:14px; border:1px solid #2b3a52;
    background:rgba(11,18,32,.7); color:var(--text);
    outline:none;
  }
  input:focus, select:focus{border-color: #6d28d9; box-shadow:0 0 0 3px rgba(124,58,237,.25);}
  .btn{
    margin-top:14px; padding:11px 14px; border:0; border-radius:14px;
    background:var(--accent); color:white; font-weight:900; cursor:pointer;
  }
  .btn:hover{opacity:.92;}
  .btn-secondary{
    padding:9px 12px; border-radius:12px; border:1px solid var(--line);
    background:rgba(11,18,32,.65); color:#cbd5e1; font-weight:800; cursor:pointer;
  }
  .btn-secondary:hover{opacity:.95;}
  .btn-danger{
    padding:9px 12px; border-radius:12px; border:1px solid rgba(239,68,68,.35);
    background:rgba(239,68,68,.12); color:#fecaca; font-weight:900; cursor:pointer;
  }
  .btn-danger:hover{background:rgba(239,68,68,.18);}
  .pill{display:inline-block; padding:7px 12px; border-radius:999px;
        background:rgba(11,18,32,.65); border:1px solid var(--line);
        color:#cbd5e1; font-size:12px;}
  .space{height:14px;}
  table{width:100%; border-collapse:collapse; margin-top:14px; overflow:hidden; border-radius:14px;}
  th, td{border-bottom:1px solid var(--line); padding:10px; text-align:left; vertical-align:top;}
  th{color:#cbd5e1; font-size:13px; font-weight:900;}
  .mono{font-family:monospace; font-size:12px; color:#cbd5e1; word-break:break-all;}
  .small{color:var(--muted); font-size:12px;}
  .topbar{display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:10px;}
  .error{color:#fca5a5; font-weight:900;}
  .logo{display:flex; align-items:center; gap:10px;}
  .logo img{width:44px; height:44px; object-fit:contain; border-radius:10px; border:1px solid var(--line); background:rgba(11,18,32,.65);}
  .actions{display:flex; gap:8px; align-items:center; flex-wrap:wrap;}
</style>

<script>
async function copyLink(text){
  try{
    await navigator.clipboard.writeText(text);
    alert("Copied ✅\\n" + text);
  } catch(e){
    // fallback
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    alert("Copied ✅\\n" + text);
  }
}
</script>
"""

LOGIN_PAGE = BASE_STYLE + """
<div class="wrap">
  <div class="card">
    <div class="title">
      <div class="logo">
        <img src="{{logo_url}}" alt="Logo" onerror="this.style.display='none'">
        <div>
          <h1>Admin Login</h1>
          <div class="sub">{{school}}</div>
        </div>
      </div>
      <div class="pill">Protected</div>
    </div>

    {% if error %}
      <p class="error">{{error}}</p>
    {% endif %}

    <form method="post" action="/login">
      <label>Password</label>
      <input type="password" name="password" required>
      <button class="btn" type="submit">Login</button>
    </form>

    <div class="space"></div>
    <p class="small">Teachers do not need a password. They use their special link.</p>
  </div>
</div>
"""

ADMIN_PAGE = BASE_STYLE + """
<div class="wrap">
  <div class="card">
    <div class="title">
      <div class="logo">
        <img src="{{logo_url}}" alt="Logo" onerror="this.style.display='none'">
        <div>
          <h1>Grade Portal Admin</h1>
          <div class="sub">Create teacher links (one per group) and manage reports.</div>
        </div>
      </div>
      <div class="topbar">
        <span class="pill">{{school}}</span>
        <span class="pill"><a href="/report">Open Reports</a></span>
        <span class="pill"><a href="/logout">Logout</a></span>
      </div>
    </div>

    <form method="post" action="/create-link">
      <div class="row">
        <div>
          <label>Teacher name</label>
          <input name="teacher_name" placeholder="e.g., Ms. Ana Perez" required>
        </div>
        <div>
          <label>Group (Course / Section)</label>
          <input name="group_name" placeholder="e.g., 2nd A / B1 Evening" required>
        </div>
      </div>
      <button class="btn" type="submit">Create Link</button>
    </form>

    {% if links %}
      <div class="space"></div>
      <h3 style="margin:0;">Existing Links</h3>
      <table>
        <tr>
          <th>Teacher</th>
          <th>Group</th>
          <th>Teacher Link</th>
          <th>Actions</th>
          <th>Created</th>
        </tr>
        {% for L in links %}
          <tr>
            <td>{{L.teacher_name}}</td>
            <td>{{L.group_name}}</td>
            <td class="mono">{{host}}entry/{{L.token}}</td>
            <td>
              <div class="actions">
                <button type="button" class="btn-secondary"
                  onclick="copyLink('{{host}}entry/{{L.token}}')">Copy link</button>
                <a class="pill" href="{{host}}entry/{{L.token}}" target="_blank">Open</a>
              </div>
            </td>
            <td class="small">{{L.created_at[:10]}}</td>
          </tr>
        {% endfor %}
      </table>
    {% endif %}
  </div>
</div>
"""

ENTRY_FORM = BASE_STYLE + """
<div class="wrap">
  <div class="card">
    <div class="title">
      <div>
        <h1>Enter Grades</h1>
        <div class="sub"><b>Teacher:</b> {{teacher}} &nbsp; | &nbsp; <b>Group:</b> {{group}}</div>
      </div>
      <div class="pill">Points / 100</div>
    </div>

    {% if error %}
      <p class="error">{{error}}</p>
    {% endif %}

    <form method="post">
      <div class="row">
        <div>
          <label>Student name</label>
          <input name="student_name" required>
        </div>
        <div>
          <label>Student ID</label>
          <input name="student_id" required>
        </div>
      </div>

      <label>Level</label>
      <input name="level" required>

      <div class="row">
        <div>
          <label>Participation (0–30)</label>
          <input type="number" name="participation" min="0" max="30" step="0.01" required>
        </div>
        <div>
          <label>Homework (0–10)</label>
          <input type="number" name="homework" min="0" max="10" step="0.01" required>
        </div>
      </div>

      <div class="row">
        <div>
          <label>Oral Test (0–40)</label>
          <input type="number" name="oral_test" min="0" max="40" step="0.01" required>
        </div>
        <div>
          <label>Attendance (0–10)</label>
          <input type="number" name="attendance" min="0" max="10" step="0.01" required>
        </div>
      </div>

      <label>Questions (0–10)</label>
      <input type="number" name="questions" min="0" max="10" step="0.01" required>

      <button class="btn" type="submit">Save</button>
    </form>
  </div>
</div>
"""

SUCCESS = BASE_STYLE + """
<div class="wrap">
  <div class="card">
    <h1>Saved ✅</h1>
    <div class="sub"><b>{{student}}</b> (ID: {{sid}} | Level: {{level}})</div>
    <div class="space"></div>
    <div class="pill"><b>Total:</b> {{total}} / 100</div>
    <div class="space"></div>
    <a class="pill" href="">Add another</a>
  </div>
</div>
"""

REPORT_PAGE = BASE_STYLE + """
<div class="wrap">
  <div class="card">
    <div class="title">
      <div class="logo">
        <img src="{{logo_url}}" alt="Logo" onerror="this.style.display='none'">
        <div>
          <h1>Reports</h1>
          <div class="sub">Filter by teacher and group, download PDF reports, and delete records.</div>
        </div>
      </div>
      <div class="topbar">
        <span class="pill"><a href="/">Admin</a></span>
        <span class="pill"><a href="/logout">Logout</a></span>
      </div>
    </div>

    <form method="get">
      <div class="row">
        <div>
          <label>Teacher</label>
          <select name="teacher">
            <option value="">(All)</option>
            {% for t in teachers %}
              <option value="{{t}}" {% if t==selected_teacher %}selected{% endif %}>{{t}}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label>Group</label>
          <select name="group">
            <option value="">(All)</option>
            {% for g in groups %}
              <option value="{{g}}" {% if g==selected_group %}selected{% endif %}>{{g}}</option>
            {% endfor %}
          </select>
        </div>
      </div>
      <button class="btn" type="submit">Apply Filter</button>
      <a class="pill" href="/report" style="margin-left:8px;">Reset</a>
    </form>

    <div class="topbar">
      {% if selected_teacher %}
        <span class="pill"><a href="/report/pdf/teacher/{{selected_teacher|urlencode}}">Download Teacher PDF</a></span>
      {% endif %}
      {% if selected_group %}
        <span class="pill"><a href="/report/pdf/group/{{selected_group|urlencode}}">Download Group PDF</a></span>
      {% endif %}
    </div>

    {% if not rows %}
      <div class="space"></div>
      <p class="small">No grades found for this filter.</p>
    {% else %}
      <table>
        <tr>
          <th>ID</th><th>Teacher</th><th>Group</th>
          <th>Student</th><th>Student ID</th><th>Level</th>
          <th>Total</th><th>PDF</th><th>Date</th><th>Delete</th>
        </tr>
        {% for r in rows %}
          <tr>
            <td>{{r.id}}</td>
            <td>{{r.teacher_name}}</td>
            <td>{{r.group_name}}</td>
            <td>{{r.student_name}}</td>
            <td>{{r.student_id}}</td>
            <td>{{r.level}}</td>
            <td><b>{{"%.2f"|format(r.total_points)}}</b></td>
            <td><a href="/bulletin/{{r.id}}">PDF</a></td>
            <td class="small">{{r.created_at[:10]}}</td>
            <td>
              <form method="post" action="/delete/{{r.id}}" onsubmit="return confirm('Delete this record?');">
                <button class="btn-danger" type="submit">Delete</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </table>
    {% endif %}
  </div>
</div>
"""


# =========================
# AUTH ROUTES
# =========================
@app.get("/login")
def login_page():
    if is_admin():
        return redirect(url_for("index"))
    return render_template_string(LOGIN_PAGE, error=None, school=SCHOOL_NAME, logo_url=LOGO_URL)


@app.post("/login")
def login_post():
    password = request.form.get("password", "")
    if password == ADMIN_PASSWORD:
        session["is_admin"] = True
        return redirect(url_for("index"))
    return render_template_string(LOGIN_PAGE, error="Wrong password", school=SCHOOL_NAME, logo_url=LOGO_URL)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# =========================
# MAIN ROUTES
# =========================
@app.get("/")
def index():
    guard = require_admin()
    if guard:
        return guard
    links = Link.query.order_by(Link.created_at.desc()).all()
    return render_template_string(
        ADMIN_PAGE,
        links=links,
        host=request.host_url,
        school=SCHOOL_NAME,
        logo_url=LOGO_URL
    )


@app.post("/create-link")
def create_link():
    guard = require_admin()
    if guard:
        return guard

    teacher = request.form["teacher_name"].strip()
    group = request.form["group_name"].strip()
    if not teacher or not group:
        return "<h3 style='color:red'>Teacher and Group are required.</h3><p><a href='/'>Back</a></p>"

    token = secrets.token_urlsafe(16)
    now = datetime.datetime.utcnow().isoformat()

    db.session.add(Link(token=token, teacher_name=teacher, group_name=group, created_at=now))
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/entry/<token>", methods=["GET", "POST"])
def entry(token):
    link = Link.query.get_or_404(token)

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
                teacher_name=link.teacher_name,
                group_name=link.group_name,
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
                SUCCESS, student=student_name, sid=student_id, level=level, total=f"{total:.2f}"
            )
        except ValueError as e:
            return render_template_string(
                ENTRY_FORM,
                teacher=link.teacher_name,
                group=link.group_name,
                error=str(e)
            )

    return render_template_string(
        ENTRY_FORM,
        teacher=link.teacher_name,
        group=link.group_name,
        error=None
    )


@app.get("/report")
def report():
    guard = require_admin()
    if guard:
        return guard

    selected_teacher = request.args.get("teacher", "").strip()
    selected_group = request.args.get("group", "").strip()

    teachers = [t[0] for t in db.session.query(Grade.teacher_name).distinct().order_by(Grade.teacher_name).all()]
    groups = [g[0] for g in db.session.query(Grade.group_name).distinct().order_by(Grade.group_name).all()]

    q = Grade.query
    if selected_teacher:
        q = q.filter(Grade.teacher_name == selected_teacher)
    if selected_group:
        q = q.filter(Grade.group_name == selected_group)

    rows = q.order_by(Grade.id.desc()).all()

    return render_template_string(
        REPORT_PAGE,
        rows=rows,
        teachers=teachers,
        groups=groups,
        selected_teacher=selected_teacher,
        selected_group=selected_group,
        logo_url=LOGO_URL
    )


@app.post("/delete/<int:grade_id>")
def delete_record(grade_id):
    guard = require_admin()
    if guard:
        return guard

    record = Grade.query.get_or_404(grade_id)
    db.session.delete(record)
    db.session.commit()

    # return to same filter page if user had filters open
    return redirect(request.referrer or url_for("report"))


def _generate_report_pdf(title: str, rows: list, filename: str):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 55, title)
    c.setFont("Helvetica", 11)
    c.drawString(50, height - 75, SCHOOL_NAME)

    y = height - 105
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "ID")
    c.drawString(80, y, "Student")
    c.drawString(230, y, "Student ID")
    c.drawString(330, y, "Level")
    c.drawString(380, y, "Total")
    c.drawString(430, y, "Date")
    c.line(50, y - 5, width - 50, y - 5)

    c.setFont("Helvetica", 10)
    y -= 20

    for r in rows:
        if y < 80:
            c.showPage()
            y = height - 55
            c.setFont("Helvetica-Bold", 16)
            c.drawString(50, y, title)
            c.setFont("Helvetica", 11)
            c.drawString(50, y - 18, SCHOOL_NAME)
            y -= 50
            c.setFont("Helvetica", 10)

        name = r.student_name
        if len(name) > 26:
            name = name[:26] + "…"

        c.drawString(50, y, str(r.id))
        c.drawString(80, y, name)
        c.drawString(230, y, r.student_id[:18])
        c.drawString(330, y, r.level[:10])
        c.drawString(380, y, f"{r.total_points:.2f}")
        c.drawString(430, y, r.created_at[:10])
        y -= 16

    c.setFont("Helvetica", 9)
    c.drawString(50, 40, f"Generated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    c.save()
    buffer.seek(0)

    return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")


@app.get("/report/pdf/teacher/<path:teacher_name>")
def report_pdf_teacher(teacher_name):
    guard = require_admin()
    if guard:
        return guard

    rows = Grade.query.filter(Grade.teacher_name == teacher_name).order_by(Grade.id.desc()).all()
    if not rows:
        return f"<h3>No records found for teacher: {teacher_name}</h3><p><a href='/report'>Back</a></p>"
    return _generate_report_pdf(
        title=f"Teacher Report: {teacher_name}",
        rows=rows,
        filename=f"teacher_report_{teacher_name.replace(' ', '_')}.pdf"
    )


@app.get("/report/pdf/group/<path:group_name>")
def report_pdf_group(group_name):
    guard = require_admin()
    if guard:
        return guard

    rows = Grade.query.filter(Grade.group_name == group_name).order_by(Grade.id.desc()).all()
    if not rows:
        return f"<h3>No records found for group: {group_name}</h3><p><a href='/report'>Back</a></p>"
    return _generate_report_pdf(
        title=f"Group Report: {group_name}",
        rows=rows,
        filename=f"group_report_{group_name.replace(' ', '_')}.pdf"
    )


@app.get("/bulletin/<int:grade_id>")
def bulletin(grade_id):
    guard = require_admin()
    if guard:
        return guard

    g = Grade.query.get_or_404(grade_id)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Optional Logo for PDF: assets/logo.png
    logo_path = os.path.join(BASE_DIR, "assets", "logo.png")
    if os.path.exists(logo_path):
        logo = ImageReader(logo_path)
        c.drawImage(logo, 50, height - 110, width=70, height=70, mask="auto")

    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width / 2, height - 55, "STUDENT REPORT CARD")
    c.setFont("Helvetica", 12)
    c.drawCentredString(width / 2, height - 80, SCHOOL_NAME)
    c.line(50, height - 115, width - 50, height - 115)

    y = height - 145
    c.setFont("Helvetica", 11)
    c.drawString(50, y, f"Teacher: {g.teacher_name}")
    c.drawRightString(width - 50, y, f"Group: {g.group_name}")

    y -= 25
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Student Information")
    y -= 20

    c.setFont("Helvetica", 11)
    c.drawString(50, y, f"Student Name: {g.student_name}")
    y -= 16
    c.drawString(50, y, f"Student ID: {g.student_id}")
    y -= 16
    c.drawString(50, y, f"Level: {g.level}")
    y -= 16
    c.drawString(50, y, f"Date: {g.created_at[:10]}")
    y -= 28

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Grades (Points System)")
    y -= 18

    c.setFont("Helvetica", 11)
    c.drawString(60, y, f"Participation (0–30): {g.participation}")
    y -= 15
    c.drawString(60, y, f"Homework (0–10): {g.homework}")
    y -= 15
    c.drawString(60, y, f"Oral Test (0–40): {g.oral_test}")
    y -= 15
    c.drawString(60, y, f"Attendance (0–10): {g.attendance}")
    y -= 15
    c.drawString(60, y, f"Questions (0–10): {g.questions}")
    y -= 26

    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, y, f"TOTAL SCORE: {g.total_points:.2f} / 100")
    y -= 38

    c.line(50, y, 250, y)
    c.setFont("Helvetica", 10)
    c.drawString(50, y - 14, "Teacher Signature")

    c.line(330, y, 530, y)
    c.drawString(330, y - 14, "Principal Signature")

    c.setFont("Helvetica", 9)
    c.drawCentredString(width / 2, 40, "Generated by Grade Portal System")

    c.showPage()
    c.save()

    buffer.seek(0)
    filename = f"boletin_{g.student_name.replace(' ', '_')}_{g.id}.pdf"
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")


if __name__ == "__main__":
    app.run(debug=True)