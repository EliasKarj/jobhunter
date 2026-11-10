"""CV:n räätälöinti ilmoitukselle niin, ettei mitään voi keksiä.

1. CV (data/cv.md) pilkotaan tunnistetuiksi osiksi: osio S2, työpaikka/projekti S2E1, sen rivi S2E1B1 jne.
2. Kielimalli tekee VAIN valinnat: osioiden järjestys, pois jätettävät kohdat ja korostettavat taidot,
   sekä 2-3 lauseen profiiliteksti. Se ei kirjoita CV:n sisältöä.
3. Koodi kokoaa CV:n alkuperäisestä tekstistä sanasta sanaan. Profiiliteksti tarkistetaan (verify.check);
   jos yksikin väite liputetaan, käytetään alkuperäistä profiiliosiota.
4. Suomenkielinen versio käännetään rivi kerrallaan. Jokainen käännös tarkistetaan alkuperäistä vasten
   (tekniikat, luvut, lihavoinnit ja kielten välinen samankaltaisuus); hylätty rivi jää englanniksi ja liputetaan.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

import numpy as np

from .claims import cliches, unsupported_claims
from .llm import LLM
from .style import STYLE, polish, third_person_fi
from .verify import _present, check, tech_terms

PROTECTED = ("education", "koulutus", "languages", "kielitaito", "kielet")   # näitä ei jätetä pois
SKILLS_SECTION = ("technical skills", "skills", "osaaminen", "tekninen osaaminen")
PROFILE_SECTION = ("profile", "profiili", "summary", "yhteenveto")
FI_TITLES = {"profile": "Profiili", "work experience": "Työkokemus", "projects": "Projektit", "education": "Koulutus",
             "technical skills": "Tekninen osaaminen", "languages": "Kielitaito", "skills": "Osaaminen"}
TRANSLATION_MIN_SIM = 0.70   # bge-m3, kielten välinen kosini alkuperäisen ja käännöksen välillä


@dataclass
class Unit:          # yksi tekstiyksikkö (kappale tai luettelon rivi)
    id: str
    text: str
    kind: str = "bullet"      # bullet | para
    included: bool = True


@dataclass
class Entry:         # ### Työpaikka tai projekti
    id: str
    title: str
    meta: str = ""
    units: list[Unit] = field(default_factory=list)
    included: bool = True


@dataclass
class Section:
    id: str
    title: str
    units: list[Unit] = field(default_factory=list)      # osion omat kappaleet ja rivit (ei työpaikkoja)
    entries: list[Entry] = field(default_factory=list)


@dataclass
class CV:
    name: str
    header: list[str]
    sections: list[Section]


def parse(md: str) -> CV:
    name, header, sections = "", [], []
    cur: Section | None = None
    ent: Entry | None = None
    for raw in md.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if s.startswith("# ") and not name:
            name = s[2:].strip()
        elif s.startswith("## "):
            cur = Section(f"S{len(sections) + 1}", s[3:].strip())
            sections.append(cur)
            ent = None
        elif s.startswith("### ") and cur:
            ent = Entry(f"{cur.id}E{len(cur.entries) + 1}", s[4:].strip())
            cur.entries.append(ent)
        elif not s:
            continue
        elif cur is None:
            header.append(s)
        elif ent and not ent.units and not ent.meta and re.fullmatch(r"\*[^*].*\*", s):
            ent.meta = s.strip("*").strip()
        else:
            owner = ent or cur
            bullet = s.startswith(("- ", "* "))
            text = s[2:].strip() if bullet else s
            prefix = owner.id + ("B" if bullet else "P")
            n = sum(1 for u in owner.units if u.id.startswith(prefix)) + 1
            owner.units.append(Unit(f"{prefix}{n}", text, "bullet" if bullet else "para"))
    return CV(name, header, sections)


def _is(title: str, names) -> bool:
    t = title.lower()
    return any(t == n or t.startswith(n) for n in names)


def outline(cv: CV) -> str:
    """CV tunnisteineen mallille."""
    out = []
    for s in cv.sections:
        out.append(f"[{s.id}] ## {s.title}")
        for u in s.units:
            out.append(f"  [{u.id}] {u.text}")
        for e in s.entries:
            out.append(f"  [{e.id}] ### {e.title}" + (f" ({e.meta})" if e.meta else ""))
            for u in e.units:
                out.append(f"    [{u.id}] {u.text}")
    return "\n".join(out)


def full_text(cv: CV) -> str:
    parts = [cv.name, *cv.header]
    for s in cv.sections:
        parts += [s.title, *(u.text for u in s.units)]
        for e in s.entries:
            parts += [e.title, e.meta, *(u.text for u in e.units)]
    return "\n".join(parts)


# ------------------------------------------------------------ valinnat

PLAN_SYSTEM = """Räätälöit hakijan CV:n työpaikkailmoitukseen. Et kirjoita CV:n sisältöä: valitset vain, mitä
nostetaan esiin ja mitä jätetään pois. Koodi kokoaa CV:n alkuperäisestä tekstistä.

