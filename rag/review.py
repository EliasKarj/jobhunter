"""Lomakkeen lopputarkistus: hakija uskoo hakemuksen olevan valmis ja pyytää laajennuksesta tarkistuksen.

Kolme vaihetta:
1. Säännöt ilman kielimallia: tyhjät pakolliset kentät, lomakkeen omat virheilmoitukset, rastittamattomat
   suostumukset, linkkien muoto, merkkirajat, yhteystietojen vastaavuus CV:hen, väitteet ilman lähdettä CV:ssä,
   täytefraasit ja pakotetut suomennokset.
2. Kielimalli: kielivirheet (tarkka lainaus ja korjaus), rakenne ja se, mitä ilmoitus painottaa mutta hakemus ei
   käytä (vain CV:n tiedoilla).
3. Mallin vastauksen tarkistus: kielikorjaus näytetään vain, jos lainattu kohta todella on kentässä, ja ehdotettu
   lause ajetaan väitetarkistuksen läpi. Lähteetön ehdotus poistetaan.

Palkkatoiveeseen ei koskaan oteta kantaa (hakijan oma päätös). Mitään ei lähetetä: tarkistus vain listaa.
"""
from __future__ import annotations

import json
import re

from . import config
from .clean import sha1
from .fi_terms import PROMPT as FI_TERMS
from .style import STYLE, find as style_find

MAX_FIELDS = 80
LONG_TEXT = 120          # tätä pidempi vapaa teksti tarkistetaan väitteiden ja tyylin osalta

_SALARY = re.compile(r"palkka|salary|compensation|pay expectation|wage", re.I)
_LINK = re.compile(r"linkedin|github|portfolio|verkkosivu|kotisivu|website|url|linkki", re.I)
_CONSENT = re.compile(r"olen lukenut|hyväksyn|tietosuoja|privacy|i agree|i accept|accept the|terms|ehdot|suostu", re.I)
_EMAIL = re.compile(r"sähköposti|e-?mail", re.I)
_PHONE = re.compile(r"puhelin|phone|mobile|matkapuhelin", re.I)


def _clean_fields(raw: list) -> list[dict]:
    out = []
    for i, f in enumerate(raw[:MAX_FIELDS]):
        if not isinstance(f, dict):
            continue
        out.append({"i": int(f.get("i", i)), "label": str(f.get("label") or "")[:300].strip(),
                    "type": str(f.get("type") or "text")[:20], "value": str(f.get("value") or "")[:6000],
                    "checked": bool(f.get("checked")), "required": bool(f.get("required")),
                    "invalid": bool(f.get("invalid")), "error": str(f.get("error") or "")[:300].strip(),
                    "maxlength": int(f["maxlength"]) if str(f.get("maxlength") or "").isdigit() else 0})
    return out


def _salary(f: dict) -> bool:
    return bool(_SALARY.search(f["label"]))


def _digits(s: str) -> str:
    d = re.sub(r"\D", "", s)
    return "0" + d[3:] if d.startswith("358") else d


def rule_checks(fields: list[dict], contact: dict[str, str]) -> dict[str, list[dict]]:
    """Deterministiset löydökset: must_fix (korjattava ennen lähettämistä) ja check (tarkista itse)."""
    must, check = [], []
    add = lambda lst, f, issue, fix="": lst.append({"i": f["i"], "field": f["label"], "issue": issue, "fix": fix})  # noqa: E731
    for f in fields:
        if _salary(f):
            continue
        val = f["value"].strip()
        if f["type"] == "checkbox":
            if _CONSENT.search(f["label"]) and not f["checked"]:
                add(must if f["required"] else check, f, "Ruutua ei ole rastitettu: lomake ei välttämättä lähde ilman sitä.")
            continue
        if f["required"] and not val and not f["checked"]:
            add(must, f, "Pakollinen kenttä on tyhjä.")
        if f["invalid"] or f["error"]:
            add(must, f, f"Lomake ilmoittaa virheen{': ' + f['error'] if f['error'] else ''}.")
        if f["maxlength"] and len(val) > f["maxlength"]:
            add(must, f, f"Teksti on {len(val)} merkkiä, raja on {f['maxlength']}.")
        if val and _LINK.search(f["label"]) and not re.match(r"https?://\S+$", val):
            fix = "https://" + val if re.match(r"(?:www\.)?[\w-]+\.[a-z]{2,}(?:/\S*)?$", val, re.I) else ""
            add(must, f, "Linkki ei ole osoitemuodossa (https://…).", fix)
        if val and _EMAIL.search(f["label"]) and len(f["label"]) < 40:
            if contact.get("email") and val.lower() != contact["email"].lower():
                add(check, f, f"Sähköposti poikkeaa CV:stä ({contact['email']}).")
        if val and _PHONE.search(f["label"]) and len(f["label"]) < 40:
            if contact.get("phone") and _digits(val) != _digits(contact["phone"]):
                add(check, f, f"Puhelinnumero poikkeaa CV:stä ({contact['phone']}).")
    return {"must_fix": must, "check": check}


