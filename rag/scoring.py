"""Kytkentä nykyiseen pisteytykseen. SCORING_MODE=keyword palauttaa rivit koskematta (ei raskaita importteja)."""
from __future__ import annotations

import sys
import threading

from . import config
from .clean import job_id

_cache: dict = {"key": None, "val": {}}
_embedder: dict = {}
_lock = threading.Lock()


def row_key(r: dict) -> str:
    j = r.get("job", r)
    return job_id(j.get("url", ""), j.get("source", ""))


def _embedder_factory(s: config.Settings):
    def make():
        # malli pidetään muistissa prosessin ajan, ettei profiilin muutos lataa sitä joka kerta uudestaan
        if s.config_id not in _embedder:
            from .embed import get_embedder
            _embedder[s.config_id] = get_embedder(s)
        return _embedder[s.config_id]
    return make


def semantic_raw(s: config.Settings) -> dict[str, float]:
    """job_id -> raaka semanttinen piste. Välimuistissa niin kauan kuin indeksi ja profiili ovat ennallaan."""
    if not s.db_path.exists():
        return {}
    from .profile import build_profile
    from .semantic import semantic_scores
    from .store import Store
    chunks = build_profile(s)

    def key():
        st = s.db_path.stat()
        return (s.config_id, s.aggregation, s.agg_k, st.st_mtime_ns, st.st_size, tuple(c.hash for c in chunks))

    with _lock:
        if _cache["key"] == key():
            return _cache["val"]
        store = Store(s.db_path)
        try:
            val = semantic_scores(store, s, _embedder_factory(s), chunks)
        finally:
            store.close()
        _cache.update(key=key(), val=val)  # avain vasta nyt: profiilivektorien tallennus muuttaa tiedostoa
        return val


def apply_mode(rows: list[dict], s: config.Settings | None = None) -> list[dict]:
    """Aseta rivien score .env:n SCORING_MODE:n mukaan. Virhe semanttisessa osassa ei kaada pisteytystä."""
    s = s or config.load()
    if s.scoring_mode == "keyword":
        return rows
    from . import hybrid
    try:
        sem = semantic_raw(s)
    except Exception as e:  # noqa: BLE001
        print(f"! semanttinen pisteytys epäonnistui, käytetään avainsanapisteitä: {e}", file=sys.stderr)
        sem = {}
    return hybrid.apply(rows, sem, s, row_key)


def all_modes(rows: list[dict], s: config.Settings) -> list[dict]:
    """Evaluointiin: riveille kaikki kolme pistettä (score_keyword, score_semantic, score_hybrid)."""
    from . import hybrid
    return hybrid.apply(rows, semantic_raw(s), s.with_(scoring_mode="keyword"), row_key)
