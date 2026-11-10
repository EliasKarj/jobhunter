"""Suomenkielisen pohjan (data/cv_fi.md) pitäminen ajan tasalla, kun data/cv.md muuttuu.

Rivien tunnisteet (S3E2B1) ovat paikkasidonnaisia: uusi projekti siirtää kaikkien sen jälkeisten tunnisteita. Siksi
suomennokset muistetaan sisällön mukaan: data/cv_fi_map.json = {englanninkielinen rivi: suomenkielinen rivi}.

- snapshot(): kun pohjat ovat samanrakenteiset eikä cv.md ole muuttunut edellisen kirjauksen jälkeen, parit
  luetaan tunnisteittain (myös käyttäjän omat muokkaukset cv_fi.md:hen tallentuvat).
- status(): rivit, joiden englanninkielistä tekstiä ei ole muistissa = uudet tai muuttuneet rivit.
- translate_missing(): vain ne käännetään (samat tarkistukset kuin pohjan luonnissa).
- save(): pohja kootaan uudelleen cv.md:n rakenteella: muistetut suomennokset + tarkistetut uudet rivit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import cvtailor as ct

MAP_NAME = "cv_fi_map.json"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def map_path(s) -> Path:
    return s.cv_path.with_name(MAP_NAME)


def _load(s) -> dict:
    p = map_path(s)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"en_hash": "", "pairs": {}}


def _save(s, m: dict) -> None:
    map_path(s).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


def _units(md: str) -> dict[str, str]:
    return ct.translation_units(ct.parse(md))


def snapshot(s, force: bool = False) -> bool:
    """Kirjaa parit tunnisteittain, jos pohjat ovat samanrakenteiset ja cv.md on ennallaan (tai force).
    Kutsutaan ennen cv.md:n muokkausta ja jokaisen cv_fi.md:n kirjoituksen jälkeen (force=True)."""
    fp = ct.fi_path(s.cv_path)
    if not fp.exists() or not s.cv_path.exists():
        return False
    en_md = s.cv_path.read_text(encoding="utf-8")
    ue, uf = _units(en_md), _units(fp.read_text(encoding="utf-8"))
    m = _load(s)
    if set(ue) != set(uf) or not (force or not m["pairs"] or m["en_hash"] == _hash(en_md)):
        return False
    m["pairs"].update({ue[k]: uf[k] for k in ue})
    m["en_hash"] = _hash(en_md)
    _save(s, m)
    return True


def status(s) -> dict:
    fp = ct.fi_path(s.cv_path)
    if not fp.exists():
        return {"exists": False, "in_sync": False, "missing": []}
    snapshot(s)
    ue = _units(s.cv_path.read_text(encoding="utf-8"))
    uf = _units(fp.read_text(encoding="utf-8"))
    pairs = _load(s)["pairs"]
    missing = [{"id": k, "en": v} for k, v in ue.items() if v not in pairs]
    stale = [k for k, v in ue.items() if v in pairs and uf.get(k) != pairs[v]]   # rakenne siirtynyt
    return {"exists": True, "in_sync": not missing and not stale, "missing": missing, "restructure": len(stale)}


def translate_missing(s, llm, embed=None) -> dict[str, dict]:
    en = ct.parse(s.cv_path.read_text(encoding="utf-8"))
    pairs = _load(s)["pairs"]
    ue = ct.translation_units(en)
    todo = {k: v for k, v in ue.items() if v not in pairs}
    if not todo:
        return {}
    tr = ct.translate(todo, llm, embed, ct.full_text(en))
    for sec in en.sections:   # virallisia nimiä ei voi tarkistaa säännöillä
        if ct._is(sec.title, ("education", "koulutus")):
            for u in sec.units:
                if u.id in tr:
                    tr[u.id]["reasons"].append("tutkinnon ja oppilaitoksen virallinen suomenkielinen nimi: tarkista")
        for e in sec.entries:
            if e.id in tr and not ct._is(sec.title, ("projects", "projektit")):
                tr[e.id]["reasons"].append("ammattinimike: tarkista")
    return tr


def save(s, lines: dict[str, str]) -> dict:
    """Kokoa cv_fi.md: muistetut suomennokset + annetut rivit. Rivi, jolle ei ole suomennosta, jää englanniksi."""
    en_md = s.cv_path.read_text(encoding="utf-8")
    ue = _units(en_md)
    pairs = _load(s)["pairs"]
    full = {k: pairs[v] for k, v in ue.items() if v in pairs}
    full.update({k: str(v).strip() for k, v in lines.items() if k in ue and str(v).strip()})
    res = ct.save_fi_base(full, s, overwrite=True)
    snapshot(s, force=True)
    res["left_english"] = [k for k in ue if k not in full]
    return res
