"""Chat ilmoituksille: haku -> konteksti numeroiduista lähteistä -> streamattu vastaus -> viitteiden tarkistus.

Tapahtumat (iteraattori dictejä, sellaisenaan SSE:ksi):
  {"type": "filters", "filters": [...]}
  {"type": "sources", "sources": [{n, job_id, title, company, city, url, posted, chunks: [...]}]}
  {"type": "delta", "text": "..."}
  {"type": "done", "answer": ..., "cited": [n], "invalid": [n], "uncited": [lause], "found": bool}
  {"type": "error", "message": "..."}
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterator

from . import config
from .llm import LLM, LLMError, Messages
from .retrieve import Filters, Hit, parse_filters, search

NOT_FOUND = "En löytänyt ilmoituksia, jotka vastaisivat kysymykseen."
MIN_SIM = 0.42   # tätä heikompi paras kosiniosuma ilman sanahakuosumaa = ei relevanttia (bge-m3, kalibroitu käsin)

SYSTEM = """Olet työnhakuavustaja. Vastaat käyttäjän kysymyksiin työpaikkailmoituksista.

Säännöt:
- Käytä VAIN alla annettuja lähteitä [1]...[n]. Älä käytä omaa tietoasi yrityksistä tai työmarkkinoista.
- Merkitse jokaisen väitteen perään lähde hakasulkeissa, esim. "Wapice hakee .NET-kehittäjää [2]." Useampi lähde: [1][3].
- Jos lähteistä ei löydy vastausta tai mikään ilmoitus ei täytä kysymyksen ehtoja, sano se suoraan
  ("En löytänyt ...") äläkä arvaa.
- Kieltoehdot (esim. "ei ruotsia"): ilmoitus täyttää ehdon vain, jos lähteessä EI mainita kiellettyä asiaa
  vaatimuksena. Jos et voi todeta sitä lähteestä, sano että tieto puuttuu.
