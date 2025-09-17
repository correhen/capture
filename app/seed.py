from __future__ import annotations
import json, os, secrets
from database import db
from models import DIFFICULTY_POINTS, sha256_hex

TEAM_FILE = os.path.join(os.path.dirname(__file__), "seed_teams.json")
CHAL_FILE = os.path.join(os.path.dirname(__file__), "seed_challenges.json")

with db() as conn:
    cur = conn.cursor()
    with open(os.path.join(os.path.dirname(__file__), "schema.sql"), "r", encoding="utf-8") as f:
        cur.executescript(f.read())

    teams = json.load(open(TEAM_FILE, "r", encoding="utf-8")) if os.path.exists(TEAM_FILE) else []
    for t in teams:
        name = t["name"].strip()
        token = secrets.token_urlsafe(24)
        join_code = str(secrets.randbelow(900000) + 100000)
        cur.execute("INSERT OR IGNORE INTO teams(name, join_code, token) VALUES(?,?,?)", (name, join_code, token))

    chals = json.load(open(CHAL_FILE, "r", encoding="utf-8")) if os.path.exists(CHAL_FILE) else []
    for c in chals:
        title = c["title"].strip()
        diff = c["difficulty"].strip()
        points = DIFFICULTY_POINTS.get(diff)
        if not points:
            raise ValueError(f"Unknown difficulty: {diff}")
        flag_hash = sha256_hex(c["flag"].strip())
        active = 1 if c.get("active", True) else 0
        cur.execute(
            "INSERT OR IGNORE INTO challenges(title, difficulty, flag_hash, points, is_active) VALUES(?,?,?,?,?)",
            (title, diff, flag_hash, points, active)
        )

print("Seeded teams and challenges.")
