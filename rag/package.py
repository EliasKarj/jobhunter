"""Hakemuspaketti: saatekirje + vastausluonnokset portaalikysymyksiin, ja jokaisen osan väitetarkistus.

EHDOTON SÄÄNTÖ: vain profiilissa (CV, osaaminen) olevat taidot ja kokemukset. Kehote kieltää keksimisen,
ja verify.check liputtaa deterministisesti väitteet, joille lähdettä ei löydy. Palkkatoive jätetään aina
käyttäjälle ([Täytä itse]); mallia ei pyydetä kirjoittamaan sitä. Mitään ei lähetetä minnekään.
"""
from __future__ import annotations

import json
import re

import numpy as np

from . import config
from .clean import sha1
from .grounding import Grounding, build, cache_get, cache_put
from .llm import LLM
from .style import STYLE, polish
from .verify import _present, ad_terms, check, summarize, tech_terms

SALARY_PLACEHOLDER = {"fi": "[Täytä itse: palkkatoive]", "en": "[Fill in yourself: salary expectation]"}
SECTIONS = {
    "fi": [("cover_letter", "Saatekirje"), ("about_me", "Kerro itsestäsi"), ("why_apply", "Miksi haet tätä paikkaa?"),
           ("why_you", "Miksi sinut kannattaa valita?"), ("strengths", "Mitkä ovat vahvuutesi?"),
           ("develop", "Mitä osaamista haluat kehittää?"), ("salary", "Palkkatoive")],
    "en": [("cover_letter", "Cover letter"), ("about_me", "Tell us about yourself"),
           ("why_apply", "Why are you applying for this position?"), ("why_you", "Why should we hire you?"),
           ("strengths", "What are your strengths?"), ("develop", "What skills do you want to develop?"),
           ("salary", "Salary expectation")],
}

SYSTEM = {
    "fi": """Kirjoitat työnhakijan puolesta hakemusluonnoksia SUOMEKSI. Hakija tarkistaa ja muokkaa ne itse.

EHDOTTOMAT SÄÄNNÖT:
- Käytä VAIN PROFIILI-osion tietoja hakijan taidoista, projekteista, koulutuksesta ja kokemuksesta.
- Älä keksi taitoja, työkaluja, projekteja, työpaikkoja, tuloksia, lukuja tai kokemusvuosia.
- Jos ilmoitus vaatii jotain, mitä profiilissa ei ole, älä väitä hakijan osaavan sitä. Voit sanoa hakijan
  olevan valmis opettelemaan sen, jos se on uskottavaa.
- Ilmoituksesta saat käyttää tietoja tehtävästä ja yrityksestä.
- Älä lupaa tai vahvista mitään hakijan puolesta, ellei se ole PROFIILI-osiossa: suostumus turvallisuusselvitykseen
  tai testeihin, aloitusaika, saatavuus, opiskelijastatus harjoittelun aikana, matkustus- tai muuttovalmius.
  Jos ilmoitus kysyy tällaista, kirjoita sen kohdalle [Täytä itse].
- Älä päättele hakijan luonnetta, oppimistapaa tai sitä, mitä jokin kokemus on "opettanut", ellei PROFIILI sano sitä.
- Älä kirjoita palkkatoivetta.
Tyyli: asiallinen, konkreettinen ja lyhyt. Saatekirje 180-280 sanaa, muut vastaukset 50-100 sanaa.
Kirjoita minä-muodossa. Allekirjoita saatekirje hakijan nimellä.
Vastaukset: about_me = kuka olet ja mitä teet nyt. why_apply = mikä juuri tässä tehtävässä ja yrityksessä kiinnostaa.
why_you = 1-2 konkreettista projektia, jotka liittyvät tehtävän vaatimuksiin. strengths = vahvuudet esimerkein.
develop = ilmoituksen asia, josta haluat oppia lisää: myönteisesti oppimishalun kautta, ilman puutteiden luettelua.""",
    "en": """You write job application drafts IN ENGLISH on behalf of the applicant, who will review and edit them.

ABSOLUTE RULES:
- Use ONLY the PROFILE section for the applicant's skills, projects, education and experience.
- Never invent skills, tools, projects, employers, results, numbers or years of experience.
- If the posting requires something not in the profile, do not claim the applicant has it. You may say the
  applicant is ready to learn it, if plausible.
- You may use the posting for facts about the role and the company.
- Never promise or confirm anything on the applicant's behalf unless the PROFILE says it: consent to security
  clearance or tests, start date, availability, student status during the internship, willingness to travel or
  relocate. If the posting asks for such a thing, write [Fill in yourself] in its place.
- Do not infer the applicant's character, learning style or what an experience "taught" them unless the PROFILE says it.
- Do not write a salary expectation.
Style: professional, concrete, concise. Cover letter 180-280 words, other answers 50-100 words.
Write in the first person. Sign the cover letter with the applicant's name.
Answers: about_me = who you are and what you do now. why_apply = what interests you in this role and company.
why_you = 1-2 concrete projects related to the role's requirements. strengths = strengths with examples.
develop = something from the posting you want to learn more about: positive, through eagerness to learn, without listing gaps.""",
}

