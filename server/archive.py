"""Räätälöityjen CV:iden arkisto: jokainen tallennettu versio omaksi tiedostokseen,
archive/cv/<yritys>/<tehtävä>/<aika>_<kieli>[_HAETTU].md (+ .json).

cvs.json pitää vain viimeisimmän version ilmoitusta ja kieltä kohden, joten uusi paneeliajo tai muokkaus korvaisi
edellisen. Arkistoon kirjoitetaan jokaisesta tallennuksesta luettava Markdown (sama sisältö kuin tulostettavassa
CV:ssä: vain mukaan valitut rivit) ja koko tieto JSONina. Kun hakemus merkitään haetuksi, sen hetkinen versio
tallennetaan lisäksi merkinnällä HAETTU, jotta jälkikäteen tiedetään, mikä versio lähti työnantajalle.
"""
from __future__ import annotations
from datadir import DATA

import datetime as dt
import hashlib
import json
import re
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ARCHIVE = DATA / "archive" / "cv"
_lock = threading.Lock()


def _folder(s: str, fallback: str, n: int = 80) -> str:
    """Luettava kansion nimi (yritys tai tehtävä): Windowsissa kielletyt merkit pois, pituus rajattu."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()[:n].rstrip(" .")
    return s or fallback


def _dir(cv: dict, root: Path) -> Path:
    """archive/cv/<yritys>/<tehtävä>/"""
    return root / _folder(cv.get("company") or "", "Tuntematon yritys") / _folder(cv.get("job_title") or "", "Tuntematon tehtävä")


def cv_markdown(cv: dict) -> str:
    """CV tekstinä sellaisena kuin se tulostuu: otsikko, yhteystiedot, profiiliteksti ja mukaan valitut rivit."""
    out = [f"# {cv.get('name', '')}"] + [h for h in cv.get("header") or []]
    for s in cv.get("sections") or []:
        body: list[str] = []
        if s.get("profile"):
            text = ((cv.get("summary") or {}).get("text") or "").strip()
            if text:
                body.append(text)
        else:
            for u in s.get("units") or []:
                if u.get("included", True):
                    body.append(("- " if u.get("kind") == "bullet" else "") + u["text"])
            for e in s.get("entries") or []:
                if not e.get("included", True):
                    continue
                body.append(f"### {e.get('title', '')}" + (f"\n{e['meta']}" if e.get("meta") else ""))
                body += [("- " if u.get("kind") == "bullet" else "") + u["text"]
                         for u in e.get("units") or [] if u.get("included", True)]
        if body:
            out += ["", f"## {s.get('title', '')}", *body]
    return "\n".join(out).strip() + "\n"


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _latest(url: str, lang: str, root: Path) -> dict | None:
    idx = _index(root)
    rows = [r for r in idx if r["url"] == url and r["lang"] == lang]
    return rows[-1] if rows else None


def _index(root: Path) -> list[dict]:
    p = root / "index.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (OSError, json.JSONDecodeError):
        return []


def save(url: str, lang: str, cv: dict, sent: bool = False, root: Path | None = None) -> dict | None:
    """Arkistoi version. Sama sisältö peräkkäin tallentuu vain kerran (paitsi HAETTU-merkintä).
    Palauttaa indeksirivin tai None, jos mitään ei kirjoitettu."""
    root = root or ARCHIVE
    md = cv_markdown(cv)
    h = _digest(md)
    with _lock:
        prev = _latest(url, lang, root)
        if prev and prev["hash"] == h and not sent:
            return None
        now = dt.datetime.now()
        folder = _dir(cv, root)
        base = f"{now:%Y-%m-%d_%H%M%S}_{lang}" + ("_HAETTU" if sent else "")
        name, i = base, 2
        while (folder / f"{name}.md").exists():      # kaksi tallennusta samalla sekunnilla
            name, i = f"{base}_{i}", i + 1
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.md").write_text(md, encoding="utf-8")
        (folder / f"{name}.json").write_text(json.dumps({"url": url, "lang": lang, "cv": cv}, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
        row = {"file": (folder / f"{name}.md").relative_to(root).as_posix(), "url": url, "lang": lang, "company": cv.get("company") or "",
               "title": cv.get("job_title") or "", "saved": now.isoformat(timespec="seconds"), "hash": h,
               "sent": sent, "panel": (cv.get("panel") or {}).get("status")}
        idx = _index(root)
        idx.append(row)
        (root / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    from . import pdf   # sama PDF kuin itse tulostettuna, taustalla (selain vie pari sekuntia)
    pdf.render_async(url, lang, folder / f"{name}.pdf")
    return row


def mark_sent(url: str, cvs: dict, root: Path | None = None) -> list[dict]:
    """Hakemus merkittiin haetuksi: arkistoi ilmoituksen kaikki kieliversiot merkinnällä HAETTU."""
    return [r for lang, cv in (cvs.get(url) or {}).items() if isinstance(cv, dict)
            for r in [save(url, lang, cv, sent=True, root=root)] if r]


def listing(url: str | None = None, root: Path | None = None) -> list[dict]:
    rows = _index(root or ARCHIVE)
    return [r for r in rows if url is None or r["url"] == url][::-1]
