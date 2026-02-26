import os
import csv
import secrets
import datetime
from io import BytesIO, StringIO

from flask import (
    Flask, request, render_template_string,
    send_file, redirect, url_for, session
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader


# =========================
# APP CONFIG
# =========================
app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB_PATH = os.path.join(BASE_DIR, "grades.db")

db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url or f"sqlite:///{LOCAL_DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# Admin password
ADMIN_PASSWORD = "NewChallenge2026"

# Session secret
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "new-challenge-secret-key-2026")

SCHOOL_NAME = os.environ.get("SCHOOL_NAME", "New Challenge Institute")

# Web logo (login/admin/dashboard)
# Put file here: static/logo.png
LOGO_URL = "/static/logo.png"

# PDF logo (report card)
# Put file here: assets/logo.png
PDF_LOGO_PATH = os.path.join(BASE_DIR, "assets", "logo.png")

PASSING_SCORE = float(os.environ.get("PASSING_SCORE", "75"))
DEFAULT_PERIOD = os.environ.get("DEFAULT_PERIOD", "2026-1")

# Risk rules
RISK_LOW_SCORE = float(os.environ.get("RISK_LOW_SCORE", "70"))
RISK_DROP_POINTS = float(os.environ.get("RISK_DROP_POINTS", "10"))


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

    period = db.Column(db.String(30), nullable=True)

    participation = db.Column(db.Float, nullable=False)
    homework = db.Column(db.Float, nullable=False)
    oral_test = db.Column(db.Float, nullable=False)
    attendance = db.Column(db.Float, nullable=False)
    questions = db.Column(db.Float, nullable=False)

    total_points = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.String(40), nullable=False)

    comments = db.Column(db.Text, nullable=True)


class Student(db.Model):
    __tablename__ = "students"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(50), nullable=False)
    student_name = db.Column(db.String(120), nullable=False)
    level = db.Column(db.String(50), nullable=True)
    group_name = db.Column(db.String(120), nullable=True)
    period = db.Column(db.String(30), nullable=True)
    created_at = db.Column(db.String(40), nullable=False)


def ensure_schema():
    """
    Adds missing columns to existing DB WITHOUT deleting data.
    Works for SQLite and Postgres (Render).
    """
    insp = inspect(db.engine)
    tables = set(insp.get_table_names())

    # Create tables if missing
    if "links" not in tables or "grades" not in tables or "students" not in tables:
        db.create_all()
        return

    # ---- links columns
    try:
        link_cols = {c["name"] for c in insp.get_columns("links")}
        if "group_name" not in link_cols:
            db.session.execute(text("ALTER TABLE links ADD COLUMN group_name VARCHAR(120)"))
            db.session.commit()
        if "teacher_name" not in link_cols:
            db.session.execute(text("ALTER TABLE links ADD COLUMN teacher_name VARCHAR(120)"))
            db.session.commit()
        if "created_at" not in link_cols:
            db.session.execute(text("ALTER TABLE links ADD COLUMN created_at VARCHAR(40)"))
            db.session.commit()
    except Exception:
        db.session.rollback()

    # ---- grades columns
    try:
        grade_cols = {c["name"] for c in insp.get_columns("grades")}
        if "comments" not in grade_cols:
            db.session.execute(text("ALTER TABLE grades ADD COLUMN comments TEXT"))
            db.session.commit()
        if "period" not in grade_cols:
            db.session.execute(text("ALTER TABLE grades ADD COLUMN period VARCHAR(30)"))
            db.session.commit()
    except Exception:
        db.session.rollback()

    # ---- students columns (safe adds if you evolve later)
    try:
        student_cols = {c["name"] for c in insp.get_columns("students")}
        if "group_name" not in student_cols:
            db.session.execute(text("ALTER TABLE students ADD COLUMN group_name VARCHAR(120)"))
            db.session.commit()
        if "period" not in student_cols:
            db.session.execute(text("ALTER TABLE students ADD COLUMN period VARCHAR(30)"))
            db.session.commit()
        if "level" not in student_cols:
            db.session.execute(text("ALTER TABLE students ADD COLUMN level VARCHAR(50)"))
            db.session.commit()
    except Exception:
        db.session.rollback()


with app.app_context():
    db.create_all()
    ensure_schema()


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


def pass_status(score: float) -> str:
    return "PASSED" if score >= PASSING_SCORE else "FAILED"


def compute_risk_ids(base_rows):
    """
    Rules:
      - latest score < RISK_LOW_SCORE
      - OR drop > RISK_DROP_POINTS compared to previous record
    Returns set(student_id)
    """
    history = {}
    for r in base_rows:
        history.setdefault(r.student_id, []).append(r)

    risk_ids = set()
    for sid, recs in history.items():
        recs = sorted(recs, key=lambda x: x.created_at)
        latest = recs[-1]

        if latest.total_points < RISK_LOW_SCORE:
            risk_ids.add(sid)
            continue

        if len(recs) >= 2:
            prev = recs[-2]
            if (prev.total_points - latest.total_points) > RISK_DROP_POINTS:
                risk_ids.add(sid)

    return risk_ids