_ANSWERS = ["cover_letter", "about_me", "why_apply", "why_you", "strengths", "develop"]
SCHEMA = {"type": "object", "additionalProperties": False, "required": _ANSWERS,
          "properties": {k: {"type": "string"} for k in _ANSWERS}}


def applicant_name(s: config.Settings) -> str:
    try:
        for line in s.cv_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return ""


def choose_lang(g: Grounding, lang: str | None) -> str:
    if lang in ("fi", "en"):
        return lang
    return "en" if g.job.get("lang") == "en" else "fi"


def verify_section(text: str, g: Grounding, cv_full: str, name: str, embed=None, vectors=None) -> dict:
    profile = {k: p.text for k, p in g.profile.items()}
    profile["CV"] = cv_full   # koko CV (myös pisteytyksestä rajatut osiot) kelpaa lähteeksi
    ad = {k: a["text"] for k, a in g.ad.items()}
    # Ei ilmoituksen otsikkoa: "Rust ja tekoälysovellukset" otsikossa ei saa todistaa "osaan Rustia" -väitettä.
    # Otsikon termit kelpaavat silti muissa kuin minä-väitteissä, koska ne ovat ilmoituksen tekstissä.
    allowed = " ".join([g.job.get("company") or "", g.job.get("city") or "", name])
    ad["title"] = g.job.get("title") or ""
    res = summarize(check(text, profile, ad, allowed, embed, vectors, extra_terms=ad_terms("\n".join(ad.values()))))
    for c in res["claims"]:
        if c["source"]:
            c["source"].update({k: v for k, v in (g.ref(c["source"]["id"]) or {}).items() if k in ("text", "source_ref", "start", "end")})
    return res


def missing_terms(g: Grounding) -> list[str]:
    """Ilmoituksen tekniikat (myös listojen pienellä kirjoitetut), joita profiilissa ei mainita."""
    ad = "\n".join(a["text"] for a in g.ad.values())
    prof = g.profile_text().lower()
    terms = list(dict.fromkeys(tech_terms(ad) + ad_terms(ad)))
    return [t for t in terms if not _present(t, prof)][:20]


