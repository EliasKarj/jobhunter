"""Kysymyshaku chatille: metadatasuodattimet kysymyksestä + hybridihaku (vektori + FTS5) ilmoituspaloista.

Vaiheen 1 hybridi vertasi ilmoituksia profiiliin; chatissa vertailukohta on käyttäjän kysymys:
  - vektorihaku (bge-m3) löytää merkityksen, myös kielten välillä (fi-kysymys, en-ilmoitus)
  - FTS5-sanahaku löytää tarkat termit (Azure, PLC, SAP), joita vektori voi sumentaa
  - järjestykset yhdistetään RRF:llä ilmoitustasolla

Kieltoehdot ("ei ruotsia", "without Java") poistetaan sanahausta, mutta ilmoituksen kielletyn termin
sisältävä pala otetaan kontekstiin, jotta malli voi todeta ehdon täyttymättä jäämisen lähteestä.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from dataclasses import dataclass, field

import numpy as np

from .store import Store

# Taivutuksessa vaihtuvat kantasanat (astevaihtelu): nimi -> lisäkannat
_CITY_STEMS = {"helsinki": ["helsing"], "turku": ["turu"], "lahti": ["lahde", "lahte"], "kokkola": ["kokkola"],
               "seinäjoki": ["seinäjoe", "seinäjoen"], "joensuu": ["joensuu"], "hämeenlinna": ["hämeenlinna"]}
_NEG = re.compile(r"\b(?:ei|eivät|eikä|ilman|paitsi|not|without|no|except)\s+(?:vaadi\w*\s+|edellytä\w*\s+|require\w*\s+|need\w*\s+)?"
                  r"([\wåäö+#.\-]{2,})", re.I)
_WORD = re.compile(r"[\wåäö+#.\-]{2,}", re.I)
_STOP = set("""mitkä mikä mitä missä mihin kuka ketkä onko ovat oli olla ole on ei eivät eikä joka jotka jossa joissa
paikat paikkoja paikka työpaikat työpaikkoja työpaikka ilmoitukset ilmoituksia ilmoitus tehtävät tehtäviä tehtävä
vaativat vaatii vaadi vaativa vaatimus edellyttävät edellyttää edellytä haluavat haluaa hakevat hakee
ja tai sekä mutta kuin kun että jos niin myös vain ihan tosi aika
löytyy löytyykö onko kerro näytä listaa etsi hae anna mitkä what which where who are is the a an and or but
jobs job positions position roles role require requires requiring need needs with in at of for to on
viime tällä tänään eilen viikolla viikon päivän päivää kuukauden kuukausi uusia uudet uusi""".split())
_PREFIX_MIN = 4   # FTS-prefiksihaku vasta näin pitkistä sanoista (taivutus: azure* -> azuren, azurea)


@dataclass
class Filters:
    cities: list[str] = field(default_factory=list)
    since: str | None = None        # ISO-päivä
    sources: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)   # kielletyt termit (vain mallin tiedoksi + todistepalat)

    def describe(self) -> list[str]:
        out = []
        if self.cities:
            out.append("kaupunki: " + ", ".join(self.cities))
        if self.since:
            out.append("julkaistu " + self.since + " jälkeen")
        if self.sources:
            out.append("lähde: " + ", ".join(self.sources))
        if self.exclude:
            out.append("ei: " + ", ".join(self.exclude))
        return out


def _city_match(word: str, city: str) -> bool:
    c = city.lower()
    if len(c) < 4:
        return word == c
    stems = [c[:max(4, len(c) - 2)]] + _CITY_STEMS.get(c, [])
    return any(word.startswith(s) for s in stems) and len(word) <= len(c) + 7


def parse_filters(question: str, cities: list[str], sources: list[str] = ("duunitori", "jobly"),
                  today: dt.date | None = None) -> Filters:
    """Säännöt eivätkä kielimalli: ennustettava ja testattava. Tunnistamaton jätetään suodattamatta."""
    today = today or dt.date.today()
    q = question.lower()
    words = _WORD.findall(q)
    f = Filters()
    for c in sorted(set(cities), key=len, reverse=True):
        if any(_city_match(w, c) for w in words) and c not in f.cities:
            f.cities.append(c)
    days = None
    if re.search(r"\btänään\b|\btoday\b", q):
        days = 0
    elif re.search(r"\beilen\b|\byesterday\b", q):
        days = 1
    elif re.search(r"(viime|tällä|kuluneen|edellisen)\s+viiko|\bviikon sisällä|\b(this|last|past)\s+week", q):
        days = 7
    elif re.search(r"(viime|tässä|kuluneen)\s+kuu|\bkuukauden sisällä|\b(this|last|past)\s+month", q):
        days = 30
    m = re.search(r"(\d{1,3})\s*(?:päivän|päivää|pv|days?)\b", q)
    if m:
        days = int(m.group(1))
    if days is not None:
        f.since = (today - dt.timedelta(days=days)).isoformat()
    f.sources = [s for s in sources if s in q]
    f.exclude = list(dict.fromkeys(m.group(1).lower().strip(".-") for m in _NEG.finditer(q)
                                   if m.group(1).lower() not in _STOP))
    return f


def search_terms(question: str, f: Filters) -> list[str]:
    """Sanahaun termit: ei stop-sanoja, kaupunkeja, kiellettyjä termejä eikä numeroita."""
    out = []
    for w in _WORD.findall(question.lower()):
        w = w.strip(".-")
        if (len(w) < 2 or w in _STOP or w.isdigit() or any(_city_match(w, c) for c in f.cities)
                or any(w.startswith(x[:max(3, len(x) - 2)]) for x in f.exclude)):
            continue
        out.append(w)
    return list(dict.fromkeys(out))


def _fts_query(terms: list[str]) -> str:
    parts = []
    for t in terms:
        t = re.sub(r'["*]', "", t)
        if not t:
            continue
        # taivutus: haetaan kantaa (ruotsia -> ruots*), lyhyet sanat sellaisinaan
        stem = t[:max(_PREFIX_MIN, len(t) - 2)] if len(t) > _PREFIX_MIN + 1 else t
        parts.append(f'"{stem}"*' if len(stem) >= _PREFIX_MIN else f'"{t}"')
    return " OR ".join(parts)


# ------------------------------------------------------------ indeksit muistissa

FTS_SCHEMA = "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='unicode61 remove_diacritics 0')"
_mem: dict = {"key": None}
_mem_lock = threading.Lock()


def sync_fts(store: Store, config_id: str) -> None:
    """Pidä FTS-taulu chunks-taulun tasalla (lisää puuttuvat, poista karsitut). Halpa, kun mitään ei muutu."""
    store.db.execute(FTS_SCHEMA)
    store.db.execute("""INSERT INTO chunks_fts(rowid, text)
                        SELECT id, text FROM chunks WHERE config_id=? AND kind='body'
                        AND id NOT IN (SELECT rowid FROM chunks_fts)""", (config_id,))
    store.db.execute("DELETE FROM chunks_fts WHERE rowid NOT IN (SELECT id FROM chunks)")
    store.db.commit()


def chunk_matrix(store: Store, config_id: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """(chunk_id[], matriisi, job_id[]) body-paloista. Välimuistissa, kunnes palojen määrä/maksimi-id muuttuu."""
    key = (config_id,) + tuple(store.db.execute(
        "SELECT COUNT(*), MAX(id) FROM chunks WHERE config_id=? AND kind='body'", (config_id,)).fetchone())
    with _mem_lock:
        if _mem["key"] != key:
            rows = store.db.execute(f"""SELECT c.id, c.job_id, v.embedding FROM chunks c
                                        JOIN {store.vec_table(config_id)} v ON v.rowid = c.id
                                        WHERE c.config_id=? AND c.kind='body' ORDER BY c.id""", (config_id,)).fetchall()
            ids = np.array([r[0] for r in rows], dtype=np.int64)
            mat = (np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32).reshape(len(rows), -1)
                   if rows else np.zeros((0, 1), dtype=np.float32))
            _mem.update(key=key, ids=ids, mat=mat, jobs=[r[1] for r in rows])
        return _mem["ids"], _mem["mat"], _mem["jobs"]


# ------------------------------------------------------------ haku

@dataclass
class Hit:
    job_id: str
    title: str
    company: str
    city: str
    url: str
    posted: str
    source: str
    score: float           # RRF
    best_sim: float        # paras kosini kysymykseen
    chunks: list[dict]     # {id, text, start, end, why}


def candidate_jobs(store: Store, config_id: str, f: Filters) -> set[str] | None:
    """Suodattimiin osuvat ilmoitukset (None = ei suodattimia)."""
    if not (f.cities or f.since or f.sources):
        return None
    sql = "SELECT j.job_id, j.city, j.places FROM jobs j JOIN job_index x ON x.job_id=j.job_id AND x.config_id=? WHERE 1=1"
    args: list = [config_id]
    if f.since:
        sql += " AND j.posted >= ?"
        args.append(f.since)
    if f.sources:
        sql += f" AND j.source IN ({','.join('?' * len(f.sources))})"
        args += f.sources
    out = set()
    want = [c.lower() for c in f.cities]
    for r in store.db.execute(sql, args):
        if want:
            places = " ".join([r["city"] or ""] + json.loads(r["places"] or "[]")).lower()
            if not any(c in places for c in want):
                continue
        out.add(r["job_id"])
    return out


def _strip_header(text: str) -> str:
    return text.split("\n", 1)[1] if "\n" in text else text


def search(store: Store, config_id: str, question: str, qvec: np.ndarray, f: Filters,
           n_jobs: int = 8, per_job: int = 3, rrf_k: int = 60) -> list[Hit]:
    sync_fts(store, config_id)
    cand = candidate_jobs(store, config_id, f)
    if cand is not None and not cand:
        return []
    ids, mat, jobs = chunk_matrix(store, config_id)
    if not len(ids):
        return []
    sims = mat @ np.asarray(qvec, dtype=np.float32)
    mask = np.array([j in cand for j in jobs]) if cand is not None else np.ones(len(jobs), dtype=bool)

    # vektorirank ilmoitustasolla: paras pala per ilmoitus
    best: dict[str, float] = {}
    for i in np.flatnonzero(mask):
        j = jobs[i]
        if sims[i] > best.get(j, -2):
            best[j] = float(sims[i])
    vec_rank = {j: r for r, j in enumerate(sorted(best, key=best.get, reverse=True)[:300], 1)}

    # sanahaku
    terms = search_terms(question, f)
    fts_hits: dict[int, float] = {}
    if terms:
        q = _fts_query(terms)
        if q:
            for r in store.db.execute("SELECT rowid, bm25(chunks_fts) s FROM chunks_fts WHERE chunks_fts MATCH ? "
                                      "ORDER BY s LIMIT 2000", (q,)):
                fts_hits[r[0]] = -r[1]   # bm25: pienempi = parempi
    pos = {int(c): i for i, c in enumerate(ids)}
    fts_job: dict[str, float] = {}
    for cid, sc in fts_hits.items():
        i = pos.get(cid)
        if i is None or not mask[i]:
            continue
        fts_job[jobs[i]] = max(fts_job.get(jobs[i], -1e9), sc)
    fts_rank = {j: r for r, j in enumerate(sorted(fts_job, key=fts_job.get, reverse=True)[:300], 1)}

    fused = {j: 1 / (rrf_k + vec_rank[j]) + (1 / (rrf_k + fts_rank[j]) if j in fts_rank else 0)
             for j in vec_rank}
    for j in fts_rank:
        fused.setdefault(j, 1 / (rrf_k + fts_rank[j]))
    top = sorted(fused, key=fused.get, reverse=True)[:n_jobs]

    hits = []
    for j in top:
        meta = store.db.execute("SELECT title, company, city, url, posted, source FROM jobs WHERE job_id=?", (j,)).fetchone()
        rows = store.db.execute("SELECT id, text, char_start, char_end FROM chunks WHERE job_id=? AND config_id=? "
                                "AND kind='body' ORDER BY idx", (j, config_id)).fetchall()
        scored = []
        for r in rows:
            i = pos.get(r["id"])
            s = float(sims[i]) if i is not None else -1
            why = "merkitys"
            if r["id"] in fts_hits:
                s += 0.15
                why = "sana"
            scored.append((s, r, why))
        scored.sort(key=lambda x: -x[0])
        picked = scored[:per_job]
        # kieltoehdon todiste: pala, jossa kielletty termi mainitaan
        for x in f.exclude:
            stem = x[:max(3, len(x) - 2)]
            ev = next((t for t in scored if stem in t[1]["text"].lower()), None)
            if ev and ev not in picked:
                picked.append((ev[0], ev[1], "kielletty termi"))
        picked.sort(key=lambda t: t[1]["char_start"] or 0)   # lukujärjestys
        hits.append(Hit(j, meta["title"], meta["company"] or "", meta["city"] or "", meta["url"], meta["posted"] or "",
                        meta["source"] or "", fused[j], best.get(j, -1),
                        [{"id": r["id"], "text": _strip_header(r["text"]), "start": r["char_start"],
                          "end": r["char_end"], "why": why} for _, r, why in picked]))
    return hits