# =========================
# UI STYLE + JS (BONITO)
# =========================
BASE_STYLE = """
<style>
  :root{
    --bg:#0b1020;
    --panel: rgba(16, 24, 39, .75);
    --panel2: rgba(17, 24, 39, .92);
    --line: rgba(148, 163, 184, .14);
    --text:#e5e7eb;
    --muted:#9ca3af;

    --accent:#7c3aed;
    --accent2:#a78bfa;
    --good:#22c55e;
    --bad:#ef4444;
    --warn:#f59e0b;

    --shadow: 0 18px 55px rgba(0,0,0,.45);
    --radius: 18px;
  }

  *{box-sizing:border-box;}
  html, body {height:100%;}
  body{
    margin:0;
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    color:var(--text);
    background:
      radial-gradient(1200px 800px at 10% 10%, rgba(124,58,237,.22), transparent 60%),
      radial-gradient(900px 700px at 90% 20%, rgba(59,130,246,.16), transparent 55%),
      radial-gradient(700px 600px at 40% 95%, rgba(16,185,129,.10), transparent 55%),
      linear-gradient(180deg, #070b16 0%, var(--bg) 55%, #070b16 100%);
    padding: 28px;
  }

  a{color:var(--accent2); text-decoration:none;}
  a:hover{text-decoration:underline;}

  .wrap{max-width:1200px; margin:0 auto;}

  .card{
    background: linear-gradient(180deg, rgba(17,24,39,.85) 0%, rgba(17,24,39,.70) 100%);
    border:1px solid var(--line);
    border-radius: var(--radius);
    padding: 22px;
    box-shadow: var(--shadow);
    backdrop-filter: blur(10px);
  }

  .title{
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    gap:14px;
    flex-wrap:wrap;
  }

  h1{
    margin:0;
    font-size: 34px;
    letter-spacing: -.02em;
    line-height:1.05;
  }

  h3{
    margin: 18px 0 0 0;
    font-size: 16px;
    letter-spacing: .02em;
    color:#d1d5db;
  }

  .sub{
    color:var(--muted);
    margin-top:8px;
    font-size: 13px;
    line-height:1.4;
  }

  .topbar{
    display:flex;
    gap:10px;
    align-items:center;
    flex-wrap:wrap;
    margin-top:8px;
  }

  .pill{
    display:inline-flex;
    align-items:center;
    gap:8px;
    padding: 7px 12px;
    border-radius:999px;
    background: rgba(15,23,42,.55);
    border:1px solid var(--line);
    color:#cbd5e1;
    font-size: 12px;
    white-space: nowrap;
  }
  .pill a{color:#cbd5e1;}
  .pill a:hover{text-decoration:none; color:white;}

  .row{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:12px;
  }
  @media (max-width:900px){ .row{grid-template-columns:1fr;} }

  label{
    display:block;
    margin-top: 12px;
    font-weight: 900;
    color:#e5e7eb;
    font-size: 13px;
  }

  input, select, textarea{
    width:100%;
    padding: 12px 12px;
    margin-top: 6px;
    border-radius: 14px;
    border: 1px solid rgba(148,163,184,.20);
    background: rgba(7,11,22,.55);
    color: var(--text);
    outline:none;
    transition: transform .08s ease, border-color .15s ease, box-shadow .15s ease;
  }
  input:focus, select:focus, textarea:focus{
    border-color: rgba(124,58,237,.55);
    box-shadow: 0 0 0 4px rgba(124,58,237,.14);
  }
  textarea{resize:vertical;}

  .btn{
    margin-top: 14px;
    padding: 11px 15px;
    border:0;
    border-radius: 14px;
    background: linear-gradient(135deg, rgba(124,58,237,1) 0%, rgba(99,102,241,1) 100%);
    color:white;
    font-weight: 900;
    cursor:pointer;
    box-shadow: 0 10px 22px rgba(124,58,237,.18);
    transition: transform .08s ease, filter .2s ease;
  }
  .btn:hover{filter:brightness(1.05);}
  .btn:active{transform: translateY(1px);}

  .btn-secondary{
    padding: 9px 12px;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: rgba(15,23,42,.55);
    color:#cbd5e1;
    font-weight: 900;
    cursor:pointer;
  }

  .btn-danger{
    padding: 9px 12px;
    border-radius: 12px;
    border: 1px solid rgba(239,68,68,.35);
    background: rgba(239,68,68,.12);
    color:#fecaca;
    font-weight: 900;
    cursor:pointer;
  }

  table{
    width:100%;
    border-collapse: separate;
    border-spacing:0;
    margin-top:14px;
    overflow:hidden;
    border-radius: 14px;
    border: 1px solid var(--line);
    background: rgba(7,11,22,.35);
  }

  th, td{
    border-bottom: 1px solid rgba(148,163,184,.12);
    padding: 10px;
    text-align:left;
    vertical-align: top;
  }

  th{
    background: rgba(15,23,42,.55);
    color:#cbd5e1;
    font-size: 12px;
    font-weight: 900;
    position: sticky;
    top: 0;
    z-index: 1;
  }

  tr:hover td{
    background: rgba(124,58,237,.06);
  }

  .mono{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size:12px; color:#cbd5e1; word-break:break-all;}
  .small{color:var(--muted); font-size:12px;}
  .space{height:14px;}

  .error{
    color:#fecaca;
    font-weight: 900;
    background: rgba(239,68,68,.12);
    border: 1px solid rgba(239,68,68,.25);
    padding: 10px 12px;
    border-radius: 14px;
    margin-top: 12px;
  }

  .status-pass{color:var(--good); font-weight: 900;}
  .status-fail{color:var(--bad); font-weight: 900;}

  .logo{
    display:flex;
    align-items:center;
    gap:12px;
  }
  .logo img{
    width:48px;
    height:48px;
    object-fit:contain;
    border-radius: 14px;
    border: 1px solid var(--line);
    background: rgba(7,11,22,.55);
    padding: 6px;
  }
</style>

<script>
async function copyLink(text){
  try{
    await navigator.clipboard.writeText(text);
    alert("Copied ✅\\n" + text);
  } catch(e){
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


# =========================
# PAGES (Templates)
# =========================
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

    {% if error %}<div class="error">{{error}}</div>{% endif %}

    <form method="post" action="/login">
      <label>Password</label>
      <input type="password" name="password" required>
      <button class="btn" type="submit">Login</button>
    </form>
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
          <div class="sub">Create teacher links, import students, and manage reports.</div>
        </div>
      </div>
      <div class="topbar">
        <span class="pill">{{school}}</span>
        <span class="pill"><a href="/dashboard">Dashboard</a></span>
        <span class="pill"><a href="/report">Reports</a></span>
        <span class="pill"><a href="/students">Students</a></span>
        <span class="pill"><a href="/logout">Logout</a></span>
      </div>
    </div>

    <h3>Create Teacher Link</h3>
    <form method="post" action="/create-link">
      <div class="row">
        <div>
          <label>Teacher name</label>
          <input name="teacher_name" required>
        </div>
        <div>
          <label>Group (Course / Section)</label>
          <input name="group_name" required>
        </div>
      </div>
      <button class="btn" type="submit">Create Link</button>
    </form>

    <div class="space"></div>
    <h3>Import Students (CSV)</h3>
    <p class="small">CSV columns: <b>student_id, student_name, level, group_name, period</b></p>

    <form method="post" action="/import-students" enctype="multipart/form-data">
      <input type="file" name="file" accept=".csv" required>
      <button class="btn" type="submit">Upload CSV</button>
    </form>

    {% if links %}
      <div class="space"></div>
      <h3 style="margin:0;">Existing Links</h3>
      <table>
        <tr><th>Teacher</th><th>Group</th><th>Teacher Link</th><th>Actions</th><th>Created</th></tr>
        {% for L in links %}
          <tr>
            <td>{{L.teacher_name}}</td>
            <td>{{L.group_name}}</td>
            <td class="mono">{{host}}entry/{{L.token}}</td>
            <td>
              <button type="button" class="btn-secondary" onclick="copyLink('{{host}}entry/{{L.token}}')">Copy link</button>
              <a class="pill" href="{{host}}entry/{{L.token}}" target="_blank">Open</a>
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
      <div class="pill">Passing: {{passing}}+</div>
    </div>

    {% if error %}<div class="error">{{error}}</div>{% endif %}

    <form method="post">
      <div class="row">
        <div>
          <label>Student name</label>
          <input name="student_name" list="student_list" required placeholder="Type to search...">
          <datalist id="student_list">
            {% for s in students %}
              <option value="{{s.student_name}}">
            {% endfor %}
          </datalist>
        </div>
        <div>
          <label>Student ID</label>
          <input name="student_id" required>
        </div>
      </div>

      <div class="row">
        <div>
          <label>Level</label>
          <input name="level" required>
        </div>
        <div>
          <label>Period</label>
          <input name="period" value="{{default_period}}">
        </div>
      </div>

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

      <label>Teacher Comments</label>
      <textarea name="comments" rows="4" placeholder="Write a short comment (optional)..."></textarea>

      <button class="btn" type="submit">Save</button>
    </form>
  </div>
</div>
"""

