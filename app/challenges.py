# app/challenges.py
from __future__ import annotations
import html, io, re, zipfile, unicodedata
from pathlib import Path
from typing import Iterable, List, Tuple, Optional, Dict

import markdown
from markupsafe import Markup
from flask import (
    Blueprint, abort, send_from_directory, send_file,
    session, redirect, url_for, render_template, request
)
from database import db  # voor thema-kleuren uit settings

# --------------------------------- #
# Blueprint
# --------------------------------- #
# Belangrijk: jouw templates gebruiken 'ch' als blueprint-naam
ch = Blueprint("ch", __name__, url_prefix="")

# Pad naar de challenges-root
CHALL_ROOT = Path(__file__).resolve().parent / "static" / "challenges"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a"}
VIDEO_EXTS = {".mp4", ".webm", ".mov"}
TEXT_EXTS = {".txt", ".md"}

# Level-mappen die we proberen te groeperen (val terug als ze niet bestaan)
LEVEL_DIRS = [
    "1 - Easy",
    "2 - Medium",
    "3 - Hard",
]

LEVEL_LABELS = {
    "1 - Easy": "Makkelijk",
    "2 - Medium": "Gemiddeld",
    "3 - Hard": "Moeilijk",
}

# --------------------------------- #
# Helpers
# --------------------------------- #

def is_team_logged_in() -> bool:
    return _current_team_row() is not None

def login_required_redirect():
    return redirect(url_for("home", login_required="challenges"))

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-")
    return text.lower()

def _is_sensitive_file(p: Path) -> bool:
    """Bestanden die NOOIT publiek/download mee mogen."""
    name = p.name.lower()
    if name in {"flag.txt", "flag.sha256"}:
        return True
    if name.startswith("flag.") or name.endswith(".flag"):
        return True
    if p.stem.lower() == "flag":
        return True
    return False

def _is_hidden_or_tech(p: Path) -> bool:
    """Folders/bestanden die we niet willen serveren."""
    bad = {".git", "__pycache__", ".ds_store"}
    return any(part.lower() in bad for part in p.parts)

def list_files_recursive(root: Path) -> List[Tuple[str, Path]]:
    """Geef alle bestanden terug als (relatief_pad, absolute_path)."""
    out: List[Tuple[str, Path]] = []
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if _is_hidden_or_tech(p):
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        out.append((rel, p))
    return out

def render_challenge_markdown(text: str) -> Markup:
    escaped = html.escape(text or "")
    rendered = markdown.markdown(
        escaped,
        extensions=["extra", "sane_lists"],
        output_format="html",
    )
    rendered = re.sub(r"<img\b[^>]*>", "", rendered, flags=re.IGNORECASE)

    def safe_href(match):
        url = html.unescape(match.group(1)).strip()
        if re.match(r"^(https?://|mailto:|/|#)", url, re.IGNORECASE):
            return f'href="{html.escape(url, quote=True)}"'
        return 'href="#"'

    rendered = re.sub(r'href="([^"]*)"', safe_href, rendered, flags=re.IGNORECASE)
    return Markup(rendered)

def file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    if ext == ".zip":
        return "zip"
    return "file"

def is_challenge_markdown(rel: str, path: Path) -> bool:
    return path.name.lower() == "challenge.md" and "/" not in rel

def file_item(challenge_slug: str, rel: str, path: Path) -> Dict[str, object]:
    kind = file_kind(path)
    return {
        "name": path.name,
        "rel": rel,
        "kind": kind,
        "is_image": kind == "image",
        "is_audio": kind == "audio",
        "is_video": kind == "video",
        "is_text": kind == "text",
        "download_url": url_for("ch.challenge_download", cid=challenge_slug, relpath=rel),
        "view_url": url_for("ch.challenge_asset", cid=challenge_slug, relpath=rel),
    }

def challenge_assets(chobj: Dict[str, object]) -> Dict[str, object]:
    slug = chobj["slug"]
    root = Path(chobj["path"])
    markdown_path = root / "challenge.md"
    markdown_html = None
    has_markdown = markdown_path.is_file() and not _is_sensitive_file(markdown_path)
    if has_markdown:
        markdown_html = render_challenge_markdown(markdown_path.read_text(encoding="utf-8"))

    pdfs = []
    attachments = []
    inline_images = []
    for rel, path in list_files_recursive(root):
        if _is_sensitive_file(path) or is_challenge_markdown(rel, path):
            continue
        item = file_item(slug, rel, path)
        if item["kind"] == "pdf":
            pdfs.append(item)
        else:
            attachments.append(item)
            if item["kind"] == "image":
                inline_images.append(item)

    return {
        "has_markdown": has_markdown,
        "markdown_html": markdown_html,
        "pdfs": pdfs,
        "attachments": attachments,
        "inline_images": inline_images,
        "files_count": len(pdfs) + len(attachments) + (1 if has_markdown else 0),
        "has_files": bool(pdfs or attachments or has_markdown),
    }

