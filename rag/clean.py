"""Ilmoitustekstin siivous ennen pilkkomista ja tunnisteet."""
from __future__ import annotations

import hashlib
import re

_DEADLINE = re.compile(r"(?im)^\s*Hakuaika umpeutuu \d{2}\.\d{2}\.\d{4}\s*$")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_EMAIL = re.compile(r"(?i)\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?<![\w])(?:\+358|0)[\s-]?\d{1,3}(?:[\s-]?\d{2,4}){2,3}(?![\w])")

_FI = {"ja", "on", "että", "tai", "sekä", "olet", "meillä", "työ", "sinulla", "haemme", "kanssa", "voit", "myös"}
_EN = {"and", "the", "you", "with", "for", "our", "are", "we", "to", "of", "your", "will", "is"}


def clean_text(text: str) -> str:
    """Poista yhteystiedot, linkit ja jobhunterin lisäämä hakuaikarivi; säilytä rivijako pilkkomista varten."""
    text = _DEADLINE.sub("", text or "")
    text = _URL.sub("", text)
    text = _EMAIL.sub("", text)
    text = _PHONE.sub("", text)
    out: list[str] = []
    seen: set[str] = set()
    for line in text.replace("\r", "").split("\n"):
        line = " ".join(line.split())
        key = line.lower()
        if line and len(line) > 30 and key in seen:
            continue  # sama pitkä rivi toistuu (esim. sivun alatunniste kuvauksessa)
        seen.add(key)
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def detect_lang(text: str) -> str:
    words = re.findall(r"[a-zäöå]+", text.lower()[:3000])
    fi = sum(w in _FI for w in words)
    en = sum(w in _EN for w in words)
    if fi + en < 3:
        return "?"
    return "fi" if fi >= en else "en"


def job_id(url: str, source: str = "") -> str:
    """Pysyvä tunniste: lähde + ilmoituksen numero URL:n lopusta. Muuten URL:n tiiviste."""
    m = re.search(r"-(\d{5,})/?$", url or "")
    src = source or "job"
    if m:
        return f"{src}:{m.group(1)}"
    return f"{src}:{hashlib.sha1((url or '').encode()).hexdigest()[:16]}"


def sha1(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()
