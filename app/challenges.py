# app/challenges.py
from flask import Blueprint, render_template, send_from_directory, send_file, abort, session, redirect, url_for, flash
from pathlib import Path
import unicodedata, re, io, zipfile

ch_bp = Blueprint("ch", __name__)

ROOT = Path(__file__).resolve().parent / "static" / "challenges"
ALLOWED = {".pdf", ".txt", ".zip", ".pcap", ".pcapng", ".json", ".png", ".jpg", ".jpeg"}
LEVEL_DIRS = {"1 - Easy": "Easy", "2 - Medium": "Medium", "3 - Hard": "Hard"}

# ====== AUTH: alleen teams die 'ingelogd' zijn ======
def is_team_logged_in() -> bool:
    return bool(session.get("team_token"))

@ch_bp.before_request
def require_team_login():
    # Alles onder deze blueprint vereist login
    if not is_team_logged_in():
        flash("Log in met je team om de challenges te bekijken.", "warning")
        # Pas '/join' aan naar jouw join-pagina/route
        return redirect(url_for("join"))  # bv. 'join' of 'home' afhankelijk van jouw app

# ====== helpers ======
def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "challenge"

def scan_structure():
    out = {}
    for level_dir, label in LEVEL_DIRS.items():
        p = ROOT / level_dir
        if not p.is_dir():
            continue
        level_slug = slugify(label)
        out[level_slug] = {"label": label, "challenges": []}
        for d in sorted(p.iterdir()):
            if d.is_dir():
                out[level_slug]["challenges"].append({
                    "id": slugify(d.name),
                    "title": d.name,
                    "path": d
                })
    return out

def find_challenge(cid: str):
    data = scan_structure()
    for lvl in data.values():
        for c in lvl["challenges"]:
            if c["id"] == cid:
                return c
    return None

def list_files_recursive(ch_path: Path):
    files = []
    for p in ch_path.rglob("*"):
        if p.is_file() and p.suffix.lower() in ALLOWED:
            files.append((p.relative_to(ch_path).as_posix(), p))
    files.sort()
    return files

# ====== routes ======
@ch_bp.route("/challenges")
def challenges_index():
    data = scan_structure()
    return render_template("challenges.html", data=data)

@ch_bp.route("/challenge/<cid>")
def challenge_detail(cid):
    ch = find_challenge(cid)
    if not ch:
        abort(404)
    files = [{"rel": rel, "name": Path(rel).name} for rel, _ in list_files_recursive(ch["path"])]
    if not files:
        abort(404)
    return render_template("challenge_detail.html", c=ch, files=files)

@ch_bp.route("/download/<cid>/<path:relpath>")
def challenge_download(cid, relpath):
    ch = find_challenge(cid)
    if not ch:
        abort(404)
    base = ch["path"].resolve()
    file_path = (base / relpath).resolve()
    if base not in file_path.parents or not file_path.exists() or file_path.suffix.lower() not in ALLOWED:
        abort(404)
    return send_from_directory(base, relpath, as_attachment=True)

@ch_bp.route("/download-bundle/<cid>")
def challenge_bundle(cid):
    ch = find_challenge(cid)
    if not ch:
        abort(404)
    items = list_files_recursive(ch["path"])
    if not items:
        abort(404)
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, p in items:
            zf.write(p, arcname=rel)
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name=f"{slugify(ch['title'])}.zip")