def generate(g: Grounding, llm: LLM, lang: str, name: str, fi_cv: str = "") -> dict:
    voice = next((p.text for p in g.profile.values() if p.kind == "cv"), "")
    user = (f"HAKIJAN OMA PROFIILITEKSTI (sävyn malliksi, älä kopioi): {voice[:700]}\n\n"
            f"HAKIJAN NIMI: {name or '(ei tiedossa)'}\n\nILMOITUS: {g.job['title']} | {g.job['company']} | "
            f"{g.job['city']}\n{g.ad_block()}\n\nPROFIILI:\n{g.profile_block()}")
    if _fi_block(lang, fi_cv):
        user += "\n\n" + _fi_block(lang, fi_cv).rstrip()
    instr = application_instructions("\n".join(a["text"] for a in g.ad.values()))
    if instr:
        user += "\n\n" + _instructions_block(lang, instr, asks_for_one(instr))
    missing = missing_terms(g)
    if missing:   # pienelle mallille suora lista: näitä hakija EI osaa
        user += ("\n\nILMOITUKSEN VAATIMUKSET, JOITA PROFIILISSA EI OLE (älä väitä hakijan osaavan näitä; voit sanoa "
                 "olevasi valmis opettelemaan): " + ", ".join(missing))
    from .styleprofile import prompt_block   # hakijan oma tyyli tyylinäytteistä ja hienovaraiset nimet
    raw = llm.complete_json(SYSTEM[lang] + "\n\n" + STYLE[lang] + "\n\n" + FLOW[lang] + "\n\n" + prompt_block(lang, long=True),
                            [{"role": "user", "content": user}], SCHEMA, max_tokens=16000)
    return {k: re.sub(r"\[(?:[AP]\d{1,2}(?:,\s*)?)+\]", "", str(raw.get(k) or "")).strip() for k in SCHEMA["required"]}


def build_package(g: Grounding, llm: LLM, lang: str, name: str, cv_full: str, embed=None, vectors=None,
                  fi_cv: str = "") -> dict:
    texts, style_left = polish(generate(g, llm, lang, name, fi_cv), llm, lang)   # täytefraasit pois ennen tarkistusta
    texts["salary"] = SALARY_PLACEHOLDER[lang]
    sections = []
    for key, title in SECTIONS[lang]:
        sec = {"key": key, "title": title, "text": texts[key], "edited": False, "style": style_left.get(key, [])}
        sec["verify"] = ({"claims": [], "flagged": 0, "check": 0} if key == "salary"
                         else verify_section(texts[key], g, cv_full, name, embed, vectors))
        sections.append(sec)
    return {"job_id": g.job["job_id"], "url": g.job["url"], "title": g.job["title"], "company": g.job["company"],
            "lang": lang, "model": llm.name, "sections": sections}


def source_vectors(g: Grounding, store, s: config.Settings, embed_docs) -> dict[str, np.ndarray]:
    """Profiilipalojen vektorit välimuistista (tai embedataan) + ilmoituspalojen vektorit indeksistä."""
    out: dict[str, np.ndarray] = {}
    missing = []
    for k, p in g.profile.items():
        b = store.profile_vector(p.hash, s.config_id)
        if b is None:
            missing.append(k)
        else:
            out[k] = np.frombuffer(b, dtype=np.float32)
    if missing:
        vecs = embed_docs([g.profile[k].text for k in missing])
        for k, v in zip(missing, vecs):
            out[k] = v
    for k, a in g.ad.items():
        if a["id"] is None:
            continue
        r = store.db.execute(f"SELECT embedding FROM {store.vec_table(s.config_id)} WHERE rowid=?", (a["id"],)).fetchone()
        if r:
            out[k] = np.frombuffer(r[0], dtype=np.float32)
    return out


def tools(s: config.Settings, url: str):
    """Grounding, embedder ja lähdevektorit hakemuspaketille / uudelleentarkistukselle."""
    from . import service
    from .scoring import _embedder_factory
    st = service.store(s)
    full = s.with_(cv_skip_sections=())   # hakemukseen saa käyttää koko CV:tä
    from .profile import build_profile
    g = build(st, s, url, build_profile(full))
    emb = _embedder_factory(s)()
    vectors = source_vectors(g, st, s, emb.embed_queries)
    cv_full = s.cv_path.read_text(encoding="utf-8") if s.cv_path.exists() else ""
    return st, g, emb.embed_queries, vectors, cv_full


