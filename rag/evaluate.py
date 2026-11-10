"""Kolmen rankingin (avainsana, semanttinen, hybridi) vertailu: precision@10 ja NDCG@10.

Kaksi näkymää:
  pool  - vain merkityt ilmoitukset järjestetään (uudelleenjärjestys). Pääluku: kaikki kärjessä on merkitty.
  full  - koko korpus järjestetään; merkitsemättömät lasketaan ei-relevanteiksi. Kattavuus kertoo,
          montako top-10:stä on ylipäätään merkitty (matala kattavuus = luku on alakanttiin).
30-50 merkinnällä muutaman pisteen ero on kohinaa, siksi pool-luvuille bootstrap-luottamusväli.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random

from . import config

RUNS = config.DATA / "eval" / "runs.jsonl"
RANKERS = {"avainsana": "score_keyword", "semanttinen": "score_semantic", "hybridi": "score_hybrid", "kielimalli": "score_llm",
           "yhdistelmä": "score_combined"}


def precision_at_k(ranked: list[str], labels: dict[str, int], k: int = 10) -> float:
    if k <= 0:
        return 0.0
    return sum(1 for u in ranked[:k] if labels.get(u, 0) >= 1) / k


def ndcg_at_k(ranked: list[str], labels: dict[str, int], k: int = 10) -> float:
    def dcg(rels):
        return sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rels))
    ideal = dcg(sorted(labels.values(), reverse=True)[:k])
    if ideal == 0:
        return 0.0
    return dcg([labels.get(u, 0) for u in ranked[:k]]) / ideal


def rank(rows: list[dict], field: str) -> list[str]:
    """Järjestys pisteen mukaan; tasapisteissä url ratkaisee (deterministinen). Puuttuva piste = viimeiseksi."""
    return [r["job"]["url"] for r in sorted(rows, key=lambda r: (-(r.get(field) if r.get(field) is not None else -1e9),
                                                                   r["job"]["url"]))]


def _bootstrap(items: list[dict], labels: dict[str, int], field: str, k: int, n: int = 1000,
               seed: int = 0) -> dict[str, list[float]]:
    rng = random.Random(seed)
    ps, ns = [], []
    for _ in range(n):
        sample = [rng.choice(items) for _ in items]
        # kopiot erotetaan uniikilla avaimella
        keyed = [{"job": {"url": f"{i}|{r['job']['url']}"}, field: r.get(field)} for i, r in enumerate(sample)]
        lab = {f"{i}|{r['job']['url']}": labels[r["job"]["url"]] for i, r in enumerate(sample)}
        order = rank(keyed, field)
        ps.append(precision_at_k(order, lab, k))
        ns.append(ndcg_at_k(order, lab, k))
    ps.sort(), ns.sort()
    lo, hi = int(0.025 * n), int(0.975 * n) - 1
    return {"p_ci": [round(ps[lo], 3), round(ps[hi], 3)], "ndcg_ci": [round(ns[lo], 3), round(ns[hi], 3)]}


def evaluate(rows: list[dict], labels: dict[str, int], k: int = 10, bootstrap: bool = True) -> dict:
    ok = [r for r in rows if r.get("status") == "ok"]
    pool = [r for r in ok if r["job"]["url"] in labels]
    lab = {r["job"]["url"]: labels[r["job"]["url"]] for r in pool}
    out = {"n_labels": len(pool), "n_relevant": sum(1 for v in lab.values() if v >= 1),
           "n_missing": len(set(labels) - {r["job"]["url"] for r in pool}), "rankers": {}}
    for name, field in RANKERS.items():
        if not any(r.get(field) is not None for r in ok):
            continue
        order = rank(pool, field)
        full = rank(ok, field)
        m = {"p@k": round(precision_at_k(order, lab, k), 3), "ndcg@k": round(ndcg_at_k(order, lab, k), 3),
             "full_p@k": round(precision_at_k(full, lab, k), 3),
             "full_ndcg@k": round(ndcg_at_k(full, lab, k), 3),
             "full_coverage": round(sum(1 for u in full[:k] if u in lab) / k, 2)}
        if bootstrap and len(pool) >= 5:
            m.update(_bootstrap(pool, lab, field, k))
        out["rankers"][name] = m
    return out


def print_table(res: dict, k: int = 10) -> None:
    print(f"\nMerkittyjä {res['n_labels']} (relevantteja {res['n_relevant']})"
          + (f", {res['n_missing']} merkittyä puuttuu nykyisistä tuloksista" if res["n_missing"] else ""))
    head = f"{'Ranking':<12} {'P@'+str(k):>7} {'95% väli':>13} {'NDCG@'+str(k):>8} {'95% väli':>13} │ " \
           f"{'koko P@'+str(k):>9} {'koko NDCG':>9} {'kattavuus':>9}"
    print(head)
    print("─" * len(head))
    for name, m in res["rankers"].items():
        pci = "–".join(f"{x:.2f}" for x in m.get("p_ci", [])) or "-"
        nci = "–".join(f"{x:.2f}" for x in m.get("ndcg_ci", [])) or "-"
        print(f"{name:<12} {m['p@k']:>7.2f} {pci:>13} {m['ndcg@k']:>8.2f} {nci:>13} │ "
              f"{m['full_p@k']:>9.2f} {m['full_ndcg@k']:>9.2f} {m['full_coverage']:>9.0%}")


def sweep(rows: list[dict], labels: dict[str, int], s: config.Settings, k: int = 10) -> list[dict]:
    """Hybridipainojen läpikäynti: W_SEMANTIC 0...1, W_KEYWORD = 1 - W_SEMANTIC."""
    from . import hybrid
    from .scoring import row_key, semantic_raw
    sem = semantic_raw(s)
    out = []
    for i in range(11):
        ws = i / 10
        st = s.with_(w_keyword=1 - ws, w_semantic=ws, scoring_mode="keyword")
        rs = [dict(r, reasons=[]) for r in rows]
        for r in rs:
            r.pop("score_hybrid", None)
        hybrid.apply(rs, sem, st, row_key)
        m = evaluate(rs, labels, k, bootstrap=False)["rankers"]["hybridi"]
        out.append({"w_semantic": ws, "p@k": m["p@k"], "ndcg@k": m["ndcg@k"]})
    return out


def save_run(res: dict, s: config.Settings, note: str = "", extra: dict | None = None) -> dict:
    rec = {"at": dt.datetime.now().isoformat(timespec="seconds"), "note": note, "config": s.describe(), **res,
           **(extra or {})}
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load_runs() -> list[dict]:
    if not RUNS.exists():
        return []
    return [json.loads(x) for x in RUNS.read_text(encoding="utf-8").splitlines() if x.strip()]


def print_runs(runs: list[dict]) -> None:
    head = (f"{'#':>3} {'aika':<16} {'indeksi':<30} {'agg':<11} {'paino kw/sem':>12} {'n':>3} │ "
            f"{'kw P/NDCG':>11} {'sem P/NDCG':>11} {'hyb P/NDCG':>11}  huomio")
    print(head)
    print("─" * len(head))
    for i, r in enumerate(runs, 1):
        c = r["config"]
        agg = c["aggregation"] + (f"@{c['agg_k']}" if c["aggregation"] == "topk_mean" else "")
        w = f"{c['w_keyword']:.1f}/{c['w_semantic']:.1f}" + ("r" if c.get("hybrid_method") == "rrf" else "")

        def cell(name):
            m = r["rankers"].get(name)
            return f"{m['p@k']:.2f}/{m['ndcg@k']:.2f}" if m else "-"
        print(f"{i:>3} {r['at'][:16]:<16} {c['config_id'][:30]:<30} {agg:<11} {w:>12} {r['n_labels']:>3} │ "
              f"{cell('avainsana'):>11} {cell('semanttinen'):>11} {cell('hybridi'):>11}  {r.get('note', '')}")