- Vastaa samalla kielellä kuin kysymys. Ole lyhyt: luettelo ilmoituksista on usein paras muoto.
- Älä keksi palkkoja, päivämääriä tai vaatimuksia, joita lähteissä ei ole."""


def source_list(hits: list[Hit]) -> list[dict]:
    return [{"n": i, "job_id": h.job_id, "title": h.title, "company": h.company, "city": h.city, "url": h.url,
             "posted": h.posted, "source": h.source, "sim": round(h.best_sim, 3), "chunks": h.chunks}
            for i, h in enumerate(hits, 1)]


def context_block(sources: list[dict]) -> str:
    out = []
    for s in sources:
        head = " | ".join(x for x in (s["title"], s["company"], s["city"], f"julkaistu {s['posted']}" if s["posted"] else "") if x)
        body = "\n…\n".join(c["text"].strip() for c in s["chunks"])
        out.append(f"[{s['n']}] {head}\n{body}")
    return "\n\n".join(out)


_CITE = re.compile(r"\[(\d{1,2})\]")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def check_citations(answer: str, n_sources: int) -> dict:
    """Mihin lähteisiin viitattiin, mitkä viitteet ovat olemattomia ja mitkä väitelauseet jäivät ilman viitettä."""
    nums = [int(x) for x in _CITE.findall(answer)]
    cited = sorted({n for n in nums if 1 <= n <= n_sources})
    invalid = sorted({n for n in nums if not 1 <= n <= n_sources})
    uncited = []
    for s in _SENT.split(answer):
        t = s.strip(" -*•\t")
        if len(t) < 25 or _CITE.search(t) or t.endswith(":"):
            continue
        if re.match(r"(en löytänyt|i could not|i didn't find|no matching|lähteissä ei|tieto puuttuu|yksikään)", t, re.I):
            continue
        uncited.append(t)
    return {"cited": cited, "invalid": invalid, "uncited": uncited}


def strip_invalid(answer: str, n_sources: int) -> str:
    return _CITE.sub(lambda m: m.group(0) if 1 <= int(m.group(1)) <= n_sources else "", answer)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`\"“”„]", "", s)).strip().lower()


def _title_key(title: str) -> str:
    """Otsikon tunnistettava alku (ennen pilkkua/sulkua), esim. 'Commissioning Manager'."""
    return _norm(re.split(r"[,(|–-]", title, maxsplit=1)[0])


_QUOTE = re.compile(r"[\"“”„]([^\"“”„]{12,200})[\"“”„]")


def fix_citations(answer: str, sources: list[dict]) -> tuple[str, list[dict]]:
    """Korjaa viite, kun lauseessa nimetään toisen lähteen otsikko, mutta ei viitatun. Liputa lainaukset,
    joita viitatusta lähteestä ei löydy. Pienet mallit sekoittavat numeroita; tämä on deterministinen varmistus."""
    by_n = {s["n"]: s for s in sources}
    keys = {s["n"]: _title_key(s["title"]) for s in sources}
    texts = {s["n"]: _norm(" ".join(c["text"] for c in s["chunks"])) for s in sources}
    issues: list[dict] = []
    out_lines = []
    for line in answer.split("\n"):
        nums = [int(x) for x in _CITE.findall(line)]
        low = _norm(line)
        named = [n for n, k in keys.items() if len(k) >= 6 and k in low]
        if nums and named:
            for n in nums:
                if n in by_n and n not in named and len(named) == 1:
                    right = named[0]
                    line = line.replace(f"[{n}]", f"[{right}]")
                    issues.append({"kind": "fixed", "from": n, "to": right, "text": line.strip()})
        for q in _QUOTE.findall(line):
            cited = [int(x) for x in _CITE.findall(line)]
            # "You will work with ... using Azure DevOps": väliin jätetyt kohdat sallitaan, osat tarkistetaan erikseen
            parts = [p for p in (_norm(x) for x in re.split(r"\.{3}|…", q)) if len(p) >= 8]
            if cited and parts and not any(all(p in texts.get(n, "") for p in parts) for n in cited):
                issues.append({"kind": "quote_not_found", "quote": q, "cited": cited})
        out_lines.append(line)
    return "\n".join(out_lines), issues


def relevant(hits: list[Hit]) -> bool:
    return bool(hits) and (max(h.best_sim for h in hits) >= MIN_SIM or any(
        c["why"] == "sana" for h in hits for c in h.chunks))


def answer(question: str, history: Messages, llm: LLM, retrieve) -> Iterator[dict]:
    """retrieve(question) -> (Filters, list[Hit]). Erotettu, jotta testit voivat antaa haun tuloksen suoraan."""
    try:
        f, hits = retrieve(question)
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "message": f"Haku epäonnistui: {e}"}
        return
    yield {"type": "filters", "filters": f.describe()}
    if not relevant(hits):
        yield {"type": "sources", "sources": []}
        yield {"type": "delta", "text": NOT_FOUND + (" Suodattimet: " + "; ".join(f.describe()) + "." if f.describe() else "")}
        yield {"type": "done", "answer": NOT_FOUND, "cited": [], "invalid": [], "uncited": [], "issues": [], "found": False}
        return
    sources = source_list(hits)
    yield {"type": "sources", "sources": sources}
    hint = ""
    if f.exclude:
        hint = f"\nKysymyksen kieltoehdot: {', '.join(f.exclude)}. Tarkista ne lähteistä."
    today = dt.date.today().isoformat()
    user = f"Tänään on {today}.\n\nLÄHTEET:\n{context_block(sources)}\n\nKYSYMYS: {question}{hint}"
    msgs = [*history[-6:], {"role": "user", "content": user}]
    parts = []
    try:
        for piece in llm.stream(SYSTEM, msgs, max_tokens=1500):
            parts.append(piece)
            yield {"type": "delta", "text": piece}
    except LLMError as e:
        yield {"type": "error", "message": str(e)}
        return
    text, issues = fix_citations(strip_invalid("".join(parts), len(sources)), sources)
    chk = check_citations(text, len(sources))
    yield {"type": "done", "answer": text, **chk, "issues": issues, "found": True}


def make_retriever(s: config.Settings, store, embed_query, cities: list[str]):
    def retrieve(question: str):
        f = parse_filters(question, cities)
        qv = embed_query(question)
        return f, search(store, s.config_id, question, qv, f)
    return retrieve