def package_url(url: str, s: config.Settings | None = None, lang: str | None = None) -> dict:
    from . import service
    s = s or config.load()
    st, g, embed, vectors, cv_full = tools(s, url)
    name = applicant_name(s)
    lang = choose_lang(g, lang)
    return build_package(g, service.llm("writing", s), lang, name, cv_full, embed, vectors,
                         fi_cv=fi_wording(s) if lang == "fi" else "")


QUESTION_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer"],
                   "properties": {"answer": {"type": "string"}}}

# Lomakkeen kenttä, johon kuuluu kokonainen hakemus eikä lyhyt vastaus ("Vapaamuotoinen hakemuksesi tehtävään")
LONG_FORM = re.compile(r"vapaamuotoi|hakemuksesi|hakemusteksti|saatekirj|saate\b|motivaatiokirj|cover letter|"
                       r"motivation letter|personal statement|application letter|letter of motivation", re.I)

# Sujuvuus: pelkkä "lyhyet virkkeet" tuotti irrallisen faktaluettelon, jossa jokainen virke alkoi samalla rakenteella
FLOW = {
    "fi": """SUJUVUUS:
- Kirjoita yhtenäistä tekstiä, älä faktaluetteloa. Aloita siitä, mitä kysytään, ja kerro sitten esimerkit.
- Sido jokainen esimerkki tehtävään: mihin ilmoituksen tehtävään tai vaatimukseen se liittyy. Älä silti väitä
  enempää kuin PROFIILI kertoo, äläkä päättele siitä taitoja tai ominaisuuksia.
- Vaihtele virkkeiden alkuja ja käytä sidossanoja (siksi, lisäksi, esimerkiksi). Älä aloita peräkkäisiä virkkeitä
  samalla rakenteella.
- Kirjoita luontevaa suomen yleiskieltä, älä käännöskieltä. Jos SUOMENKIELINEN CV on annettu, käytä sen
  sanamuotoja; älä käännä englanninkielistä profiilia sanasta sanaan.
- Kirjoita nimet ja lyhenteet oikein (esimerkiksi lyhenteet isoilla kirjaimilla).""",
    "en": """FLOW:
- Write connected prose, not a list of facts. Start with what is asked, then give the examples.
- Tie each example to the role: which task or requirement in the posting it relates to. Still claim no more than
  the PROFILE says, and do not infer skills or traits from it.
- Vary sentence openings and use linking words. Do not start consecutive sentences with the same structure.
- Write the names and abbreviations correctly.""",
}
LONG_RULE = {
    "fi": "Tämä kenttä on vapaamuotoinen hakemus, ei lyhyt vastaus: 180-280 sanaa, 3-4 kappaletta. (1) miksi haet "
          "juuri tähän tehtävään tähän organisaatioon, (2) mitä tuot mukanasi: 1-2 projektia tai kokemusta sidottuna "
          "tehtävään, (3) mitä haluat tehtävässä oppia lisää, (4) lyhyt lopetus. Älä luettele puutteita (\"en ole "
          "tehnyt X enkä Y\"): jos ilmoituksen vaatimusta ei ole PROFIILI-osiossa, jätä se mainitsematta tai sano "
          "lyhyesti olevasi innokas oppimaan siitä lisää, mutta älä väitä osaavasi sitä. Ei tervehdystä eikä "
          "allekirjoitusta. Jos tieto puuttuu PROFIILI-osiosta, kirjoita sen kohdalle [Täytä itse].",
    "en": "This field is a free-form application, not a short answer: 180-280 words, 3-4 paragraphs. (1) why this "
          "role at this organisation, (2) what you bring: 1-2 projects or experiences tied to the role, (3) what "
          "you want to learn more about in the role, (4) a short close. Do not list gaps (\"I have not done X or Y\"): "
          "if a requirement is not in the PROFILE, leave it out or say briefly you are eager to learn more about it, "
          "but never claim the skill. No greeting or signature. If information is missing from the PROFILE, write "
          "[Fill in yourself] in its place.",
}


