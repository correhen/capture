DIFFICULTY_POINTS = {"makkelijk": 1, "gemiddeld": 2, "moeilijk": 3}

import hashlib

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()
