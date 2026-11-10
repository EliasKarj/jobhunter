"""Duplikaattien esto, uudelleenembeddauksen välttäminen ja karsinta."""
import pytest

from rag.config import Settings
from rag.embed import FakeEmbedder
from rag.ingest import ingest, prune
from rag.store import Store

TEXT = ("Haemme junior-ohjelmistokehittäjää.\nTyöskentelet Reactin ja TypeScriptin parissa.\n"
        "Edellytämme SQL-osaamista ja kiinnostusta pilvipalveluihin.\nTarjoamme hybridityön.")


def row(n: int, text: str = TEXT, city: str = "Vaasa", deadline: str | None = "2099-01-01", status: str = "ok"):
    return {"status": status, "job": {
        "title": f"Junior Developer {n}", "company": "Firma Oy", "location": city, "source": "duunitori",
        "url": f"https://duunitori.fi/tyopaikat/tyo/firma-junior-{10000000 + n}", "text": text,
        "detail": {"posted": "2026-11-01", "deadline": deadline, "places": [city]}}}


@pytest.fixture
def env(tmp_path):
    s = Settings(embed_backend="fake", embed_model="fake", chunk_size=120, chunk_overlap=30,
                 db_path=tmp_path / "rag.sqlite", batch_size=2)
    store = Store(s.db_path)
    yield s, store, FakeEmbedder()
    store.close()


def counts(store, s):
    return (store.count("jobs"), store.count("chunks"), store.count(store.vec_table(s.config_id)))


def test_rerun_does_not_duplicate_or_reembed(env):
    s, store, emb = env
    rows = [row(i) for i in range(5)]
    first = ingest(rows, store, emb, s, today="2026-11-03", progress=lambda m: None)
    assert first["indexed"] == 5
    before, texts_before = counts(store, s), emb.texts_embedded

    second = ingest(rows, store, emb, s, today="2026-11-04", progress=lambda m: None)
    assert second["indexed"] == 0 and second["skipped"] == 5
    assert counts(store, s) == before
    assert emb.texts_embedded == texts_before  # ei yhtään uutta embedding-kutsua
    last_seen = store.db.execute("SELECT DISTINCT last_seen FROM jobs").fetchall()
    assert [r[0] for r in last_seen] == ["2026-11-04"]


def test_same_job_twice_in_input_is_stored_once(env):
    s, store, emb = env
    ingest([row(1), row(1)], store, emb, s, progress=lambda m: None)
    assert store.count("jobs") == 1


def test_changed_content_is_reembedded_and_old_chunks_removed(env):
    s, store, emb = env
    ingest([row(1)], store, emb, s, progress=lambda m: None)
    n_before = store.count("chunks")
    stats = ingest([row(1, TEXT + "\nUusi rivi: Kubernetes-osaaminen on eduksi.")], store, emb, s,
                   progress=lambda m: None)
    assert stats["indexed"] == 1
    texts = [r[0] for r in store.db.execute("SELECT text FROM chunks")]
    assert any("Kubernetes" in t for t in texts)
    assert store.count("chunks") >= n_before
    assert store.count("chunks") == store.count(store.vec_table(s.config_id))  # ei orpoja vektoreita
    idx = [r[0] for r in store.db.execute("SELECT idx FROM chunks ORDER BY idx")]
    assert idx == list(range(len(idx)))


def test_identical_text_under_two_ids_is_embedded_once(env):
    s, store, emb = env
    a, b = row(1), row(2)
    b["job"]["title"] = a["job"]["title"]  # sama ilmoitus, eri URL (toinen kaupunki listauksessa)
    ingest([a, b], store, emb, s, progress=lambda m: None)
    n_chunks = store.count("chunks")
    assert store.count("jobs") == 2
    assert emb.texts_embedded == n_chunks // 2


def test_excluded_and_expired_rows_are_not_embedded(env):
    s, store, emb = env
    ingest([row(1, status="excluded"), row(2, status="expired"), row(3)], store, emb, s, progress=lambda m: None)
    assert store.count("jobs") == 1


def test_nothing_to_do_does_not_touch_embedder(env):
    s, store, _ = env

    class Boom:
        @property
        def dim(self):
            raise AssertionError("mallia ei saa ladata")

        def embed_documents(self, texts):
            raise AssertionError("ei saa embeddata")

    assert ingest([], store, Boom(), s, progress=lambda m: None)["indexed"] == 0


def test_prune_removes_expired_and_stale_but_keeps_followed(env):
    s, store, emb = env
    rows = [row(1, deadline="2026-10-01"), row(2, deadline="2026-10-01"), row(3), row(4)]
    ingest(rows, store, emb, s, today="2026-10-01", progress=lambda m: None)
    ingest([row(4)], store, emb, s, today="2026-11-03", progress=lambda m: None)  # 4 nähty tänään
    keep = {rows[1]["job"]["url"]}  # tallennettu ilmoitus säilyy, vaikka vanhentunut
    removed = prune(store, "2026-11-03", 14, keep)
    left = {r[0] for r in store.db.execute("SELECT url FROM jobs")}
    assert removed == 2  # 1 (hakuaika mennyt) ja 3 (ei nähty 33 päivään)
    assert left == {rows[1]["job"]["url"], rows[3]["job"]["url"]}
    assert store.count("chunks") == store.count(store.vec_table(s.config_id))
    assert store.count("job_index") == 2


def test_different_chunk_settings_get_separate_index(env):
    s, store, emb = env
    ingest([row(1)], store, emb, s, progress=lambda m: None)
    s2 = s.with_(chunk_size=300, chunk_overlap=0)
    stats = ingest([row(1)], store, emb, s2, progress=lambda m: None)
    assert stats["indexed"] == 1
    assert set(store.configs()) == {s.config_id, s2.config_id}
    assert store.count("jobs") == 1