SUCCESS_PAGE = BASE_STYLE + """
<div class="wrap">
  <div class="card">
    <h1>Saved ✅</h1>
    <div class="sub"><b>{{student}}</b> (ID: {{sid}} | Level: {{level}} | Period: {{period}})</div>
    <div class="space"></div>
    <div class="pill"><b>Total:</b> {{total}} / 100</div>
    <div class="pill" style="margin-left:8px;">
      <b>Status:</b> <span class="{{status_class}}">{{status}}</span>
    </div>
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
          <div class="sub">Passing: {{passing}}+</div>
        </div>
      </div>
      <div class="topbar">
        <span class="pill"><a href="/">Admin</a></span>
        <span class="pill"><a href="/dashboard">Dashboard</a></span>
        <span class="pill"><a href="/students">Students</a></span>
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

      <div class="row">
        <div>
          <label>Period</label>
          <input name="period" value="{{selected_period or ''}}" placeholder="e.g., 2026-1">
        </div>
        <div>
          <label>Search (Name or ID)</label>
          <input name="search" value="{{selected_search or ''}}" placeholder="e.g., Ana or 2026-001">
        </div>
      </div>

      <button class="btn" type="submit">Apply</button>
      <a class="pill" href="/report" style="margin-left:8px;">Reset</a>
    </form>

    {% if not rows %}
      <div class="space"></div>
      <p class="small">No grades found for this filter.</p>
    {% else %}
      <div class="topbar">
        <span class="pill">Total Records: {{total_records}}</span>
        <span class="pill">Average: {{"%.2f"|format(average)}}</span>
        <span class="pill">Passed: {{passed_count}}</span>
        <span class="pill">Failed: {{failed_count}}</span>
        <span class="pill">
          <a href="/export/pdf?teacher={{selected_teacher|urlencode}}&group={{selected_group|urlencode}}&period={{selected_period|urlencode}}&search={{selected_search|urlencode}}">
            Download PDF (Filtered)
          </a>
        </span>
      </div>

      <table>
        <tr>
          <th>ID</th><th>Teacher</th><th>Group</th><th>Period</th>
          <th>Student</th><th>Student ID</th><th>Level</th>
          <th>Total</th><th>Status</th><th>History</th><th>PDF</th><th>Date</th><th>Delete</th>
        </tr>
        {% for r in rows %}
          <tr>
            <td class="mono">{{r.id}}</td>
            <td>{{r.teacher_name}}</td>
            <td>{{r.group_name}}</td>
            <td>{{r.period or ""}}</td>
            <td><b>{{r.student_name}}</b></td>
            <td class="mono">{{r.student_id}}</td>
            <td>{{r.level}}</td>
            <td><b>{{"%.2f"|format(r.total_points)}}</b></td>
            <td>
              {% if r.total_points >= passing %}
                <span class="status-pass">PASSED</span>
              {% else %}
                <span class="status-fail">FAILED</span>
              {% endif %}
            </td>
            <td><a class="pill" href="/student/{{r.student_id}}">View</a></td>
            <td><a class="pill" href="/bulletin/{{r.id}}">PDF</a></td>
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

STUDENT_HISTORY_PAGE = BASE_STYLE + """
<div class="wrap">
  <div class="card">
    <div class="title">
      <div>
        <h1>Student History</h1>
        <div class="sub"><b>Student ID:</b> {{student_id}} | Passing: {{passing}}+</div>
      </div>
      <div class="topbar">
        <span class="pill"><a href="/report">Back to Reports</a></span>
      </div>
    </div>

    {% if not rows %}
      <p class="small">No records found for this student.</p>
    {% else %}
      <table>
        <tr>
          <th>ID</th><th>Student</th><th>Period</th><th>Teacher</th><th>Group</th><th>Level</th>
          <th>Total</th><th>Status</th><th>PDF</th><th>Date</th>
        </tr>
        {% for r in rows %}
          <tr>
            <td class="mono">{{r.id}}</td>
            <td><b>{{r.student_name}}</b></td>
            <td>{{r.period or ""}}</td>
            <td>{{r.teacher_name}}</td>
            <td>{{r.group_name}}</td>
            <td>{{r.level}}</td>
            <td><b>{{"%.2f"|format(r.total_points)}}</b></td>
            <td>
              {% if r.total_points >= passing %}
                <span class="status-pass">PASSED</span>
              {% else %}
                <span class="status-fail">FAILED</span>
              {% endif %}
            </td>
            <td><a class="pill" href="/bulletin/{{r.id}}">PDF</a></td>
            <td class="small">{{r.created_at[:10]}}</td>
          </tr>
        {% endfor %}
      </table>
    {% endif %}
  </div>
