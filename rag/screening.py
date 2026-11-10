"""Koneellinen esiseulonta: näkyykö ilmoituksen tekniikka räätälöidyssä CV:ssä, ja millä näytöllä.

Oikeat seulontajärjestelmät (ATS-avainsanahaku ja kielimallipohjaiset seulojat) etsivät ilmoituksen termejä
CV:stä ja painottavat taitoa, joka näkyy työ- tai projektirivillä, enemmän kuin pelkkää taitolistaa. Tämä
moduuli laskee saman ilman kielimallia ja kertoo, mikä on korjattavissa: termi on faktapohjassa mutta
puuttuu CV:stä, tai näkyy vain taitolistassa vaikka faktapohjan rivi näyttäisi sen käytössä.

Termi, jota faktapohjassa ei ole, on tieto hakijasta, ei CV:n vika: sitä ei koskaan ehdoteta lisättäväksi.
"""
from __future__ import annotations

import re

from . import cvtailor as ct
from .verify import _present, ad_terms, tech_terms

# Sama tekniikka eri kirjoitusasuissa. Vain täsmälleen sama asia: ei päättelyä (GitHub Actions ei ole "CI/CD").
ALIASES: list[tuple[str, ...]] = [
    ("JavaScript", "JS"), ("TypeScript", "TS"), ("PostgreSQL", "Postgres", "psql"), ("Kubernetes", "k8s"),
    ("Node.js", "NodeJS"), ("React", "React.js", "ReactJS"), ("Vue", "Vue.js", "VueJS"),
    ("Next.js", "NextJS"), ("C#", "C sharp"), ("C++", "cpp"), (".NET", "dotnet"),
    ("Amazon Web Services", "AWS"), ("Google Cloud", "GCP", "Google Cloud Platform"), ("Microsoft Azure", "Azure"),
    ("machine learning", "ML", "koneoppiminen"), ("artificial intelligence", "AI", "tekoäly"),
    ("REST API", "REST", "RESTful"), ("Tailwind CSS", "Tailwind"), ("MongoDB", "Mongo"),
    ("scikit-learn", "sklearn"),
]
_ALIAS = {a.lower(): grp for grp in ALIASES for a in grp}
_DOMAIN = re.compile(r"(?i)^(www\.)?[\w-]+\.(com|fi|io|net|org|eu|se|de)(/|$)")
_NOT_REQ = {"linkedin", "epassi", "smartum", "edenred", "instagram", "facebook", "tiktok", "youtube", "twitter",
            "duunitori", "monster", "jobly", "oikotie", "teams", "hr", "ceo", "cto", "it", "ict", "oy", "ab", "ry", "faq"}


def aliases(term: str) -> tuple[str, ...]:
    return _ALIAS.get(term.lower(), (term,))


def present_any(term: str, text_low: str) -> str | None:
    """Termi tai sen täsmällinen vastine tekstissä: palauttaa löytyneen kirjoitusasun."""
    for a in aliases(term):
        if _present(a, text_low):
            return a
    return None


def requirements(ad_text: str) -> list[str]:
    """Ilmoituksen tekniikkatermit, vastineet yhdistettynä (JS ja JavaScript ovat yksi vaatimus)."""
    found = tech_terms(ad_text) + ad_terms(ad_text)
    for a in _ALIAS:   # vastineiden kirjoitusasut, joita tekniikkasanasto ei tunne (Postgres, k8s)
        if re.search(rf"(?<![\w.#+]){re.escape(a)}(?![\w#+])", ad_text, re.I if len(a) > 3 else 0):
            found.append(re.search(rf"(?<![\w.#+]){re.escape(a)}(?![\w#+])", ad_text, re.I).group(0))
    # verkko-osoitteet ja ilmoitusten tavalliset ei-tekniset nimet (työsuhde-edut, some) eivät ole vaatimuksia
    found = [t for t in found if not _DOMAIN.search(t) and t.lower() not in _NOT_REQ
             and not any(t.lower().startswith(w) for w in _NOT_REQ if len(w) >= 5)]   # LinkedInissä
    out: list[str] = []
    for t in found:
        if not any(t.lower() in (x.lower() for x in aliases(o)) for o in out):
            out.append(t)
    # pidempi termi syrjäyttää osansa: "GitHub Actions" -> ei erikseen "GitHub"
    return [t for t in out if not any(t.lower() != o.lower() and re.search(rf"(?<!\w){re.escape(t.lower())}(?!\w)", o.lower())
                                      for o in out)]


def _units(cv: dict):
    """(tunniste, teksti, on taitolista) jokaiselle mukana olevalle riville sekä profiilille."""
    for s in cv["sections"]:
        skills = ct._is(s["title"], ct.SKILLS_SECTION)
        if s["profile"]:
            yield "SUMMARY", cv["summary"]["text"], False
            continue
        for u in s["units"]:
            if u["included"]:
                yield u["id"], u["text"], skills
        for e in s["entries"]:
            if e["included"]:
                yield e["id"], f"{e['title']} {e['meta']}", False
                for u in e["units"]:
                    if u["included"]:
                        yield u["id"], u["text"], skills


