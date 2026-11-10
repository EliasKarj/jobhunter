"""Yhteiset lähdepalat selityksille ja hakemuspaketille: ilmoituksen palat [A1..] ja profiilin palat [P1..].

Lyhyet tunnisteet ovat mallille helppoja kopioida; backend muuntaa ne takaisin todellisiksi lähteiksi
(chunks.id + merkkiväli jobs.clean_text-kentässä, profiilipalan source_ref).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass

from . import config
from .clean import sha1
from .profile import ProfileChunk, build_profile
from .store import Store

CACHE_SCHEMA = """CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY, kind TEXT, job_id TEXT, model TEXT, created TEXT, value TEXT NOT NULL)"""


@dataclass
class Grounding:
    job: dict                      # jobs-taulun rivi
    ad: dict[str, dict]            # "A1" -> {id, text, start, end}
    profile: dict[str, ProfileChunk]   # "P1" -> pala

    def ad_block(self) -> str:
        return "\n\n".join(f"[{k}] {v['text']}" for k, v in self.ad.items())

    def profile_block(self) -> str:
        return "\n\n".join(f"[{k}] ({p.source_ref}) {p.text}" for k, p in self.profile.items())

    def profile_text(self) -> str:
        return "\n".join(p.text for p in self.profile.values())

    def ref(self, key: str) -> dict | None:
        if key in self.ad:
            a = self.ad[key]
            return {"id": key, "type": "ad", "chunk_id": a["id"], "start": a["start"], "end": a["end"], "text": a["text"]}
        if key in self.profile:
            p = self.profile[key]
            return {"id": key, "type": "profile", "kind": p.kind, "source_ref": p.source_ref, "text": p.text}
        return None

    def hash(self) -> str:
        return sha1(self.job["content_hash"], *(p.hash for p in self.profile.values()))


def find_job(store: Store, url: str) -> dict:
    r = store.db.execute("SELECT * FROM jobs WHERE url=?", (url,)).fetchone()
    if not r:
        raise LookupError("Ilmoitusta ei ole indeksissä. Päivitä indeksi: python -m rag ingest")
    return dict(r)


def build(store: Store, s: config.Settings, url: str, profile: list[ProfileChunk] | None = None) -> Grounding:
    job = find_job(store, url)
    rows = store.db.execute("SELECT id, text, char_start, char_end FROM chunks WHERE job_id=? AND config_id=? "
                            "AND kind='body' ORDER BY idx", (job["job_id"], s.config_id)).fetchall()
    if rows:
        ad = {f"A{i}": {"id": r["id"], "text": r["text"].split("\n", 1)[-1], "start": r["char_start"], "end": r["char_end"]}
              for i, r in enumerate(rows, 1)}
    else:  # indeksoimaton ilmoitus: koko teksti yhtenä palana
        ad = {"A1": {"id": None, "text": job["clean_text"] or "", "start": 0, "end": len(job["clean_text"] or "")}}
    prof = build_profile(s) if profile is None else profile
    return Grounding(job, ad, {f"P{i}": p for i, p in enumerate(prof, 1)})


_REF = re.compile(r"\b([AP]\d{1,2})\b")


def clean_refs(refs, g: Grounding, prefix: str | None = None) -> list[str]:
    """Hyväksy vain olemassa olevat tunnisteet (valinnaisesti vain A- tai P-alkuiset)."""
    out = []
    for r in refs or []:
        for k in _REF.findall(str(r)):
            if (prefix is None or k.startswith(prefix)) and g.ref(k) and k not in out:
                out.append(k)
    return out


# ------------------------------------------------------------ välimuisti

def cache_get(store: Store, key: str) -> dict | None:
    store.db.execute(CACHE_SCHEMA)
    r = store.db.execute("SELECT value, created, model FROM llm_cache WHERE key=?", (key,)).fetchone()
    if not r:
        return None
    return {**json.loads(r[0]), "cached": True, "created": r[1], "model": r[2]}


def cache_put(store: Store, key: str, kind: str, job_id: str, model: str, value: dict) -> None:
    store.db.execute(CACHE_SCHEMA)
    store.db.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?)",
                     (key, kind, job_id, model, dt.datetime.now().isoformat(timespec="seconds"),
                      json.dumps(value, ensure_ascii=False)))
    store.db.commit()
