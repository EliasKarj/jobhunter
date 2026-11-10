"""Tyylinäytteet: keksittyjä harjoitusilmoituksia, joihin kirjoitusmalli tekee luonnoksen samalla putkella kuin
oikeisiin hakemuksiin. Käyttäjä muokkaa luonnoksen sellaiseksi, jonka lähettäisi itse. Parit (luonnos -> oma versio)
ovat lähtökohta tyyliprofiilille: mitä käyttäjä muuttaa ja miten hän itse kirjoittaa.

Ilmoitukset ovat keksittyjä (yritykset eivät ole oikeita), eikä niitä näytetä ilmoituslistassa. Faktat tulevat
edelleen vain CV:stä: omat versiot ovat tyylin mallina, eivät faktojen lähteenä.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

from . import config

PATH = config.DATA / "style_samples.json"
_lock = threading.Lock()

FICTIONAL = [
    {"id": "fs1", "lang": "fi", "company": "Pohjanmaan Ohjelmistopaja Oy (keksitty)", "city": "Vaasa",
     "title": "Junior Full Stack -kehittäjä",
     "ad": "Pohjanmaan Ohjelmistopaja tekee verkkopalveluita teollisuuden asiakkaille. Haemme junior full stack "
           "-kehittäjää tiimiimme Vaasaan. Työssä kehität React- ja TypeScript-käyttöliittymiä sekä Python- ja "
           "Node.js-taustapalveluja, jotka ajetaan Dockerilla. Arvostamme kiinnostusta tietokantoihin (PostgreSQL) ja "
           "CI/CD-putkiin. Sopii valmistumassa olevalle tai vastavalmistuneelle. Hybridityö mahdollinen.",
     "questions": ["Vapaamuotoinen hakemuksesi tehtävään", "Miksi haet juuri tähän tehtävään?"]},
    {"id": "da1", "lang": "fi", "company": "Rannikon Energia Oy (keksitty)", "city": "Seinäjoki",
     "title": "Data-analyytikkoharjoittelija",
     "ad": "Rannikon Energia etsii data-analyytikkoharjoittelijaa kesälle 2027. Teet Power BI -raportteja, käsittelet "
           "mittausdataa Pythonilla ja SQL:llä ja autat automatisoimaan raportointia. Haemme tietotekniikan tai "
           "tilastotieteen opiskelijaa, joka on kiinnostunut datasta ja selkeästä viestinnästä.",
     "questions": ["Kerro itsestäsi lyhyesti.", "Millaista kokemusta sinulla on datan käsittelystä?"]},
    {"id": "it1", "lang": "fi", "company": "Lakeuden IT-palvelut Oy (keksitty)", "city": "Vaasa",
     "title": "IT-tukihenkilö, osa-aikainen",
     "ad": "Lakeuden IT-palvelut tarjoaa käyttäjätukea pk-yrityksille. Haemme osa-aikaista IT-tukihenkilöä opintojen "
           "ohelle. Tehtäviin kuuluu käyttäjien neuvominen, laitteiden asennus, Microsoft 365 -tunnusten hallinta ja "
           "verkkoyhteyksien vianselvitys. Asiakaspalveluasenne on meille tärkeä.",
     "questions": ["Mikä on vahvuutesi asiakaspalvelussa?"]},
    {"id": "sec1", "lang": "fi", "company": "Nordic Shield Security Oy (keksitty)", "city": "Tampere",
     "title": "Tietoturvaharjoittelija (SOC)",
     "ad": "Nordic Shield valvoo asiakkaidensa tietoverkkoja ympäri vuorokauden. Haemme harjoittelijaa SOC-tiimiin. "
           "Opit tunnistamaan poikkeamia lokeista, käsittelemään hälytyksiä ja dokumentoimaan havainnot. Linux-, "
           "verkko- ja skriptausosaaminen on eduksi. Harjoittelu sopii tietotekniikan opiskelijalle.",
     "questions": ["Vapaamuotoinen hakemuksesi tehtävään"]},
    {"id": "th1", "lang": "fi", "company": "Merikarin Teknologia Oy (keksitty)", "city": "Vaasa",
     "title": "Opinnäytetyöntekijä, ohjelmistokehitys",
     "ad": "Merikarin Teknologia kehittää ohjelmistoja meriteollisuuden laitteiden etävalvontaan. Tarjoamme "
           "opinnäytetyöpaikkaa keväälle 2027: aiheena valvontadatan keruu ja esitys web-käyttöliittymässä. "
           "Toivomme osaamista Pythonista tai TypeScriptistä sekä kiinnostusta tietoliikenteeseen.",
     "questions": ["Mitä haluaisit oppia opinnäytetyön aikana?"]},
    {"id": "en1", "lang": "en", "company": "Fjordline Software Ltd (fictional)", "city": "Helsinki / remote",
     "title": "Junior DevOps Engineer",
     "ad": "Fjordline Software builds logistics software for Nordic customers. We are looking for a Junior DevOps "
           "Engineer to help us run our services on Docker and Kubernetes, maintain GitHub Actions pipelines and "
           "improve monitoring. Fully remote within Finland is possible. Students graduating soon are welcome.",
     "questions": ["Why are you interested in this role?"]},
]


def _load() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {"samples": []}


def _save(d: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def listing() -> list[dict]:
    return _load()["samples"]


def _grounding(post: dict, s):
    from .grounding import Grounding
    from .profile import build_profile
    job = {"job_id": f"style:{post['id']}", "url": f"style:{post['id']}", "title": post["title"],
           "company": post["company"], "city": post["city"], "lang": post["lang"], "content_hash": post["id"],
           "clean_text": post["ad"]}
    prof = build_profile(s.with_(cv_skip_sections=()))
    return Grounding(job, {"A1": {"id": None, "text": post["ad"], "start": 0, "end": len(post["ad"])}},
                     {f"P{i}": p for i, p in enumerate(prof, 1)})


def generate(s=None, llm=None, only_missing: bool = True) -> list[dict]:
    """Kirjoita luonnos jokaiseen harjoituskysymykseen (kirjoitusmalli, samat säännöt kuin oikeissa)."""
    from . import package, service
    s = s or config.load()
    llm = llm or service.llm("writing", s)
    d = _load()
    have = {x["id"] for x in d["samples"]}
    name = package.applicant_name(s)
    cv_full = s.cv_path.read_text(encoding="utf-8")
    fi_cv = package.fi_wording(s)
    for post in FICTIONAL:
        g = _grounding(post, s)
        for i, q in enumerate(post["questions"], 1):
            sid = f"{post['id']}-{i}"
            if only_missing and sid in have:
                continue
            r = package.answer_question(g, llm, q, post["lang"], name, cv_full, fi_cv=fi_cv if post["lang"] == "fi" else "")
            d["samples"] = [x for x in d["samples"] if x["id"] != sid] + [{
                "id": sid, "lang": post["lang"], "company": post["company"], "city": post["city"],
                "title": post["title"], "ad": post["ad"], "question": q, "generated": r["text"],
                "reasons": r["reasons"], "model": llm.name, "mine": "",
                "created": dt.datetime.now().isoformat(timespec="seconds"), "updated": ""}]
            _save(d)
    return d["samples"]


def save_mine(sid: str, text: str) -> dict:
    with _lock:
        d = _load()
        for x in d["samples"]:
            if x["id"] == sid:
                x["mine"] = str(text or "")[:8000]
                x["updated"] = dt.datetime.now().isoformat(timespec="seconds")
                _save(d)
                return x
    raise LookupError("Tyylinäytettä ei löydy")