def _iter_challenge_dirs() -> Iterable[Path]:
    """Doorloop alle challenge-mappen (één niveau onder elk 'LEVEL_DIRS'-mapje).
       Valt terug op alle submappen als LEVEL_DIRS niet bestaat of leeg is."""
    if not CHALL_ROOT.exists():
        return []
    used = False
    for level in LEVEL_DIRS:
        base = CHALL_ROOT / level
        if base.exists():
            used = True
            for d in base.iterdir():
                if d.is_dir():
                    yield d
    if not used:
        for d in CHALL_ROOT.iterdir():
            if d.is_dir():
                yield d

def get_all_challenges() -> List[Dict[str, object]]:
    """Return lijst met challenges: {'title': str, 'path': Path, 'slug': str}"""
    items: List[Dict[str, object]] = []
    for d in _iter_challenge_dirs():
        title = d.name
        items.append({"title": title, "path": d, "slug": slugify(title)})
    return items

def find_challenge(cid: str) -> Optional[Dict[str, object]]:
    """Zoek challenge op mapnaam (case-insensitief), slug, of PDF-stem."""
    cid_low = (cid or "").strip().lower()
    for chobj in get_all_challenges():
        name_low = chobj["title"].lower()
        if cid_low == name_low or cid_low == chobj["slug"]:
            return chobj

    # Probeer PDF-stem match (handig voor deeplinks)
    for chobj in get_all_challenges():
        for p in Path(chobj["path"]).glob("*.pdf"):
            if p.stem.lower() == cid_low:
                return chobj

    # Substring fallback op naam
    for chobj in get_all_challenges():
        if cid_low in chobj["title"].lower():
            return chobj

    return None

def secure_join(base: Path, rel: str) -> Optional[Path]:
    """
    Veilig samenvoegen van base + rel zonder directory traversal.
    Retourneert None als het buiten base valt.
    """
    if rel is None:
        return None
    rel = rel.replace("\\", "/").lstrip("/")
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except Exception:
        return None
    return target

def get_theme():
    with db() as conn:
        c1 = conn.execute("SELECT value FROM settings WHERE key='theme_c1'").fetchone()["value"]
        c2 = conn.execute("SELECT value FROM settings WHERE key='theme_c2'").fetchone()["value"]
    return {"c1": c1, "c2": c2}

def _current_team_row():
    token = session.get("team_token")
    if not token:
        return None
    with db() as conn:
        return conn.execute("SELECT * FROM teams WHERE token=?", (token,)).fetchone()

# --------------------------------- #
# Routes
# --------------------------------- #

@ch.route("/challenges")
def challenges_index():
    """
    Overzichtspagina die jouw templates/challenges.html gebruikt.
    Data structuur:
      data[level_key] = {
        'label': 'Makkelijk/Gemiddeld/Moeilijk' of mapnaam,
        'challenges': [ { 'id': slug, 'title': title } ... ]
      }
    """
    team = _current_team_row()
    with db() as conn:
        rows = conn.execute("""
            SELECT id, title, difficulty, points, pdf_url, hint, hint_revealed
            FROM challenges
            WHERE is_active=1
            ORDER BY
              CASE difficulty
                WHEN 'makkelijk' THEN 1
                WHEN 'gemiddeld' THEN 2
                WHEN 'moeilijk' THEN 3
                ELSE 4
              END,
              title ASC
        """).fetchall()
        solved = {
            r["challenge_id"]
            for r in conn.execute(
                "SELECT challenge_id FROM solves WHERE team_id=?",
                (team["id"],)
            ).fetchall()
        } if team else set()

    challenges = []
    for row in rows:
        slug = slugify(row["title"])
        folder = find_challenge(row["title"]) or find_challenge(slug)
        assets = challenge_assets(folder) if folder else {"pdfs": [], "attachments": [], "has_files": False}
        challenges.append({
            "id": row["id"],
            "slug": slug,
            "title": row["title"],
            "difficulty": row["difficulty"],
            "points": row["points"],
            "pdf_url": row["pdf_url"],
            "hint": row["hint"],
            "hint_revealed": row["hint_revealed"],
            "solved": row["id"] in solved,
            "has_files": bool(folder),
            "has_pdf": bool(assets["pdfs"]),
            "has_attachments": bool(assets["attachments"]),
        })

    return render_template(
        "challenges.html",
        challenges=challenges,
        team_logged_in=team is not None,
        theme=get_theme(),
    )

