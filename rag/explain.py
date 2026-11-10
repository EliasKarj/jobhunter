"""Sopivuusselitys: vahvuudet, puutteet ja kokonaisarvio viitteineen ilmoitukseen [A..] ja profiiliin [P..].

Tallennetaan välimuistiin (llm_cache): avaimena ilmoituksen sisältö, profiili, malli ja kehotteen versio.
Sama selitys luetaan välimuistista, kunnes jokin niistä muuttuu tai käyttäjä pyytää uutta.
"""
from __future__ import annotations

import re

from . import config
from .clean import sha1
from .grounding import Grounding, build, cache_get, cache_put, clean_refs
from .llm import LLM

PROMPT_VERSION = "explain-2"   # 2: viitteet poistetaan tekstistä ja palautetaan tekstistä, jos listat ovat tyhjiä

SYSTEM = """Arvioit, miten hyvin työnhakijan profiili sopii työpaikkailmoitukseen. Kirjoita suomeksi.

Lähteet:
- ILMOITUS: palat [A1], [A2], ...
- PROFIILI: hakijan CV:n osiot, osaaminen ja tavoiteroolit [P1], [P2], ...

Säännöt:
- Vahvuus = ilmoituksen vaatimus tai toive, joka löytyy profiilista. Jokaisessa vahvuudessa on oltava
  sekä ilmoitusviite (A) että profiiliviite (P). Älä listaa vahvuutta, jolle et löydä profiilista lähdettä.
- Puute = ilmoituksen vaatimus, jota profiilissa ei ole. Viittaa ilmoituksen kohtaan (A). Merkitse,
  onko se ehdoton vaatimus ("must") vai toive ("nice").
- Älä oleta hakijalle taitoja, joita profiilissa ei mainita. Älä keksi vaatimuksia, joita ilmoituksessa ei ole.
- Huomioi taso: harjoittelija/junior vs. senior ja vaaditut kokemusvuodet.
- verdict: "hyvä", "kohtalainen" tai "heikko". summary: 2-3 lausetta."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["strengths", "gaps", "verdict", "summary"],
    "properties": {
        "strengths": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "ad", "profile"],
            "properties": {"text": {"type": "string"}, "ad": {"type": "array", "items": {"type": "string"}},
                           "profile": {"type": "array", "items": {"type": "string"}}}}},
        "gaps": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "ad", "severity"],
            "properties": {"text": {"type": "string"}, "ad": {"type": "array", "items": {"type": "string"}},
                           "severity": {"type": "string", "enum": ["must", "nice"]}}}},
        "verdict": {"type": "string", "enum": ["hyvä", "kohtalainen", "heikko"]},
        "summary": {"type": "string"},
    },
}


_INLINE = re.compile(r"\s*[(\[](?:\s*(?:[AP]\d{1,2}|must|nice)\s*,?)+[)\]]")


def _text(v) -> str:
    """Viitteet näytetään erikseen, joten tekstiin kirjoitetut (A1)/(P2, P3) poistetaan."""
    return re.sub(r"\s+([,.])", r"\1", _INLINE.sub("", str(v or ""))).strip()


def validate(raw: dict, g: Grounding) -> dict:
    """Pidä vain viitteet, jotka ovat olemassa. Vahvuus ilman profiiliviitettä siirretään 'dropped'-listaan."""
    out = {"strengths": [], "gaps": [], "dropped": [],
           "verdict": raw.get("verdict") if raw.get("verdict") in ("hyvä", "kohtalainen", "heikko") else "kohtalainen",
           "summary": _text(raw.get("summary"))}
    for s in raw.get("strengths") or []:
        # pienet mallit kirjoittavat viitteet joskus vain tekstiin: "(A2) ... (P5)"
        ad = clean_refs(s.get("ad"), g, "A") or clean_refs([s.get("text")], g, "A")
        prof = clean_refs(s.get("profile"), g, "P") or clean_refs([s.get("text")], g, "P")
        item = {"text": _text(s.get("text")), "ad": ad, "profile": prof}
        if not item["text"]:
            continue
        if not prof:
            out["dropped"].append({**item, "reason": "ei lähdettä profiilissa"})
        else:
            out["strengths"].append(item)
    for s in raw.get("gaps") or []:
        item = {"text": _text(s.get("text")),
                "ad": clean_refs(s.get("ad"), g, "A") or clean_refs([s.get("text")], g, "A"),
                "severity": s.get("severity") if s.get("severity") in ("must", "nice") else "nice"}
        if item["text"]:
            out["gaps"].append(item)
    used = {k for x in out["strengths"] + out["gaps"] for k in x["ad"] + x.get("profile", [])}
    out["refs"] = {k: g.ref(k) for k in sorted(used)}
    return out


def explain(g: Grounding, llm: LLM, store=None, refresh: bool = False, cached_only: bool = False) -> dict:
    key = sha1(PROMPT_VERSION, g.hash(), llm.name)
    if store is not None and not refresh:
        hit = cache_get(store, key)
        if hit:
            return hit
    if cached_only:
        raise LookupError("Selitystä ei ole vielä generoitu")
    user = (f"ILMOITUS: {g.job['title']} | {g.job['company']} | {g.job['city']}\n\n{g.ad_block()}\n\n"
            f"PROFIILI:\n{g.profile_block()}")
    raw = llm.complete_json(SYSTEM, [{"role": "user", "content": user}], SCHEMA, max_tokens=2500)
    res = validate(raw, g)
    res.update(job_id=g.job["job_id"], url=g.job["url"], title=g.job["title"], company=g.job["company"])
    if store is not None:
        cache_put(store, key, "explain", g.job["job_id"], llm.name, res)
    return {**res, "cached": False, "model": llm.name}


def explain_url(url: str, s: config.Settings | None = None, refresh: bool = False, cached_only: bool = False) -> dict:
    from . import service
    s = s or config.load()
    st = service.store(s)
    return explain(build(st, s, url), service.llm("explain", s), st, refresh, cached_only)
