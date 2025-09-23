#!/usr/bin/env python3
"""
import_flags.py

Gebruik:
  - Zet een CSV bestand `flags.csv` of JSON bestand `flags.json` naast dit script (in app/).
  - CSV formaat: identifier,flag
    Voorbeeld:
      "CTF02 - Exchanges","CTF{exchanges-flag}"
      "CTF40 - Meme","CTF{meme-flag}"
  - JSON formaat:
      { "CTF02 - Exchanges": "CTF{exchanges-flag}", "CTF40 - Meme": "CTF{meme-flag}" }

Wat het doet:
  - Scant ./static/challenges voor challenge-mappen.
  - Voor iedere identifier uit de mapping:
      * maakt/overschrijft een flag.txt bestand in de challenge-map
      * zet de SHA256-hash van de flag in de SQLite database
      * zet challenge actief
  - Run met --no-db om alleen flag.txt te schrijven
  - Run met --dry-run om alleen te laten zien wat er zou gebeuren
"""

import argparse, csv, json, os, hashlib, sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CHALL_ROOT = BASE_DIR / "static" / "challenges"
DB_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "ctf.sqlite"))

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def load_mapping(path: Path):
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    # anders CSV
    mapping = {}
    with path.open("r", encoding="utf-8") as f:
        rdr = csv.reader(f)
        for row in rdr:
            if len(row) >= 2:
                mapping[row[0].strip()] = row[1].strip()
    return mapping

def list_challenge_dirs():
    out = []
    for level in ["1 - Easy", "2 - Medium", "3 - Hard"]:
        base = CHALL_ROOT / level
        if base.exists():
            out += [p for p in base.iterdir() if p.is_dir()]
    return out

def match_identifier(identifier: str, dirs):
    # exact map
    for d in dirs:
        if d.name == identifier:
            return d
    # case-insensitive map
    for d in dirs:
        if d.name.lower() == identifier.lower():
            return d
    # pdf-stem exact
    low = identifier.lower()
    for d in dirs:
        for p in d.glob("*.pdf"):
            if p.stem.lower() == low:
                return d
    # substring in mapnaam
    for d in dirs:
        if low in d.name.lower():
            return d
    return None

def ensure_db(conn):
    conn.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS teams (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT UNIQUE NOT NULL,
      join_code TEXT UNIQUE NOT NULL,
      token TEXT UNIQUE NOT NULL,
      score INTEGER NOT NULL DEFAULT 0,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS challenges (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      difficulty TEXT,
      flag_hash TEXT NOT NULL,
      points INTEGER NOT NULL,
      is_active INTEGER NOT NULL DEFAULT 1,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()

DIFF_MAP = {"1 - Easy":"makkelijk","2 - Medium":"gemiddeld","3 - Hard":"moeilijk"}
POINTS = {"makkelijk":1,"gemiddeld":2,"moeilijk":3}

def upsert_challenge(conn, title, difficulty, flag_hash, points):
    cur = conn.cursor()
    row = cur.execute("SELECT id FROM challenges WHERE LOWER(title)=LOWER(?)", (title.lower(),)).fetchone()
    if row:
        cur.execute("UPDATE challenges SET difficulty=?, points=?, flag_hash=?, is_active=1 WHERE id=?",
                    (difficulty, points, flag_hash, row[0]))
    else:
        cur.execute("INSERT INTO challenges(title, difficulty, flag_hash, points, is_active) VALUES(?,?,?,?,1)",
                    (title, difficulty, flag_hash, points))
    conn.commit()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mapping", nargs="?", default="flags.csv")
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    mapping = load_mapping(BASE_DIR / args.mapping)
    dirs = list_challenge_dirs()
    if not dirs:
        print("Geen challenge mappen gevonden in", CHALL_ROOT)
        return

    to_update = []
    for ident, flag in mapping.items():
        d = match_identifier(ident, dirs)
        if not d:
            print(f"[WARN] Geen match voor {ident}")
            continue
        flag_path = d / "flag.txt"
        if args.dry_run:
            print(f"[DRY] Zou {flag_path} schrijven met {flag}")
        else:
            flag_path.write_text(flag + "\n", encoding="utf-8")
            print(f"[OK] flag.txt geschreven in {flag_path}")
        fh = sha256_hex(flag)
        diff = DIFF_MAP.get(d.parent.name, "makkelijk")
        pts = POINTS[diff]
        to_update.append((d.name, diff, fh, pts))

    if args.no_db or args.dry_run or not to_update:
        print("DB niet aangepast (--no-db of dry-run).")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_db(conn)
        for title, diff, fh, pts in to_update:
            upsert_challenge(conn, title, diff, fh, pts)
            print(f"[DB] Challenge {title} bijgewerkt/toegevoegd")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
