"""Hakemuspakettien luonnokset: drafts.json (käyttäjän dataa, mukana varmuuskopioissa).

Rakenne: {url: {title, company, lang, model, created, updated, sections: [...], history: [{at, sections}]}}
Jokainen tallennus säilyttää edellisen version historiaan (enintään HISTORY kpl), jotta muokkauksen voi perua.
"""
from __future__ import annotations
from datadir import DATA

import datetime as dt
import json
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DRAFTS = DATA / "drafts.json"
HISTORY = 10
MAX_TEXT = 20_000
_lock = threading.Lock()


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _read(path: Path | None = None) -> dict:
    path = path or DRAFTS   # haetaan kutsuhetkellä (testit vaihtavat DRAFTS-polun)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(data: dict, path: Path | None = None) -> None:
    path = path or DRAFTS
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def get(url: str, path: Path | None = None) -> dict | None:
    return _read(path).get(url)


def all_drafts(path: Path | None = None) -> list[dict]:
    return [{"url": u, "title": d.get("title"), "company": d.get("company"), "updated": d.get("updated"),
             "flagged": sum(s.get("verify", {}).get("flagged", 0) for s in d.get("sections", []))}
            for u, d in _read(path).items()]


def save_generated(pkg: dict, path: Path | None = None) -> dict:
    with _lock:
        data = _read(path)
        old = data.get(pkg["url"])
        rec = {k: pkg[k] for k in ("title", "company", "lang", "model", "sections")}
        rec.update(created=_now(), updated=_now(), history=[])
        if old:
            rec["history"] = ([{"at": old.get("updated"), "sections": old.get("sections", [])}] + old.get("history", []))[:HISTORY]
        data[pkg["url"]] = rec
        _write(data, path)
        return rec


def save_edit(url: str, sections: list[dict], path: Path | None = None) -> dict:
    """Käyttäjän muokkaus: vain tekstit ja tarkistustulos tallennetaan; osioiden rakenne pysyy."""
    with _lock:
        data = _read(path)
        rec = data.get(url)
        if not rec:
            raise KeyError("Luonnosta ei löydy")
        by_key = {s.get("key"): s for s in sections if isinstance(s, dict)}
        new = []
        for s in rec["sections"]:
            upd = by_key.get(s["key"])
            if upd is not None and isinstance(upd.get("text"), str):
                text = upd["text"][:MAX_TEXT]
                s = {**s, "text": text, "edited": s.get("edited") or text != s["text"]}
                if isinstance(upd.get("verify"), dict):
                    s["verify"] = upd["verify"]
            new.append(s)
        rec["history"] = ([{"at": rec.get("updated"), "sections": rec["sections"]}] + rec.get("history", []))[:HISTORY]
        rec["sections"], rec["updated"] = new, _now()
        _write(data, path)
        return rec


def delete(url: str, path: Path | None = None) -> None:
    with _lock:
        data = _read(path)
        data.pop(url, None)
        _write(data, path)


# ------------------------------------------------------------ räätälöidyt CV:t (cvs.json: {url: {lang: cv}})

CVS = DATA / "cvs.json"


def get_cv(url: str, lang: str, path: Path | None = None) -> dict | None:
    return _read(path or CVS).get(url, {}).get(lang)


def save_cv(url: str, lang: str, cv: dict, path: Path | None = None) -> dict:
    with _lock:
        data = _read(path or CVS)
        data.setdefault(url, {})[lang] = {**cv, "updated": _now()}
        _write(data, path or CVS)
        saved = data[url][lang]
    if path is None:   # testit kirjoittavat omaan tiedostoonsa eivätkä arkistoon
        try:
            from . import archive
            archive.save(url, lang, saved)     # jokainen versio talteen, cvs.json pitää vain viimeisimmän
        except OSError as e:
            print(f"! CV:n arkistointi epäonnistui: {e}")
    return saved


MAX_UNIT, MAX_LINE, MAX_NEW = 700, 300, 40


def _clean(v, limit: int) -> str:
    return " ".join(str(v or "").split())[:limit]


