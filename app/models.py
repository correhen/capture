DIFFICULTY_POINTS = {"makkelijk": 1, "gemiddeld": 2, "moeilijk": 3}

import hashlib
import re

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

_CTF_WRAPPER_RE = re.compile(r"^ctf\{(.*)\}$", re.IGNORECASE | re.DOTALL)

def unwrap_ctf_flag(value: str) -> str:
    """Trim whitespace and remove one optional CTF{...} wrapper."""
    cleaned = (value or "").strip()
    match = _CTF_WRAPPER_RE.match(cleaned)
    if match:
        return match.group(1).strip()
    return cleaned

def flag_hash_candidates(value: str) -> set[str]:
    """
    Return hashes for safe variants of a submitted flag.

    The database stores only hashes, so we cannot normalize the stored answer
    directly. Instead we hash the exact input plus common equivalents:
    CTF-wrapped, unwrapped, and upper/lower-case content.
    """
    raw = (value or "").strip()
    inner = unwrap_ctf_flag(raw)
    if not inner:
        return set()

    variants = {
        raw,
        inner,
        f"CTF{{{inner}}}",
        f"ctf{{{inner}}}",
        inner.upper(),
        inner.lower(),
        f"CTF{{{inner.upper()}}}",
        f"CTF{{{inner.lower()}}}",
        f"ctf{{{inner.upper()}}}",
        f"ctf{{{inner.lower()}}}",
    }
    return {sha256_hex(v) for v in variants if v.strip()}
