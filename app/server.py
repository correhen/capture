from __future__ import annotations
import os, time, json, secrets, io, datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from database import db
from models import sha256_hex, DIFFICULTY_POINTS
from challenges import ch

# -------- Config --------
SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24))
RATE_LIMIT_SUBMIT = os.getenv("RATE_LIMIT_SUBMIT", "10 per minute")
RATE_LIMIT_TEAM = os.getenv("RATE_LIMIT_TEAM", "60 per hour")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
BASE_DIR = os.path.dirname(__file__)

app = Flask(__name__)
app.register_blueprint(ch)
app.secret_key = SECRET_KEY
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"])


# -------- DB init/seed + schema upgrades --------
def init_db_if_needed():
    with open(os.path.join(BASE_DIR, "schema.sql"), "r", encoding="utf-8") as f:
        schema_sql = f.read()
    with db() as conn:
        conn.executescript(schema_sql)

        # settings table for theme
        conn.execute("""
        CREATE TABLE IF NOT EXISTS settings(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )""")
        if not conn.execute("SELECT 1 FROM settings WHERE key='theme_c1'").fetchone():
            conn.execute("INSERT INTO settings(key,value) VALUES('theme_c1', '#0d9488')")
        if not conn.execute("SELECT 1 FROM settings WHERE key='theme_c2'").fetchone():
            conn.execute("INSERT INTO settings(key,value) VALUES('theme_c2', '#14b8a6')")

        # Schema uitbreidingen (idempotent)
        try: conn.execute("ALTER TABLE teams ADD COLUMN island TEXT")
        except Exception: pass
        try: conn.execute("ALTER TABLE teams ADD COLUMN logo TEXT")
        except Exception: pass  # niet gebruikt nu, maar kan later handig zijn
        try: conn.execute("ALTER TABLE challenges ADD COLUMN pdf_url TEXT")
        except Exception: pass
        try: conn.execute("ALTER TABLE challenges ADD COLUMN hint TEXT")
        except Exception: pass
        try: conn.execute("ALTER TABLE challenges ADD COLUMN hint_revealed INTEGER DEFAULT 0")
        except Exception: pass

        # Seed alleen als DB leeg is
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
                island = t.get("island")
                cur.execute("INSERT OR IGNORE INTO teams(name, join_code, token, island) VALUES(?,?,?,?)",
                            (name, join_code, token, island))
            for cobj in chals:
                title = cobj["title"].strip()
                diff = cobj["difficulty"].strip()
                points = DIFFICULTY_POINTS.get(diff)
                if not points:
                    raise ValueError(f"Unknown difficulty: {diff}")
                flag_hash = sha256_hex(cobj["flag"].strip())
                active = 1 if cobj.get("active", True) else 0
                pdf_url = cobj.get("pdf_url")
                hint = cobj.get("hint")
                cur.execute(
                    "INSERT OR IGNORE INTO challenges(title, difficulty, flag_hash, points, is_active, pdf_url, hint, hint_revealed) VALUES(?,?,?,?,?,?,?,0)",
                    (title, diff, flag_hash, points, active, pdf_url, hint)
                )

init_db_if_needed()


# -------- Helpers --------
def current_team():
    token = session.get("team_token")
    if not token:
        return None
    with db() as conn:
        cur = conn.execute("SELECT * FROM teams WHERE token = ?", (token,))
        return cur.fetchone()

def admin_logged_in() -> bool:
    return session.get("admin_ok") is True

def get_theme():
    with db() as conn:
        c1 = conn.execute("SELECT value FROM settings WHERE key='theme_c1'").fetchone()["value"]
        c2 = conn.execute("SELECT value FROM settings WHERE key='theme_c2'").fetchone()["value"]
    return {"c1": c1, "c2": c2}


# -------- Public routes --------
@app.get("/")
def home():
    with db() as conn:
        teams = conn.execute("SELECT name FROM teams ORDER BY name ASC").fetchall()
    return render_template("home.html",
                           teams=[t["name"] for t in teams],
                           error=None,
                           theme=get_theme())

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
            teams = [t["name"] for t in conn.execute("SELECT name FROM teams ORDER BY name").fetchall()]
            return render_template("home.html", teams=teams, error="Onjuiste join code of team.", theme=get_theme()), 401
        session["team_token"] = row["token"]
    return redirect(url_for("submit"))