</div>
"""

STUDENTS_PAGE = BASE_STYLE + """
<div class="wrap">
  <div class="card">
    <div class="title">
      <div class="logo">
        <img src="{{logo_url}}" alt="Logo" onerror="this.style.display='none'">
        <div>
          <h1>Students</h1>
          <div class="sub">List of imported students</div>
        </div>
      </div>
      <div class="topbar">
        <span class="pill"><a href="/">Admin</a></span>
        <span class="pill"><a href="/dashboard">Dashboard</a></span>
        <span class="pill"><a href="/report">Reports</a></span>
        <span class="pill"><a href="/logout">Logout</a></span>
      </div>
    </div>

    <form method="get">
      <div class="row">
        <div>
          <label>Group</label>
          <input name="group" value="{{selected_group or ''}}" placeholder="e.g., A1-MON">
        </div>
        <div>
          <label>Search (Name or ID)</label>
          <input name="search" value="{{selected_search or ''}}" placeholder="e.g., Ana or 2026-001">
        </div>
      </div>
      <button class="btn" type="submit">Search</button>
      <a class="pill" href="/students" style="margin-left:8px;">Reset</a>
    </form>

    {% if not rows %}
      <div class="space"></div>
      <p class="small">No students found.</p>
    {% else %}
      <div class="topbar">
        <span class="pill">Total: {{rows|length}}</span>
      </div>
      <table>
        <tr><th>ID</th><th>Name</th><th>Level</th><th>Group</th><th>Period</th><th>Added</th></tr>
        {% for s in rows %}
          <tr>
            <td class="mono">{{s.student_id}}</td>
            <td><b>{{s.student_name}}</b></td>
            <td>{{s.level or ""}}</td>
            <td>{{s.group_name or ""}}</td>
            <td>{{s.period or ""}}</td>
            <td class="small">{{s.created_at[:10]}}</td>
          </tr>
        {% endfor %}
      </table>
    {% endif %}
  </div>
