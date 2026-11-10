"""Embedding-taustat. Profiilipalat ovat "kyselyitä", ilmoituspalat "dokumentteja" (e5 tarvitsee etuliitteet).

Kaikki vektorit normalisoidaan pituuteen 1, joten pistetulo = kosinisamankaltaisuus.
"""
from __future__ import annotations

import hashlib
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

import numpy as np

from .config import Settings


def _normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


class LocalEmbedder:
    """sentence-transformers paikallisesti (CPU). Malli ladataan vasta ensimmäisellä käytöllä."""

    def __init__(self, model: str, batch_size: int = 16):
        self.model_name = model
        self.batch_size = batch_size
        self._model = None
        self._e5 = "e5" in model.lower()

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            print(f"  ladataan embedding-malli {self.model_name} ...", file=sys.stderr)
            self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model

    @property
    def dim(self) -> int:
        m = self._load()
        get = getattr(m, "get_embedding_dimension", None) or m.get_sentence_embedding_dimension
        return int(get())

    def _encode(self, texts: list[str]) -> np.ndarray:
        m = self._load()
        v = m.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                     show_progress_bar=False, convert_to_numpy=True)
        return _normalize(v)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._encode([("passage: " + t) if self._e5 else t for t in texts])

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        return self._encode([("query: " + t) if self._e5 else t for t in texts])


class OpenAIEmbedder:
    """OpenAI embeddings -rajapinta suoraan urllibillä (ei lisäriippuvuutta)."""

    URL = "https://api.openai.com/v1/embeddings"
    DIMS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}

    def __init__(self, model: str, api_key: str, batch_size: int = 96):
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY puuttuu .env-tiedostosta")
        self.model_name, self.key, self.batch_size = model, api_key, batch_size
        self.dim = self.DIMS.get(model, 1536)

    def _post(self, texts: list[str]) -> list[list[float]]:
        body = json.dumps({"model": self.model_name, "input": texts}).encode()
        req = urllib.request.Request(self.URL, data=body, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                    data = json.loads(r.read())
                return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < 3:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise

    def _encode(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            out += self._post(texts[i:i + self.batch_size])
        return _normalize(np.array(out, dtype=np.float32).reshape(len(texts), self.dim))

    embed_documents = _encode
    embed_queries = _encode


class FakeEmbedder:
    """Testeihin: deterministinen sanapussi-hajautus. Samoja sanoja sisältävät tekstit ovat lähellä toisiaan."""

    def __init__(self, dim: int = 64):
        self.dim = dim
        self.model_name = "fake"
        self.calls = 0
        self.texts_embedded = 0

    def _encode(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        self.texts_embedded += len(texts)
        v = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"\w+", t.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                v[i, h % self.dim] += 1.0
        return _normalize(v)

    embed_documents = _encode
    embed_queries = _encode


def get_embedder(s: Settings):
    if s.embed_backend == "local":
        return LocalEmbedder(s.embed_model, s.batch_size)
    if s.embed_backend == "openai":
        return OpenAIEmbedder(s.embed_model, s.openai_api_key)
    if s.embed_backend == "fake":
        return FakeEmbedder()
    raise ValueError(f"tuntematon EMBED_BACKEND: {s.embed_backend}")