@app.get("/submit")
def submit():
    t = current_team()
    if not t:
        return redirect(url_for("home"))
    with db() as conn:
        team = conn.execute("SELECT * FROM teams WHERE id=?", (t["id"],)).fetchone()
        challenges = conn.execute("""
            SELECT id, title, difficulty, points, pdf_url, hint, hint_revealed
            FROM challenges
            WHERE is_active=1
            ORDER BY id ASC
        """).fetchall()
        solved = {r["challenge_id"] for r in conn.execute(
            "SELECT challenge_id FROM solves WHERE team_id=?", (team["id"],)
        ).fetchall()}
    return render_template("submit.html",
                           team=team,
                           challenges=challenges,
                           solved=solved,
                           theme=get_theme())

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
        chal = conn.execute(
            "SELECT id, title, points, flag_hash FROM challenges WHERE id=? AND is_active=1", (challenge_id,)
        ).fetchone()
        if not chal:
            return jsonify({"ok": False, "error": "Challenge niet gevonden of inactief."}), 404
        if flagh != chal["flag_hash"]:
            return jsonify({"ok": True, "correct": False, "message": "Helaas, dat is niet de juiste flag."})
        existing = conn.execute(
            "SELECT 1 FROM solves WHERE team_id=? AND challenge_id=?", (team["id"], chal["id"])
        ).fetchone()
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
        teams_full = conn.execute("SELECT id, name, score, logo FROM teams ORDER BY score DESC, name ASC").fetchall()
        solves = conn.execute("SELECT team_id, COUNT(*) c FROM solves GROUP BY team_id").fetchall()
        scount = {r["team_id"]: r["c"] for r in solves}
    return render_template("scoreboard.html", teams=teams_full, scount=scount, theme=get_theme())

@app.get("/scoreboard/islands")
def scoreboard_islands():
    with db() as conn:
        rows = conn.execute("""
            SELECT COALESCE(t.island, 'Onbekend') AS island, COUNT(DISTINCT s.challenge_id) AS solved_unique
            FROM solves s
            JOIN teams t ON t.id = s.team_id
            GROUP BY COALESCE(t.island, 'Onbekend')
            ORDER BY solved_unique DESC, island ASC
        """).fetchall()
    return render_template("scoreboard_islands.html", rows=rows, theme=get_theme())

# -------- Health --------
@app.route("/health", methods=["GET", "HEAD"])
@limiter.exempt  # niet limiteren
def health():
    if request.method == "HEAD":
        return "", 200
    return {"status": "ok", "time": int(time.time())}, 200



# -------- Admin APIs (met header-token) --------
@app.post("/admin/activate")
@limiter.limit("30 per hour")
def admin_activate():
    token = request.headers.get("X-Admin-Token", "")
    if token != ADMIN_TOKEN:
        abort(401)
    payload = request.get_json(force=True)
    challenge_id = payload.get("challenge_id")
    active = 1 if bool(payload.get("active", True)) else 0
    with db() as conn:
        conn.execute("UPDATE challenges SET is_active=? WHERE id=?", (active, challenge_id))
    return {"ok": True}

@app.post("/admin/add-team")
@limiter.limit("30 per hour")
def admin_add_team():
    token = request.headers.get("X-Admin-Token", "")
    if token != ADMIN_TOKEN:
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

@app.get("/admin/list-teams")
def admin_list_teams():
    token = request.headers.get("X-Admin-Token", "")
    if token != ADMIN_TOKEN:
        abort(401)
    with db() as conn:
        rows = conn.execute("SELECT name, join_code FROM teams ORDER BY name ASC").fetchall()
    return {"teams": [dict(r) for r in rows]}


# -------- Admin web: login --------
@app.post("/admin/teams/login")
def admin_teams_login():
    token = request.form.get("token", "")
    if token == ADMIN_TOKEN:
        session["admin_ok"] = True
        return redirect("/admin/teams")
    return "Fout token", 401