</div>
"""

# DASHBOARD PRO
DASHBOARD_PAGE = BASE_STYLE + """
<style>
  .kpi-grid{
    display:grid;
    grid-template-columns: repeat(3, 1fr);
    gap:12px;
    margin-top:12px;
  }
  @media (max-width: 980px){ .kpi-grid{grid-template-columns:1fr;} }

  .kpi{
    border:1px solid var(--line);
    border-radius: 18px;
    padding: 14px 14px;
    background: rgba(7,11,22,.35);
    position: relative;
    overflow:hidden;
  }
  .kpi::before{
    content:"";
    position:absolute;
    inset:-2px;
    background:
      radial-gradient(500px 220px at 10% 0%, rgba(124,58,237,.22), transparent 60%),
      radial-gradient(420px 220px at 90% 10%, rgba(59,130,246,.14), transparent 60%);
    opacity:.7;
    pointer-events:none;
  }
  .kpi > *{position:relative;}
  .kpi-top{
    display:flex;
    justify-content:space-between;
    align-items:flex-start;
    gap:10px;
  }
  .kpi-label{color:var(--muted); font-size:12px; font-weight:900; letter-spacing:.04em; text-transform:uppercase;}
  .kpi-value{font-size:30px; font-weight:1000; margin-top:6px; letter-spacing:-.02em;}
  .kpi-foot{margin-top:10px; color:var(--muted); font-size:12px;}
  .badge{
    display:inline-flex; align-items:center; justify-content:center;
    width:34px; height:34px; border-radius:12px;
    border:1px solid var(--line);
    background: rgba(15,23,42,.55);
    font-size:16px;
  }

  .split{
    display:grid;
    grid-template-columns: 1.2fr .8fr;
    gap:12px;
    margin-top:12px;
  }
  @media (max-width: 980px){ .split{grid-template-columns:1fr;} }

  .progress{
    border:1px solid var(--line);
    border-radius: 18px;
    padding: 14px;
    background: rgba(7,11,22,.35);
  }
  .bar{
    height: 12px;
    border-radius: 999px;
    background: rgba(148,163,184,.16);
    overflow:hidden;
    margin-top: 10px;
    border:1px solid rgba(148,163,184,.10);
  }
  .bar > div{
    height:100%;
    width: {{ pass_ratio }}%;
    background: linear-gradient(90deg, rgba(34,197,94,1), rgba(124,58,237,1));
  }
  .mini{
    display:flex;
    justify-content:space-between;
    gap:10px;
    margin-top:10px;
    color:var(--muted);
    font-size:12px;
  }

  .section-title{
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:12px;
    margin-top: 18px;
  }
  .section-title h3{margin:0;}
  .hint{color:var(--muted); font-size:12px;}
</style>

<div class="wrap">
  <div class="card">
    <div class="title">
      <div class="logo">
        <img src="{{logo_url}}" alt="Logo" onerror="this.style.display='none'">
        <div>
          <h1>Academic Dashboard</h1>
          <div class="sub">Passing: {{passing}}+ · Real-time summary from saved records</div>
        </div>
      </div>
      <div class="topbar">
        <span class="pill"><a href="/">Admin</a></span>
        <span class="pill"><a href="/report">Reports</a></span>
        <span class="pill"><a href="/students">Students</a></span>
        <span class="pill"><a href="/logout">Logout</a></span>
      </div>
    </div>

    <div class="kpi-grid">
      <div class="kpi">
        <div class="kpi-top">
          <div>
            <div class="kpi-label">Total Records</div>
            <div class="kpi-value">{{stats.total_records}}</div>
          </div>
          <div class="badge">🗂️</div>
        </div>
        <div class="kpi-foot">All grade entries saved in the system.</div>
      </div>

      <div class="kpi">
        <div class="kpi-top">
          <div>
            <div class="kpi-label">Average Score</div>
            <div class="kpi-value">{{"%.2f"|format(stats.average)}}</div>
          </div>
          <div class="badge">📈</div>
        </div>
        <div class="kpi-foot">Overall average of all records.</div>
      </div>

      <div class="kpi">
        <div class="kpi-top">
          <div>
            <div class="kpi-label">Teachers / Groups</div>
            <div class="kpi-value">{{stats.teachers}} / {{stats.groups}}</div>
          </div>
          <div class="badge">👩‍🏫</div>
        </div>
        <div class="kpi-foot">Unique teachers and groups with saved records.</div>
      </div>

      <div class="kpi">
        <div class="kpi-top">
          <div>
            <div class="kpi-label">Passed</div>
            <div class="kpi-value" style="color:var(--good);">{{stats.passed}}</div>
          </div>
          <div class="badge">✅</div>
        </div>
        <div class="kpi-foot">Students scoring {{passing}} or above.</div>
      </div>

      <div class="kpi">
        <div class="kpi-top">
          <div>
            <div class="kpi-label">Failed</div>
            <div class="kpi-value" style="color:var(--bad);">{{stats.failed}}</div>
          </div>
          <div class="badge">⛔</div>
        </div>
        <div class="kpi-foot">Students below {{passing}}.</div>
      </div>

      <div class="kpi">
        <div class="kpi-top">
          <div>
            <div class="kpi-label">Pass Rate</div>
            <div class="kpi-value">{{ pass_ratio }}%</div>
          </div>
          <div class="badge">🎯</div>
        </div>
        <div class="kpi-foot">Passed ÷ Total (rounded).</div>
      </div>
    </div>

    <div class="split">
      <div class="progress">
        <div class="kpi-label">Pass vs Fail</div>
        <div class="bar"><div></div></div>
        <div class="mini">
          <span><b style="color:var(--good);">{{stats.passed}}</b> Passed</span>
          <span><b style="color:var(--bad);">{{stats.failed}}</b> Failed</span>
          <span><b>{{stats.total_records}}</b> Total</span>
        </div>
      </div>

      <div class="progress">
        <div class="kpi-label">Quick Actions</div>
        <div class="space"></div>
        <div class="topbar" style="margin-top:0;">
          <span class="pill"><a href="/report">Open Reports</a></span>
          <span class="pill"><a href="/students">Students</a></span>
          <span class="pill"><a href="/">Create Links</a></span>
        </div>
        <div class="space"></div>
        <div class="hint">Tip: Use Reports filters (Teacher/Group/Period) to download PDFs by group.</div>
      </div>
    </div>

    <div class="section-title">
      <h3>Top 5 Students</h3>
      <div class="hint">Best totals across current records</div>
    </div>
    {% if top5 %}
      <table>
        <tr><th>#</th><th>Student</th><th>ID</th><th>Teacher</th><th>Group</th><th>Total</th><th>PDF</th></tr>
        {% for r in top5 %}
          <tr>
            <td>{{loop.index}}</td>
            <td><b>{{r.student_name}}</b></td>
            <td class="mono">{{r.student_id}}</td>
            <td>{{r.teacher_name}}</td>
            <td>{{r.group_name}}</td>
            <td><b>{{"%.2f"|format(r.total_points)}}</b></td>
            <td><a class="pill" href="/bulletin/{{r.id}}">PDF</a></td>
          </tr>
        {% endfor %}
      </table>
    {% else %}
      <p class="small">No records yet.</p>
    {% endif %}

    <div class="section-title">
      <h3>Latest 5 Records</h3>
      <div class="hint">Most recent entries</div>
    </div>
    {% if latest5 %}
      <table>
        <tr><th>ID</th><th>Student</th><th>Teacher</th><th>Group</th><th>Total</th><th>Status</th><th>Date</th></tr>
        {% for r in latest5 %}
          <tr>
            <td class="mono">{{r.id}}</td>
            <td><b>{{r.student_name}}</b> <span class="small">({{r.student_id}})</span></td>
            <td>{{r.teacher_name}}</td>
            <td>{{r.group_name}}</td>
            <td><b>{{"%.2f"|format(r.total_points)}}</b></td>
            <td>
              {% if r.total_points >= passing %}
                <span class="status-pass">PASSED</span>
              {% else %}
                <span class="status-fail">FAILED</span>
              {% endif %}
            </td>
            <td class="small">{{r.created_at[:10]}}</td>
          </tr>
        {% endfor %}
      </table>
    {% else %}
      <p class="small">No records yet.</p>
    {% endif %}

    <div class="section-title">
      <h3>At Risk</h3>
      <div class="hint">Below 70 or dropped > 10 points</div>
    </div>
    {% if at_risk %}
      <table>
        <tr><th>Student</th><th>ID</th><th>Latest Total</th><th>History</th></tr>
        {% for r in at_risk %}
          <tr>
            <td><b>{{r.student_name}}</b></td>
            <td class="mono">{{r.student_id}}</td>
            <td><b style="color:var(--warn);">{{"%.2f"|format(r.total_points)}}</b></td>
            <td><a class="pill" href="/student/{{r.student_id}}">View</a></td>
          </tr>
        {% endfor %}
      </table>
    {% else %}
      <p class="small">No at-risk students found ✅</p>
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

    token = secrets.token_urlsafe(16)
    now = datetime.datetime.utcnow().isoformat()

    db.session.add(Link(token=token, teacher_name=teacher, group_name=group, created_at=now))
    db.session.commit()
    return redirect(url_for("index"))


