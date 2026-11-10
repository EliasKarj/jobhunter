"""Kirjoitetun tekstin (profiili, rivien muotoilut) väitesanat: ei mitään, mitä lähteessä ei ole.

Kielimalli lisää helposti uskottavan kuuloisen väitteen, jota CV:ssä ei ole: luonteenpiirteen ("nopea oppija"),
johtamisen ("led"), tuloksen ("improved performance"), laajuuden ("production", "scalable") tai johtopäätöksen
("demonstrating a focus on"). Tekniikka- ja lukutarkistus ei näe näitä. Tämä lista hylkää ne koodissa aina,
kun sana tai sen vartalo ei esiinny lähteessä.

novel_words antaa lisäksi kaikki sanat, joita lähteessä ei ole: ne näytetään faktantarkistajalle käytäväksi läpi.
"""
from __future__ import annotations

import re

# vartalot (sanan alku); kirjoitettu pienellä. Englanti ja suomi.
CLAIM_STEMS = [
    # luonteenpiirteet ja itsearviot
    "passion", "motivated", "enthusias", "eager", "dedicated", "hardworking", "hard-working", "detail-oriented",
    "results-driven", "self-starter", "team player", "fast learner", "quick learner", "proactive", "dynamic",
    "excellent", "outstanding", "exceptional", "strong", "proven", "solid", "extensive", "deep", "expert",
    "skilled", "proficient", "talented", "reliable", "creative", "innovative",
    "intohimo", "motivoitun", "innokas", "innostun", "omistautu", "ahkera", "tunnollinen", "huolellinen",
    "oma-aloittei", "nopea oppija", "nopeasti oppiva", "tiimipelaaja", "ratkaisukeskei", "erinomai", "vahva",
    "vankka", "laaja", "syvällinen", "asiantunt", "taitava", "osaava", "kokenut", "luotettav", "paineensieto",
    "luova", "innovatiivi",
    # johtaminen ja vastuu
    "led ", "lead", "managed", "manager", "mentor", "supervis", "owned", "spearhead", "head of", "senior",
    "johdin", "johtanut", "johtaj", "vetäj", "vedin", "esihenkilö", "mentoroin", "ohjasin", "senior",
    # tulokset ja vaikutus
    "improv", "increas", "reduc", "optimi", "boost", "accelerat", "achiev", "deliver", "impact", "success",
    "paransin", "parantanut", "paransi", "nopeutin", "lisäsin", "vähensin", "pienensin", "optimoin", "tehostin",
    "saavutin", "onnistu", "vaikutt",
    # laajuus
    "production", "scalab", "robust", "enterprise", "large-scale", "high-traffic", "mission-critical",
    "tuotanto", "skaalautuv", "laajamittai", "kriittinen",
    # johtopäätökset hakijasta
    "demonstrat", "showcas", "proving", "osoittaa", "osoittavat", "todistaa",
    "taught me", "i learn best", "opettanut", "opetti minu", "opin parhaiten",
    # lupaukset hakijan puolesta (suostumus, saatavuus, opiskelijastatus): vain hakija itse voi antaa ne
    "suostun", "i consent", "i agree to undergo", "willing to undergo", "kirjoilla", "enrolled throughout",
    "valmis muuttamaan", "willing to relocate",
]
# Fraasit, joista rekrytoija tunnistaa tekoälyn kirjoittaman tekstin. Ne eivät ole välttämättä vääriä väitteitä,
# mutta tekevät tekstistä geneerisen; sallitaan vain, jos hakija on itse kirjoittanut saman lähteeseen.
CLICHES = [
    "demonstrating a", "showcasing", "a testament to", "passionate about", "leverag", "seamless", "cutting-edge",
    "fast-paced", "dynamic environment", "ever-evolving", "ever-changing", "rapidly evolving", "delve", "tapestry",
    "unlock", "harness", "foster", "synerg", "holistic", "game-changer", "drive innovation",
    "i am excited to", "i'm excited to", "i am thrilled", "eager to contribute", "make a meaningful", "in today's",
    "commitment to excellence", "wealth of", "track record", "hit the ground running", "value-add",
    "intohimoinen", "intohimoa", "dynaamisessa ympäristö", "dynaamiseen ympäristö", "dynaaminen ympäristö",
    "nopeasti muuttuva", "nopeatempoise", "jatkuvasti kehittyvä",
    "nykypäivän", "lisäarvo", "synergi", "kokonaisvaltai", "innostunut mahdollisuudesta", "olen innoissani",
    "vahva näyttö", "—",
]
_WORD = re.compile(r"[a-zåäö]+(?:-[a-zåäö]+)*")


def _has_stem(stem: str, low: str) -> bool:
    return re.search(rf"(?<![a-zåäö]){re.escape(stem.rstrip())}", low) is not None


def unsupported_claims(text: str, source: str) -> list[str]:
    """Väitesanat, jotka ovat tekstissä mutta eivät lähteessä (sana tai vartalo)."""
    low, src = text.lower(), source.lower()
    hits = list(dict.fromkeys(s.strip() for s in CLAIM_STEMS if _has_stem(s, low) and not _has_stem(s, src)))
    return [h for h in hits if not any(o != h and h.startswith(o) for o in hits)]   # paransin + paransi -> paransi


def cliches(text: str, source: str = "") -> list[str]:
    """Tekoälyltä kuulostavat fraasit, joita hakija ei ole itse käyttänyt lähteessä."""
    low, src = text.lower(), source.lower()
    return [c for c in CLICHES if (c in low if not c[0].isalpha() else _has_stem(c, low))
            and not (c in src if not c[0].isalpha() else _has_stem(c, src))]


def novel_words(text: str, source: str, min_len: int = 5) -> list[str]:
    """Tekstin sanat, joita lähteessä ei ole edes saman alun (5 merkkiä) taivutusmuotona."""
    src_words = set(_WORD.findall(source.lower()))
    stems = {w[:5] for w in src_words if len(w) >= 5}
    out = []
    for w in _WORD.findall(text.lower()):
        if len(w) >= min_len and w not in src_words and w[:5] not in stems:
            out.append(w)
    return list(dict.fromkeys(out))
