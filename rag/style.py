"""Luonteva kieli: tyyliohje kehotteisiin ja tekoälylle tyypillisten fraasien tunnistus.

Pienet mallit kirjoittavat hakemuksiin samoja täytefraaseja ("highly motivated", "brings a strong foundation",
"olen innostunut"), jotka lukija tunnistaa heti. Fraasit tunnistetaan säännöillä; jos niitä löytyy, teksti
kirjoitetaan kerran uudelleen palautteen kanssa, ja jäljelle jääneet näytetään käyttäjälle.
"""
from __future__ import annotations

import re

from .llm import LLM

# Fraasit (säännöllisinä lausekkeina). Pidetään täsmällisinä: sana "motivated" yksin ei ole klisee.
CLICHES = {
    "en": [
        r"highly motivated", r"self-motivated", r"motivated and", r"passionate about", r"\bpassion for",
        r"\bthis candidate\b", r"\bthe candidate\b", r"\bbrings? (?:a |an )?(?:strong|solid|unique|wealth|diverse)",
        r"strong foundation", r"solid foundation", r"proven track record",
        r"\bdynamic\b(?!\s+(?:analysis|testing|programming|routing|memory|typing|linking))", r"fast-paced",
        r"\bleverag(?:e|ing)\b", r"\bsynerg", r"results-driven", r"detail-oriented", r"team player",
        r"eager to (?:learn|contribute|grow)", r"i am excited", r"i am thrilled", r"thrilled to", r"\bdelve\b",
        r"\bspearhead", r"cutting-edge", r"\bwell-versed\b", r"\bseamless", r"\brobust\b", r"\bshowcas",
        r"a diverse background", r"wide range of", r"complemented by", r"\bpossessing\b", r"valuable asset",
        r"make a (?:meaningful |significant )?impact", r"hit the ground running", r"go-getter", r"\bembark",
        # "I combine X with Y": geneerinen itsekuvaus (käyttäjän toive); "combine two tables" yms. tekninen käyttö sallitaan
        r"\bI combine\b", r"\bcombin(?:e|es|ing) (?:hands-on |practical |strong |solid )?(?:[\w/-]+ )?"
        r"(?:skills|experience|knowledge|expertise|background)\b",
    ],
    "fi": [
        r"erittäin motivoitunut", r"\bintohim", r"olen innostunut", r"olen erittäin innostunut", r"innokas oppimaan",
        r"vahva pohja", r"vankka pohja",
        r"\bdynaami(?!\w*\s+(?:analyy|testau|testaa|ohjelmoin|reitity|muisti|tyypity))",   # "dynaaminen analyysi" on termi
        r"monipuolinen osaaja", r"tiimipelaaja", r"\bhakij",
        r"tuoda lisäarvoa", r"arvokasta lisäarvoa", r"tuoda arvokasta", r"\bsynergi", r"\bhyödyntäen\b",
        r"laaja-alais", r"kattava osaaminen", r"olen varma, että", r"odotan innolla", r"täydellinen sopivuus",
        r"\bhuippuluok", r"\bsaumato", r"intensiiv", r"valmis ottamaan haasteen", r"uskon vahvasti",
        # "Yhdistän X:n Y:hyn": geneerinen itsekuvaus (käyttäjän toive); "yhdistää tauluja" yms. tekninen käyttö sallitaan
        r"\byhdistän\b", r"\byhdistä\w* (?:käytännön |vahvan |monipuolisen )?(?:[\w/-]+-)?(?:osaamis|taito|kokemu|taust)",
    ],
}

STYLE = {
    "en": """WRITING STYLE (important):
- Write like a real student wrote it: plain, concrete, specific. Prefer facts (what was built, with what, for whom)
  over adjectives about personality.
- No filler or buzzwords: never use "highly motivated", "passionate", "brings", "strong foundation", "dynamic",
  "leverage", "eager to", "proven track record", "complemented by", "this candidate", "demonstrating a focus on",
  "showcasing", "seamless", "fast-paced", "in today's", "I combine X with Y". No em dashes.
- Never refer to the applicant in the third person ("the candidate", "he").
- Short sentences. No sentence should be generic enough to fit any applicant.""",
    "fi": """KIRJOITUSTYYLI (tärkeä):
- Kirjoita kuin opiskelija itse kirjoittaisi: selkeästi, konkreettisesti ja asiallisesti. Kerro mitä on tehty,
  millä välineillä ja kenelle, äläkä luettele luonteenpiirteitä adjektiiveilla.
- Ei täytefraaseja: älä käytä ilmauksia "erittäin motivoitunut", "intohimo", "olen innostunut", "vahva pohja",
  "dynaaminen", "tuoda lisäarvoa", "odotan innolla", "monipuolinen osaaja", "kokonaisvaltainen",
  "nykypäivän nopeasti muuttuva", "yhdistän X:n Y:hyn". Ei pitkiä ajatusviivoja.
- Kirjoita minä-muodossa ilman minä-sanaa: "Rakennan…", "Valmistun keväällä 2028", "Olen tehnyt…".
  Ei koskaan kolmatta persoonaa: ei "Rakentaa…", "Opiskelee…" eikä "opiskelija, joka valmistuu…".
- Lyhyet virkkeet. Yksikään lause ei saa olla niin yleinen, että se sopisi kenelle tahansa hakijalle.""",
}