@app.post("/import-students")
def import_students():
    guard = require_admin()
    if guard:
        return guard

    f = request.files.get("file")
    if not f:
        return "<h3>No file uploaded</h3><p><a href='/'>Back</a></p>"

    reader = csv.DictReader(StringIO(f.stream.read().decode("utf-8", errors="ignore")))
    now = datetime.datetime.utcnow().isoformat()

    added = 0
    for row in reader:
        sid = (row.get("student_id") or "").strip()
        sname = (row.get("student_name") or "").strip()
        level = (row.get("level") or "").strip()
        group_name = (row.get("group_name") or "").strip()
        period = (row.get("period") or "").strip()

        if not sid or not sname:
            continue

        db.session.add(Student(
            student_id=sid,
            student_name=sname,
            level=level or None,
            group_name=group_name or None,
            period=period or None,
            created_at=now
        ))
        added += 1

    db.session.commit()
    return f"<h3>Imported ✅ {added} students</h3><p><a href='/'>Back to Admin</a></p>"


@app.get("/students")
def students_page():
    guard = require_admin()
    if guard:
        return guard

    selected_group = request.args.get("group", "").strip()
    search = request.args.get("search", "").strip()

    q = Student.query
    if selected_group:
        q = q.filter(Student.group_name == selected_group)
    if search:
        q = q.filter(
            (Student.student_name.ilike(f"%{search}%")) |
            (Student.student_id.ilike(f"%{search}%"))
        )

    rows = q.order_by(Student.student_name.asc()).all()
    return render_template_string(
        STUDENTS_PAGE,
        rows=rows,
        logo_url=LOGO_URL,
        selected_group=selected_group,
        selected_search=search
    )


@app.route("/entry/<token>", methods=["GET", "POST"])
def entry(token):
    link = Link.query.get_or_404(token)

    students = Student.query.filter(
        (Student.group_name == link.group_name) | (Student.group_name.is_(None))
    ).order_by(Student.student_name.asc()).all()

    if request.method == "POST":
        try:
            student_name = request.form["student_name"].strip()
            student_id = request.form["student_id"].strip()
            level = request.form["level"].strip()
            period = request.form.get("period", "").strip() or DEFAULT_PERIOD

            p = safe_float(request.form["participation"], "Participation", 0, 30)
            h = safe_float(request.form["homework"], "Homework", 0, 10)
            o = safe_float(request.form["oral_test"], "Oral Test", 0, 40)
            a = safe_float(request.form["attendance"], "Attendance", 0, 10)
            qv = safe_float(request.form["questions"], "Questions", 0, 10)

            total = p + h + o + a + qv
            now = datetime.datetime.utcnow().isoformat()

            db.session.add(Grade(
                token=token,
                teacher_name=link.teacher_name,
                group_name=link.group_name,
                student_name=student_name,
                student_id=student_id,
                level=level,
                period=period,
                participation=p,
                homework=h,
                oral_test=o,
                attendance=a,
                questions=qv,
                total_points=total,
                created_at=now,
                comments=request.form.get("comments", "").strip()
            ))
            db.session.commit()

            status = pass_status(total)
            return render_template_string(
                SUCCESS_PAGE,
                student=student_name,
                sid=student_id,
                level=level,
                period=period,
                total=f"{total:.2f}",
                status=status,
                status_class=("status-pass" if status == "PASSED" else "status-fail")
            )
        except ValueError as e:
            return render_template_string(
                ENTRY_FORM,
                teacher=link.teacher_name,
                group=link.group_name,
                error=str(e),
                passing=PASSING_SCORE,
                default_period=DEFAULT_PERIOD,
                students=students
            )

    return render_template_string(
        ENTRY_FORM,
        teacher=link.teacher_name,
        group=link.group_name,
        error=None,
        passing=PASSING_SCORE,
        default_period=DEFAULT_PERIOD,
        students=students
    )