# -------- Admin web: TEAMS --------
@app.get("/admin/teams")
def admin_teams_page():
    if not admin_logged_in():
        return """
        <form method='post' action='/admin/teams/login' style='max-width:320px;margin:40px auto;font-family:sans-serif'>
            <h2>Admin login</h2>
            <input name='token' placeholder='Admin token' style='width:100%;padding:8px;margin:8px 0' />
            <button style='padding:8px 12px;background:#0d9488;color:#fff;border:none;border-radius:4px'>Inloggen</button>
        </form>
        """

    with db() as conn:
        rows = conn.execute("SELECT name, join_code, island FROM teams ORDER BY name ASC").fetchall()

    last = session.pop("last_join_code", None)
    msg  = session.pop("admin_msg", None)

    last_html = f"""
      <div style='margin:12px 0;padding:10px;background:#ecfeff;border:1px solid #a5f3fc;border-radius:6px'>
        Nieuw team <strong>{last['name']}</strong> — join-code: <strong>{last['join_code']}</strong>
      </div>
    """ if last else ""

    msg_html = f"""
      <div style='margin:12px 0;padding:10px;background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px'>
        {msg}
      </div>
    """ if msg else ""

    row_html = ""
    for r in rows:
        island_val = r["island"] or ""
        row_html += f"""
          <tr>
            <td style='padding:8px 12px'>{r['name']}</td>
            <td style='padding:8px 12px;font-weight:600'>{r['join_code']}</td>
            <td style='padding:8px 12px'>
              <form method="post" action="/admin/teams/delete" onsubmit="return confirm('Team \\'{r['name']}\\' verwijderen? Dit wist ook hun solves.');" style="display:inline-block;margin-right:6px">
                <input type="hidden" name="name" value="{r['name']}"/>
                <button style='padding:6px 10px;background:#ef4444;color:#fff;border:none;border-radius:6px'>Verwijderen</button>
              </form>
              <form method="post" action="/admin/teams/island" style="display:inline-block">
                <input type="hidden" name="name" value="{r['name']}"/>
                <input name="island" value="{island_val}" placeholder="Eiland"
                       style="padding:6px;border:1px solid #cbd5e1;border-radius:6px;width:120px"/>
                <button style='padding:6px 10px;background:#334155;color:#fff;border:none;border-radius:6px'>Opslaan</button>
              </form>
            </td>
          </tr>
        """

    return f"""
    <div style='font-family:sans-serif;max-width:960px;margin:24px auto'>
      <h2 style='text-align:center;margin:10px 0 16px 0'>Teambeheer</h2>
      {msg_html}
      {last_html}

      <div style='display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:8px 0 16px 0'>
        <form method="post" action="/admin/reset-all" onsubmit="return confirm('Weet je zeker dat je ALLE scores en solves wilt wissen?');">
          <button style='padding:8px 12px;background:#0f172a;color:#fff;border:none;border-radius:6px'>Scorebord resetten</button>
        </form>

        <form method="post" action="/admin/teams/add" style='display:flex;gap:8px;align-items:center'>
          <input name="name" placeholder="Nieuw teamnaam" required
                 style='padding:8px;border:1px solid #cbd5e1;border-radius:6px' />
          <button style='padding:8px 12px;background:#0d9488;color:#fff;border:none;border-radius:6px'>Toevoegen</button>
        </form>
      </div>

      <table style='border-collapse:collapse;width:100%;background:#fff;border:1px solid #e2e8f0'>
        <thead style='background:#f1f5f9'>
          <tr>
            <th style='text-align:left;padding:8px 12px'>Team</th>
            <th style='text-align:left;padding:8px 12px'>Join-code</th>
            <th style='text-align:left;padding:8px 12px'>Acties</th>
          </tr>
        </thead>
        <tbody>
          {row_html or "<tr><td colspan='3' style='padding:12px'>Nog geen teams</td></tr>"}
        </tbody>
      </table>

      <hr style='margin:28px 0;border:none;border-top:1px solid #e2e8f0' />
      <p>
        <a href="/admin/challenges">👉 Challenges-beheer</a> &nbsp;•&nbsp;
        <a href="/admin/backup">🗂 Back-up &amp; Restore</a> &nbsp;•&nbsp;
        <a href="/admin/theme">🎨 Thema</a> &nbsp;•&nbsp;
        <a href="/scoreboard/islands" target="_blank">🌴 Eiland-score</a>
      </p>
    </div>
    """

@app.post("/admin/reset-all")
def admin_reset_all():
    if not (admin_logged_in() or request.headers.get("X-Admin-Token") == ADMIN_TOKEN):
        return "Niet ingelogd als admin", 401
    with db() as conn:
        conn.execute("DELETE FROM solves")
        conn.execute("UPDATE teams SET score = 0")
    session["admin_msg"] = "Alle scores en solves zijn gewist."
    return redirect("/admin/teams")