# Ilmoituksen omat hakuohjeet ("Kerro yhdestä rakentamastasi ratkaisusta…", "Liitä GitHub-linkki"): menevät yleisten
# pituus- ja rakenneohjeiden edelle. Ilman tätä yleisohje "1-2 projektia" voitti, ja vastaus luetteli neljä projektia.
_DIRECTIVE = re.compile(r"\b(lähetä|kerro|kuvaile|kuvaa|liitä|hakemuksessa|hakemukseesi|hakemukseen|vastaa|perustele|"
                        r"tell us|describe|include|attach|in your (?:application|cover letter)|please (?:send|write|share)|"
                        r"your cover letter should|motivation letter should)\b", re.I)
_ONE = re.compile(r"\b(yhdestä|yksi|yhden|one|a single|single)\b[^.]{0,40}\b(projekt|ratkaisu|työ|sovellu|project|solution|"
                  r"piece of work|example)", re.I)


def application_instructions(ad_text: str) -> list[str]:
    """Ilmoituksen virkkeet, jotka ohjeistavat hakemuksen sisältöä (enintään 5)."""
    out = []
    for line in re.split(r"(?<=[.!?])\s+|\n+", ad_text or ""):
        s = " ".join(line.split())
        if 15 <= len(s) <= 400 and _DIRECTIVE.search(s) and s not in out:
            out.append(s)
    return out[:5]


def asks_for_one(instructions: list[str], question: str = "") -> bool:
    return any(_ONE.search(x) for x in [*instructions, question])


def cv_project_names(cv_md: str) -> list[str]:
    """CV:n projektien lyhyet nimet (otsikko ennen ' – '), joilla lasketaan, montako projektia vastaus mainitsee."""
    from .cvtailor import parse
    names = []
    for s in parse(cv_md).sections:
        for e in s.entries:
            n = re.split(r"\s+[–-]\s+", e.title, maxsplit=1)[0].strip()
            if n and len(n) >= 3 and "|" not in n and n not in names:
                names.append(n)
    return names


def projects_mentioned(text: str, names: list[str]) -> list[str]:
    low = text.lower()
    return [n for n in names if re.search(rf"(?<![\w-]){re.escape(n.lower())}", low)]


def _instructions_block(lang: str, instructions: list[str], one: bool) -> str:
    if not instructions and not one:
        return ""
    fi = lang == "fi"
    head = ("ILMOITUKSEN HAKUOHJEET (noudata näitä ensisijaisesti: ne menevät yleisten pituus- ja rakenneohjeiden edelle; "
            "älä kirjoita ohjetta näkyviin, esim. \"Ilmoituksessa pyydetään…\"):" if fi else
            "APPLICATION INSTRUCTIONS IN THE POSTING (follow these first: they override the general length and structure "
            "rules; do not restate the instruction, e.g. \"The posting asks…\"):")
    out = head + "\n" + "\n".join(f"- {x}" for x in instructions)
    if one:
        out += ("\nKERRO VAIN YHDESTÄ projektista tai ratkaisusta: valitse tehtävään sopivin äläkä mainitse muita projekteja "
                "nimeltä." if fi else
                "\nWrite about ONE project or solution only: pick the best fit and do not name other projects.")
    return out