@app.get("/report")
def report():
    guard = require_admin()
    if guard:
        return guard

    selected_teacher = request.args.get("teacher", "").strip()
    selected_group = request.args.get("group", "").strip()
    selected_period = request.args.get("period", "").strip()
    search = request.args.get("search", "").strip()

    teachers = [t[0] for t in db.session.query(Grade.teacher_name).distinct().order_by(Grade.teacher_name).all()]
    groups = [g[0] for g in db.session.query(Grade.group_name).distinct().order_by(Grade.group_name).all()]

    q = Grade.query
    if selected_teacher:
        q = q.filter(Grade.teacher_name == selected_teacher)
    if selected_group:
        q = q.filter(Grade.group_name == selected_group)
    if selected_period:
        q = q.filter(Grade.period == selected_period)
    if search:
        q = q.filter(
            (Grade.student_name.ilike(f"%{search}%")) |
            (Grade.student_id.ilike(f"%{search}%"))
        )

    rows = q.order_by(Grade.id.desc()).all()

    average = (sum(r.total_points for r in rows) / len(rows)) if rows else 0.0
    passed_count = sum(1 for r in rows if r.total_points >= PASSING_SCORE)
    failed_count = len(rows) - passed_count

    return render_template_string(
        REPORT_PAGE,
        rows=rows,
        teachers=teachers,
        groups=groups,
        selected_teacher=selected_teacher,
        selected_group=selected_group,
        selected_period=selected_period,
        selected_search=search,
        average=average,
        total_records=len(rows),
        passed_count=passed_count,
        failed_count=failed_count,
        logo_url=LOGO_URL,
        passing=PASSING_SCORE
    )


@app.post("/delete/<int:grade_id>")
def delete_record(grade_id):
    guard = require_admin()
    if guard:
        return guard

    record = Grade.query.get_or_404(grade_id)
    db.session.delete(record)
    db.session.commit()
    return redirect(request.referrer or url_for("report"))


@app.get("/student/<student_id>")
def student_history(student_id):
    guard = require_admin()
    if guard:
        return guard

    rows = Grade.query.filter(Grade.student_id == student_id).order_by(Grade.id.desc()).all()
    return render_template_string(
        STUDENT_HISTORY_PAGE,
        student_id=student_id,
        rows=rows,
        passing=PASSING_SCORE
    )


@app.get("/dashboard")
def dashboard():
    guard = require_admin()
    if guard:
        return guard

    rows = Grade.query.order_by(Grade.id.desc()).all()

    total_records = len(rows)
    average = (sum(r.total_points for r in rows) / total_records) if total_records else 0.0
    passed_count = sum(1 for r in rows if r.total_points >= PASSING_SCORE)
    failed_count = total_records - passed_count

    teacher_count = len(set(r.teacher_name for r in rows)) if rows else 0
    group_count = len(set(r.group_name for r in rows)) if rows else 0

    top5 = Grade.query.order_by(Grade.total_points.desc()).limit(5).all()
    latest5 = Grade.query.order_by(Grade.id.desc()).limit(5).all()

    risk_ids = compute_risk_ids(rows)
    at_risk = []
    if risk_ids:
        for sid in risk_ids:
            latest = Grade.query.filter(Grade.student_id == sid).order_by(Grade.id.desc()).first()
            if latest:
                at_risk.append(latest)
        at_risk = sorted(at_risk, key=lambda x: x.total_points)

    pass_ratio = int(round((passed_count / total_records) * 100)) if total_records else 0

    stats = {
        "total_records": total_records,
        "average": average,
        "passed": passed_count,
        "failed": failed_count,
        "teachers": teacher_count,
        "groups": group_count,
    }

    return render_template_string(
        DASHBOARD_PAGE,
        logo_url=LOGO_URL,
        passing=PASSING_SCORE,
        stats=stats,
        top5=top5,
        latest5=latest5,
        at_risk=at_risk,
        pass_ratio=pass_ratio
    )


