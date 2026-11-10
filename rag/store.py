"""Tallennus: SQLite + sqlite-vec. Metadata tavallisissa tauluissa, vektorit vec0-tauluissa (yksi per indeksi).

Tietomalli on tehty niin, että myöhemmät selitykset/chat voivat viitata lähteeseen:
chunks.char_start/char_end osoittavat jobs.clean_text-kenttään, profile_chunks.source_ref CV:n osioon tai OPS-URL:iin.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    source TEXT,
    title TEXT,
    company TEXT,
    city TEXT,
    places TEXT,              -- JSON-lista
    posted TEXT,
    deadline TEXT,
    lang TEXT,
    clean_text TEXT,
    content_hash TEXT NOT NULL,
    first_seen TEXT,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS index_configs (
    config_id TEXT PRIMARY KEY,
    backend TEXT, model TEXT, dim INTEGER,
    chunk_size INTEGER, chunk_overlap INTEGER,
    created TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    config_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    kind TEXT NOT NULL,       -- title | body
    text TEXT NOT NULL,       -- embedattu teksti (otsake + pala)
    text_hash TEXT NOT NULL,
    char_start INTEGER,       -- viittaus jobs.clean_text-kenttään
    char_end INTEGER,
    UNIQUE (job_id, config_id, idx)
);
CREATE INDEX IF NOT EXISTS chunks_hash ON chunks(config_id, text_hash);
CREATE TABLE IF NOT EXISTS job_index (
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    config_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    indexed_at TEXT,
    PRIMARY KEY (job_id, config_id)
);
CREATE TABLE IF NOT EXISTS profile_vectors (
    text_hash TEXT NOT NULL,
    config_id TEXT NOT NULL,
    kind TEXT, source_ref TEXT, text TEXT,
    vec BLOB NOT NULL,
    PRIMARY KEY (text_hash, config_id)
);
"""


def _blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


