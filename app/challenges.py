# app/challenges.py
from __future__ import annotations
import io, re, zipfile, unicodedata
from pathlib import Path
from typing import Iterable, List, Tuple, Optional, Dict

from flask import (
    Blueprint, abort, send_from_directory, send_file,
    session, redirect, url_for, render_template
)
from database import db  # voor thema-kleuren uit settings

# --------------------------------- #
# Blueprint
# --------------------------------- #
# Let op: jouw template gebruikt "ch" als blueprint-naam
ch = Blueprint("ch", __name__, url_prefix="")

# Pad naar de challenges-root
CHALL_ROOT = Path(__file__).resolve().parent / "static" / "challenges"

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
    return bool(session.get("team_token"))

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
    cid_low = cid.strip().lower()
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

def get_theme():
    with db() as conn:
        c1 = conn.execute("SELECT value FROM settings WHERE key='theme_c1'").fetchone()["value"]
        c2 = conn.execute("SELECT value FROM settings WHERE key='theme_c2'").fetchone()["value"]
    return {"c1": c1, "c2": c2}

# --------------------------------- #
# Routes
# --------------------------------- #

@ch.route("/ch/static/<path:subpath>")
def serve_challenge_asset(subpath: str):
    """
    Veilig statische challenge-bestanden serveren (flags geblokkeerd).
    Gebruik /ch/static/... ipv /challenges/... zodat /challenges vrij is voor de indexpagina.
    """
    low = subpath.lower()

    # Blokkeer directe flag-toegang overal
    if (
        low.endswith("flag.txt")
        or low.endswith("flag.sha256")
        or low.endswith(".flag")
        or low.startswith("flag.")
        or "/flag." in low
        or low.split("/")[-1] in {"flag.txt", "flag.sha256"}
    ):
        abort(403)

    # Folders die we nooit willen serveren
    if any(part in {".git", "__pycache__"} for part in Path(subpath).parts):
        abort(403)

    if not CHALL_ROOT.exists():
        abort(404)

    return send_from_directory(CHALL_ROOT, subpath)

@ch.route("/download-bundle/<cid>")
def challenge_bundle(cid: str):
    """
    Download één challenge als ZIP (zonder flags).
    <cid> kan mapnaam, slug of pdf-stem zijn.
    Alleen voor ingelogde teams.
    """
    if not is_team_logged_in():
        return redirect(url_for("submit"))

    chobj = find_challenge(cid)
    if not chobj:
        abort(404)

    files = list_files_recursive(chobj["path"])
    files = [(rel, p) for rel, p in files if not _is_sensitive_file(p)]

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
    Alleen voor ingelogde teams.
    """
    if not is_team_logged_in():
        return redirect(url_for("submit"))

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
        zf.writestr(
            "README.txt",
            "CTF Challenges export\nFlags: EXCLUDED\n"
        )
        for rel, p in all_items:
            zf.write(p, arcname=rel)

    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="alle-challenges.zip", mimetype="application/zip")

@ch.route("/challenge/<cid>")
def challenge_detail(cid: str):
    """
    Eenvoudige detailpagina:
    - titel
    - (optioneel) link naar eerste PDF
    - download-zip knop
    """
    if not is_team_logged_in():
        return redirect(url_for("submit"))

    chobj = find_challenge(cid)
    if not chobj:
        abort(404)

    # Probeer 1 PDF te tonen (handig als 'open pdf')
    pdfs = list(Path(chobj["path"]).glob("*.pdf"))
    first_pdf_rel = None
    if pdfs:
        first_pdf_rel = f"{chobj['path'].relative_to(CHALL_ROOT).as_posix()}/{pdfs[0].name}"

    # Inline HTML (je mag hier natuurlijk ook een eigen template voor maken)
    return render_template(
        "challenge_detail.html" if Path(__file__).with_name("templates").exists() else "base.html",
        # Als je geen template hebt, kun je dit overschrijven; voor nu geven we data mee:
        challenge={
            "title": chobj["title"],
            "slug": chobj["slug"],
            "first_pdf_rel": first_pdf_rel,
        },
        theme=get_theme(),
    )

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
    if not is_team_logged_in():
        return redirect(url_for("submit"))

    # Bouw per level een lijst
    data: Dict[str, Dict[str, object]] = {}

    if CHALL_ROOT.exists():
        used = False
        for level in LEVEL_DIRS:
            base = CHALL_ROOT / level
            if base.exists():
                used = True
                key = slugify(level)  # bv. "1-easy"
                data[key] = {"label": LEVEL_LABELS.get(level, level), "challenges": []}
                for d in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
                    data[key]["challenges"].append({
                        "id": slugify(d.name),
                        "title": d.name
                    })
        if not used:
            # Fallback: groepeer alles onder 'Overig'
            key = "overig"
            data[key] = {"label": "Overig", "challenges": []}
            for d in sorted((p for p in CHALL_ROOT.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
                data[key]["challenges"].append({
                    "id": slugify(d.name),
                    "title": d.name
                })

    return render_template(
        "challenges.html",
        data=data,
        theme=get_theme(),
    )