@app.post("/admin/teams/add")
def admin_teams_add_web():
    if not admin_logged_in():
        return "Niet ingelogd als admin", 401
    name = (request.form.get("name") or "").strip()
    if not name:
        session["admin_msg"] = "Teamnaam verplicht."
        return redirect("/admin/teams")

    join_code = str(secrets.randbelow(900000) + 100000)
    ttoken = secrets.token_urlsafe(24)
    try:
        with db() as conn:
            conn.execute("INSERT INTO teams(name, join_code, token) VALUES(?,?,?)", (name, join_code, ttoken))
        session["last_join_code"] = {"name": name, "join_code": join_code}
    except Exception as e:
        session["admin_msg"] = f"Kon team niet toevoegen: {e}"
    return redirect("/admin/teams")

@app.post("/admin/teams/delete")
def admin_teams_delete():
    if not admin_logged_in():
        return "Niet ingelogd als admin", 401
    name = (request.form.get("name") or "").strip()
    if not name:
        session["admin_msg"] = "Teamnaam ontbreekt."
        return redirect("/admin/teams")
    with db() as conn:
        cur = conn.execute("DELETE FROM teams WHERE name = ?", (name,))
        if cur.rowcount == 0:
            session["admin_msg"] = f"Team '{name}' niet gevonden."
        else:
            session["admin_msg"] = f"Team '{name}' verwijderd."
    return redirect("/admin/teams")

@app.post("/admin/teams/island")
def admin_teams_island():
    if not admin_logged_in():
        return "Niet ingelogd als admin", 401
    name   = (request.form.get("name") or "").strip()
    island = (request.form.get("island") or "").strip() or None
    with db() as conn:
        conn.execute("UPDATE teams SET island=? WHERE name=?", (island, name))
    session["admin_msg"] = f"Eiland ingesteld voor {name}."
    return redirect("/admin/teams")


