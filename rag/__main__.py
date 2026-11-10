"""Komentorivi: python -m rag <komento>

  ingest [--only-labeled]   päivitä vektori-indeksi results/latest.json:sta (ei duplikoi, ei embeddaa uudestaan)
  prune                     karsi vanhentuneet / haussa näkymättömät ilmoitukset indeksistä
  profile                   näytä profiilipalat (CV, osaaminen, tavoiteroolit)
  search "teksti"           semanttinen haku indeksistä
  rank [--mode M] [--top N] näytä ranking valitulla tilalla
  label [--import-state]    merkitse ilmoituksia relevanteiksi / ei-relevanteiksi
  evaluate [--sweep]        vertaa avainsana / semanttinen / hybridi (P@10, NDCG@10) ja tallenna ajo
  runs                      näytä tallennetut evaluointiajot
  chat "kysymys" [--retrieve-only]   kysy ilmoituksista (LLM_PROVIDER); --retrieve-only näyttää vain haun
  explain URL               sopivuusselitys ilmoitukselle
  package URL [--lang fi|en] hakemuspaketti ja väitteiden tarkistus

Asetukset tulevat .env:stä; yksittäisen ajon voi ohittaa lipuilla (esim. --chunk-size 500 --model ...).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import config


def _jh():
    if str(config.BASE) not in sys.path:
        sys.path.insert(0, str(config.BASE))
    import jobhunter
    return jobhunter


def scored_rows(s: config.Settings) -> list[dict]:
    """Nykyiset ilmoitukset pisteytettynä tuoreeltaan nykyisellä profiililla, kaikki kolme pistettä mukana."""
    from .ingest import load_rows
    from .scoring import all_modes
    jh = _jh()
    prof = jh.load_profile(config.DATA / "profile.json")
    jobs = [jh.Job(**r["job"]) for r in load_rows()]
    from .llmscore import attach_saved
    return attach_saved(all_modes(jh.score_all(jobs, prof), s), s)


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="python -m rag", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", help="local | openai | fake")
    ap.add_argument("--model")
    ap.add_argument("--chunk-size", type=int)
    ap.add_argument("--overlap", type=int)
    ap.add_argument("--agg", choices=["max", "topk_mean", "mean"])
    ap.add_argument("--k", type=int, help="top-k aggregoinnin k")
    ap.add_argument("--w-kw", type=float)
    ap.add_argument("--w-sem", type=float)
    ap.add_argument("--method", choices=["weighted", "rrf"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest")
    p.add_argument("--only-labeled", action="store_true", help="embeddaa vain merkityt (nopea asetusvertailu)")
    p.add_argument("--no-prune", action="store_true")
    sub.add_parser("prune")
    p = sub.add_parser("chat")
    p.add_argument("question")
    p.add_argument("--retrieve-only", action="store_true")
    p = sub.add_parser("explain")
    p.add_argument("url")
    p.add_argument("--refresh", action="store_true", help="ohita välimuisti")
    p = sub.add_parser("llm-score")
    p.add_argument("--top", type=int, help="kärjen koko (oletus SCORE_TOP)")
    p = sub.add_parser("projects", help="ehdotuksia CV:n projekteihin omista git-repoista (ei muuta CV:tä)")
    p.add_argument("--no-llm", action="store_true", help="ei luonnoksia uusille projekteille")
    p.add_argument("--root", help="skannattava kansio (oletus AABIGBOMBOCLAT tai data/projects.json: root)")
    p.add_argument("--dismiss", metavar="ID", help="piilota ehdotus")
    p = sub.add_parser("bench")
    p.add_argument("--models", default="gemma3:12b,qwen2.5:latest,llama3.1:latest")
    p = sub.add_parser("package")
    p.add_argument("url")
    p.add_argument("--lang", choices=["fi", "en"])
    sub.add_parser("profile")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("-n", type=int, default=10)
    p = sub.add_parser("rank")
    p.add_argument("--mode", choices=config.MODES)
    p.add_argument("--top", type=int, default=20)
    p = sub.add_parser("label")
    p.add_argument("--import-state", action="store_true", help="tuo merkinnät webappin tallennetuista/piilotetuista")
    p.add_argument("-n", type=int, default=40)
    p = sub.add_parser("evaluate")
    p.add_argument("--sweep", action="store_true", help="käy hybridipainot 0...1 läpi")
    p.add_argument("--no-save", action="store_true")
    p.add_argument("--note", default="", help="vapaa kuvaus ajolle (tallennetaan)")
    sub.add_parser("runs")
    a = ap.parse_args(argv)

    s = config.load().with_(embed_backend=a.backend, embed_model=a.model, chunk_size=a.chunk_size,
                            chunk_overlap=a.overlap, aggregation=a.agg, agg_k=a.k, w_keyword=a.w_kw,
                            w_semantic=a.w_sem, hybrid_method=a.method)

    if a.cmd == "ingest":
        from .ingest import update_index
        from .label import load_labels
        only = set(load_labels()) if a.only_labeled else None
        if only is not None and not only:
            sys.exit("Ei merkintöjä: aja ensin 'python -m rag label'")
        stats = update_index(s, only_urls=only, do_prune=not a.no_prune and only is None)
        print(f"Indeksi {s.config_id}: {stats}")
    elif a.cmd == "prune":
        from .ingest import keep_urls, prune
        from .store import Store
        store = Store(s.db_path)
        n = prune(store, dt.date.today().isoformat(), s.prune_days, keep_urls())
        print(f"Karsittu {n} ilmoitusta, jäljellä {store.count('jobs')}")
    elif a.cmd == "profile":
        from .profile import build_profile
        chunks = build_profile(s)
        if not s.cv_path.exists():
            print(f"(CV puuttuu: {s.cv_path} - profiili muodostetaan osaamisesta ja tavoiteroolista)")
        for c in chunks:
            print(f"[{c.kind}] {c.source_ref}\n  {c.text[:300]}\n")
    elif a.cmd == "search":
        from .embed import get_embedder
        from .store import Store
        store = Store(s.db_path)
        if not store.has_config(s.config_id):
            sys.exit(f"Indeksiä {s.config_id} ei ole: aja 'python -m rag ingest'")
        q = get_embedder(s).embed_queries([a.query])[0]
        seen = set()
        for r in store.knn(s.config_id, q, a.n * 3):
            if r["job_id"] in seen:
                continue
            seen.add(r["job_id"])
            print(f"{1 - r['distance']:.3f}  {r['title'][:60]}  [{r['company']}, {r['city']}]\n       {r['url']}")
            if len(seen) >= a.n:
                break
    elif a.cmd == "rank":
        rows = scored_rows(s)
        field = {"keyword": "score_keyword", "semantic": "score_semantic", "hybrid": "score_hybrid"}[a.mode or s.scoring_mode]
        ok = [r for r in rows if r["status"] == "ok" and r.get(field) is not None]
        ok.sort(key=lambda r: -r[field])
        for i, r in enumerate(ok[:a.top], 1):
            print(f"{i:>3}. {r[field]:>5.1f}  (kw {r['score_keyword']:>4.0f} | sem "
                  f"{r['score_semantic'] if r['score_semantic'] is not None else '-':>5})  "
                  f"{r['job']['title'][:55]}  [{r['job']['location']}]")
    elif a.cmd == "label":
        from .ingest import load_rows
        from .label import import_state, interactive
        if a.import_state:
            print(f"Tuotu {import_state(load_rows())} merkintää state.json:sta")
            return
        interactive(scored_rows(s), a.n)
    elif a.cmd == "evaluate":
        from .evaluate import evaluate, print_table, save_run, sweep
        from .label import load_labels
        labels = load_labels()
        if not labels:
            sys.exit("Ei merkintöjä: aja ensin 'python -m rag label'")
        rows = scored_rows(s)
        res = evaluate(rows, labels)
        print(f"Indeksi {s.config_id} | aggregointi {s.aggregation}"
              + (f"@{s.agg_k}" if s.aggregation == "topk_mean" else "")
              + f" | hybridi {s.hybrid_method} kw {s.w_keyword:g} / sem {s.w_semantic:g}")
        print_table(res)
        extra = {}
        if a.sweep:
            sw = sweep(rows, labels, s)
            print("\nHybridipainot (W_SEMANTIC, W_KEYWORD = 1 - W_SEMANTIC):")
            for x in sw:
                print(f"  sem {x['w_semantic']:.1f}  P@10 {x['p@k']:.2f}  NDCG@10 {x['ndcg@k']:.2f}")
            extra["sweep"] = sw
        if not a.no_save:
            save_run(res, s, a.note, extra)
            print("\nTallennettu: eval/runs.jsonl  (vertailu: python -m rag runs)")
    elif a.cmd == "chat":
        from . import service
        from .chat import answer
        retrieve = service.retriever(s)
        if a.retrieve_only:
            f, hits = retrieve(a.question)
            print("Suodattimet:", "; ".join(f.describe()) or "-")
            for i, h in enumerate(hits, 1):
                print(f"[{i}] {h.best_sim:.3f}  {h.title[:60]} | {h.company} | {h.city} | {h.posted}")
                for c in h.chunks:
                    print(f"      ({c['why']}) {c['text'][:110].replace(chr(10), ' ')}")
            return
        for ev in answer(a.question, [], service.llm("chat", s), retrieve):
            if ev["type"] == "filters" and ev["filters"]:
                print("Suodattimet:", "; ".join(ev["filters"]), end="\n\n")
            elif ev["type"] == "delta":
                print(ev["text"], end="", flush=True)
            elif ev["type"] == "error":
                sys.exit("\n! " + ev["message"])
            elif ev["type"] == "sources":
                srcs = ev["sources"]
            elif ev["type"] == "done":
                print("\n")
                for x in srcs:
                    if x["n"] in ev["cited"]:
                        print(f"  [{x['n']}] {x['title']} | {x['company']} | {x['url']}")
                if ev["invalid"]:
                    print("  ! olemattomat viitteet poistettu:", ev["invalid"])
                for x in ev.get("issues", []):
                    print("  ! viite korjattu" if x["kind"] == "fixed" else "  ! lainausta ei löydy lähteestä",
                          {k: v for k, v in x.items() if k != "kind"})
                for u in ev["uncited"]:
                    print("  ! ilman lähdettä:", u[:100])
    elif a.cmd == "explain":
        from .explain import explain_url
        import json as _json
        print(_json.dumps(explain_url(a.url, s, refresh=a.refresh), ensure_ascii=False, indent=2))
    elif a.cmd == "package":
        from .package import package_url
        import json as _json
        print(_json.dumps(package_url(a.url, s, lang=a.lang), ensure_ascii=False, indent=2))
    elif a.cmd == "llm-score":
        from .llmscore import update
        from dataclasses import replace
        rows = scored_rows(s)
        res = update(rows, replace(s, score_top=a.top or s.score_top), lambda d, n: print(f"  {d}/{n}", flush=True))
        print(res if res else "SCORE_PROVIDER=off tai CV/indeksi puuttuu")
    elif a.cmd == "projects":
        from pathlib import Path as _P
        from . import projects
        if a.dismiss:
            print("piilotettu" if projects.dismiss(a.dismiss) else "tunnistetta ei löytynyt")
        else:
            res = projects.scan(s, root=_P(a.root) if a.root else None, use_llm=not a.no_llm)
            kinds = {k: sum(1 for p in res["proposals"] if p["kind"] == k and p["status"] == "open")
                     for k in ("new_project", "new_tech", "activity")}
            print(f"Ehdotuksia: {kinds}. Raportti: {projects.REPORT}")
    elif a.cmd == "bench":
        from .bench import run
        run([m.strip() for m in a.models.split(",") if m.strip()], s)
    elif a.cmd == "runs":
        from .evaluate import load_runs, print_runs
        print_runs(load_runs())


if __name__ == "__main__":
    main()