def text_checks(fields: list[dict], verify_fn, lang: str, ad_text: str = "") -> dict[str, list[dict]]:
    """Pitkät vapaat tekstit: väitteet ilman lähdettä CV:ssä (tarkista) ja tyylihuomiot (paranna)."""
    check, improve = [], []
    for f in fields:
        val = f["value"].strip()
        if _salary(f) or len(val) < LONG_TEXT:
            continue
        v = verify_fn(val)
        for c in v.get("claims", []):
            if c.get("status") == "flag":
                reasons = c.get("reasons") or []
                # päivämäärät ja luvut, jotka ovat ilmoituksessa (aloituspäivä 11.1.2027), eivät ole keksittyjä
                nums = [m.group(1) for r in reasons if (m := re.match(r"luku (\S+)", r))]
                if nums and len(nums) == len(reasons) and all(n in ad_text for n in nums):
                    continue
                why = "; ".join(reasons)[:200]
                check.append({"i": f["i"], "field": f["label"], "issue": f"Väite ei näy CV:ssä: \"{c['text'][:160]}\""
                              + (f" ({why})" if why else "") + ". Pidä vain, jos se pitää paikkansa.", "fix": ""})
        style = style_find(val, lang)
        if style:
            improve.append({"i": f["i"], "field": f["label"], "issue": "Tyylihuomio: " + ", ".join(style[:6]), "fix": ""})
    return {"check": check, "improve": improve}


SYSTEM = """Tarkistat työhakemuslomakkeen ennen kuin hakija lähettää sen. Hakija uskoo hakemuksen olevan valmis.
Saat ilmoituksen, hakijan CV:n, lomakkeen kentät arvoineen ja jo koodilla löydetyt huomiot.

Palauta vain todelliset, konkreettiset huomiot. Jos jokin on kunnossa, älä mainitse sitä. Enintään 6 kohtaa per lista.
- must_fix: virheet, jotka pitää korjata ennen lähettämistä. Kielivirheissä kind="kieli", quote = virheellinen kohta
  TÄSMÄLLEEN kentän tekstistä kopioituna (lyhyt, 2-12 sanaa) ja fix = korjattu muoto. Muut: kind="muoto".
- improve: parannukset. Esim. ilmoitus painottaa jotain (asiakaspalvelu, käyttäjätuki), ja CV:ssä on siihen sopiva
  kokemus, jota hakemus ei käytä: kerro mikä ja anna text = valmis lause, jonka voi lisätä. Myös rakenne (irrallinen
  kappale, toisto). kind="sisältö" tai "rakenne".
- check: asiat, jotka vain hakija tietää ja jotka kannattaa varmistaa: ristiriita ilmoituksen ja lomakkeen välillä
  (esim. lähityö toisella paikkakunnalla vs. asuinpaikka, aloituspäivä vs. opinnot), lupaukset joita CV ei kerro,
  ristiriita lomakkeen ja CV:n välillä (esim. eri ammattinimike). Näissä quote ja fix jätetään tyhjiksi.
- must_fix on vain todellisille virheille: kielivirhe (kind="kieli") tai lomakkeen muotovirhe (kind="muoto").
  Tyyli, sisältö ja rakenne ovat aina improve-listassa.
- verdict: "valmis", jos must_fix on tyhjä, muuten "korjattavaa". summary: yksi lyhyt lause.

EHDOTTOMAT SÄÄNNÖT:
- Ehdotetuissa lauseissa vain CV:ssä olevat taidot, kokemukset, luvut ja vuodet. Älä kerro, missä tai miten taitoa
  on käytetty (opinnoissa, projekteissa, työssä), ellei CV sano sitä juuri sen taidon kohdalla. Älä keksi mitään, älä lupaa
  hakijan puolesta mitään (muutto, aloitus, saatavuus), äläkä päättele luonteenpiirteitä.
- Palkkatoiveeseen ei oteta kantaa millään tavalla.
- Jos kentällä on merkkiraja, lisättäväksi ehdotettu teksti ei saa ylittää jäljellä olevaa tilaa: ehdota silloin
  lyhyempää tai kerro, mitä voisi poistaa tilalta.
- Hakija on itse valinnut ilmauksen "olen innokas oppimaan (näistä) lisää": älä ehdota sen poistamista tai muuttamista.
- Älä toista koodin jo löytämiä huomioita.
- field = kentän otsikko täsmälleen sellaisena kuin se annettiin.
- Kirjoita suomeksi, lyhyesti ja asiallisesti."""

