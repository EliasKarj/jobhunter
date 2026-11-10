"""Semanttinen pistemäärä: profiilipalojen ja ilmoituspalojen kosinisamankaltaisuudesta.

Aggregointi (AGGREGATION):
  1. Jokaiselle profiilipalalle sen paras osuma ilmoituksessa (max palojen yli).
  2. Profiilipalojen yli:
     - topk_mean (oletus): k parhaan profiilipalan keskiarvo. Mittaa, kuinka monta profiilin
       osa-aluetta ilmoitus koskettaa vahvasti; yksittäinen sattumaosuma ei riitä.
     - max: paras yksittäinen pari (herkkä sattumaosumille).
     - mean: kaikkien profiilipalojen keskiarvo (rankaisee, kun ilmoitus ei kata koko profiilia).
"""
from __future__ import annotations

import numpy as np

from . import config
from .profile import ProfileChunk, build_profile
from .store import Store


def aggregate(per_profile: np.ndarray, method: str, k: int) -> np.ndarray:
    """per_profile: (profiilipalat × ilmoitukset), kunkin profiilipalan paras osuma. Palauttaa pisteen per ilmoitus."""
    if per_profile.size == 0:
        return np.zeros(per_profile.shape[1] if per_profile.ndim == 2 else 0, dtype=np.float32)
    if method == "max":
        return per_profile.max(axis=0)
    if method == "mean":
        return per_profile.mean(axis=0)
    k = max(1, min(k, per_profile.shape[0]))
    top = -np.sort(-per_profile, axis=0)[:k]
    return top.mean(axis=0)


def profile_matrix(store: Store, s: config.Settings, chunks: list[ProfileChunk], embedder_factory) -> np.ndarray:
    """Profiilivektorit välimuistista; malli ladataan vain, jos profiili on muuttunut."""
    cid = s.config_id
    vecs: dict[str, np.ndarray] = {}
    missing = []
    for c in chunks:
        b = store.profile_vector(c.hash, cid)
        if b is None:
            missing.append(c)
        else:
            vecs[c.hash] = np.frombuffer(b, dtype=np.float32)
    if missing:
        emb = embedder_factory()
        mat = emb.embed_queries([c.text for c in missing])
        for c, v in zip(missing, mat):
            store.save_profile_vector(c.hash, cid, c.kind, c.source_ref, c.text, v)
            vecs[c.hash] = v
        store.db.commit()
    return np.stack([vecs[c.hash] for c in chunks]) if chunks else np.zeros((0, 0), dtype=np.float32)


def semantic_scores(store: Store, s: config.Settings, embedder_factory=None,
                    chunks: list[ProfileChunk] | None = None) -> dict[str, float]:
    """job_id -> raaka semanttinen pistemäärä (kosini, n. -1...1). Tyhjä, jos indeksiä tai profiilia ei ole."""
    if not store.has_config(s.config_id):
        return {}
    chunks = build_profile(s) if chunks is None else chunks
    if not chunks:
        return {}
    if embedder_factory is None:
        from .embed import get_embedder
        embedder_factory = lambda: get_embedder(s)  # noqa: E731
    P = profile_matrix(store, s, chunks, embedder_factory)
    ids, C = store.all_job_vectors(s.config_id)
    if not ids:
        return {}
    sims = P @ C.T                                  # (profiili × kaikki palat)
    starts = [0] + [i for i in range(1, len(ids)) if ids[i] != ids[i - 1]]
    per_profile = np.maximum.reduceat(sims, starts, axis=1)   # paras pala per ilmoitus
    scores = aggregate(per_profile, s.aggregation, s.agg_k)
    return {ids[st]: float(v) for st, v in zip(starts, scores)}