def _fact_lines(base: ct.CV):
    for s in base.sections:
        skills = ct._is(s.title, ct.SKILLS_SECTION)
        for u in s.units:
            yield u.id, u.text, skills
        for e in s.entries:
            yield e.id, f"{e.title} {e.meta}", False
            for u in e.units:
                yield u.id, u.text, skills


def coverage(ad_text: str, cv: dict, base: ct.CV, extra_facts: str = "") -> dict:
    """Jokaiselle ilmoituksen termille tila:
    evidenced   näkyy CV:n työ-, projekti- tai profiilirivillä
    skills_only näkyy vain taitolistassa (fixable_ids: faktapohjan rivit, jotka näyttäisivät sen käytössä)
    dropped     faktapohjassa, mutta ei räätälöidyssä CV:ssä
    missing     ei faktapohjassa: hakijalla ei ole sitä, ei lisätä
    alias       CV käyttää eri kirjoitusasua kuin ilmoitus (form = CV:n asu); avainsanahaku voi ohittaa sen."""
    lines = list(_units(cv))
    facts = list(_fact_lines(base))
    facts_low = "\n".join(t for _, t, _ in facts).lower() + "\n" + extra_facts.lower()
    terms = []
    for term in requirements(ad_text):
        ev = [(i, present_any(term, t.lower())) for i, t, sk in lines if not sk]
        ev = [(i, f) for i, f in ev if f]
        sk = [(i, present_any(term, t.lower())) for i, t, s in lines if s]
        sk = [(i, f) for i, f in sk if f]
        shown = ev or sk
        if shown:
            status = "evidenced" if ev else "skills_only"
        elif present_any(term, facts_low):
            status = "dropped"
        else:
            status = "missing"
        included = {i for i, _, _ in lines}
        fixable = [i for i, t, s in facts if not s and i not in included and present_any(term, t.lower())] \
            if status in ("skills_only", "dropped") else []
        form = shown[0][1] if shown else None
        terms.append({"term": term, "status": status, "ids": [i for i, _ in shown][:4], "fixable_ids": fixable[:4],
                      "form": form if form and form.lower() != term.lower() else None})
    have = [t for t in terms if t["status"] != "missing"]
    ev = sum(t["status"] == "evidenced" for t in have)
    sk = sum(t["status"] == "skills_only" for t in have)
    return {"terms": terms, "have": len(have), "evidenced": ev, "skills_only": sk,
            "dropped": sum(t["status"] == "dropped" for t in have),
            "missing": sum(t["status"] == "missing" for t in terms),
            # näkyvyys: näytetty käytössä = 1, vain taitolistassa = 0.5; vain hakijan oikeista taidoista
            "score": round((ev + 0.5 * sk) / len(have), 2) if have else None}


def findings(cov: dict) -> list[dict]:
    """Korjattavat havainnot räätälöijälle: ehdotuksia, eivät estä hyväksyntää."""
    out = []
    for t in cov["terms"]:
        ids = ", ".join(t["fixable_ids"])
        if t["status"] == "dropped":
            out.append({"category": "ats_dropped", "quote": t["term"], "source_id": t["fixable_ids"][0] if t["fixable_ids"] else "",
                        "issue": f"The posting asks for \"{t['term']}\" and source_facts show it, but the tailored CV "
                                 "does not mention it. Screening software will treat it as missing.",
                        "fix": (f"Keep a source line that shows it ({ids})" if ids else "Keep the source line that shows it")
                               + ", or name it in the summary if it matters for this posting."})
        elif t["status"] == "skills_only" and t["fixable_ids"]:
            out.append({"category": "ats_skills_only", "quote": t["term"], "source_id": t["fixable_ids"][0],
                        "issue": f"\"{t['term']}\" appears only in the skills list. Screeners weigh a skill shown in a job "
                                 "or project line more than a bare list item.",
                        "fix": f"Keep a line that shows it in use ({ids})."})
        elif t["form"]:
            out.append({"category": "ats_alias", "quote": t["form"], "source_id": t["ids"][0] if t["ids"] else "",
                        "issue": f"The posting says \"{t['term']}\", the CV says \"{t['form']}\". Literal keyword "
                                 "matching may miss it.",
                        "fix": f"Where natural, use the posting's form \"{t['term']}\" in a rewrite (same technology)."})
    return out


_GAMING = re.compile(r"(?i)(ignore (all |any |the )?(previous |prior )?instructions|disregard (the )?(above|previous)|"
                     r"(rank|rate|score|recommend) this (candidate|cv|resume)|system prompt)")


def gaming(text: str) -> list[str]:
    """Seulojan ohjailuksi tulkittavat kohdat (ohjeet tekoälylle). Oikeat seulojat tunnistavat ne ja karsivat CV:n."""
    return [m.group(0) for m in _GAMING.finditer(text)]
