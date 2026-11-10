"""Prosessin yhteiset resurssit generatiivisille ominaisuuksille: tietokanta, kyselyembedder ja kielimalli.

Embedding-malli ja palamatriisi ladataan kerran ja pidetään muistissa (ensimmäinen kysymys ~1 min, sen jälkeen nopea).
"""
from __future__ import annotations

import json
import threading

import numpy as np

from . import config
from .llm import LLM, get_llm
from .store import Store

_lock = threading.Lock()
_state: dict = {}


class IndexMissing(RuntimeError):
    pass


def settings() -> config.Settings:
    return config.load()


def store(s: config.Settings | None = None) -> Store:
    s = s or settings()
    with _lock:
        st = _state.get("store")
        if st is None or _state.get("db") != s.db_path:
            if not s.db_path.exists():
                raise IndexMissing("Semanttista indeksiä ei ole. Aja ensin: python -m rag ingest")
            st = Store(s.db_path)
            _state.update(store=st, db=s.db_path)
        if not st.has_config(s.config_id):
            raise IndexMissing(f"Indeksiä {s.config_id} ei ole. Aja: python -m rag ingest")
        return st


def embed_query(text: str, s: config.Settings | None = None) -> np.ndarray:
    from .scoring import _embedder_factory
    s = s or settings()
    return _embedder_factory(s)().embed_queries([text])[0]


def cities(st: Store) -> list[str]:
    out = set()
    for city, places in st.db.execute("SELECT DISTINCT city, places FROM jobs"):
        if city:
            out.add(city)
        for p in json.loads(places or "[]"):
            for part in str(p).split(","):
                part = part.strip()
                if 3 <= len(part) <= 30 and part[0].isupper() and " " not in part:
                    out.add(part)
    return sorted(out)


def llm(task: str = "chat", s: config.Settings | None = None) -> LLM:
    return get_llm(s or settings(), task)


def retriever(s: config.Settings | None = None):
    from .chat import make_retriever
    s = s or settings()
    st = store(s)
    key = ("cities", s.db_path)
    if _state.get("cities_key") != key:
        _state.update(cities=cities(st), cities_key=key)
    return make_retriever(s, st, lambda q: embed_query(q, s), _state["cities"])
