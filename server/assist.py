"""Selainlaajennuksen vastausehdotukset: portaalin kysymys -> valmis vastaus.

Järjestys: yhteystiedot CV:stä ja vakiovastaukset (käyttäjän omat tiedot), sitten hakemuspaketin vastaus.
Kielimallia käytetään vain, kun käyttäjä pyytää sitä erikseen (POST /api/assist/generate). Omia tietoja, kuten
palkkatoivetta tai aloituspäivää, malli ei koskaan arvaa: tyhjästä kentästä tulee ohje täyttää vakiovastaus.
"""
from __future__ import annotations
from datadir import DATA

import re
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
KEY_FILE = DATA / "extension.key"

# (lähde, avain, tunnistus). Järjestys ratkaisee: tarkemmat ensin.
QUESTIONS: list[tuple[str, str, str]] = [
    ("fact", "salary", r"palkka|salary|compensation|pay expectation"),
    ("fact", "start_date", r"aloit(?:taa|us)|start date|when can you start|availability|earliest|voit tulla töihin"),
    ("fact", "drivers_license", r"ajokortti|ajo-oikeus|driver'?s? licen[cs]e|driving licen[cs]e"),
    ("fact", "work_permit", r"työlupa|oikeus työskennellä|työskentelyoikeus|work permit|right to work|authori[sz]ed to work|visa"),
    ("fact", "references", r"suosittelij|referenss|references?\b|referees?"),
    ("fact", "links", r"github|linkedin|portfolio|verkkosivu|website"),
    ("fact", "location", r"muutt|relocat|asuinpaikka|paikkakunta|sijainti|where do you live|location"),
    ("fact", "work_type", r"työaika|osa-aika|kokoaika|etätyö|full[- ]time|part[- ]time|remote|hybrid|työmuoto"),
    ("package", "cover_letter", r"saatekirje|hakemuskirje|hakemusteksti|vapaamuotoinen hakemus|cover letter|motivation letter"),
    ("package", "why_you", r"miksi (?:juuri )?sinut|miksi (?:meidän|juuri sinä)|why should we|why (?:are you|would you be) the|why hire|what makes you"),
    ("package", "why_apply", r"miksi haet|miksi olet kiinnostunut|miksi tämä|hakuperuste|motivaatio|why (?:are you )?(?:apply|interested)|why do you want|why this|motivation"),
    ("package", "strengths", r"vahvuu|strength|parhaat puolesi"),
    ("package", "develop", r"kehittä|kehitys(?:kohde|tarve)|heikkou|weakness|improve|develop"),
    ("package", "about_me", r"kerro itsestäsi|kerro lyhyesti|esittele itsesi|tell us about yourself|about you|introduce yourself|describe yourself"),
]
# Lyhyet kentät (etunimi, sähköposti...): vain jos koko otsikko on lähes pelkkä kentän nimi
CONTACT: list[tuple[str, str]] = [
    ("first_name", r"etunimi|etunimet|first name|given name"),
    ("last_name", r"sukunimi|last name|surname|family name"),
    ("full_name", r"^(?:koko )?nimi\b|^(?:full )?name\b"),
    ("email", r"sähköposti|e-?mail"),
    ("phone", r"puhelin|matkapuhelin|phone|mobile"),
    ("city", r"^kaupunki|^kotikunta|^city"),
]
CONTACT_LABELS = {"first_name": "Etunimi", "last_name": "Sukunimi", "full_name": "Nimi", "email": "Sähköposti",
                  "phone": "Puhelin", "city": "Kaupunki"}


def classify(question: str) -> tuple[str, str] | None:
    q = " ".join(question.lower().split())
    if len(q) <= 40:
        for key, pat in CONTACT:
            if re.search(pat, q):
                return "contact", key
    for kind, key, pat in QUESTIONS:
        if re.search(pat, q):
            return kind, key
    return None


def contact_info(cv_md: str) -> dict[str, str]:
    """Yhteystiedot CV:n otsikosta. Nimi ensimmäiseltä #-riviltä, muut tunnistetaan muodosta."""
    out: dict[str, str] = {}
    head = cv_md.split("\n## ", 1)[0]
    m = re.search(r"^# (.+)$", head, re.M)
    if m:
        out["full_name"] = m.group(1).strip()
        parts = out["full_name"].split()
        out["first_name"], out["last_name"] = parts[0], " ".join(parts[1:])
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", head)
    if m:
        out["email"] = m.group(0)
    m = re.search(r"(?:\+?\d[\d ()-]{6,}\d)", head)
    if m:
        out["phone"] = m.group(0).strip()
    m = re.search(r"^([A-ZÅÄÖ][\wåäö-]+),? [A-ZÅÄÖ]\w+ \|", head, re.M)
    if m:
        out["city"] = m.group(1)
    return out


def suggest(question: str, answers: list[dict], draft: dict | None, contact: dict[str, str]) -> dict:
    """Valmis ehdotus ilman kielimallia. can_generate: kysymykseen voi pyytää mallilta ehdotuksen."""
    hit = classify(question)
    res: dict = {"question": question, "match": hit[1] if hit else None, "suggestions": [], "hint": "", "can_generate": False}
    if hit and hit[0] == "contact":
        if contact.get(hit[1]):
            res["suggestions"].append({"source": "CV", "label": CONTACT_LABELS[hit[1]], "text": contact[hit[1]], "flagged": 0, "check": 0})
        return res
    if hit and hit[0] == "fact":
        f = next((a for a in answers if a["key"] == hit[1]), None)
        if f and f["value"].strip():
            res["suggestions"].append({"source": "Vakiovastaus", "label": f["label"], "text": f["value"], "flagged": 0, "check": 0})
        else:
            res["hint"] = f"Täytä ensin vakiovastaus \"{f['label'] if f else hit[1]}\": Jobhunter → Profiili → Vakiovastaukset."
        return res   # omia tietoja malli ei arvaa
    if hit and draft:
        s = next((x for x in draft.get("sections", []) if x.get("key") == hit[1] and x.get("text", "").strip()), None)
        if s:
            v = s.get("verify") or {}
            res["suggestions"].append({"source": "Hakemuspaketti", "label": s.get("title", ""), "text": s["text"],
                                       "flagged": v.get("flagged", 0), "check": v.get("check", 0)})
    res["can_generate"] = True
    if not res["suggestions"]:
        res["hint"] = "Tälle kysymykselle ei ole valmista vastausta." if draft else \
            "Tälle hakemukselle ei ole vielä hakemuspakettia. Voit luoda sen Jobhunterissa tai pyytää ehdotuksen."
    return res


def match_job(items: list[dict], page_title: str, host: str) -> str | None:
    """Arvaa hakemus sivun otsikosta tai osoitteesta yrityksen nimellä. Käyttäjä voi aina vaihtaa valintaa."""
    text = f"{page_title} {host}".lower()
    for it in items:
        name = re.sub(r"\b(oy|oyj|ab|ltd|inc|gmbh)\b", "", (it.get("company") or "").lower()).strip()
        if len(name) >= 3 and (name in text or name.replace(" ", "") in text):
            return it["url"]
    return None


def extension_key(path: Path | None = None) -> str:
    p = path or KEY_FILE
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(24)
    p.write_text(key, encoding="utf-8")
    return key