Palauta:
- section_order: osioiden tunnisteet (S1, S2, ...) siinä järjestyksessä, joka palvelee tätä paikkaa parhaiten.
- drop: tunnisteet (työpaikat SxEy tai rivit SxEyBz / SxBz), jotka eivät liity tähän paikkaan ja vievät tilaa.
  Jätä pois vain selvästi epäolennaista. Älä jätä pois koulutusta tai kielitaitoa.
- focus_skills: enintään 8 CV:ssä MAINITTUA taitoa, jotka ilmoitus vaatii tai joista on hyötyä.
- summary: 2-3 lauseen profiiliteksti CV:n alkuun ENGLANNIKSI, CV:n omaan tyyliin. Suomeksi minä-muodossa ilman
  minä-sanaa ("Rakennan…", "valmistun…"), englanniksi CV-tyyliin ilman pronominia. Ei koskaan kolmatta persoonaa.
  Käytä VAIN CV:ssä olevia tietoja. Älä lisää taitoja, työkaluja, vuosia tai lukuja, joita CV:ssä ei ole.
  Kirjoita samalla äänellä kuin CV:n oma profiiliosio: se on hakijan itse kirjoittama. Aloita siitä, kuka hakija on
  ja mitä hän tekee (esim. "IT engineering student who..."), ja nosta 1-2 tähän paikkaan liittyvää konkreettista asiaa.
  Älä käytä sanaa hakija tai candidate. Kirjoita kokonaisia, kieliopillisesti oikeita virkkeitä."""

PLAN_EXAMPLE_FI = "Tietotekniikan insinööriopiskelija, joka..."

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["section_order", "drop", "focus_skills", "summary"],
    "properties": {"section_order": {"type": "array", "items": {"type": "string"}},
                   "drop": {"type": "array", "items": {"type": "string"}},
                   "focus_skills": {"type": "array", "items": {"type": "string"}},
                   "summary": {"type": "string"}},
}


def apply_plan(cv: CV, plan: dict) -> tuple[list[Section], list[dict]]:
    """Järjestä ja rajaa alkuperäisiä osia. Palauttaa (osiot, muutokset). Tuntemattomat tunnisteet ohitetaan."""
    by_id = {s.id: s for s in cv.sections}
    order = [by_id[i] for i in dict.fromkeys(plan.get("section_order") or []) if i in by_id]
    order += [s for s in cv.sections if s not in order]
    changes: list[dict] = []
    if [s.id for s in order] != [s.id for s in cv.sections]:
        changes.append({"kind": "order", "text": "Osioiden järjestys: " + " → ".join(s.title for s in order)})
    drop = set(plan.get("drop") or [])
    for s in cv.sections:
        if _is(s.title, PROTECTED):
            continue
        for e in s.entries:
            if e.id in drop:
                e.included = False
            for u in e.units:
                if u.id in drop:
                    u.included = False
            if e.units and not any(u.included for u in e.units):
                for u in e.units:      # kaikkia rivejä ei poisteta: silloin pois jää koko kohta
                    u.included = True
                e.included = False
        for u in s.units:
            if u.id in drop and not _is(s.title, PROFILE_SECTION):
                u.included = False
        if s.entries and not any(e.included for e in s.entries):
            for e in s.entries:
                e.included = True
    for s in cv.sections:
        for e in s.entries:
            if not e.included:
                changes.append({"kind": "drop", "id": e.id, "text": f"Pois: {e.title}"})
            else:
                changes += [{"kind": "drop", "id": u.id, "text": f"Pois rivi: {u.text[:80]}"} for u in e.units if not u.included]
        changes += [{"kind": "drop", "id": u.id, "text": f"Pois rivi: {u.text[:80]}"} for u in s.units if not u.included]
    return order, changes


def focus(cv: CV, skills: list[str]) -> list[str]:
    """Korostettavat taidot: vain ne, jotka todella löytyvät CV:stä."""
    low = full_text(cv).lower()
    return [x for x in dict.fromkeys(str(s).strip() for s in skills or []) if x and _present(x, low)][:8]


def reorder_skills(sections: list[Section], skills: list[str]) -> None:
    """Taito-osion rivit, joissa on korostettavia taitoja, ensin (sisältö ennallaan)."""
    if not skills:
        return
    for s in sections:
        if _is(s.title, SKILLS_SECTION):
            s.units.sort(key=lambda u: -sum(_present(k, u.text.lower()) for k in skills))


def check_summary(summary: str, cv: CV, embed=None, vectors=None) -> list[dict]:
    """Jokainen lause on väite hakijasta: tekniikat, luvut ja käsitteet vain CV:stä (ei ilmoituksesta)."""
    text = summary.strip()
    if not text:
        return [{"text": "", "reasons": ["profiiliteksti puuttuu"]}]
    claims = check(text, {"CV": full_text(cv)}, {}, cv.name, embed, vectors, about_applicant=True)
    out = [{"text": c.text, "reasons": c.reasons} for c in claims if c.status == "flag"]
    # luonteenpiirteet, johtaminen, tulokset, laajuus ja johtopäätökset vain, jos CV itse sanoo ne
    words = unsupported_claims(text, full_text(cv))
    reasons = [f"väite, jota CV:ssä ei ole: \"{w}\"" for w in words]
    reasons += [f"kuulostaa tekoälyn tekstiltä: \"{w}\"" for w in cliches(text, full_text(cv))]
    if reasons:
        out.append({"text": text, "reasons": reasons})
    return out


# ------------------------------------------------------------ yhden sivun sovitus

CHARS_PER_LINE = 108     # A4, sisältöleveys 178 mm, 12.5 px Geist (tarkistettu oikeista PDF:istä)
PAGE_LINES = 64.0        # arvio 56 riviä vastasi PDF:ssä 81 % täyttöä; 64 jättää noin 7 % varmuusmarginaalin
MAX_PROJECTS = 5
MIN_PROJECTS = 2
FI_GROWTH = 1.12         # suomennos on noin 10 % pidempi


def _lines(text: str, width: int = CHARS_PER_LINE) -> int:
    return max(1, -(-len(text.replace("**", "")) // width))


def estimate_lines(cv: CV, sections: list[Section], summary: str, growth: float = 1.0) -> float:
    """CV:n arvioitu korkeus riveinä. Vain arvio: käyttöliittymä mittaa lopullisen korkeuden."""
    total = 5.5 + (1 if len(cv.header) > 2 else 0)        # nimi, titteli, yhteystiedot
    for s in sections:
        body = 0.0
        if _is(s.title, PROFILE_SECTION):
            body += _lines(summary) * growth + 0.3
        else:
            body += sum(_lines(u.text, CHARS_PER_LINE - 4) * growth + 0.1 for u in s.units if u.included)
        for e in s.entries:
            if e.included:
                body += _lines(f"{e.title}   {e.meta}") + 0.4
                body += sum(_lines(u.text, CHARS_PER_LINE - 4) * growth + 0.1 for u in e.units if u.included)
        if body:
            total += 2.0 + body
    return total


def _relevance(texts: list[str], ad_text: str, skills: list[str], embed=None) -> list[float]:
    """Tekstien osuvuus ilmoitukseen: kosini (bge-m3) tai ilman embedderiä yhteiset tekniikat. Korostetut taidot nostavat."""
    ad_low = ad_text.lower()
    if embed is not None and texts:
        vecs = embed([ad_text[:2000]] + texts)
        base = [float(vecs[0] @ v) for v in vecs[1:]]
    else:
        base = [0.1 * sum(_present(t, ad_low) for t in tech_terms(x)) for x in texts]
    return [b + 0.03 * sum(_present(k, x.lower()) for k in skills) for b, x in zip(base, texts)]


def fit_one_page(cv: CV, sections: list[Section], summary: str, ad_text: str, skills: list[str], embed=None,
                 lang: str = "en") -> list[dict]:
    """Rajaa CV yhdelle A4-sivulle. Vähiten ilmoitukseen liittyvä jää pois ensin; työhistorian nimikkeet, koulutus,
    taidot ja kielet säilyvät aina. Palauttaa muutokset."""
    growth = FI_GROWTH if lang == "fi" else 1.0
    size = lambda: estimate_lines(cv, sections, summary, growth)  # noqa: E731
    changes: list[dict] = []
    projects = next((s for s in sections if _is(s.title, ("projects", "projektit"))), None)
    work = [e for s in sections if s is not projects for e in s.entries if e.included]
    live = [e for e in (projects.entries if projects else []) if e.included]
    rel = dict(zip((e.id for e in live), _relevance([" ".join([e.title, e.meta, *(u.text for u in e.units)]) for e in live],
                                                    ad_text, skills, embed)))
    if projects:   # osuvimmat projektit ensin
        projects.entries.sort(key=lambda e: -rel.get(e.id, -9))

    def drop_project(e: Entry) -> None:
        e.included = False
        live.remove(e)
        changes.append({"kind": "fit", "id": e.id, "text": f"Pois (yksi sivu): {e.title}"})

    while len(live) > MAX_PROJECTS:   # ensin rajataan projektien määrä, koska se säästää eniten tilaa
        drop_project(min(live, key=lambda e: rel[e.id]))
    if size() <= PAGE_LINES:
        return changes

    # sitten yksittäiset rivit: projektilta jää vähintään yksi, työpaikalta nimike ja ajat
    cand = [(e, u) for e in live for u in e.units if u.included] + [(e, u) for e in work for u in e.units if u.included]
    scores = _relevance([u.text for _, u in cand], ad_text, skills, embed)
    for (e, u), _s in sorted(zip(cand, scores), key=lambda x: x[1]):
        if size() <= PAGE_LINES:
            return changes
        if e in live and sum(x.included for x in e.units) <= 1:
            continue
        u.included = False
        changes.append({"kind": "fit", "id": u.id, "text": f"Pois (yksi sivu): {u.text[:70]}"})
    while size() > PAGE_LINES and len(live) > MIN_PROJECTS:
        drop_project(min(live, key=lambda e: rel[e.id]))
    return changes


# ------------------------------------------------------------ käännös

TRANSLATE_SYSTEM = """Käännä CV:n rivit suomeksi. Säännöt:
- Käännä merkitys tarkasti. Älä lisää, poista tai yleistä mitään.
- Yritysten, oppilaitosten, projektien ja tekniikoiden nimet sekä luvut ja vuosiluvut pysyvät ennallaan.
- Säilytä **lihavoinnit** samoissa kohdissa.
- CV-tyyli: lyhyet lauseet, ei minä-muotoa, jos alkuperäisessä ei ole sitä.
Palauta jokainen rivi sen tunnisteella."""


def _translate_schema(ids: list[str]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ids,
            "properties": {i: {"type": "string"} for i in ids}}


_DIGITS = re.compile(r"\d+")


def check_translation(orig: str, tr: str, allowed: str = "") -> list[str]:
    """Deterministiset ehdot käännökselle (allowed: muu CV:n teksti, jossa uusi termi saa esiintyä)."""
    reasons = []
    if sorted(_DIGITS.findall(orig)) != sorted(_DIGITS.findall(tr)):
        reasons.append("luvut eivät täsmää")
    olow = orig.lower() + "\n" + allowed.lower()
    for t in tech_terms(tr):
        if not _present(t, olow):
            reasons.append(f"\"{t}\" ei ole alkuperäisessä")
    trlow = tr.lower()
    for t in tech_terms(orig):
        if not _present(t, trlow):
            reasons.append(f"\"{t}\" puuttuu käännöksestä")
    if orig.count("**") != tr.count("**"):
        reasons.append("lihavoinnit muuttuivat")
    if not tr.strip():
        reasons.append("tyhjä käännös")
    return reasons


def translate(units: dict[str, str], llm: LLM, embed=None, allowed: str = "", batch: int = 25) -> dict[str, dict]:
    """id -> {orig, text, reasons}. Rivi, jolla on syitä, näytetään tarkistettavaksi ennen kuin pohja tallennetaan."""
    out: dict[str, dict] = {}
    items = list(units.items())
    for i in range(0, len(items), batch):
        part = dict(items[i:i + batch])
        user = "\n".join(f"[{k}] {v}" for k, v in part.items())
        try:
            raw = llm.complete_json(TRANSLATE_SYSTEM, [{"role": "user", "content": user}], _translate_schema(list(part)), max_tokens=4000)
        except Exception as e:  # noqa: BLE001
            out.update({k: {"orig": v, "text": v, "reasons": [f"käännös epäonnistui: {e}"]} for k, v in part.items()})
            continue
        tr = {k: str(raw.get(k) or "").strip() for k in part}
        sims = {}
        if embed is not None:
            keys = list(part)
            a, b = embed([part[k] for k in keys]), embed([tr[k] or "-" for k in keys])
            sims = {k: float(a[j] @ b[j]) for j, k in enumerate(keys)}
        for k, orig in part.items():
            reasons = check_translation(orig, tr[k], allowed)
            if k in sims and sims[k] < TRANSLATION_MIN_SIM and len(orig) > 25:
                reasons.append(f"merkitys poikkeaa (samankaltaisuus {sims[k]:.2f})")
            out[k] = {"orig": orig, "text": tr[k] or orig, "reasons": reasons}
    return out


_EN_FI_META = [(r"\bpresent\b", "nyt"), (r"\bSpring\b", "Kevät"), (r"\bSummer\b", "Kesä"), (r"\bAutumn\b", "Syksy"),
               (r"\bWinter\b", "Talvi"), (r"zero-hours contract", "nollatuntisopimus")]


def meta_fi(meta: str) -> str:
    for a, b in _EN_FI_META:
        meta = re.sub(a, b, meta)
    return meta


# ------------------------------------------------------------ suomenkielinen pohja (data/cv_fi.md)

def fi_path(cv_path):
    return cv_path.with_name(cv_path.stem + "_fi" + cv_path.suffix)


def translation_units(cv: CV) -> dict[str, str]:
    """Koko CV käännettäväksi (myös räätälöinnissä pois jätetyt kohdat: pohja tehdään kerran koko CV:stä)."""
    units = {f"H{i}": h for i, h in enumerate(cv.header) if not re.search(r"@|\d{3}", h)}
    for s in cv.sections:
        units[s.id] = FI_TITLES.get(s.title.lower(), s.title)
        units.update({u.id: u.text for u in s.units})
        for e in s.entries:
            units[e.id] = e.title
            if e.meta:
                units[e.id + "M"] = meta_fi(e.meta)
            units.update({u.id: u.text for u in e.units})
    return units


def to_markdown(cv: CV, lines: dict[str, str]) -> str:
    """CV markdownina; lines korvaa tekstit tunnisteittain (suomenkielinen pohja)."""
    t = lambda k, v: (lines.get(k) or v).replace("\n", " ").strip()  # noqa: E731
    out = [f"# {cv.name}", ""]
    out += [t(f"H{i}", h) for i, h in enumerate(cv.header)]
    for s in cv.sections:
        out += ["", f"## {t(s.id, s.title)}", ""]
        out += [("- " if u.kind == "bullet" else "") + t(u.id, u.text) for u in s.units]
        for e in s.entries:
            out += ["", f"### {t(e.id, e.title)}"]
            if e.meta:
                out.append(f"*{t(e.id + 'M', e.meta)}*")
            out.append("")
            out += [("- " if u.kind == "bullet" else "") + t(u.id, u.text) for u in e.units]
    return "\n".join(out).strip() + "\n"


# ------------------------------------------------------------ koko prosessi

def to_dict(cv: CV, sections: list[Section], summary: dict, lang: str, tr: dict[str, dict] | None = None) -> dict:
    t = (lambda k, v: tr[k]["text"] if k in tr else v) if tr else (lambda k, v: v)
    out = []
    for s in sections:
        out.append({
            "id": s.id, "title": t(s.id, s.title), "profile": _is(s.title, PROFILE_SECTION),
            "units": [{"id": u.id, "kind": u.kind, "text": t(u.id, u.text), "included": u.included} for u in s.units],
            "entries": [{"id": e.id, "title": t(e.id, e.title), "meta": t(e.id + "M", e.meta), "included": e.included,
                         "units": [{"id": u.id, "kind": u.kind, "text": t(u.id, u.text), "included": u.included} for u in e.units]}
                        for e in s.entries],
        })
    header = [t(f"H{i}", h) for i, h in enumerate(cv.header)]
    return {"name": cv.name, "header": header, "sections": out, "summary": summary, "lang": lang}


# ------------------------------------------------------------ rivien uudelleenmuotoilu (paneeli)

# Näitä ei muotoilla koskaan: nimikkeet, työnantajat ja ajat ovat otsikoissa (eivät rivejä), ja nämä osiot ovat faktoja
REWRITE_LOCKED = PROTECTED + PROFILE_SECTION + SKILLS_SECTION


def rewritable(cv: CV) -> dict[str, tuple[Unit, str]]:
    """Muotoiltavat rivit: tunniste -> (rivi, saman kohdan teksti). Uusi termi saa tulla vain samasta kohdasta,
    jotta kahden projektin faktoja ei voi yhdistää yhdeksi väitteeksi."""
    out: dict[str, tuple[Unit, str]] = {}
    for s in cv.sections:
        if _is(s.title, REWRITE_LOCKED):
            continue
        for e in s.entries:
            ctx = "\n".join([e.title, e.meta, *(u.text for u in e.units)])
            out.update({u.id: (u, ctx) for u in e.units})
        own = "\n".join(u.text for u in s.units)
        out.update({u.id: (u, own) for u in s.units})
    return out


def check_rewrite(orig: str, new: str, context: str, name: str = "") -> list[str]:
    """Säännöt uudelleenmuotoilulle: ei uusia lukuja, tekniikoita tai väitteitä, joita samassa kohdassa ei ole."""
    new = new.strip()
    if not new:
        return ["tyhjä muotoilu"]
    reasons = []
    if len(new) > 1.5 * len(orig) + 40:
        reasons.append("selvästi alkuperäistä pidempi")
    if not set(_DIGITS.findall(new)) <= set(_DIGITS.findall(orig)):
        reasons.append("uusi luku")
    from .screening import gaming, present_any   # sama tekniikka ilmoituksen kirjoitusasulla (Postgres -> PostgreSQL) sallitaan
    reasons += [f"seulontaohjelmalle osoitettua tekstiä: {g}" for g in gaming(new)]
    reasons += [f"väite, jota tässä kohdassa ei ole: \"{w}\"" for w in unsupported_claims(new, context)]
    reasons += [f"kuulostaa tekoälyn tekstiltä: \"{w}\"" for w in cliches(new, context)]
    low = context.lower()
    reasons += [f"\"{t}\" ei esiinny tässä kohdassa" for t in tech_terms(new) if not present_any(t, low)]
    if new.count("**") != orig.count("**"):
        reasons.append("lihavoinnit muuttuivat")
    reasons += [r for c in check(new, {"CV": context}, {}, name, about_applicant=True) if c.status == "flag" for r in c.reasons]
    return list(dict.fromkeys(reasons))


def apply_rewrites(cv: CV, rewrites: list[dict]) -> tuple[list[dict], list[dict]]:
    """Hyväksytyt muotoilut vaihdetaan riveihin. Palauttaa (muutokset, hylätyt). Hylätty rivi jää alkuperäiseksi."""
    units = rewritable(cv)
    changes, rejected, seen = [], [], set()
    for rw in rewrites or []:
        sid, new = str(rw.get("source_id") or "").strip(), str(rw.get("text") or "").strip()
        if sid in seen:
            continue
        seen.add(sid)
        if sid not in units:
            rejected.append({"source_id": sid, "text": new, "reasons": [
                "tätä kohtaa ei saa muotoilla (nimike, ajat, koulutus, kielitaito, taidot tai profiili) tai tunniste on tuntematon"]})
            continue
        u, ctx = units[sid]
        reasons = check_rewrite(u.text, new, ctx, cv.name)
        if reasons:
            rejected.append({"source_id": sid, "text": new, "before": u.text, "reasons": reasons})
            continue
        if new != u.text:
            changes.append({"kind": "rewrite", "id": sid, "before": u.text, "after": new, "reason": str(rw.get("reason") or ""),
                            "text": f"Muotoiltu: {new[:80]}"})
            u.text = new
    return changes, rejected


def original_summary(cv: CV) -> str:
    prof = next((s for s in cv.sections if _is(s.title, PROFILE_SECTION)), None)
    return " ".join(u.text for u in prof.units) if prof else ""


def assemble(base: CV, en: CV, plan: dict, summary_text: str | None, ad_text: str, lang: str, embed=None,
             rewrites: list[dict] | None = None, style: list[str] | None = None):
    """Kokoa CV suunnitelmasta (base muuttuu: jäsennä uudelleen jokaista kokoamista varten).
    summary_text=None: alkuperäinen profiiliteksti. Palauttaa (osiot, muutokset, profiili, taidot, hylätyt muotoilut)."""
    sections, changes = apply_plan(base, plan)
    rw_changes, rejected = apply_rewrites(base, rewrites or [])
    changes += rw_changes
    skills = focus(base, plan.get("focus_skills"))
    reorder_skills(sections, skills)
    if skills:
        changes.append({"kind": "focus", "text": "Korostetut taidot: " + ", ".join(skills)})
    original = original_summary(base)
    if summary_text is None:
        summary = {"text": original, "source": "original", "flags": [], "style": []}
        changes.append({"kind": "summary", "text": "Profiiliteksti: alkuperäinen"})
    else:
        # suomenkielinen pohja: väitteet tarkistetaan sekä pohjaa että alkuperäistä CV:tä vasten
        check_cv = CV(base.name, base.header + en.header, base.sections + en.sections) if base is not en else base
        flags = check_summary(summary_text, check_cv, embed)
        summary = ({"text": summary_text, "source": "generated", "flags": [], "style": style or []} if not flags
                   else {"text": original, "source": "original", "flags": flags, "rejected": summary_text, "style": []})
        changes.append({"kind": "summary", "text": "Profiiliteksti räätälöity" if not flags
                        else "Räätälöity profiiliteksti hylättiin (väite ilman lähdettä): käytetään alkuperäistä"})
    # kasvukerroin vain englanninkieliselle tekstille, joka vielä käännetään: suomenkielinen pohja on jo oikean mittainen
    changes += fit_one_page(base, sections, summary["text"], ad_text, skills, embed, "fi" if lang == "fi" and base is en else "en")
    return sections, changes, summary, skills, rejected


def tailor(cv_md: str, job: dict, ad_text: str, llm: LLM, lang: str = "en", translator: LLM | None = None,
           embed=None, fi_md: str | None = None) -> dict:
    """lang=fi ja fi_md (tarkistettu suomenkielinen pohja): kootaan pohjasta kuten englanniksi, ei käännöstä.
    lang=fi ilman pohjaa: koko CV käännetään rivi kerrallaan ja palautetaan tarkistettavaksi (review_required)."""
    en = parse(cv_md)
    base = parse(fi_md) if lang == "fi" and fi_md else en
    summary_lang = "SUOMEKSI" if base is not en else "ENGLANNIKSI"
    user = (f"ILMOITUS: {job.get('title')} | {job.get('company')} | {job.get('city')}\n{ad_text[:6000]}\n\n"
            f"CV (tunnisteet hakasulkeissa):\n{outline(base)}")
    slang = "fi" if base is not en else "en"
    system = PLAN_SYSTEM.replace("ENGLANNIKSI", summary_lang)
    if slang == "fi":
        system = system.replace("IT engineering student who...", PLAN_EXAMPLE_FI)
    plan = llm.complete_json(system + "\n\n" + STYLE[slang],
                             [{"role": "user", "content": user}], PLAN_SCHEMA, max_tokens=6000)
    polished, style_left = polish({"summary": str(plan.get("summary") or "").strip()}, llm, slang)
    gen = polished["summary"]
    third = third_person_fi(gen) if slang == "fi" else []
    sections, changes, summary, skills, _ = assemble(base, en, plan, None if third else gen, ad_text, lang, embed,
                                                     style=style_left.get("summary", []))
    if third:   # kolmas persoona jäi uudelleenkirjoituksen jälkeenkin: alkuperäinen profiili (kuten väitteissä)
        summary.update(rejected=gen, flags=[{"text": gen, "reasons": [f"kolmas persoona: {', '.join(third)}"]}])
        changes = [c if c["kind"] != "summary" else {"kind": "summary", "text": "Räätälöity profiiliteksti hylättiin "
                   "(kolmas persoona): käytetään alkuperäistä"} for c in changes]
    tr = None
    review = False
    if lang == "fi" and base is en:
        units = translation_units(en)
        units["SUMMARY"] = summary["text"]
        tr = translate(units, translator or llm, embed, full_text(en))
        # virallisia nimiä ei voi tarkistaa säännöillä: tutkinnot ja ammattinimikkeet aina ihmisen tarkistettaviksi
        for s in en.sections:
            if _is(s.title, ("education", "koulutus")):
                for u in s.units:
                    tr[u.id]["reasons"].append("tutkinnon ja oppilaitoksen virallinen suomenkielinen nimi: tarkista")
            for e in s.entries:
                if e.id in tr and not _is(s.title, ("projects", "projektit")):
                    tr[e.id]["reasons"].append("ammattinimike: tarkista")
        summary = {**summary, "text": tr["SUMMARY"]["text"]}
        review = True
    res = to_dict(base, sections, summary, lang, tr)
    res.update(job_title=job.get("title"), company=job.get("company"), changes=changes, focus_skills=skills,
               source="cv_fi.md" if base is not en else "cv.md", review_required=review,
               translation=tr or {}, model=(translator or llm).name if review else llm.name,
               created=dt.datetime.now().isoformat(timespec="seconds"))
    return res


def tailor_url(url: str, s=None, lang: str = "en") -> dict:
    from . import config, service
    from .grounding import find_job
    from .scoring import _embedder_factory
    s = s or config.load()
    if not s.cv_path.exists():
        raise LookupError(f"CV puuttuu: {s.cv_path}")
    st = service.store(s)
    job = find_job(st, url)
    emb = _embedder_factory(s)().embed_queries
    fp = fi_path(s.cv_path)
    fi_md = fp.read_text(encoding="utf-8") if lang == "fi" and fp.exists() else None
    writer = service.llm("writing", s)   # profiiliteksti on kirjoitustehtävä
    return tailor(s.cv_path.read_text(encoding="utf-8"), job, job.get("clean_text") or "", writer, lang, writer, emb, fi_md)


def save_fi_base(lines: dict[str, str], s=None, overwrite: bool = False) -> dict:
    """Tallenna tarkistettu käännös pohjaksi data/cv_fi.md. Palauttaa jäljelle jääneet varoitukset."""
    from . import config
    s = s or config.load()
    fp = fi_path(s.cv_path)
    if fp.exists() and not overwrite:
        raise ValueError(f"{fp.name} on jo olemassa. Muokkaa sitä suoraan tai vahvista korvaaminen.")
    en = parse(s.cv_path.read_text(encoding="utf-8"))
    units = translation_units(en)
    allowed = full_text(en)
    warnings = {k: r for k, v in units.items() if (r := check_translation(v, lines.get(k) or v, allowed))}
    fp.write_text(to_markdown(en, {k: str(v)[:2000] for k, v in lines.items() if k in units}), encoding="utf-8")
    return {"path": str(fp), "warnings": warnings}


def recheck_summary(text: str, s=None, lang: str = "en") -> list[dict]:
    from . import config
    s = s or config.load()
    cv = parse(s.cv_path.read_text(encoding="utf-8"))
    fp = fi_path(s.cv_path)
    if lang == "fi" and fp.exists():
        fi = parse(fp.read_text(encoding="utf-8"))
        cv = CV(cv.name, cv.header + fi.header, cv.sections + fi.sections)
    return check_summary(text, cv)


def check_user_line(text: str, source: str) -> list[str]:
    """Käyttäjän itse kirjoittama tai muokkaama CV-rivi: ei estetä (teksti on hakijan omaa), mutta liputetaan
    tekniikat, luvut ja väitteet, joita CV:ssä ei ole, sekä tekoälyltä kuulostavat fraasit. Näin näkee ennen
    lähettämistä, mitä rekrytoija voi kysyä ja mihin CV ei anna katetta."""
    low = source.lower()
    out = [f"\"{t}\" ei esiinny CV:ssäsi" for t in tech_terms(text) if not _present(t, low)]
    out += [f"luku {n} ei esiinny CV:ssäsi" for n in dict.fromkeys(_DIGITS.findall(text))
            if not re.search(rf"(?<!\d){re.escape(n)}(?!\d)", low)]
    out += [f"väite \"{w}\" ei esiinny CV:ssäsi" for w in unsupported_claims(text, source)]
    out += [f"kuulostaa tekoälyn tekstiltä: \"{w}\"" for w in cliches(text, source)]
    return out


def user_source(s=None) -> str:
    """Vertailupohja käyttäjän muokkauksille: koko cv.md ja cv_fi.md (mitä on, sitä saa käyttää)."""
    from . import config
    s = s or config.load()
    fp = fi_path(s.cv_path)
    return s.cv_path.read_text(encoding="utf-8") + ("\n" + fp.read_text(encoding="utf-8") if fp.exists() else "")