# -------- Admin web: CHALLENGES --------
@app.get("/admin/challenges")
def admin_challenges_page():
    if not admin_logged_in():
        return redirect("/admin/teams")

    with db() as conn:
        rows = conn.execute("""
          SELECT id, title, difficulty, points, is_active, hint_revealed
          FROM challenges
          ORDER BY id ASC
        """).fetchall()

    msg = session.pop("admin_msg", None)
    msg_html = f"""
      <div style='margin:12px 0;padding:10px;background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px'>
        {msg}
      </div>
    """ if msg else ""

    tools_html = """
      <div style='display:flex;gap:12px;flex-wrap:wrap;margin:12px 0 20px 0'>
        <form method="post" action="/admin/cleanup-flags" onsubmit="return confirm('Alle flag-bestanden (flag.txt/flag.sha256) in /static/challenges verwijderen?');">
          <button style='padding:8px 12px;background:#ef4444;color:#fff;border:none;border-radius:6px'>🧹 Cleanup flags in /static</button>
        </form>

        <form method="post" action="/admin/upload-flags" enctype="multipart/form-data" style="display:flex;gap:8px;align-items:center">
          <label style="display:inline-block;padding:8px 12px;background:#0d9488;color:#fff;border-radius:6px;cursor:pointer">
            📤 Upload flags.csv/json
            <input type="file" name="file" accept=".csv,.json" style="display:none" onchange="this.form.submit()">
          </label>
          <span style="color:#64748b;font-size:12px">CSV: &lt;identifier&gt;,&lt;flag&gt; — JSON: {"challenge_mapnaam": "CTF{...}"}</span>
        </form>
      </div>
    """

    row_html = ""
    for r in rows:
        checked = "checked" if r["is_active"] else ""
        hint_btn = "Hint verbergen" if r["hint_revealed"] else "Hint vrijgeven"
        hint_action = "hide" if r["hint_revealed"] else "show"
        row_html += f"""
          <tr>
            <td style='padding:8px 12px'>{r['id']}</td>
            <td style='padding:8px 12px'>{r['title']}</td>
            <td style='padding:8px 12px'>{r['difficulty']} ({r['points']} pt)</td>
            <td style='padding:8px 12px'>
              <form method="post" action="/admin/challenges/toggle" style="display:inline">
                <input type="hidden" name="id" value="{r['id']}"/>
                <label style="display:flex;align-items:center;gap:8px">
                  <input type="checkbox" name="active" value="1" {checked} onchange="this.form.submit()"/>
                  <span>{'Actief' if r['is_active'] else 'Uit'}</span>
                </label>
              </form>
              <form method="post" action="/admin/challenges/hint" style="display:inline;margin-left:8px">
                <input type="hidden" name="id" value="{r['id']}"/>
                <input type="hidden" name="action" value="{hint_action}"/>
                <button style='padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px'>{hint_btn}</button>
              </form>
            </td>
          </tr>
        """

    diff_options = "".join(
        f"<option value='{k}'>{k} ({v} pt)</option>" for k, v in DIFFICULTY_POINTS.items()
    )

    return f"""
    <div style='font-family:sans-serif;max-width:960px;margin:24px auto'>
      <h2 style='text-align:center;margin:10px 0 16px 0'>Challenges</h2>
      {msg_html}
      {tools_html}

      <table style='border-collapse:collapse;width:100%;background:#fff;border:1px solid #e2e8f0;margin-bottom:20px'>
        <thead style='background:#f1f5f9'>
          <tr>
            <th style='text-align:left;padding:8px 12px'>ID</th>
            <th style='text-align:left;padding:8px 12px'>Titel</th>
            <th style='text-align:left;padding:8px 12px'>Moeilijkheid (pt)</th>
            <th style='text-align:left;padding:8px 12px'>Status + Hint</th>
          </tr>
        </thead>
        <tbody>
          {row_html or "<tr><td colspan='4' style='padding:12px'>Nog geen challenges</td></tr>"}
        </tbody>
      </table>

      <h3>Nieuwe challenge toevoegen</h3>
      <form method="post" action="/admin/challenges/add" style='display:grid;grid-template-columns:1fr 160px 1fr 1fr auto;gap:8px;align-items:center'>
        <input name="title" placeholder="Titel" required style='padding:8px;border:1px solid #cbd5e1;border-radius:6px' />
        <select name="difficulty" required style='padding:8px;border:1px solid #cbd5e1;border-radius:6px'>
          {diff_options}
        </select>
        <input name="flag" placeholder="CTF{{...}}" required style='padding:8px;border:1px solid #cbd5e1;border-radius:6px' />
        <input name="pdf_url" placeholder="PDF URL (optioneel)" style='padding:8px;border:1px solid #cbd5e1;border-radius:6px' />
        <input name="hint" placeholder="Tip/hint (optioneel)" style='padding:8px;border:1px solid #cbd5e1;border-radius:6px' />
        <label style='display:flex;gap:6px;align-items:center;'>
          <input type="checkbox" name="active" value="1" checked />
          Actief
        </label>
        <button style='padding:8px 12px;background:#0d9488;color:#fff;border:none;border-radius:6px;grid-column:1/-1;justify-self:start'>Toevoegen</button>
      </form>

      <p style='margin-top:16px'>
        <a href="/admin/teams">← Terug naar Teambeheer</a> &nbsp;•&nbsp;
        <a href="/admin/backup">🗂 Back-up &amp; Restore</a>
      </p>
    </div>
    """

@app.post("/admin/challenges/toggle")
def admin_challenges_toggle():
    if not admin_logged_in():
        return "Niet ingelogd als admin", 401
    cid = request.form.get("id")
    is_active = 1 if request.form.get("active") == "1" else 0
    with db() as conn:
        conn.execute("UPDATE challenges SET is_active=? WHERE id=?", (is_active, cid))
    session["admin_msg"] = f"Challenge {cid} {'geactiveerd' if is_active else 'uitgeschakeld'}."
    return redirect("/admin/challenges")

@app.post("/admin/challenges/hint")
def admin_challenges_hint():
    if not admin_logged_in():
        return "Niet ingelogd als admin", 401
    cid = request.form.get("id")
    action = request.form.get("action","show")
    val = 0 if action == "hide" else 1
    with db() as conn:
        conn.execute("UPDATE challenges SET hint_revealed=? WHERE id=?", (val, cid))
    session["admin_msg"] = f"Hint {'vrijgegeven' if val else 'verborgen'} voor challenge {cid}."
    return redirect("/admin/challenges")

