"""Hakijan oma kirjoitustyyli tyylinäytteistä (rag/stylesamples.py): mallin luonnos -> hakijan oma versio.

build() pyytää kirjoitusmallilta tiiviin kuvauksen siitä, mitä hakija muuttaa ja miten hän kirjoittaa, ja tallentaa
sen tiedostoon data/style_profile.json. prompt_block() liittää kuvauksen ja 1-2 hakijan omaa tekstiä kirjoituspyyntöön.
Omat tekstit ovat vain tyylin mallina: faktat tulevat edelleen vain CV:stä (PROFIILI-osio).
"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import config, stylesamples

PATH = config.DATA / "style_profile.json"

BUILD_SYSTEM = """You analyse how a job applicant edits AI-written application texts, to describe the applicant's own
writing style. You get pairs: DRAFT (written by a model) and MINE (the applicant's final version). Describe only what
the edits and the applicant's own wording show. Do not invent preferences the pairs do not show.

Write the style guide IN FINNISH as 6-12 short bullet points, concrete enough for another model to follow:
tense, sentence openings and length, how texts start and end, words or phrases the applicant adds or removes,
how the applicant refers to companies or clients, tone (formal/personal, hedging). Quote short example phrases
from MINE where useful. Do not list facts about the applicant (skills, projects): only style.

Never write a rule that would let the model claim skills, experience or facts (the applicant may have added such
claims in MINE; ignore them: facts come only from the CV). Never tell the model to remove [Täytä itse] / [Fill in
yourself] placeholders. Ignore typos and punctuation mistakes in MINE: do not turn them into rules. Do not
generalise from a single sample if another sample shows the opposite.

Standing preference stated by the applicant: do not list gaps ("I have not done X or Y"). Never write a rule that
asks to state missing skills; at most "say briefly you are eager to learn more about it"."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["rules"],
          "properties": {"rules": {"type": "array", "items": {"type": "string"}}}}


def _edited(samples: list[dict]) -> list[dict]:
    return [s for s in samples if s.get("updated") and (s.get("mine") or "").strip()]


def own_text() -> str:
    """Hakijan omat versiot yhtenä tekstinä: tarkistukset sallivat ilmaukset, joita hakija on itse käyttänyt."""
    return "\n".join(s["mine"] for s in _edited(stylesamples.listing()))


def load() -> dict | None:
    return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else None


def build(s=None, llm=None) -> dict:
    from . import service
    s = s or config.load()
    pairs = _edited(stylesamples.listing())
    if not pairs:
        raise ValueError("Tyylinäytteitä ei ole vielä muokattu (Profiili → Kirjoitustyyli).")
    llm = llm or service.llm("writing", s)
    user = "\n\n".join(f"### {p['question']} ({p['lang']})\nDRAFT:\n{p['generated']}\n\nMINE:\n{p['mine']}" for p in pairs)
    raw = llm.complete_json(BUILD_SYSTEM, [{"role": "user", "content": user}], SCHEMA, max_tokens=6000)
    rules = [str(r).strip() for r in raw.get("rules") or [] if str(r).strip()][:14]
    prof = {"rules": rules, "samples": len(pairs), "model": llm.name,
            "created": dt.datetime.now().isoformat(timespec="seconds")}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    return prof


def _examples(lang: str, long: bool, n: int) -> list[str]:
    """Hakijan omat tekstit esimerkeiksi: sama kieli ja pituusluokka ensin, eniten muokatut ensin."""
    pairs = [p for p in _edited(stylesamples.listing()) if p["lang"] == lang] or _edited(stylesamples.listing())

    def changed(p):   # kuinka paljon hakija muutti: mitä enemmän, sitä enemmän tyyliä esimerkissä
        a, b = set(re.findall(r"\w+", p["generated"].lower())), set(re.findall(r"\w+", p["mine"].lower()))
        return len(a ^ b) / max(1, len(a | b))
    pairs.sort(key=lambda p: ((len(p["mine"].split()) > 100) != long, -changed(p)))
    return [p["mine"] for p in pairs[:n]]


def names_rule(lang: str) -> str:
    """Nimet, joita hakija ei halua toistaa (profile.json: discreet_names): käytä kuvausta."""
    try:
        names = json.loads((config.DATA / "profile.json").read_text(encoding="utf-8")).get("discreet_names") or {}
    except (OSError, json.JSONDecodeError):
        return ""
    if not names:
        return ""
    if lang == "fi":
        return "NIMET: älä mainitse nimeltä " + "; ".join(
            f"{k} (kirjoita sen sijaan esim. \"{v.get('fi', '')}\")" for k, v in names.items()) + "."
    return "NAMES: do not name " + "; ".join(f"{k} (write e.g. \"{v.get('en', '')}\" instead)" for k, v in names.items()) + "."


def prompt_block(lang: str, long: bool = False) -> str:
    """Kirjoituspyyntöön: opittu tyyli, 1-2 hakijan omaa tekstiä ja nimisääntö. Tyhjä, jos mitään ei ole."""
    out = []
    p = load()
    if p and p.get("rules"):
        head = "HAKIJAN OMA KIRJOITUSTYYLI (opittu hakijan muokkauksista; noudata):" if lang == "fi" else \
            "THE APPLICANT'S OWN WRITING STYLE (learned from the applicant's edits; follow it):"
        out.append(head + "\n" + "\n".join(f"- {r}" for r in p["rules"]))
        ex = _examples(lang, long, 2 if long else 1)
        if ex:
            out.append(("HAKIJAN OMA TEKSTI ESIMERKKINÄ (vain äänensävyn ja rakenteen malliksi: älä kopioi lauseita äläkä ota "
                        "siitä faktoja, faktat vain PROFIILI-osiosta):" if lang == "fi" else
                        "THE APPLICANT'S OWN TEXT AS AN EXAMPLE (tone and structure only: do not copy sentences or take facts "
                        "from it; facts only from the PROFILE):") + "\n" + "\n---\n".join(ex))
    nr = names_rule(lang)
    if nr:
        out.append(nr)
    return "\n\n".join(out)