_ITEM = {"type": "object", "additionalProperties": False, "required": ["field", "kind", "issue", "quote", "fix", "text"],
         "properties": {k: {"type": "string"} for k in ("field", "kind", "issue", "quote", "fix", "text")}}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["verdict", "summary", "must_fix", "improve", "check"],
          "properties": {"verdict": {"type": "string", "enum": ["valmis", "korjattavaa"]}, "summary": {"type": "string"},
                         "must_fix": {"type": "array", "items": _ITEM}, "improve": {"type": "array", "items": _ITEM},
                         "check": {"type": "array", "items": _ITEM}}}


def _form_block(fields: list[dict]) -> str:
    rows = []
    for f in fields:
        if _salary(f):
            rows.append(f"- {f['label']}: [hakijan oma tieto, ei arvioida]")
            continue
        if f["type"] == "checkbox":
            rows.append(f"- {f['label']}: {'rastitettu' if f['checked'] else 'EI rastitettu'}")
            continue
        val = f["value"].strip() or "(tyhjä)"
        room = f" [merkkiraja {f['maxlength']}, käytetty {len(f['value'].strip())}]" if f["maxlength"] else ""
        rows.append(f"- {f['label']}{' *' if f['required'] else ''}{room}: {val}")
    return "\n".join(rows)


def _field_index(fields: list[dict]) -> dict[str, int]:
    return {f["label"].lower(): f["i"] for f in fields}


def llm_review(fields: list[dict], ad_text: str, cv_text: str, rules: dict, llm, lang: str = "fi") -> dict:
    found = [f"- {x['field']}: {x['issue']}" for k in ("must_fix", "check", "improve") for x in rules.get(k, [])]
    user = (f"ILMOITUS:\n{ad_text[:7000]}\n\nCV:\n{cv_text[:8000]}\n\nLOMAKE:\n{_form_block(fields)}\n\n"
            f"KOODIN LÖYTÄMÄT HUOMIOT (älä toista):\n" + ("\n".join(found) or "(ei)"))
    return llm.complete_json(SYSTEM + "\n\n" + STYLE[lang] + ("\n\n" + FI_TERMS if lang == "fi" else ""),
                             [{"role": "user", "content": user}], SCHEMA, max_tokens=12000)   # Opus ajattelee ennen vastausta: ajattelu kuluttaa samaa rajaa