@app.post("/admin/challenges/add")
def admin_challenges_add():
    if not admin_logged_in():
        return "Niet ingelogd als admin", 401
    title = (request.form.get("title") or "").strip()
    difficulty = (request.form.get("difficulty") or "").strip()
    flag = (request.form.get("flag") or "").strip()
    pdf_url = (request.form.get("pdf_url") or "").strip()
    hint = (request.form.get("hint") or "").strip()
    is_active = 1 if request.form.get("active") == "1" else 0

    if not title or not difficulty or not flag:
        session["admin_msg"] = "Titel, difficulty en flag zijn verplicht."
        return redirect("/admin/challenges")
    if difficulty not in DIFFICULTY_POINTS:
        session["admin_msg"] = f"Onbekende difficulty: {difficulty}"
        return redirect("/admin/challenges")
    if not (flag.startswith("CTF{") and flag.endswith("}")):
        session["admin_msg"] = "Flag moet de vorm CTF{...} hebben."
        return redirect("/admin/challenges")

    points = DIFFICULTY_POINTS[difficulty]
    fhash = sha256_hex(flag)
    with db() as conn:
        conn.execute(
            "INSERT INTO challenges(title, difficulty, flag_hash, points, is_active, pdf_url, hint, hint_revealed) VALUES(?,?,?,?,?,?,?,0)",
            (title, difficulty, fhash, points, is_active, (pdf_url or None), (hint or None))
        )
    session["admin_msg"] = f"Challenge '{title}' toegevoegd."
    return redirect("/admin/challenges")


# -------- Admin web: BACKUP / RESTORE --------
@app.get("/admin/backup")
def admin_backup_page():
    if not admin_logged_in():
        return redirect("/admin/teams")
    msg  = session.pop("admin_msg", None)
    msg_html = f"""
      <div style='margin:12px 0;padding:10px;background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px'>
        {msg}
      </div>
    """ if msg else ""

    return f"""
    <div style='font-family:sans-serif;max-width:760px;margin:24px auto'>
      <h2 style='text-align:center;margin:10px 0 16px 0'>Back-up &amp; Restore</h2>
      {msg_html}

      <div style='display:flex;gap:12px;flex-wrap:wrap;margin:12px 0 24px 0'>
        <form method="get" action="/admin/backup/export">
          <button style='padding:8px 12px;background:#0d9488;color:#fff;border:none;border-radius:6px'>⬇️ Exporteren (JSON)</button>
        </form>
      </div>

      <h3>Restore (JSON importeren)</h3>
      <form method="post" action="/admin/backup/import" enctype="multipart/form-data" style='display:flex;gap:8px;align-items:center;margin-top:8px'>
        <input type="file" name="file" accept="application/json" required
               style='flex:1;padding:8px;border:1px solid #cbd5e1;border-radius:6px;background:#fff' />
        <label style='display:flex;gap:6px;align-items:center;'>
          <input type="checkbox" name="replace" value="1" checked />
          Bestaande data eerst wissen (aanbevolen)
        </label>
        <button style='padding:8px 12px;background:#0f172a;color:#fff;border:none;border-radius:6px'>⬆️ Importeren</button>
      </form>

      <p style='color:#64748b;font-size:12px;margin-top:6px'>
        Tip: gebruik alleen exports van deze CTF-app (zelfde database-structuur).
      </p>

      <p style='margin-top:16px'>
        <a href="/admin/teams">← Terug naar Teambeheer</a> &nbsp;•&nbsp;
        <a href="/admin/challenges">Challenges</a>
      </p>
    </div>
    """

