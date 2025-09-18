from __future__ import annotations
import os, time, json, secrets
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from database import db
from models import sha256_hex, DIFFICULTY_POINTS

SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24))
RATE_LIMIT_SUBMIT = os.getenv("RATE_LIMIT_SUBMIT", "10 per minute")
RATE_LIMIT_TEAM = os.getenv("RATE_LIMIT_TEAM", "60 per hour")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

BASE_DIR = os.path.dirname(__file__)

app = Flask(__name__)
app.secret_key = SECRET_KEY

limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"])

def init_db_if_needed():
    with open(os.path.join(BASE_DIR, "schema.sql"), "r", encoding="utf-8") as f:
        schema_sql = f.read()
    with db() as conn:
        conn.executescript(schema_sql)
        c = conn.execute("SELECT COUNT(*) AS c FROM teams").fetchone()["c"]
        d = conn.execute("SELECT COUNT(*) AS c FROM challenges").fetchone()["c"]
        if c == 0 and d == 0:
            team_file = os.path.join(BASE_DIR, "seed_teams.json")
            chal_file = os.path.join(BASE_DIR, "seed_challenges.json")
            teams = json.load(open(team_file, "r", encoding="utf-8")) if os.path.exists(team_file) else []
            chals = json.load(open(chal_file, "r", encoding="utf-8")) if os.path.exists(chal_file) else []
            cur = conn.cursor()
            for t in teams:
                name = t["name"].strip()
                token = secrets.token_urlsafe(24)
                join_code = str(secrets.randbelow(900000) + 100000)
                cur.execute("INSERT OR IGNORE INTO teams(name, join_code, token) VALUES(?,?,?)", (name, join_code, token))
            for cobj in chals:
                title = cobj["title"].strip()
                diff = cobj["difficulty"].strip()
                points = DIFFICULTY_POINTS.get(diff)
                if not points:
                    raise ValueError(f"Unknown difficulty: {diff}")
                flag_hash = sha256_hex(cobj["flag"].strip())
                active = 1 if cobj.get("active", True) else 0
                cur.execute(
                    "INSERT OR IGNORE INTO challenges(title, difficulty, flag_hash, points, is_active) VALUES(?,?,?,?,?)",
                    (title, diff, flag_hash, points, active)
                )

init_db_if_needed()

def current_team():
    token = session.get("team_token")
    if not token:
        return None
    with db() as conn:
        cur = conn.execute("SELECT * FROM teams WHERE token = ?", (token,))
        return cur.fetchone()

# ---------- Public routes ----------
@app.get("/")
def home():
    with db() as conn:
        teams = conn.execute("SELECT name FROM teams ORDER BY name ASC").fetchall()
    return render_template("home.html", teams=[t["name"] for t in teams])

@app.post("/join")
@limiter.limit("3
