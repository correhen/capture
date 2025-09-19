# app/challenges.py
from __future__ import annotations
import io, re, zipfile, unicodedata
from pathlib import Path
from typing import Iterable, List, Tuple, Optional, Dict

from flask import Blueprint, abort, send_from_directory, send_file, session, redirect, url_for

# Blueprint registreren in server.py met:
#   from challenges import ch_bp
#   app.register_blueprint(ch_bp)
ch_bp = Blueprint("challenges", __name__, url_prefix="")

# Pad naar de challenges-root
CHALL_ROOT = Path(__file__).resolve().parent / "static" / "challenges"

# Optionele mapping voor je mappenstructuur (maakt niets uit als de mappen anders heten)
LEVEL_DIRS = [
    "1 - Easy",
    "2 - Medium",
    "3 - Hard",
]

# ------------- Helpers ------------- #

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
    # Eventuele “verstopte” flags:
    if "flag" == p.stem.lower():
        return True
    return False

def _is_hidden_or_tech(p: Path) -> bool:
    """Folders die we niet willen serveren."""
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
       Valt terug op alle submappen als LEVEL_DIRS niet bestaat."""
    if CHALL_ROOT.exists():
        # Eerst proberen met vaste level mappen
        used = False
        for level in LEVEL_DIRS:
            base = CHALL_ROOT / level
            if base.exists():
                used = True
                for d in base.iterdir():
                    if d.is_dir():
                        yield d
        if not used:
            # Fallback: alle submappen in CHALL_ROOT
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
    # Probeer directe naam/slug match
    for ch in get_all_challenges():
        name_low = ch["title"].lower()
        if cid_low == name_low or cid_low == ch["slug"]:
            return ch

    # Probeer PDF-stem match (bijv. /download-bundle/<pdfnaam>)
    for ch in get_all_challenges():
        for p in Path(ch["path"]).glob("*.pdf"):
            if p.stem.lower() == cid_low:
                return ch

    # Substring fallback op naam
    for ch in get_all_challenges():
        if cid_low in ch["title"].lower():
            return ch

    return None


# ------------- Routes ------------- #

@ch_bp.route("/challenges/<path:subpath>")
def serve_challenge_asset(subpath: str):
    """
    Veilig statische challenge-bestanden serveren, maar ALTIJD flags blokkeren.
    Voorbeeld: /challenges/1 - Easy/Intro/intro.pdf
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


@ch_bp.route("/download-bundle/<cid>")
def challenge_bundle(cid: str):
    """
    Download één challenge als ZIP (zonder flags).
    <cid> kan mapnaam, slug of pdf-stem zijn.
    Alleen voor ingelogde teams.
    """
    if not is_team_logged_in():
        return redirect(url_for("submit"))

    ch = find_challenge(cid)
    if not ch:
        abort(404)

    files = list_files_recursive(ch["path"])
    files = [(rel, p) for rel, p in files if not _is_sensitive_file(p)]

    if not files:
        abort(404)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Zet alle bestanden onder een nette rootmap in de zip
        rootname = f"{ch['title']}"
        for rel, p in files:
            arcname = f"{rootname}/{rel}"
            zf.write(p, arcname=arcname)

    mem.seek(0)
    fname = f"{slugify(ch['title'])}.zip"
    return send_file(mem, as_attachment=True, download_name=fname, mimetype="application/zip")


@ch_bp.route("/download-all")
def challenges_download_all():
    """
    Download ALLE challenges als één ZIP (zonder flags).
    Alleen voor ingelogde teams.
    """
    if not is_team_logged_in():
        return redirect(url_for("submit"))

    all_items: List[Tuple[str, Path]] = []

    for ch in get_all_challenges():
        for rel, p in list_files_recursive(ch["path"]):
            if _is_sensitive_file(p):
                continue
            prefixed_rel = f"{ch['title']}/{rel}"
            all_items.append((prefixed_rel, p))

    if not all_items:
        abort(404)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Info-bestand
        zf.writestr(
            "README.txt",
            "CTF Challenges export\n"
            "Flags: EXCLUDED\n"
        )
        for rel, p in all_items:
            zf.write(p, arcname=rel)

    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="alle-challenges.zip", mimetype="application/zip")