@app.get("/admin/backup/export")
def admin_backup_export():
    if not (admin_logged_in() or request.headers.get("X-Admin-Token") == ADMIN_TOKEN):
        return "Niet ingelogd als admin", 401

    with db() as conn:
        teams = [dict(r) for r in conn.execute("SELECT id, name, join_code, token, score, island FROM teams ORDER BY id").fetchall()]
        chals = [dict(r) for r in conn.execute("SELECT id, title, difficulty, flag_hash, points, is_active, pdf_url, hint, hint_revealed FROM challenges ORDER BY id").fetchall()]
        solves = [dict(r) for r in conn.execute("SELECT id, team_id, challenge_id FROM solves ORDER BY id").fetchall()]

    payload = {
        "meta": {"version": 2, "exported_at": datetime.datetime.utcnow().isoformat() + "Z"},
        "teams": teams,
        "challenges": chals,
        "solves": solves
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    buf = io.BytesIO(data)
    fname = "ctf-backup-" + datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S") + ".json"
    return send_file(buf, mimetype="application/json", as_attachment=True, download_name=fname)

@app.post("/admin/backup/import")
def admin_backup_import():
    if not admin_logged_in():
        return "Niet ingelogd als admin", 401
    f = request.files.get("file")
    if not f:
        session["admin_msg"] = "Geen bestand gekozen."
        return redirect("/admin/backup")

    try:
        payload = json.loads(f.read().decode("utf-8"))
    except Exception as e:
        session["admin_msg"] = f"Kon JSON niet lezen: {e}"
        return redirect("/admin/backup")

    teams = payload.get("teams", [])
    chals = payload.get("challenges", [])
    solves = payload.get("solves", [])
    replace = request.form.get("replace") == "1"

    try:
        with db() as conn:
            cur = conn.cursor()
            if replace:
                cur.execute("DELETE FROM solves")
                cur.execute("DELETE FROM teams")
                cur.execute("DELETE FROM challenges")
            for t in teams:
                cur.execute(
                    "INSERT OR REPLACE INTO teams(id, name, join_code, token, score, island) VALUES(?,?,?,?,?,?)",
                    (t.get("id"), t.get("name"), t.get("join_code"), t.get("token"), t.get("score", 0), t.get("island"))
                )
            for c in chals:
                cur.execute(
                    "INSERT OR REPLACE INTO challenges(id, title, difficulty, flag_hash, points, is_active, pdf_url, hint, hint_revealed) VALUES(?,?,?,?,?,?,?,?,?)",
                    (c.get("id"), c.get("title"), c.get("difficulty"), c.get("flag_hash"), c.get("points"),
                     c.get("is_active", 1), c.get("pdf_url"), c.get("hint"), c.get("hint_revealed", 0))
                )
            for s in solves:
                cur.execute(
                    "INSERT OR REPLACE INTO solves(id, team_id, challenge_id) VALUES(?,?,?)",
                    (s.get("id"), s.get("team_id"), s.get("challenge_id"))
                )
        session["admin_msg"] = "Import voltooid."
    except Exception as e:
        session["admin_msg"] = f"Import mislukt: {e}"

    return redirect("/admin/backup")


# -------- Admin web: THEME --------
@app.get("/admin/theme")
def admin_theme_page():
    if not admin_logged_in():
        return redirect("/admin/teams")
    th = get_theme()
    return f"""
    <div style='font-family:sans-serif;max-width:640px;margin:24px auto'>
      <h2>Thema-kleuren</h2>
      <form method="post" action="/admin/theme" style="display:grid;grid-template-columns:1fr 1fr auto;gap:10px;align-items:end">
        <label>Primair<br><input type="color" name="c1" value="{th['c1']}" style="width:100%;height:42px;border:1px solid #cbd5e1;border-radius:6px"></label>
        <label>Secundair (gradient)<br><input type="color" name="c2" value="{th['c2']}" style="width:100%;height:42px;border:1px solid #cbd5e1;border-radius:6px"></label>
        <button style="padding:10px 14px;background:{th['c1']};color:#fff;border:none;border-radius:8px">Opslaan</button>
      </form>

      <div style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap">
        <form method="post" action="/admin/theme">
          <input type="hidden" name="c1" value="#0d9488"><input type="hidden" name="c2" value="#14b8a6">
          <button style="padding:8px 12px;border:1px solid #cbd5e1;border-radius:8px">Preset: Teal</button>
        </form>
        <form method="post" action="/admin/theme">
          <input type="hidden" name="c1" value="#2563eb"><input type="hidden" name="c2" value="#06b6d4">
          <button style="padding:8px 12px;border:1px solid #cbd5e1;border-radius:8px">Preset: Blauw</button>
        </form>
        <form method="post" action="/admin/theme">
          <input type="hidden" name="c1" value="#f59e0b"><input type="hidden" name="c2" value="#f43f5e">
          <button style="padding:8px 12px;border:1px solid #cbd5e1;border-radius:8px">Preset: Sunset</button>
        </form>
        <form method="post" action="/admin/theme">
          <input type="hidden" name="c1" value="#10b981"><input type="hidden" name="c2" value="#84cc16">
          <button style="padding:8px 12px;border:1px solid #cbd5e1;border-radius:8px">Preset: Lime</button>
        </form>
      </div>

      <p style='margin-top:16px'><a href="/admin/teams">← Terug</a></p>
    </div>
    """

@app.post("/admin/theme")
def admin_theme_save():
    if not admin_logged_in():
        return "Niet ingelogd als admin", 401
    c1 = (request.form.get("c1") or "#0d9488").strip()
    c2 = (request.form.get("c2") or "#14b8a6").strip()
    for c in [c1, c2]:
        if not (len(c) in (4,7) and c.startswith("#")):
            return "Ongeldige kleur", 400
    with db() as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES('theme_c1',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (c1,))
        conn.execute("INSERT INTO settings(key,value) VALUES('theme_c2',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (c2,))
    return redirect("/admin/theme")

@app.post("/admin/upload-flags")
def admin_upload_flags():
    if not admin_logged_in():
        return "Niet ingelogd als admin", 401

    f = request.files.get("file")
    if not f or not f.filename:
        session["admin_msg"] = "Geen bestand geüpload."
        return redirect("/admin/challenges")

    import io as _io, csv, json as _json, hashlib
    from pathlib import Path

    def _sha256_hex(s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    text = f.read().decode("utf-8", errors="replace")
    mapping = {}

    # Detecteer JSON of CSV
    if f.filename.lower().endswith(".json") or text.strip().startswith("{"):
        try:
            mapping = _json.loads(text)
        except Exception:
            session["admin_msg"] = "Ongeldige JSON."
            return redirect("/admin/challenges")
    else:
        rdr = csv.reader(_io.StringIO(text))
        for row in rdr:
            if len(row) >= 2:
                ident, flag = row[0].strip(), row[1].strip()
                if ident and flag:
                    mapping[ident] = flag

    if not mapping:
        session["admin_msg"] = "Geen geldige regels gevonden."
        return redirect("/admin/challenges")

    # challenge-mappen scannen
    CHALL_ROOT = Path(__file__).resolve().parent / "static" / "challenges"
    DIFF_MAP = {"1 - Easy": "makkelijk", "2 - Medium": "gemiddeld", "3 - Hard": "moeilijk"}
    POINTS   = {"makkelijk": 1, "gemiddeld": 2, "moeilijk": 3}

    def list_dirs():
        out = []
        for level in DIFF_MAP:
            base = CHALL_ROOT / level
            if base.exists():
                out += [p for p in base.iterdir() if p.is_dir()]
        return out

    dirs = list_dirs()

    def match_identifier(identifier: str):
        low = identifier.lower()
        # exact mapnaam
        for d in dirs:
            if d.name.lower() == low:
                return d
            # PDF-bestandsnaam matchen
            for p in d.glob("*.pdf"):
                if p.stem.lower() == low:
                    return d
        # fuzzy (substring)
        for d in dirs:
            if low in d.name.lower():
                return d
        return None

    updated_db, unmatched = 0, 0
    with db() as conn:
        for ident, flag in mapping.items():
            d = match_identifier(ident)
            if not d:
                unmatched += 1
                continue

            fh   = _sha256_hex(flag)
            diff = DIFF_MAP.get(d.parent.name, "makkelijk")
            pts  = POINTS.get(diff, 1)

            # match op titel == mapnaam (case-insensitive)
            row = conn.execute(
                "SELECT id FROM challenges WHERE LOWER(title)=LOWER(?)",
                (d.name.lower(),)
            ).fetchone()

            if row:
                conn.execute(
                    "UPDATE challenges SET difficulty=?, points=?, flag_hash=?, is_active=1 WHERE id=?",
                    (diff, pts, fh, row["id"])
                )
            else:
                conn.execute(
                    "INSERT INTO challenges(title, difficulty, flag_hash, points, is_active) VALUES(?,?,?,?,1)",
                    (d.name, diff, fh, pts)
                )
            updated_db += 1

    session["admin_msg"] = f"Flags verwerkt: {updated_db} challenges bijgewerkt, {unmatched} niet gematcht."
    return redirect("/admin/challenges")

@app.post("/admin/cleanup-flags")
def admin_cleanup_flags():
    if not admin_logged_in():
        return "Niet ingelogd als admin", 401

    from pathlib import Path
    CHALL_ROOT = Path(__file__).resolve().parent / "static" / "challenges"
    removed = 0
    for p in CHALL_ROOT.rglob("*"):
        if p.is_file() and p.name.lower() in {"flag.txt", "flag.sha256"}:
            try:
                p.unlink()
                removed += 1
            except Exception:
                pass

    session["admin_msg"] = f"Cleanup klaar — {removed} flag-bestanden verwijderd."
    return redirect("/admin/challenges")