def vet(raw: dict, fields: list[dict], verify_fn) -> dict:
    """Mallin vastauksen tarkistus: lainausten on oltava kentässä, ehdotetut lauseet väitetarkistuksen läpi."""
    idx = _field_index(fields)
    values = {f["label"].lower(): f["value"] for f in fields}
    all_text = "\n".join(f["value"] for f in fields)
    out: dict[str, list[dict]] = {"must_fix": [], "improve": [], "check": []}
    dropped = 0
    for key in out:
        for it in (raw.get(key) or [])[:6]:
            field = str(it.get("field") or "").strip()
            if _SALARY.search(field):
                dropped += 1
                continue
            item = {"i": idx.get(field.lower(), -1), "field": field, "issue": str(it.get("issue") or "").strip(),
                    "quote": str(it.get("quote") or "").strip(), "fix": str(it.get("fix") or "").strip(),
                    "text": str(it.get("text") or "").strip(), "kind": str(it.get("kind") or "")}
            if re.search(r"innokas oppimaan", item["quote"], re.I):   # hakijan oma valinta (ks. SYSTEM)
                dropped += 1
                continue
            if not item["fix"]:
                item["quote"] = ""
            dest = key
            if key == "must_fix" and re.search(r"\bCV", item["issue"]) and item["kind"] != "kieli":
                dest = "check"            # ristiriita CV:n kanssa: vain hakija tietää, kumpi on oikein
                item["quote"] = item["fix"] = ""
            elif key == "must_fix" and item["kind"] not in ("kieli", "muoto"):
                dest = "improve"          # pakollisiin vain kieli- ja muotovirheet; tyyli ja sisältö ovat ehdotuksia
            if item["kind"] == "kieli" or item["quote"]:
                where = values.get(field.lower(), all_text)
                norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()  # noqa: E731
                if not item["quote"] or norm(item["quote"]) not in norm(where) and norm(item["quote"]) not in norm(all_text):
                    dropped += 1          # lainattu kohta ei ole lomakkeella: malli keksi virheen
                    continue
                if norm(item["quote"]) == norm(item["fix"]):
                    dropped += 1
                    continue
            if item["text"]:
                v = verify_fn(item["text"])
                if v.get("flagged"):
                    why = "; ".join(dict.fromkeys(r for c in v.get("claims", []) if c.get("status") == "flag"
                                                  for r in c.get("reasons", [])))[:160]
                    item["text"] = ""
                    item["issue"] += f" (Ehdotettu lause poistettiin, siinä oli väite, jota CV:ssä ei ole{': ' + why if why else ''}.)"
            if item["issue"] or item["fix"] or item["text"]:
                out[dest].append(item)
    out["dropped"] = dropped
    return out


def _merge(*parts: dict) -> dict[str, list[dict]]:
    res: dict[str, list[dict]] = {"must_fix": [], "improve": [], "check": []}
    for p in parts:
        for k in res:
            for it in p.get(k, []):
                it.setdefault("quote", "")
                it.setdefault("text", "")
                it.setdefault("kind", "")
                if not any(x["field"] == it["field"] and x["issue"] == it["issue"] for x in res[k]):
                    res[k].append(it)
    return res


def review(url: str, raw_fields: list, s: config.Settings | None = None) -> dict:
    """Koko tarkistus yhdelle lomakkeelle. url: Jobhunterin ilmoitus (lähteenä ilmoitus ja CV)."""
    from . import service
    from .clean import detect_lang
    from .grounding import cache_get, cache_put
    from .package import applicant_name, fi_wording, tools, verify_section
    from server.assist import contact_info

    s = s or config.load()
    fields = _clean_fields(raw_fields)
    if not fields:
        return {"error": "Lomakkeelta ei löytynyt kenttiä."}
    st, g, embed, vectors, cv_full = tools(s, url)
    name = applicant_name(s)
    long_text = " ".join(f["value"] for f in fields if len(f["value"]) >= LONG_TEXT)
    lang = detect_lang(long_text) if long_text else "fi"
    lang = lang if lang in ("fi", "en") else "fi"
    verify_fn = lambda t: verify_section(t, g, cv_full, name, embed, vectors)  # noqa: E731
    rules = rule_checks(fields, contact_info(cv_full))
    llm = service.llm("review", s)   # oletus Opus: ajetaan harvoin, laatu ratkaisee
    cv_text = (fi_wording(s) if lang == "fi" else "") or cv_full
    ad_text = "\n".join(a["text"] for a in g.ad.values())
    texts = text_checks(fields, verify_fn, lang, ad_text)
    key = sha1("review-5", g.hash(), llm.name, json.dumps(fields, ensure_ascii=False, sort_keys=True), cv_text)
    hit = cache_get(st, key)
    if hit:
        return hit
    try:
        raw = llm_review(fields, ad_text, cv_text, _merge(rules, texts), llm, lang)
        model = vet(raw, fields, verify_fn)
        summary = str(raw.get("summary") or "").strip()
    except Exception as e:  # noqa: BLE001  (sääntötarkistus näytetään, vaikka malli ei vastaisi)
        model, summary = {"must_fix": [], "improve": [], "check": [], "dropped": 0}, f"Kielimallin tarkistus epäonnistui: {e}"
    res = _merge(rules, model, texts)
    res.update(verdict="valmis" if not res["must_fix"] else "korjattavaa", summary=summary,
               dropped=model.get("dropped", 0), fields=len(fields), model=llm.name)
    if not summary.startswith("Kielimallin tarkistus epäonnistui"):   # epäonnistunutta ei tallenneta
        cache_put(st, key, "review", g.job["job_id"], llm.name, res)
    return res