def merge_cv_edit(stored: dict, edit: dict) -> dict:
    """Käyttäjän muokkaus: mukaan/pois-valinnat, profiiliteksti, rivien tekstit, projektien ja työpaikkojen otsikot
    ja aikarivit, otsikkorivit sekä uudet rivit. Rakenne (osiot, tunnisteet) tulee tallennetusta versiosta.
    Käyttäjän omaa tekstiä ei estetä, mutta muuttuneet rivit merkitään (edited, orig), jotta check_edits voi
    liputtaa väitteet, joita CV:ssä ei ole."""
    sent_units: dict[str, dict] = {}
    sent_entries: dict[str, dict] = {}
    new_units: dict[str, list[dict]] = {}     # vanhemman (osio tai merkintä) id -> uudet rivit järjestyksessä
    for s in edit.get("sections") or []:
        for u in s.get("units") or []:
            sent_units[str(u.get("id"))] = u
        for e in s.get("entries") or []:
            sent_entries[str(e.get("id"))] = e
            for u in e.get("units") or []:
                sent_units[str(u.get("id"))] = u
            new_units[str(e.get("id"))] = [u for u in e.get("units") or [] if str(u.get("id", "")).startswith("NEW")]
        new_units[str(s.get("id"))] = [u for u in s.get("units") or [] if str(u.get("id", "")).startswith("NEW")]
    out = json.loads(json.dumps(stored))
    n_new = 0

    def apply_unit(u: dict) -> None:
        got = sent_units.get(u["id"])
        if got is None:
            return
        u["included"] = bool(got.get("included", u["included"]))
        text = _clean(got.get("text", u["text"]), MAX_UNIT)
        if text and text != u["text"]:
            u.setdefault("orig", u["text"])
            u["text"], u["edited"] = text, text != u["orig"]

    def add_new(units: list[dict], parent: str) -> None:
        nonlocal n_new
        for i, got in enumerate(new_units.get(parent, [])):
            text = _clean(got.get("text"), MAX_UNIT)
            if not text or n_new >= MAX_NEW:
                continue
            n_new += 1
            units.append({"id": f"{parent}N{len([x for x in units if 'N' in x['id'][len(parent):]]) + 1}",
                          "kind": "bullet", "text": text, "included": bool(got.get("included", True)),
                          "edited": True, "orig": ""})

    for s in out["sections"]:
        for u in s["units"]:
            apply_unit(u)
        add_new(s["units"], s["id"])
        for e in s["entries"]:
            got = sent_entries.get(e["id"])
            if got is not None:
                e["included"] = bool(got.get("included", e["included"]))
                for k in ("title", "meta"):
                    v = _clean(got.get(k, e.get(k, "")), MAX_LINE)
                    if v != e.get(k, "") and (v or k == "meta"):
                        e.setdefault(f"orig_{k}", e.get(k, ""))
                        e[k] = v
            for u in e["units"]:
                apply_unit(u)
            add_new(e["units"], e["id"])
    head = edit.get("header")
    if isinstance(head, list) and len(head) == len(out.get("header") or []):
        new = [_clean(h, MAX_LINE) or old for h, old in zip(head, out["header"])]
        if new != out["header"]:
            out.setdefault("orig_header", out["header"])
            out["header"] = new
    text = str((edit.get("summary") or {}).get("text", out["summary"]["text"]))[:1500]
    if text != out["summary"]["text"]:
        out["summary"] = {**out["summary"], "text": text, "source": "edited"}
    return out


def edited_units(cv: dict) -> list[tuple[dict, str]]:
    """(rivi, sijainti) kaikista käyttäjän muuttamista tai lisäämistä riveistä."""
    out = []
    for s in cv.get("sections") or []:
        out += [(u, s["title"]) for u in s["units"] if u.get("edited")]
        for e in s["entries"]:
            out += [(u, f"{s['title']} / {e['title']}") for u in e["units"] if u.get("edited")]
    return out


# ------------------------------------------------------------ vakiovastaukset (answers.json)

ANSWERS = DATA / "answers.json"
# Tiedot, joita vain käyttäjä tietää: kielimalli ei täytä näitä
ANSWER_FIELDS = [("start_date", "Milloin voit aloittaa?"), ("salary", "Palkkatoive"),
                 ("work_type", "Toivottu työaika ja työmuoto"), ("location", "Sijainti ja muuttohalukkuus"),
                 ("drivers_license", "Ajokortti"), ("work_permit", "Oikeus työskennellä Suomessa"),
                 ("links", "GitHub, LinkedIn ja portfolio"), ("references", "Suosittelijat")]


def get_answers(path: Path | None = None) -> dict:
    saved = _read(path or ANSWERS)
    return {"fields": [{"key": k, "label": label, "value": str(saved.get(k, ""))} for k, label in ANSWER_FIELDS]}


def save_answers(values: dict, path: Path | None = None) -> dict:
    with _lock:
        keys = {k for k, _ in ANSWER_FIELDS}
        _write({k: str(v)[:1000].strip() for k, v in values.items() if k in keys}, path or ANSWERS)
    return get_answers(path)