# Kolmas persoona suomeksi: virkkeen alussa yksikön 3. persoonan verbi ("Rakentaa…", "Opiskelee…") tai
# hakijaa kuvaava relatiivilause ("opiskelija, joka valmistuu…"). "sovellus, joka kerää" on sallittu.
_FI_3SG_START = re.compile(r"(?:^|[.!?]\s+)([A-ZÅÄÖ][a-zåäö]+(?:aa|ää|ee|ii|oo|uu|yy|öö))\b")
_FI_3SG_REL = re.compile(r"\b(?:\w*opiskelija|\w*insinööri|\w*kehittäjä|osaaja|ammattilainen|tekijä),?\s+joka\s+(\w+)", re.I)


def third_person_fi(text: str) -> list[str]:
    """Suomenkielisen tekstin kolmannen persoonan ilmaukset hakijasta."""
    hits = [m.group(1) for m in _FI_3SG_START.finditer(text.strip())]
    hits += [f"joka {m.group(1)}" for m in _FI_3SG_REL.finditer(text)]
    return list(dict.fromkeys(hits))


def _own() -> str:
    try:
        from .styleprofile import own_text
        return own_text().lower()
    except Exception:  # noqa: BLE001  (tyylinäytteet ovat valinnaisia)
        return ""


def _discreet() -> list[str]:
    try:
        import json
        from .config import DATA
        return list((json.loads((DATA / "profile.json").read_text(encoding="utf-8")).get("discreet_names") or {}).keys())
    except Exception:  # noqa: BLE001
        return []


def find(text: str, lang: str = "en") -> list[str]:
    """Löydetyt kliseet tekstistä (alkuperäisessä kirjoitusasussa, toistot kerran)."""
    out = []
    if lang == "fi":
        out += [f"kolmas persoona: \"{w}\"" for w in third_person_fi(text)]
    own = _own()
    for pat in CLICHES["en"] + (CLICHES["fi"] if lang == "fi" else []):
        for m in re.finditer(pat, text, re.I):
            frag = m.group(0)
            if frag.lower() in own:      # hakija on itse käyttänyt tätä ilmausta tyylinäytteissään
                continue
            if frag.lower() not in (x.lower() for x in out):
                out.append(frag)
    for name in _discreet():             # nimet, joita hakija ei halua toistaa (profile.json: discreet_names)
        if re.search(r"(?<!\w)" + re.escape(name), text, re.I) and name not in out:
            out.append(name)
    # sama virkkeen aloitus yhä uudelleen ("Olen... Olen... Olen...") kuulostaa koneelta
    starts = re.findall(r"(?:^|[.!?]\s+)(\w+)", text.strip())
    for w in ("Olen", "I", "Minulla"):
        n = sum(1 for s in starts if s == w)
        if n >= 4:
            out.append(f"{n} virkettä alkaa sanalla \"{w}\"")
    return out


REWRITE_SYSTEM = {
    "en": "You edit job application text so it sounds natural and human. Keep every fact exactly as it is; do not add "
          "skills, numbers or claims. Replace the listed phrases with plain, concrete wording or remove them.",
    "fi": "Muokkaat hakemustekstiä luontevaksi. Pidä kaikki tosiasiat täsmälleen ennallaan; älä lisää taitoja, lukuja "
          "tai väitteitä. Korvaa luetellut ilmaukset arkisella, konkreettisella sanamuodolla tai poista ne.",
}


def polish(texts: dict[str, str], llm: LLM, lang: str = "en") -> tuple[dict[str, str], dict[str, list[str]]]:
    """Kirjoita kliseitä sisältävät tekstit kerran uudelleen. Palauttaa (tekstit, jäljelle jääneet kliseet)."""
    bad = {k: find(v, lang) for k, v in texts.items()}
    todo = {k: v for k, v in texts.items() if bad[k]}
    if not todo:
        return texts, {}
    user = "\n\n".join(f"[{k}] Poistettavat ilmaukset: {', '.join(bad[k])}\n{v}" for k, v in todo.items())
    schema = {"type": "object", "additionalProperties": False, "required": list(todo),
              "properties": {k: {"type": "string"} for k in todo}}
    try:
        raw = llm.complete_json(REWRITE_SYSTEM[lang] + "\n\n" + STYLE[lang], [{"role": "user", "content": user}], schema,
                                max_tokens=3000)
    except Exception:  # noqa: BLE001  (tyylikorjaus ei saa kaataa generointia)
        return texts, {k: v for k, v in bad.items() if v}
    out = dict(texts)
    for k in todo:
        new = str(raw.get(k) or "").strip()
        if new and len(find(new, lang)) < len(bad[k]) and len(new) > 0.5 * len(texts[k]):
            out[k] = new
    left = {k: find(v, lang) for k, v in out.items()}
    return out, {k: v for k, v in left.items() if v}
