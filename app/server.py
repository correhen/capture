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

@app.get("/")
def home():
    with db() as conn:
        teams = conn.execute("SELECT name FROM teams ORDER BY name ASC").fetchall()
    return render_template("home.html", teams=[t["name"] for t in teams])

@app.post("/join")
@limiter.limit("30 per hour")
def join():
    team_name = request.form.get("team_name", "").strip()
    join_code = request.form.get("join_code", "").strip()
    if not team_name or not join_code:
        return redirect(url_for("home"))
    with db() as conn:
        row = conn.execute("SELECT token FROM teams WHERE name=? AND join_code=?", (team_name, join_code)).fetchone()
        if not row:
            teams = [t["name"]] if (t:=None) else [x["name"] for x in conn.execute("SELECT name FROM teams ORDER BY name").fetchall()]
            return render_template("home.html", teams=teams, error="Onjuiste join code of team."), 401
        session["team_token"] = row["token"]
    return redirect(url_for("submit"))

@app.get("/submit")
def submit():
    team = current_team()
    if not team:
        return redirect(url_for("home"))
    with db() as conn:
        challenges = conn.execute("SELECT id, title, difficulty, points FROM challenges WHERE is_active=1 ORDER BY id ASC").fetchall()
        solved = {r["challenge_id"] for r in conn.execute("SELECT challenge_id FROM solves WHERE team_id=?", (team["id"],)).fetchall()}
    return render_template("submit.html", team=team, challenges=challenges, solved=solved)

@app.post("/api/submit")
@limiter.limit(lambda: RATE_LIMIT_SUBMIT)
def api_submit():
    team = current_team()
    if not team:
        return jsonify({"ok": False, "error": "Niet ingelogd bij een team."}), 401

    flag = request.form.get("flag", "").strip()
    challenge_id = request.form.get("challenge_id", "").strip()

    if not flag.startswith("CTF{") or not flag.endswith("}"):
        return jsonify({"ok": False, "correct": False, "message": "Vorm is CTF{...}."})

    flagh = sha256_hex(flag)
    with db() as conn:
        chal = conn.execute("SELECT id, title, points, flag_hash FROM challenges WHERE id=? AND is_active=1", (challenge_id,)).fetchone()
        if not chal:
            return jsonify({"ok": False, "error": "Challenge niet gevonden of inactief."}), 404
        if flagh != chal["flag_hash"]:
            return jsonify({"ok": True, "correct": False, "message": "Helaas, dat is niet de juiste flag."})
        existing = conn.execute("SELECT 1 FROM solves WHERE team_id=? AND challenge_id=?", (team["id"], chal["id"])).fetchone()
        if existing:
            return jsonify({"ok": True, "correct": True, "message": "Al opgelost — geen extra punten."})
        cur = conn.cursor()
        cur.execute("INSERT INTO solves(team_id, challenge_id) VALUES(?,?)", (team["id"], chal["id"]))
        cur.execute("UPDATE teams SET score = score + ? WHERE id=?", (chal["points"], team["id"]))
    return jsonify({"ok": True, "correct": True, "message": "Gefeliciteerd! Flag klopt. Punten toegekend."})

@app.get("/scoreboard")
@limiter.limit("120 per hour")
def scoreboard():
    with db() as conn:
        teams_full = conn.execute("SELECT id, name, score FROM teams ORDER BY score DESC, name ASC").fetchall()
        solves = conn.execute("SELECT team_id, COUNT(*) c FROM solves GROUP BY team_id").fetchall()
        scount = {r["team_id"]: r["c"] for r in solves}
    return render_template("scoreboard.html", teams=teams_full, scount=scount)

@app.get("/health")
def health():
    return {"status": "ok", "time": int(time.time())}

@app.post("/admin/activate")
@limiter.limit("30 per hour")
def admin_activate():
    token = request.headers.get("X-Admin-Token", "")
    if token != os.getenv("ADMIN_TOKEN", ""):
        abort(401)
    payload = request.get_json(force=True)
    challenge_id = payload.get("challenge_id")
    active = 1 if bool(payload.get("active", True)) else 0
    with db() as conn:
        conn.execute("UPDATE challenges SET is_active=? WHERE id=?", (active, challenge_id))
    return {"ok": True}

@app.post("/admin/add-team")
@app.get("/admin/list-teams")
def admin_list_teams():
    token = request.headers.get("X-Admin-Token", "")
    if token != os.getenv("ADMIN_TOKEN", ""):
        abort(401)
    with db() as conn:
        rows = conn.execute("SELECT name, join_code FROM teams ORDER BY name ASC").fetchall()
    return {"teams": [dict(r) for r in rows]}
@limiter.limit("30 per hour")
def admin_add_team():
    token = request.headers.get("X-Admin-Token", "")
    if token != os.getenv("ADMIN_TOKEN", ""):
        abort(401)
    payload = request.get_json(force=True)
    name = (payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Teamnaam verplicht"}, 400
    join_code = str(secrets.randbelow(900000) + 100000)
    ttoken = secrets.token_urlsafe(24)
    with db() as conn:
        conn.execute("INSERT INTO teams(name, join_code, token) VALUES(?,?,?)", (name, join_code, ttoken))
    return {"ok": True, "join_code": join_code}