# =========================
# PDF: BULLETIN
# =========================
@app.get("/bulletin/<int:grade_id>")
def bulletin(grade_id):
    guard = require_admin()
    if guard:
        return guard

    g = Grade.query.get_or_404(grade_id)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # PDF logo (optional)
    if os.path.exists(PDF_LOGO_PATH):
        try:
            logo = ImageReader(PDF_LOGO_PATH)
            c.drawImage(logo, 50, height - 110, width=70, height=70, mask="auto")
        except Exception:
            pass

    # Header
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width / 2, height - 55, "STUDENT REPORT CARD")
    c.setFont("Helvetica", 12)
    c.drawCentredString(width / 2, height - 80, SCHOOL_NAME)
    c.line(50, height - 115, width - 50, height - 115)

    y = height - 145
    c.setFont("Helvetica", 11)
    c.drawString(50, y, f"Teacher: {g.teacher_name}")
    c.drawRightString(width - 50, y, f"Group: {g.group_name}")

    y -= 20
    status = pass_status(g.total_points)
    c.drawString(50, y, f"Period: {g.period or ''}")
    c.drawRightString(width - 50, y, f"Status: {status} (Passing {int(PASSING_SCORE)}+)")

    y -= 25
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Student Information")
    y -= 18

    c.setFont("Helvetica", 11)
    c.drawString(50, y, f"Student Name: {g.student_name}")
    y -= 16
    c.drawString(50, y, f"Student ID: {g.student_id}")
    y -= 16
    c.drawString(50, y, f"Level: {g.level}")
    y -= 16
    c.drawString(50, y, f"Date: {g.created_at[:10]}")
    y -= 22

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Grades (Points System)")
    y -= 16

    c.setFont("Helvetica", 11)
    c.drawString(60, y, f"Participation (0–30): {g.participation}")
    y -= 14
    c.drawString(60, y, f"Homework (0–10): {g.homework}")
    y -= 14
    c.drawString(60, y, f"Oral Test (0–40): {g.oral_test}")
    y -= 14
    c.drawString(60, y, f"Attendance (0–10): {g.attendance}")
    y -= 14
    c.drawString(60, y, f"Questions (0–10): {g.questions}")
    y -= 20

    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, y, f"TOTAL SCORE: {g.total_points:.2f} / 100")
    y -= 22

    if g.comments:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, y, "Teacher Comments:")
        y -= 16
        c.setFont("Helvetica", 11)

        max_bottom_for_comments = 170
        text_obj = c.beginText(60, y)
        for line in (g.comments or "").splitlines():
            if text_obj.getY() <= max_bottom_for_comments:
                text_obj.textLine("…")
                break
            text_obj.textLine(line)
        c.drawText(text_obj)

    # SIGNATURE LINES (fixed)
    sig_y = 120
    c.setLineWidth(1.6)

    c.line(70, sig_y, 270, sig_y)
    c.setFont("Helvetica", 10)
    c.drawString(120, sig_y - 14, "Teacher Signature")

    c.line(width - 270, sig_y, width - 70, sig_y)
    c.drawString(width - 230, sig_y - 14, "Principal Signature")

    c.setFont("Helvetica", 9)
    c.drawString(50, 50, f"Generated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    c.showPage()
    c.save()

    buffer.seek(0)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename = f"boletin_{g.student_name.replace(' ', '_')}_{g.id}_{ts}.pdf"
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")


# =========================
# EXPORT PDF REPORT (FILTERED)
# =========================
@app.get("/export/pdf")
def export_pdf():
    guard = require_admin()
    if guard:
        return guard

    selected_teacher = request.args.get("teacher", "").strip()
    selected_group = request.args.get("group", "").strip()
    selected_period = request.args.get("period", "").strip()
    search = request.args.get("search", "").strip()

    q = Grade.query
    if selected_teacher:
        q = q.filter(Grade.teacher_name == selected_teacher)
    if selected_group:
        q = q.filter(Grade.group_name == selected_group)
    if selected_period:
        q = q.filter(Grade.period == selected_period)
    if search:
        q = q.filter(
            (Grade.student_name.ilike(f"%{search}%")) |
            (Grade.student_id.ilike(f"%{search}%"))
        )

    rows = q.order_by(Grade.total_points.desc()).all()

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    y = height - 60
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, f"{SCHOOL_NAME} - Grades Report")
    y -= 20

    c.setFont("Helvetica", 10)
    filters = []
    if selected_teacher: filters.append(f"Teacher: {selected_teacher}")
    if selected_group: filters.append(f"Group: {selected_group}")
    if selected_period: filters.append(f"Period: {selected_period}")
    if search: filters.append(f"Search: {search}")
    c.drawString(50, y, " | ".join(filters) if filters else "All records")
    y -= 25

    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Rank")
    c.drawString(90, y, "Student")
    c.drawString(250, y, "Student ID")
    c.drawString(340, y, "Level")
    c.drawString(410, y, "Total")
    c.drawString(470, y, "Status")
    y -= 10
    c.line(50, y, width - 50, y)
    y -= 16

    for i, r in enumerate(rows, start=1):
        if y < 80:
            c.showPage()
            y = height - 60
            c.setFont("Helvetica-Bold", 10)
            c.drawString(50, y, "Rank")
            c.drawString(90, y, "Student")
            c.drawString(250, y, "Student ID")
            c.drawString(340, y, "Level")
            c.drawString(410, y, "Total")
            c.drawString(470, y, "Status")
            y -= 10
            c.line(50, y, width - 50, y)
            y -= 16

        c.setFont("Helvetica", 10)
        c.drawString(50, y, str(i))
        c.drawString(90, y, (r.student_name or "")[:24])
        c.drawString(250, y, (r.student_id or "")[:18])
        c.drawString(340, y, (r.level or "")[:10])
        c.drawString(410, y, f"{r.total_points:.2f}")
        c.drawString(470, y, pass_status(r.total_points))
        y -= 14

    c.setFont("Helvetica", 9)
    c.drawString(50, 40, f"Generated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    c.save()

    buffer.seek(0)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return send_file(buffer, as_attachment=True, download_name=f"grades_report_{ts}.pdf", mimetype="application/pdf")


# =========================
# RUN LOCAL (PORT 8000)
# =========================
if __name__ == "__main__":
    # Local: http://127.0.0.1:8000
    app.run(host="127.0.0.1", port=8000, debug=True)