"""Hakemusluonnoksen väitteiden tarkistus profiilia vasten. Deterministinen: toimii myös heikolla paikallisella mallilla.

Jokainen lause on väite-ehdokas. Lause liputetaan (status "flag"), jos siinä on
  - tekniikka/työkalu, jota ei ole profiilissa (minä-muotoisessa lauseessa; muuten kelpaa myös ilmoituksesta)
  - luku tai vuosiluku, jota ei ole lähteissä (esim. keksityt "3 vuotta")
Minä-muotoinen lause, jolle ei löydy riittävän samankaltaista profiilipalaa, saa statuksen "check".
Jokaiselle lauseelle palautetaan paras lähde (profiilipala tai ilmoituksen pala) näytettäväksi.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np

# Yleisiä tekniikoita ja työkaluja (sama sanasto kuin webappin puuttuvat taidot + lisäyksiä)
TECH_TERMS = ["TypeScript", "JavaScript", "React", "Vue", "Angular", "Next.js", "Node.js", "Python", "Java",
              "C#", ".NET", "C++", "Kotlin", "Swift", "PHP", "Rust", "Go", "Golang", "Scala", "Ruby", "SQL",
              "PostgreSQL", "MySQL", "MongoDB", "Docker", "Kubernetes", "AWS", "Azure", "GCP", "Terraform", "Ansible",
              "Linux", "Git", "CI/CD", "Jenkins", "GitHub Actions", "GitLab", "REST", "GraphQL", "Microservices",
              "Scrum", "Agile", "Jira", "Figma", "HTML", "CSS", "Tailwind", "Spring", "Django", "Flask", "FastAPI",
              "Unity", "Unreal", "Matlab", "LabVIEW", "Power BI", "Tableau", "Power Platform", "SAP", "Salesforce",
              "Machine learning", "LLM", "DevOps", "Cypress", "Playwright", "Jest", "Selenium", "Kafka", "Redis",
              "Elasticsearch", "Firebase", "Supabase", "Prisma", "Embedded", "FPGA", "PLC", "SCADA", "Siemens",
              "TIA Portal", "AutoCAD", "SolidWorks", "Excel", "Snowflake", "Databricks", "Spark", "Hadoop",
              "TensorFlow", "PyTorch", "Pandas", "OpenShift", "VMware", "Windows Server", "Active Directory",
              "ServiceNow", "ITIL", "Bash", "PowerShell", "YAML", "Android", "iOS", "Flutter", "Vercel", "Azure DevOps", "Copilot", "full-stack", "full stack", "frontend", "front end", "backend",
              "back end", "Canvas", "webhook"]
# Suomenkieliset käsitteet: jos minä-lause sisältää vasemman kannan, profiilissa pitää olla jokin oikeista
CONCEPTS = [
    (("koneoppi", "machine learning"), ("koneoppi", "machine learning", "ml-", "scikit", "tensorflow", "pytorch")),
    (("syväoppi", "deep learning"), ("syväoppi", "deep learning", "neural", "neuroverk")),
    (("pilvi", "cloud"), ("pilvi", "cloud", "azure", "aws", "gcp", "vercel", "supabase")),
    (("tietokan", "database"), ("tietokan", "database", "sql", "postgres", "supabase", "mongo")),
    (("kontti", "container"), ("kontti", "container", "docker", "kubernetes")),
    (("testaus", "testau", "test automation", "testiautom"), ("testa", "test", "jest", "cypress", "playwright")),
    (("tietoturv", "kyberturv", "security"), ("tietoturv", "security", "rls", "row-level")),
    (("johtami", "esimie", "tiiminveto", "team lead", "management"), ("johta", "esimie", "lead", "manag")),
    (("sulautet", "embedded"), ("sulautet", "embedded", "mikrokontrol", "arduino")),
]
_MOTIVATION = re.compile(r"\b(kiinnostunut|kiinnostaa|innostunut|innostaa|valmis|haluan|haluaisin|odotan|toivon|motivoi|"
                         r"interested|eager|excited|look forward|willing|keen|would love|happy to)\w*", re.I)
_SKILL_CLAIM = re.compile(r"\b(osaan|osaamis|kokemus|kokemusta|käyttänyt|käytin|hallitsen|työskennel|perehtyn|toteutin|"
                          r"toteuttanut|rakensin|rakentanut|kehitin|kehittänyt|vastasin|vastannut|tehnyt|tein|"
                          r"experience|experienced|proficient|skilled|built|developed|worked|used)\w*", re.I)
# Tämän jälkeiset termit eivät ole osaamisväitteitä: oppimiskohde tai rehellinen "ei vielä kokemusta"
_LEARN = re.compile(r"\b(oppimaan|opettelemaan|opiskelemaan|oppia|opetella|tutustumaan|perehtymään|learn|learning|pick up|"
                    r"ei ole|en ole|en osaa|en vielä|no experience|not yet|have not|haven't|do not have|don't have)\w*", re.I)
_PLACEHOLDER = re.compile(r"\[[^\]]{2,80}\]")
_COMMON_CAPS = {"IT", "CV", "ICT", "HR", "OY", "AB", "EU", "FI", "EN", "OK", "AI", "I", "QA", "CEO", "CTO", "TV"}
_FIRST_PERSON = re.compile(
    r"\b(minä|minulla|minua|minun|minulle|minusta|olen|osaan|opiskelen|opiskelin|tein|toteutin|rakensin|vastasin|"
    r"työskentelin|työskentelen|kehitin|kehitän|haluan|uskon|koen|pystyn|hallitsen|käytin|käytän|olin|toin|"
    r"opin|hain|innostun|motivoi|vahvuuteni|osaamiseni|kokemukseni|taustani|projektini|suostun|sitoudun|lupaan|"
    r"i|i'm|i've|i'd|my|me|myself)\b", re.I)
_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")
_SENT = re.compile(r"[^.!?\n]+[.!?]?")
_TOKEN = re.compile(r"(?<![\w])[A-Za-zÅÄÖåäö.#+][\w#+./-]*[\w#+]|(?<![\w])[A-Z]{2,}(?![\w])")
SUPPORT_MIN = 0.55   # minä-lauseen ja parhaan profiilipalan kosini (bge-m3), alle = "tarkista"


@dataclass
class Claim:
    text: str
    start: int
    end: int
    status: str = "ok"                 # ok | check | flag
    kind: str = "fact"                 # fact | motivation (kiinnostus/valmius ei ole tosiasiaväite)
    reasons: list[str] = field(default_factory=list)
    first_person: bool = False
    source: dict | None = None         # {"id": "P3", "type": "profile", "source_ref": ..., "text": ..., "sim": ...}


def sentences(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in _SENT.finditer(text):
        s = m.group(0)
        lead = len(s) - len(s.lstrip())
        t = s.strip()
        if len(t) >= 20 and not re.fullmatch(r"\[[^\]]*\]", t):
            out.append((m.start() + lead, m.start() + lead + len(t), t))
    return out


def _has(term: str, text_low: str) -> bool:
    t = term.lower()
    if re.fullmatch(r"[\w ]+", t):
        return re.search(rf"(?<![\w]){re.escape(t)}", text_low) is not None   # taivutus: python -> pythonia ok
    return t in text_low


# full-stack tarkoittaa määritelmän mukaan sekä front endiä että back endiä
_IMPLIED = {"frontend": ("fullstack",), "backend": ("fullstack",)}


def _spellings(term: str) -> set[str]:
    """Kirjoitusasut, jotka ovat sama asia: back end = back-end = backend."""
    t = term.lower().strip()
    if not re.search(r"[\s-]", t) and t not in ("frontend", "backend", "fullstack"):
        return {t}
    parts = re.split(r"[\s-]+", t)
    return {" ".join(parts), "-".join(parts), "".join(parts)}


def _present(term: str, text_low: str) -> bool:
    """Termi tai sen taivutuskanta löytyy (DevOpsin -> DevOps, Pythonilla -> Python). Sama termi eri kirjoitusasulla
    (back end / backend) ja full-stackin kattamat front end ja back end lasketaan löytyneiksi."""
    if _has(term, text_low):
        return True
    forms = _spellings(term)
    if any(_has(f, text_low) for f in forms if f != term.lower()):
        return True
    for key, implied_by in _IMPLIED.items():
        if key in {f.replace(" ", "").replace("-", "") for f in forms}:
            if any(_has(g, text_low) for x in implied_by for g in _spellings(x if x != "fullstack" else "full-stack")):
                return True
    m = re.fullmatch(r"(.*?[A-Z0-9#+.][A-Za-z0-9#+.]*?)([a-zåäö]{1,4})", term)
    return bool(m) and len(m.group(1)) >= 2 and _has(m.group(1), text_low)


def tech_terms(sentence: str) -> list[str]:
    """Tekniikalta näyttävät termit: tunnettu sanasto + sisäiset isot kirjaimet / lyhenteet / symbolit.
    Pidempi termi syrjäyttää osansa (Azure DevOps -> ei erikseen Azure)."""
    low = sentence.lower()
    found: list[str] = []
    for t in sorted(TECH_TERMS, key=len, reverse=True):
        if _has(t, low) and not any(t.lower() in f.lower() for f in found):
            found.append(t)
    for tok in _TOKEN.findall(sentence):
        tok = tok.strip(".-/").split("-")[0]   # yhdyssana: DevOps-taidot -> DevOps
        if len(tok) < 2 or tok.upper() in _COMMON_CAPS or any(f.lower() in tok.lower() for f in found):
            continue
        inner_caps = any(c.isupper() for c in tok[1:]) and any(c.islower() for c in tok)
        caps = tok.isupper() and len(tok) >= 2 and tok.isalpha()
        symbols = any(c in tok for c in "#+") or bool(re.search(r"\w\.\w", tok))
        if inner_caps or caps or symbols:
            found.append(tok)
    return list(dict.fromkeys(found))


_COMMON_WORDS = {"pilvi", "data", "koodi", "web", "tiimi", "työ", "työt", "auto", "kela", "into", "taito", "rooli",
                 "team", "cloud", "code", "tools", "work", "skills", "teams", "agile", "tech", "apps", "app", "ui", "ux"}
_LIST_SPLIT = re.compile(r"[,;/()]|\s+(?:ja|tai|and|or|sekä|esim\.|e\.g\.)\s+", re.I)


def ad_terms(ad_text: str) -> list[str]:
    """Ilmoituksen tekniikkalistojen kaikki kohdat: "Rust, axum, sqlx ja egui" -> myös pienellä kirjoitetut nimet.
    Lista hyväksytään, jos jokin sen kohdista on tunnettu tekniikka."""
    out: list[str] = []
    known = {t.lower() for t in TECH_TERMS}
    for line in re.split(r"\n|:|(?<=[a-zåäö0-9)])\.\s+(?=[A-ZÅÄÖ])", ad_text):   # "esim. axum" ei katkea
        items = [i.strip(" -•*\"'.") for i in _LIST_SPLIT.split(line)]
        items = [i for i in items if i and i.lower() not in ("esim", "e.g", "mm", "yms", "etc")]
        if len(items) < 2 or not any(tech_terms(i) for i in items):
            continue
        for i in items:
            # lyhyet nimet (sqlx, axum, egui); pitkät suomenkieliset yleissanat ("relaatiotietokannat") eivät ole tuotenimiä
            low = i.lower()
            # tuotenimen näköinen: isoja kirjaimia/numeroita (ETL, WCAG) tai lyhyt pienaakkosnimi ilman ä/ö:tä (sqlx, axum)
            productish = bool(re.search(r"[A-Z0-9]", i[1:] if i[:1].isupper() and i[1:].islower() else i)) or \
                (len(i) <= 5 and i.islower() and not re.search(r"[äöå]", i) and low not in _COMMON_WORDS)
            if 2 <= len(i) <= 12 and " " not in i and low not in known and i.upper() not in _COMMON_CAPS and productish \
                    and re.fullmatch(r"[\w+#.\-]+", i):
                out.append(i)
    # taivutusmuoto pois, jos perusmuoto on jo listalla (axumin -> axum)
    return [t for t in dict.fromkeys(out) if not any(t.lower() != o.lower() and t.lower().startswith(o.lower()) for o in out)]


def check(text: str, profile: dict[str, str], ad: dict[str, str], allowed: str = "",
          embed: Callable[[list[str]], np.ndarray] | None = None,
          vectors: dict[str, np.ndarray] | None = None, about_applicant: bool = False,
          extra_terms: list[str] | None = None) -> list[Claim]:
    """profile/ad: tunniste -> teksti. allowed: muut sallitut nimet (yritys, otsikko, hakijan nimi).
    embed(lauseet) -> normalisoidut vektorit; vectors: tunniste -> lähdepalan vektori."""
    prof_low = "\n".join(profile.values()).lower()
    ad_low = "\n".join(ad.values()).lower()
    allowed_low = allowed.lower()
    try:   # hakijan omat ilmaukset tyylinäytteistä: ei liputeta väitteeksi tai tekoälyfraasiksi (ei faktojen lähde)
        from .styleprofile import own_text
        own = own_text().lower()
    except Exception:  # noqa: BLE001
        own = ""
    sents = sentences(text)
    # about_applicant: CV:n profiiliteksti ("IT student with DevOps skills") on aina väite hakijasta, vaikkei minä-muodossa
    claims = [Claim(t, a, b, first_person=about_applicant or bool(_FIRST_PERSON.search(t))) for a, b, t in sents]
    if embed is not None and vectors and claims:
        keys = list(vectors)
        mat = np.stack([vectors[k] for k in keys])
        sims = embed([c.text for c in claims]) @ mat.T
    else:
        sims = None
    for i, c in enumerate(claims):
        text = _PLACEHOLDER.sub(" ", c.text)   # [Täytä itse] -kohdat eivät ole väitteitä
        # "Olen kiinnostunut ERP-järjestelmistä" / "valmis oppimaan Rustia" ei väitä osaamista: ilmoituksen termit sallitaan
        motivation = c.first_person and bool(_MOTIVATION.search(text)) and not _SKILL_CLAIM.search(text)
        claim_about_me = c.first_person and not motivation
        pool_low = prof_low + "\n" + allowed_low + ("" if claim_about_me else "\n" + ad_low)
        # "Osaan X:ää ja olen valmis oppimaan Rustia": oppimisverbin jälkeiset termit ovat oppimiskohteita, eivät väitteitä
        learn = _LEARN.search(text) if claim_about_me else None
        def learning(term: str) -> bool:
            i = text.lower().find(term.lower())
            return bool(learn) and i > learn.start()
        for t in tech_terms(text):
            if learning(t):
                continue
            if not _present(t, pool_low):
                where = "profiilissa" if claim_about_me else "profiilissa eikä ilmoituksessa"
                c.reasons.append(f"\"{t}\" ei esiinny {where}")
        if claim_about_me:
            low = text.lower()
            for t in extra_terms or []:   # ilmoituksen listoista: sqlx, axum, egui...
                if _has(t, low) and not learning(t) and not _present(t, pool_low) and not any(t.lower() in r.lower() for r in c.reasons):
                    c.reasons.append(f"\"{t}\" ei esiinny profiilissa (mainitaan ilmoituksessa)")
            for stems, need in CONCEPTS:
                hit = next((x for x in stems if x in low), None)
                if hit and not any(n in pool_low for n in need):
                    c.reasons.append(f"\"{hit}…\" ei esiinny profiilissa")
        if c.first_person:
            # luonteenpiirre, johtaminen, tulos, laajuus tai johtopäätös hakijasta vain, jos lähde sanoo sen itse
            from .claims import unsupported_claims
            c.reasons += [f"väite \"{w}\" ei esiinny profiilissa" for w in unsupported_claims(text, prof_low + "\n" + allowed_low + "\n" + own)]
        from .claims import cliches   # saatekirje ja vastaukset: geneeriset tekoälyfraasit näkyviin tarkistettaviksi
        c.reasons += [f"kuulostaa tekoälyn tekstiltä: \"{w}\"" for w in cliches(text, prof_low + "\n" + own)]
        for n in _NUM.findall(text):
            if not re.search(rf"(?<![\d]){re.escape(n)}(?![\d])", pool_low):
                c.reasons.append(f"luku {n} ei löydy lähteistä")
        if c.reasons:
            c.status = "flag"
        elif motivation:
            c.kind = "motivation"
        if c.status == "ok" and _PLACEHOLDER.search(c.text):
            c.status = "check"
            c.reasons.append("sisältää täytettävän kohdan hakasulkeissa")
        if sims is not None:
            row = sims[i]
            cand = [j for j, k in enumerate(keys) if (k.startswith("P") or not c.first_person)]
            if cand:
                j = max(cand, key=lambda x: row[x])
                k = keys[j]
                c.source = {"id": k, "type": "profile" if k.startswith("P") else "ad", "sim": round(float(row[j]), 3)}
                pj = [x for x in cand if keys[x].startswith("P")]
                best_p = max((row[x] for x in pj), default=-1)
                if c.first_person and c.status == "ok" and c.kind == "fact" and best_p < SUPPORT_MIN:
                    c.status = "check"
                    c.reasons.append("profiilista ei löydy selvää lähdettä tälle väitteelle")
    return claims


def summarize(claims: list[Claim]) -> dict:
    return {"claims": [asdict(c) for c in claims],
            "flagged": sum(c.status == "flag" for c in claims),
            "check": sum(c.status == "check" for c in claims)}
