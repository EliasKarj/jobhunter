"""Relevanssimerkinnät evaluointia varten: eval/labels.csv (url, job_id, otsikko, yritys, label, note, labeled_at).

Asteikko: 2 = erittäin relevantti, 1 = relevantti, 0 = ei relevantti. Tiedostoa voi muokata myös Excelissä.

Merkittävät valitaan "poolaamalla": kunkin rankingin (myös kielimallin) kärki + satunnaisotos, sekoitettuna.
Jos merkitsisi vain avainsanarankingin kärkeä, evaluointi suosisi avainsanahakua.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import random
from pathlib import Path

from . import config
from .clean import job_id

LABELS = config.DATA / "eval" / "labels.csv"
FIELDS = ["url", "job_id", "title", "company", "label", "note", "labeled_at"]


def load_labels(path: Path = LABELS) -> dict[str, int]:
    """url -> label (0/1/2)."""
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            try:
                out[r["url"]] = int(r["label"])
            except (KeyError, ValueError):
                continue
    return out


def _load_full(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {r["url"]: r for r in csv.DictReader(f) if r.get("url")}


def save_label(job: dict, label: int, note: str = "", path: Path = LABELS) -> None:
    rows = _load_full(path)
    rows[job["url"]] = {"url": job["url"], "job_id": job_id(job["url"], job.get("source", "")),
                        "title": job.get("title", ""), "company": job.get("company", ""), "label": label,
                        "note": note, "labeled_at": dt.datetime.now().isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows.values())
    tmp.replace(path)


def candidates(rows: list[dict], labeled: set[str], per_ranker: int = 15, n_random: int = 10,
               seed: int | None = None) -> list[dict]:
    """Pooli: kunkin rankingin top-N + satunnaiset, ilman jo merkittyjä. Järjestys sekoitetaan (sokkoarvio)."""
    ok = [r for r in rows if r.get("status") == "ok" and r["job"]["url"] not in labeled]
    pool: dict[str, dict] = {}
    for field in ("score_keyword", "score_semantic", "score_hybrid", "score_llm", "score_combined"):
        scored = [r for r in ok if r.get(field) is not None]
        for r in sorted(scored, key=lambda r: -r[field])[:per_ranker]:
            pool[r["job"]["url"]] = r
    rng = random.Random(seed)
    rest = [r for r in ok if r["job"]["url"] not in pool]
    for r in rng.sample(rest, min(n_random, len(rest))):
        pool[r["job"]["url"]] = r
    out = list(pool.values())
    rng.shuffle(out)
    return out


def import_state(rows: list[dict]) -> int:
    """Esimerkintä webappin tilasta: tallennettu/haettu = 1, piilotettu = 0."""
    state_path = config.DATA / "results" / "state.json"
    if not state_path.exists():
        return 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    by_url = {r["job"]["url"]: r["job"] for r in rows}
    have = load_labels()
    n = 0
    for url, st in state.items():
        if url in have or url not in by_url:
            continue
        if st.get("saved") or st.get("applied"):
            save_label(by_url[url], 1, "state.json: tallennettu/haettu")
            n += 1
        elif st.get("hidden"):
            save_label(by_url[url], 0, "state.json: piilotettu")
            n += 1
    return n


def interactive(rows: list[dict], n: int = 40) -> int:
    labeled = set(load_labels())
    todo = candidates(rows, labeled)[:n]
    print(f"Merkittäviä {len(todo)} (merkittyjä yhteensä {len(labeled)}).")
    print("2 = erittäin relevantti, 1 = relevantti, 0 = ei relevantti, s = ohita, q = lopeta\n")
    done = 0
    for i, r in enumerate(todo, 1):
        j = r["job"]
        d = j.get("detail") or {}
        print("=" * 78)
        print(f"[{i}/{len(todo)}] {j['title']}")
        print(f"{j.get('company', '')} | {j.get('location', '')} | {d.get('employment', '')} | julkaistu {d.get('posted', '?')}")
        print(j["url"])
        print("-" * 78)
        print(j.get("text", "")[:4000].strip())   # koko ilmoitus: vaatimukset ovat usein lopussa
        while True:
            a = input("\nRelevanssi [2/1/0/s/q]: ").strip().lower()
            if a in ("0", "1", "2", "s", "q"):
                break
        if a == "q":
            break
        if a == "s":
            continue
        note = input("Huomio (valinnainen): ").strip()
        save_label(j, int(a), note)
        done += 1
    print(f"\nTallennettu {done} merkintää tiedostoon {LABELS}")
    return done
