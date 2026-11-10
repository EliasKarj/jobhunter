"""Kielimallien vertailu samoilla tehtävillä: python -m rag bench --models gemma3:12b,qwen2.5:7b

Tehtävät: kaksi chat-kysymystä, yksi sopivuusselitys ja yksi hakemuspaketti. Mitataan aika, JSON-kelpoisuus,
viitteiden laatu (olemattomat / korjatut / ilman lähdettä) ja hakemuspaketin liputetut väitteet.
Tekstit tallennetaan eval/bench/<malli>.md luettavaksi (suomen laatu arvioidaan lukemalla).
"""
from __future__ import annotations

import json
import re
import time

from . import config
from .llm import LLMError, OllamaLLM, get_llm

OUT = config.DATA / "eval" / "bench"
QUESTIONS = ["mitkä Vaasan paikat vaativat Azurea mutta eivät ruotsia?",
             "Onko Tampereella junior Python -paikkoja viime viikolla?"]


# Suomenkielinen junior-ilmoitus, joka vaatii Rustia: CV:ssä ei ole Rustia, joten malli ei saa väittää sitä
JOB = "https://duunitori.fi/tyopaikat/tyo/junioriohjelmistokehittaja-harjoittelija-tai-opinnaytetyontekija-rust-ja-tekoalysovellukset-stsyo-20634488"


def _job_url(store) -> str:
    if store.db.execute("SELECT 1 FROM jobs WHERE url=?", (JOB,)).fetchone():
        return JOB
    r = store.db.execute("SELECT url FROM jobs WHERE lower(title) LIKE '%junior%' ORDER BY job_id LIMIT 1").fetchone()
    if not r:
        raise LookupError("Vertailuun ei löydy junior-ilmoitusta indeksistä")
    return r[0]


def run(models: list[str], s: config.Settings) -> list[dict]:
    from . import service
    from .chat import answer
    from .explain import explain
    from .grounding import build
    from .package import applicant_name, build_package, choose_lang, tools
    OUT.mkdir(parents=True, exist_ok=True)
    retrieve = service.retriever(s)
    st = service.store(s)
    url = _job_url(st)
    _, g, embed, vectors, cv_full = tools(s, url)
    g_explain = build(st, s, url)
    results = []
    for m in models:
        llm = OllamaLLM(m, s.ollama_url) if not m.startswith("claude") else get_llm(s.with_(llm_provider="claude",
                                                                                          claude_model=m))
        rec: dict = {"model": m, "chat": [], "errors": []}
        md = [f"# {m}\n"]
        for q in QUESTIONS:
            t = time.time()
            done = next(e for e in answer(q, [], llm, retrieve) if e["type"] in ("done", "error"))
            if done["type"] == "error":
                rec["errors"].append(done["message"])
                continue
            rec["chat"].append({"q": q, "s": round(time.time() - t, 1), "cited": len(done["cited"]),
                                "invalid": len(done["invalid"]), "fixed": sum(i["kind"] == "fixed" for i in done["issues"]),
                                "uncited": len(done["uncited"])})
            md.append(f"## Chat: {q}\n\n{done['answer']}\n")
        try:
            t = time.time()
            ex = explain(g_explain, llm, None)
            rec["explain"] = {"s": round(time.time() - t, 1), "strengths": len(ex["strengths"]), "gaps": len(ex["gaps"]),
                              "dropped": len(ex["dropped"]), "verdict": ex["verdict"]}
            md.append("## Sopivuusselitys\n\n" + "\n".join(f"- + {x['text']} {x['ad']}{x['profile']}" for x in ex["strengths"])
                      + "\n" + "\n".join(f"- − {x['text']} ({x['severity']})" for x in ex["gaps"]) + f"\n\n{ex['summary']}\n")
        except LLMError as e:
            rec["errors"].append(f"explain: {e}")
        try:
            t = time.time()
            p = build_package(g, llm, choose_lang(g, None), applicant_name(s), cv_full, embed, vectors)
            secs = [x for x in p["sections"] if x["key"] != "salary"]
            rec["package"] = {"s": round(time.time() - t, 1),
                              "claims": sum(len(x["verify"]["claims"]) for x in secs),
                              "flagged": sum(x["verify"]["flagged"] for x in secs),
                              "check": sum(x["verify"]["check"] for x in secs),
                              "words": sum(len(x["text"].split()) for x in secs)}
            for x in secs:
                md.append(f"## {x['title']}\n\n{x['text']}\n")
                for c in x["verify"]["claims"]:
                    if c["status"] != "ok":
                        md.append(f"> **{c['status']}**: {c['text']} — {'; '.join(c['reasons'])}\n")
        except LLMError as e:
            rec["errors"].append(f"package: {e}")
        (OUT / (re.sub(r"[^\w.-]", "_", m) + ".md")).write_text("\n".join(md), encoding="utf-8")
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False))
    with (OUT / "results.jsonl").open("a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({"job": url, **r}, ensure_ascii=False) + "\n")
    return results