def fi_wording(s: config.Settings) -> str:
    """Hakijan hyväksymä suomenkielinen CV (cv_fi.md): suomenkielisten tekstien sanamuotojen lähde."""
    from .cvtailor import fi_path
    p = fi_path(s.cv_path)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def form_context(lang: str, others: list[str] | None, written: list[dict] | None) -> str:
    """Saman lomakkeen muut kysymykset ja jo kirjoitetut vastaukset: rekrytoija lukee kentät peräkkäin, joten
    sama projekti ja samat luvut eivät saa toistua jokaisessa vastauksessa."""
    others = [q.strip()[:300] for q in (others or []) if q and q.strip()][:15]
    written = [w for w in (written or []) if str(w.get("answer") or "").strip()][:8]
    if not others and not written:
        return ""
    fi = lang == "fi"
    out = []
    if others:
        out.append(("LOMAKKEEN MUUT KYSYMYKSET (niihin vastataan erikseen):" if fi else
                    "OTHER QUESTIONS ON THIS FORM (answered separately):") + "\n" + "\n".join(f"- {q}" for q in others))
    if written:
        out.append(("JO KIRJOITETUT VASTAUKSET:" if fi else "ANSWERS ALREADY WRITTEN:") + "\n" + "\n\n".join(
            f"[{str(w.get('question') or '')[:200]}]\n{str(w['answer'])[:2500]}" for w in written))
    out.append("JAA SISÄLTÖ: käytä tässä vastauksessa sitä, mikä sopii juuri tähän kysymykseen. Jätä muihin kysymyksiin "
               "paremmin sopivat projektit niille. Älä toista jo kirjoitettujen vastausten projekteja, lukuja tai "
               "lauseita; voit viitata niihin korkeintaan lyhyesti. Jos tähän kysymykseen sopii vain jo käytetty "
               "projekti, kerro siitä eri puoli." if fi else
               "SPLIT THE CONTENT: use in this answer what fits this question best. Leave projects that fit the other "
               "questions better to them. Do not repeat projects, numbers or sentences from the answers already "
               "written; refer to them briefly at most. If only an already used project fits, describe a different "
               "aspect of it.")
    return "\n\n".join(out) + "\n\n"


def _fi_block(lang: str, fi_cv: str) -> str:
    if lang != "fi" or not fi_cv.strip():
        return ""
    return ("SUOMENKIELINEN CV (hakijan itse hyväksymät sanamuodot samoista tiedoista; käytä näitä ilmauksia, "
            f"älä lisää mitään, mitä PROFIILI-osiossa ei ole):\n{fi_cv[:6000]}\n\n")