class Store:
    def __init__(self, path: Path | str):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        import sqlite_vec
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ indeksit
    @staticmethod
    def vec_table(config_id: str) -> str:
        return "vec_" + re.sub(r"[^a-z0-9_]", "_", config_id.lower())

    def ensure_config(self, config_id: str, backend: str, model: str, dim: int, size: int, overlap: int) -> None:
        row = self.db.execute("SELECT dim FROM index_configs WHERE config_id=?", (config_id,)).fetchone()
        if row and row["dim"] != dim:
            raise RuntimeError(f"indeksin {config_id} ulottuvuus {row['dim']} ei täsmää malliin ({dim})")
        if not row:
            self.db.execute("INSERT INTO index_configs VALUES (?,?,?,?,?,?,?)",
                            (config_id, backend, model, dim, size, overlap, dt.datetime.now().isoformat(timespec="seconds")))
        self.db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.vec_table(config_id)} "
                        f"USING vec0(embedding float[{dim}] distance_metric=cosine)")
        self.db.commit()

    def has_config(self, config_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM index_configs WHERE config_id=?", (config_id,)).fetchone() is not None

    def configs(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT config_id FROM index_configs")]

    # ------------------------------------------------------------ ilmoitukset
    def upsert_job(self, meta: dict, today: str) -> None:
        self.db.execute("""
            INSERT INTO jobs (job_id, url, source, title, company, city, places, posted, deadline, lang,
                              clean_text, content_hash, first_seen, last_seen)
            VALUES (:job_id, :url, :source, :title, :company, :city, :places, :posted, :deadline, :lang,
                    :clean_text, :content_hash, :today, :today)
            ON CONFLICT(job_id) DO UPDATE SET
                url=excluded.url, source=excluded.source, title=excluded.title, company=excluded.company,
                city=excluded.city, places=excluded.places, posted=excluded.posted, deadline=excluded.deadline,
                lang=excluded.lang, clean_text=excluded.clean_text, content_hash=excluded.content_hash,
                last_seen=excluded.last_seen
        """, {**meta, "places": json.dumps(meta.get("places") or [], ensure_ascii=False), "today": today})

    def indexed_hash(self, job_id: str, config_id: str) -> str | None:
        r = self.db.execute("SELECT content_hash FROM job_index WHERE job_id=? AND config_id=?",
                            (job_id, config_id)).fetchone()
        return r[0] if r else None

    def delete_chunks(self, job_id: str, config_id: str | None = None) -> None:
        where = "job_id=?" + (" AND config_id=?" if config_id else "")
        args = (job_id, config_id) if config_id else (job_id,)
        for r in self.db.execute(f"SELECT id, config_id FROM chunks WHERE {where}", args).fetchall():
            self.db.execute(f"DELETE FROM {self.vec_table(r['config_id'])} WHERE rowid=?", (r["id"],))
        self.db.execute(f"DELETE FROM chunks WHERE {where}", args)
        self.db.execute(f"DELETE FROM job_index WHERE {where}", args)

    def cached_vector(self, config_id: str, text_hash: str) -> bytes | None:
        """Sama teksti on jo embedattu tässä indeksissä (esim. sama ilmoitus toisessa kaupungissa)."""
        r = self.db.execute(f"""SELECT v.embedding FROM chunks c JOIN {self.vec_table(config_id)} v ON v.rowid = c.id
                                WHERE c.config_id=? AND c.text_hash=? LIMIT 1""", (config_id, text_hash)).fetchone()
        return r[0] if r else None

    def add_chunk(self, job_id: str, config_id: str, idx: int, kind: str, text: str, text_hash: str,
                  start: int | None, end: int | None, vec: bytes | np.ndarray) -> int:
        cur = self.db.execute("""INSERT INTO chunks (job_id, config_id, idx, kind, text, text_hash, char_start, char_end)
                                 VALUES (?,?,?,?,?,?,?,?)""", (job_id, config_id, idx, kind, text, text_hash, start, end))
        rowid = cur.lastrowid
        self.db.execute(f"INSERT INTO {self.vec_table(config_id)} (rowid, embedding) VALUES (?, ?)",
                        (rowid, vec if isinstance(vec, bytes) else _blob(vec)))
        return rowid

    def mark_indexed(self, job_id: str, config_id: str, content_hash: str) -> None:
        self.db.execute("""INSERT INTO job_index VALUES (?,?,?,?)
                           ON CONFLICT(job_id, config_id) DO UPDATE SET content_hash=excluded.content_hash,
                           indexed_at=excluded.indexed_at""",
                        (job_id, config_id, content_hash, dt.datetime.now().isoformat(timespec="seconds")))

    def delete_job(self, job_id: str) -> None:
        self.delete_chunks(job_id)
        self.db.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))

    def count(self, table: str, where: str = "", args: tuple = ()) -> int:
        return self.db.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0]

    def all_job_vectors(self, config_id: str) -> tuple[list[str], np.ndarray]:
        """Kaikki palavektorit ilmoituksittain järjestettynä: (job_id per rivi, matriisi)."""
        rows = self.db.execute(f"""SELECT c.job_id, v.embedding FROM chunks c
                                   JOIN {self.vec_table(config_id)} v ON v.rowid = c.id
                                   WHERE c.config_id=? ORDER BY c.job_id, c.idx""", (config_id,)).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        ids = [r[0] for r in rows]
        mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
        return ids, mat

    def knn(self, config_id: str, query: np.ndarray, k: int = 10) -> list[sqlite3.Row]:
        """Lähimmät palat kyselyvektorille (tulevaa chattia/selityksiä varten)."""
        return self.db.execute(f"""
            SELECT c.job_id, c.idx, c.text, c.char_start, c.char_end, j.title, j.company, j.city, j.url, v.distance
            FROM {self.vec_table(config_id)} v
            JOIN chunks c ON c.id = v.rowid JOIN jobs j ON j.job_id = c.job_id
            WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance""", (_blob(query), k)).fetchall()

    # ------------------------------------------------------------ profiili
    def profile_vector(self, text_hash: str, config_id: str) -> bytes | None:
        r = self.db.execute("SELECT vec FROM profile_vectors WHERE text_hash=? AND config_id=?",
                            (text_hash, config_id)).fetchone()
        return r[0] if r else None

    def save_profile_vector(self, text_hash: str, config_id: str, kind: str, source_ref: str, text: str,
                            vec: np.ndarray) -> None:
        self.db.execute("INSERT OR REPLACE INTO profile_vectors VALUES (?,?,?,?,?,?)",
                        (text_hash, config_id, kind, source_ref, text, _blob(vec)))
