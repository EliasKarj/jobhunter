"""Ingestion: results/latest.json → siivous → pilkkominen → embedding → sqlite-vec.

Lähde on nykyisen haun tulos (latest.json), joten verkkoon ei mennä uudestaan.
Uudelleenajo ei duplikoi: ilmoitus tunnistetaan job_id:llä ja sisällön tiivisteellä, ja jo
embedattu teksti (myös sama ilmoitus toisessa kaupungissa) otetaan välimuistista.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from . import config
from .chunking import chunk_text, header
from .clean import clean_text, detect_lang, job_id, sha1
from .store import Store

LATEST = config.DATA / "results" / "latest.json"
STATE = config.DATA / "results" / "state.json"


def load_rows(path: Path = LATEST) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("rows", [])


def job_meta(job: dict) -> dict:
    d = job.get("detail") or {}
    clean = clean_text(job.get("text", ""))
    title, company = job.get("title", "").strip(), (job.get("company") or "").strip()
    return {
        "job_id": job_id(job.get("url", ""), job.get("source", "")),
        "url": job.get("url", ""), "source": job.get("source", ""),
        "title": title, "company": company, "city": (job.get("location") or "").strip(),
        "places": d.get("places") or [], "posted": d.get("posted") or None, "deadline": d.get("deadline") or None,
        "lang": detect_lang(clean), "clean_text": clean,
        "content_hash": sha1(title, company, clean),
    }


def job_chunks(meta: dict, size: int, overlap: int) -> list[dict]:
    """Otsikkopala + tekstipalat. Jokaisen palan alkuun otsake, jotta irrallinenkin pala kantaa kontekstin."""
    head = header(meta["title"], meta["company"], meta["city"])
    out = [{"kind": "title", "text": head, "start": None, "end": None}]
    for c in chunk_text(meta["clean_text"], size, overlap):
        out.append({"kind": "body", "text": f"{head}\n{c.text}", "start": c.start, "end": c.end})
    for i, c in enumerate(out):
        c["idx"], c["hash"] = i, sha1(c["text"])
    return out


def ingest(rows: list[dict], store: Store, embedder, s: config.Settings, today: str | None = None,
           only_urls: set[str] | None = None, progress=None) -> dict:
    """Päivitä indeksi. Palauttaa tilaston: montako uutta/muuttunutta, ohitettua ja embedattua tekstiä."""
    progress = progress or (lambda m: print(m, file=sys.stderr))
    today = today or dt.date.today().isoformat()
    cid = s.config_id
    stats = {"jobs": 0, "skipped": 0, "indexed": 0, "embedded_texts": 0, "cached_texts": 0}

    pending: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        if r.get("status", "ok") != "ok":
            continue  # poissuljettuja/vanhentuneita ei koskaan rankata, ei embedata turhaan
        job = r.get("job", r)
        if only_urls is not None and job.get("url") not in only_urls:
            continue
        meta = job_meta(job)
        if not meta["url"] or meta["job_id"] in seen:
            continue
        seen.add(meta["job_id"])
        stats["jobs"] += 1
        store.upsert_job(meta, today)
        if store.has_config(cid) and store.indexed_hash(meta["job_id"], cid) == meta["content_hash"]:
            stats["skipped"] += 1
        else:
            pending.append(meta)
    store.db.commit()
    if not pending:
        progress(f"  indeksi {cid}: ei uusia ilmoituksia ({stats['skipped']} ennallaan)")
        return stats

    store.ensure_config(cid, s.embed_backend, s.embed_model, embedder.dim, s.chunk_size, s.chunk_overlap)
    step = max(1, s.batch_size)
    for g in range(0, len(pending), step):
        group = pending[g:g + step]
        plans = [(m, job_chunks(m, s.chunk_size, s.chunk_overlap)) for m in group]
        vectors: dict[str, bytes] = {}
        missing: dict[str, str] = {}
        for _, chunks in plans:
            for c in chunks:
                if c["hash"] in vectors or c["hash"] in missing:
                    continue
                cached = store.cached_vector(cid, c["hash"])
                if cached is not None:
                    vectors[c["hash"]] = cached
                    stats["cached_texts"] += 1
                else:
                    missing[c["hash"]] = c["text"]
        if missing:
            mat = embedder.embed_documents(list(missing.values()))
            for h, v in zip(missing, mat):
                vectors[h] = v.astype("float32").tobytes()
            stats["embedded_texts"] += len(missing)
        for meta, chunks in plans:
            store.delete_chunks(meta["job_id"], cid)
            for c in chunks:
                store.add_chunk(meta["job_id"], cid, c["idx"], c["kind"], c["text"], c["hash"],
                                c["start"], c["end"], vectors[c["hash"]])
            store.mark_indexed(meta["job_id"], cid, meta["content_hash"])
            stats["indexed"] += 1
        store.db.commit()  # keskeytys ei hukkaa jo embedattuja
        progress(f"  indeksi {cid}: {min(g + step, len(pending))}/{len(pending)} ilmoitusta embedattu")
    return stats


def keep_urls(extra: set[str] | None = None) -> set[str]:
    """Seuratut (tallennetut/haetut) ja evaluointiin merkityt ilmoitukset säilytetään karsinnassa."""
    urls = set(extra or ())
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        urls |= {u for u, v in state.items() if v.get("saved") or v.get("applied")}
    except (OSError, ValueError):
        pass
    from .label import load_labels
    urls |= set(load_labels())
    return urls


def prune(store: Store, today: str, days: int, keep: set[str]) -> int:
    """Poista ilmoitukset, joiden hakuaika on mennyt tai joita ei ole nähty haussa `days` päivään."""
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=days)).isoformat()
    rows = store.db.execute("SELECT job_id, url FROM jobs WHERE (deadline IS NOT NULL AND deadline < ?) "
                            "OR last_seen < ?", (today, cutoff)).fetchall()
    n = 0
    for r in rows:
        if r["url"] in keep:
            continue
        store.delete_job(r["job_id"])
        n += 1
    store.db.commit()
    return n


def update_index(s: config.Settings | None = None, rows: list[dict] | None = None, progress=None,
                 only_urls: set[str] | None = None, do_prune: bool = True) -> dict:
    """Koko putki yhdellä kutsulla (webapp ja CLI)."""
    from .embed import get_embedder
    s = s or config.load()
    rows = load_rows() if rows is None else rows
    store = Store(s.db_path)
    try:
        stats = ingest(rows, store, get_embedder(s), s, only_urls=only_urls, progress=progress)
        if do_prune:
            stats["pruned"] = prune(store, dt.date.today().isoformat(), s.prune_days, keep_urls())
        return stats
    finally:
        store.close()