def answer_question(g: Grounding, llm: LLM, question: str, lang: str, name: str, cv_full: str, embed=None, vectors=None,
                    fi_cv: str = "", others: list[str] | None = None, written: list[dict] | None = None) -> dict:
    """Vastaus yksittäiseen portaalikysymykseen samoilla säännöillä kuin hakemuspaketti: tyyli, kliseet ja väitetarkistus.
    Vapaamuotoisen hakemuksen kenttä saa hakemuksen pituuden ja rakenteen."""
    missing = missing_terms(g)
    long = bool(LONG_FORM.search(question))
    rule = LONG_RULE[lang] if long else {
        "fi": "Vastaa vain tähän kysymykseen, 40-100 sanaa. Jos vastaus vaatii tiedon, jota PROFIILI-osiossa ei ole, "
              "kirjoita sen kohdalle [Täytä itse].",
        "en": "Answer only this question, 40-100 words. If the answer needs information that is not in the PROFILE, "
              "write [Fill in yourself] in its place."}[lang]
    user = (f"ILMOITUS: {g.job['title']} | {g.job['company']} | {g.job['city']}\n{g.ad_block()}\n\nPROFIILI:\n{g.profile_block()}\n\n"
            + _fi_block(lang, fi_cv) + form_context(lang, others, written)
            + (f"EI PROFIILISSA (älä väitä osaavasi): {', '.join(missing)}\n\n" if missing else "") + f"KYSYMYS: {question}")
    # ilmoituksen omat hakuohjeet koskevat hakemusta ja projektikysymyksiä, eivät esim. "mitä kieliä osaat"
    applies = long or bool(re.search(r"projekt|ratkaisu|työnäyte|project|solution|portfolio", question, re.I))
    instr = application_instructions("\n".join(a["text"] for a in g.ad.values())) if applies else []
    one = applies and asks_for_one(instr, question)
    from .styleprofile import prompt_block   # hakijan oma tyyli tyylinäytteistä ja hienovaraiset nimet
    system = (SYSTEM[lang] + "\n\n" + STYLE[lang] + "\n\n" + FLOW[lang] + "\n\n" + prompt_block(lang, long)
              + "\n\n" + rule + ("\n\n" + _instructions_block(lang, instr, one) if instr or one else ""))
    msgs = [{"role": "user", "content": user}]
    raw = llm.complete_json(system, msgs, QUESTION_SCHEMA, max_tokens=8000 if long else 3000)
    names = cv_project_names(cv_full) if one else []
    many = projects_mentioned(str(raw.get("answer") or ""), names)
    if one and len(many) > 1:   # ohje pyysi yhtä projektia: yksi korjauskierros palautteen kanssa
        fb = (f"Vastauksessa mainitaan {len(many)} projektia ({', '.join(many)}), mutta ilmoitus pyytää kertomaan yhdestä. "
              "Kirjoita vastaus uudelleen vain yhdestä, tehtävään sopivimmasta projektista; älä mainitse muita nimeltä."
              if lang == "fi" else
              f"The answer names {len(many)} projects ({', '.join(many)}), but the posting asks for one. Rewrite it about "
              "the single best-fitting project only; do not name the others.")
        msgs = msgs + [{"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)}, {"role": "user", "content": fb}]
        raw = llm.complete_json(system, msgs, QUESTION_SCHEMA, max_tokens=8000 if long else 3000)
    texts, left = polish({"answer": str(raw.get("answer") or "").strip()}, llm, lang)
    v = verify_section(texts["answer"], g, cv_full, name, embed, vectors)
    reasons = [r for c in v["claims"] if c["status"] != "ok" for r in c["reasons"]]
    many = projects_mentioned(texts["answer"], names)
    if one and len(many) > 1:
        reasons.append(f"ilmoitus pyytää yhtä projektia, vastauksessa {len(many)}: {', '.join(many)}")
    return {"source": "Kielimalli", "label": question[:80], "text": texts["answer"], "flagged": v["flagged"], "check": v["check"],
            "reasons": reasons, "style": left.get("answer", [])}


def answer_url(url: str, question: str, s: config.Settings | None = None, others: list[str] | None = None,
               written: list[dict] | None = None) -> dict:
    from . import service
    from .clean import detect_lang
    s = s or config.load()
    st, g, embed, vectors, cv_full = tools(s, url)
    guess = detect_lang(question)
    lang = guess if guess in ("fi", "en") else choose_lang(g, None)
    llm = service.llm("writing", s)
    fi_cv = fi_wording(s) if lang == "fi" else ""
    ctx = form_context(lang, others, written)
    from .styleprofile import prompt_block
    key = sha1("answer-5", g.hash(), llm.name, lang, question, fi_cv, ctx, prompt_block(lang, bool(LONG_FORM.search(question))))   # sama lomake uudelleen: vastaus heti
    hit = cache_get(st, key)
    if hit:
        return hit
    res = answer_question(g, llm, question, lang, applicant_name(s), cv_full, embed, vectors, fi_cv=fi_cv,
                          others=others, written=written)
    cache_put(st, key, "answer", g.job["job_id"], llm.name, res)
    return res


def reverify(url: str, sections: list[dict], s: config.Settings | None = None) -> list[dict]:
    """Muokattujen luonnosten uudelleentarkistus (ei kielimallia)."""
    s = s or config.load()
    st, g, embed, vectors, cv_full = tools(s, url)
    name = applicant_name(s)
    for sec in sections:
        if sec.get("key") != "salary":
            sec["verify"] = verify_section(sec.get("text", ""), g, cv_full, name, embed, vectors)
    return sections
