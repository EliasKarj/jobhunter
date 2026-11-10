"""Avainsana- ja semanttisen pisteen yhdistäminen.

Kosinit pakkautuvat kapealle välille (esim. 0,45-0,70), joten semanttinen piste skaalataan 0-100:aan
korpuksen p5-max-välin mukaan ennen yhdistämistä. Avainsanapiste on valmiiksi 0-100.
"""
from __future__ import annotations

import numpy as np

from .config import Settings


def normalize(raw: dict[str, float], lo_pct: float = 5) -> dict[str, float]:
    """Alapää leikataan p5:een (häntä ei venytä asteikkoa), yläpää skaalataan maksimiin.

    Yläpäätä ei leikata: p95-leikkaus antoi ~5 % korpuksesta (n. 190 ilmoitusta) tasapisteen 100,
    jolloin semanttinen järjestys katosi juuri kärjestä, jossa sillä on eniten väliä.
    """
    if not raw:
        return {}
    vals = np.array(list(raw.values()), dtype=np.float64)
    lo, hi = np.percentile(vals, lo_pct), vals.max()
    if hi - lo < 1e-9:
        return {k: 50.0 for k in raw}
    return {k: float(np.clip((v - lo) / (hi - lo), 0, 1) * 100) for k, v in raw.items()}


def _ranks(scores: dict[str, float]) -> dict[str, int]:
    order = sorted(scores, key=lambda k: (-scores[k], k))
    return {k: i + 1 for i, k in enumerate(order)}


def combine(kw: dict[str, float], sem: dict[str, float], s: Settings) -> dict[str, float]:
    """Yhdistetty piste 0-100. Ilmoitus, jolla ei ole semanttista pistettä, saa avainsanapisteensä."""
    wk, ws = s.w_keyword, s.w_semantic
    if s.hybrid_method == "rrf":
        rk = _ranks(kw)
        rs = _ranks({k: v for k, v in sem.items() if k in kw})
        last = len(kw) + 1
        best = (wk + ws) / (s.rrf_k + 1)
        return {k: 100 * (wk / (s.rrf_k + rk[k]) + ws / (s.rrf_k + rs.get(k, last))) / best for k in kw}
    return {k: (wk * v + ws * sem[k]) / (wk + ws) if k in sem else v for k, v in kw.items()}


def apply(rows: list[dict], sem_raw: dict[str, float], s: Settings, key) -> list[dict]:
    """Lisää riveille score_keyword/score_semantic/score_hybrid ja aseta score valitun tilan mukaan.

    key(row) -> job_id. Vain status == ok -rivit rankataan; poissuljetut ja vanhentuneet pysyvät ennallaan.
    """
    ok = [r for r in rows if r.get("status") == "ok"]
    for r in rows:
        r.setdefault("score_keyword", r["score"])
    kw = {key(r): float(r["score_keyword"]) for r in ok}
    sem = normalize({k: sem_raw[k] for k in kw if k in sem_raw})
    hyb = combine(kw, sem, s)
    for r in ok:
        k = key(r)
        r["score_semantic"] = round(sem[k], 1) if k in sem else None
        r["score_hybrid"] = round(hyb[k], 1)
        if s.scoring_mode == "keyword":
            r["score"] = r["score_keyword"]
            continue
        if k not in sem:
            r["score"] = r["score_keyword"]
            r["reasons"].append("Semanttinen: ei vielä indeksissä (avainsanapisteet)")
            continue
        r["score"] = r["score_semantic"] if s.scoring_mode == "semantic" else r["score_hybrid"]
        r["reasons"].append(f"Semanttinen vastaavuus {sem[k]:.0f}/100 (avainsana {r['score_keyword']:.0f})")
    return rows