@ch.route("/challenge/<cid>")
def challenge_detail(cid: str):
    """
    Detailpagina die jouw template challenge_detail.html gebruikt:
    - context 'c' met { id, title }
    - context 'files' met lijst items { name, rel }
    """
    if not is_team_logged_in():
        return login_required_redirect()

    chobj = find_challenge(cid)
    if not chobj:
        abort(404)

    db_challenge = None
    solved = False
    with db() as conn:
        db_challenge = conn.execute(
            "SELECT id, difficulty, points FROM challenges WHERE LOWER(title)=LOWER(?) AND is_active=1",
            (chobj["title"],)
        ).fetchone()
        if db_challenge:
            team = _current_team_row()
            solved = conn.execute(
                "SELECT 1 FROM solves WHERE team_id=? AND challenge_id=?",
                (team["id"], db_challenge["id"])
            ).fetchone() is not None

    assets = challenge_assets(chobj)
    return render_template(
        "challenge_detail.html",
        c={
            "id": chobj["slug"],
            "title": chobj["title"],
            "db_id": db_challenge["id"] if db_challenge else None,
            "difficulty": db_challenge["difficulty"] if db_challenge else None,
            "points": db_challenge["points"] if db_challenge else None,
            "solved": solved,
        },
        assets=assets,
        theme=get_theme(),
    )

@ch.route("/challenge/<cid>/file/<path:relpath>")
def challenge_download(cid: str, relpath: str):
    """
    Download een enkel bestand behorend bij een challenge (zonder flags).
    Past bij url_for('ch.challenge_download', cid=c.id, relpath=f.rel)
    """
    if not is_team_logged_in():
        return login_required_redirect()

    chobj = find_challenge(cid)
    if not chobj:
        abort(404)

    base = Path(chobj["path"])
    target = secure_join(base, relpath)
    if not target or not target.is_file():
        abort(404)
    if _is_sensitive_file(target) or _is_hidden_or_tech(target):
        abort(403)

    # Pad relatief t.o.v. CHALL_ROOT voor send_from_directory
    rel_from_root = target.relative_to(CHALL_ROOT).as_posix()
    return send_from_directory(CHALL_ROOT, rel_from_root, as_attachment=True)

@ch.route("/challenge/<cid>/asset/<path:relpath>")
def challenge_asset(cid: str, relpath: str):
    if not is_team_logged_in():
        return login_required_redirect()

    chobj = find_challenge(cid)
    if not chobj:
        abort(404)

    base = Path(chobj["path"])
    target = secure_join(base, relpath)
    if not target or not target.is_file():
        abort(404)
    if _is_sensitive_file(target) or _is_hidden_or_tech(target):
        abort(403)

    rel_from_root = target.relative_to(CHALL_ROOT).as_posix()
    return send_from_directory(CHALL_ROOT, rel_from_root, as_attachment=False)

@ch.route("/download-bundle/<cid>")
def challenge_bundle(cid: str):
    """
    Download één challenge als ZIP (zonder flags).
    <cid> kan mapnaam, slug of pdf-stem zijn.
    """
    if not is_team_logged_in():
        return login_required_redirect()

    chobj = find_challenge(cid)
    if not chobj:
        abort(404)

    files = [(rel, p) for rel, p in list_files_recursive(chobj["path"]) if not _is_sensitive_file(p)]
    if not files:
        abort(404)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        rootname = f"{chobj['title']}"
        for rel, p in files:
            zf.write(p, arcname=f"{rootname}/{rel}")

    mem.seek(0)
    fname = f"{slugify(chobj['title'])}.zip"
    return send_file(mem, as_attachment=True, download_name=fname, mimetype="application/zip")

@ch.route("/download-all")
def challenges_download_all():
    """
    Download ALLE challenges als één ZIP (zonder flags).
    """
    if not is_team_logged_in():
        return login_required_redirect()

    all_items: List[Tuple[str, Path]] = []

    for chobj in get_all_challenges():
        for rel, p in list_files_recursive(chobj["path"]):
            if _is_sensitive_file(p):
                continue
            all_items.append((f"{chobj['title']}/{rel}", p))

    if not all_items:
        abort(404)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", "CTF Challenges export\nFlags: EXCLUDED\n")
        for rel, p in all_items:
            zf.write(p, arcname=rel)

    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="alle-challenges.zip", mimetype="application/zip")
