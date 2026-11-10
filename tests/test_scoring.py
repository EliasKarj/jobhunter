"""Pisteiden yhdistäminen, aggregointi ja evaluointimittarit."""
import copy
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest

import jobhunter as jh
from rag import hybrid
from rag.config import Settings
from rag.evaluate import evaluate, ndcg_at_k, precision_at_k
from rag.semantic import aggregate

ROOT = Path(__file__).resolve().parent.parent


def mk(url, score, status="ok"):
    return {"score": score, "status": status, "reasons": [], "job": {"url": url, "source": "x"}}


def key(r):
    return r["job"]["url"]


def S(**kw):
    return Settings(**kw)


# ------------------------------------------------------------ hybridi

def test_weighted_combination_uses_configured_weights():
    kw = {"a": 80.0, "b": 20.0}
    sem = {"a": 0.0, "b": 100.0}
    assert hybrid.combine(kw, sem, S(w_keyword=1, w_semantic=0)) == {"a": 80.0, "b": 20.0}
    assert hybrid.combine(kw, sem, S(w_keyword=0, w_semantic=1)) == {"a": 0.0, "b": 100.0}
    out = hybrid.combine(kw, sem, S(w_keyword=0.6, w_semantic=0.4))
    assert out["a"] == pytest.approx(48.0) and out["b"] == pytest.approx(52.0)


def test_weights_are_normalized():
    kw, sem = {"a": 50.0}, {"a": 100.0}
    assert hybrid.combine(kw, sem, S(w_keyword=3, w_semantic=1)) == hybrid.combine(kw, sem, S(w_keyword=0.75, w_semantic=0.25))


def test_missing_semantic_falls_back_to_keyword():
    assert hybrid.combine({"a": 42.0}, {}, S(w_keyword=0.5, w_semantic=0.5)) == {"a": 42.0}


def test_normalize_clips_low_tail_keeps_top_order():
    raw = {str(i): float(i) for i in range(101)}
    n = hybrid.normalize(raw)
    assert n["0"] == 0 and n["5"] == 0 and n["100"] == 100
    assert n["96"] < n["98"] < n["100"]  # kärjen järjestys säilyy, ei tasapisteitä
    assert hybrid.normalize({"a": 0.5, "b": 0.5}) == {"a": 50.0, "b": 50.0}


def test_rrf_prefers_items_ranked_high_by_both():
    kw = {"a": 90.0, "b": 80.0, "c": 10.0}
    sem = {"a": 0.2, "b": 0.9, "c": 0.1}
    out = hybrid.combine(kw, sem, S(hybrid_method="rrf", w_keyword=1, w_semantic=1))
    assert out["c"] < out["a"] and out["c"] < out["b"]
    assert max(out.values()) <= 100


def test_apply_respects_mode_and_keeps_excluded():
    rows = [mk("a", 80.0), mk("b", 20.0), mk("x", 0.0, "excluded"), mk("e", 0.0, "expired")]
    sem = {"a": 0.1, "b": 0.9}
    for mode in ("keyword", "semantic", "hybrid"):
        rs = hybrid.apply(copy.deepcopy(rows), sem, S(scoring_mode=mode), key)
        by = {key(r): r for r in rs}
        assert by["x"]["status"] == "excluded" and by["x"]["score"] == 0.0
        assert by["e"]["status"] == "expired"
        assert by["a"]["score_keyword"] == 80.0
    kwm = {key(r): r["score"] for r in hybrid.apply(copy.deepcopy(rows), sem, S(scoring_mode="keyword"), key)}
    assert kwm == {"a": 80.0, "b": 20.0, "x": 0.0, "e": 0.0}
    sm = {key(r): r["score"] for r in hybrid.apply(copy.deepcopy(rows), sem, S(scoring_mode="semantic"), key)}
    assert sm["b"] > sm["a"]


def test_keyword_mode_is_identical_to_existing_scoring():
    """SCORING_MODE=keyword: apply_mode ei muuta nykyisen pisteytyksen tulosta lainkaan."""
    from rag.scoring import apply_mode
    prof = jh.load_profile(ROOT / "examples" / "profile.json")
    jobs = [jh.Job(**d) for d in json.loads((ROOT / "sample_jobs.json").read_text(encoding="utf-8"))]
    today = dt.date(2026, 11, 3)
    before = jh.score_all(jobs, prof, today)
    after = apply_mode(copy.deepcopy(before), S(scoring_mode="keyword"))
    assert after == before


def test_apply_mode_survives_semantic_failure(tmp_path, monkeypatch):
    from rag import scoring
    monkeypatch.setattr(scoring, "semantic_raw", lambda s: (_ for _ in ()).throw(RuntimeError("rikki")))
    rows = scoring.apply_mode([mk("https://duunitori.fi/tyopaikat/tyo/a-12345678", 55.0)],
                              S(scoring_mode="hybrid", db_path=tmp_path / "x.sqlite"))
    assert rows[0]["score"] == 55.0


# ------------------------------------------------------------ aggregointi

def test_aggregation_methods():
    # 3 profiilipalaa × 2 ilmoitusta (kunkin profiilipalan paras osuma)
    pp = np.array([[0.9, 0.6], [0.2, 0.6], [0.1, 0.6]], dtype=np.float32)
    assert aggregate(pp, "max", 3).tolist() == pytest.approx([0.9, 0.6])
    assert aggregate(pp, "mean", 3).tolist() == pytest.approx([0.4, 0.6])
    assert aggregate(pp, "topk_mean", 2).tolist() == pytest.approx([0.55, 0.6])
    assert aggregate(pp, "topk_mean", 10).tolist() == pytest.approx([0.4, 0.6])  # k rajataan palojen määrään


def test_semantic_scores_end_to_end_with_fake_embedder(tmp_path):
    from rag.embed import FakeEmbedder
    from rag.ingest import ingest
    from rag.profile import ProfileChunk
    from rag.semantic import semantic_scores
    from rag.store import Store
    s = Settings(embed_backend="fake", embed_model="fake", db_path=tmp_path / "r.sqlite", chunk_size=200,
                 chunk_overlap=0)
    store = Store(s.db_path)
    emb = FakeEmbedder()
    rows = [
        {"status": "ok", "job": {"title": "React developer", "url": "https://d.fi/tyo/a-11111111", "source": "d",
                                 "text": "React TypeScript Next.js frontend kehitys", "company": "A"}},
        {"status": "ok", "job": {"title": "Kokki", "url": "https://d.fi/tyo/b-22222222", "source": "d",
                                 "text": "ruoanlaitto keittiö lounas", "company": "B"}},
    ]
    ingest(rows, store, emb, s, progress=lambda m: None)
    prof = [ProfileChunk("skills", "t", "React TypeScript Next.js frontend")]
    sc = semantic_scores(store, s, lambda: emb, prof)
    assert sc["d:11111111"] > sc["d:22222222"]
    calls = emb.calls
    semantic_scores(store, s, lambda: emb, prof)
    assert emb.calls == calls  # profiilivektori välimuistista
    store.close()


# ------------------------------------------------------------ mittarit

def test_precision_and_ndcg_known_values():
    labels = {"a": 2, "b": 0, "c": 1, "d": 0}
    ranked = ["b", "a", "c", "d"]
    assert precision_at_k(ranked, labels, 2) == 0.5
    assert precision_at_k(ranked, labels, 4) == 0.5
    # DCG = 3/log2(3) + 1/log2(4); IDCG = 3/1 + 1/log2(3)
    expect = (3 / np.log2(3) + 1 / 2) / (3 + 1 / np.log2(3))
    assert ndcg_at_k(ranked, labels, 4) == pytest.approx(expect)
    assert ndcg_at_k(["a", "c", "b", "d"], labels, 4) == pytest.approx(1.0)
    assert ndcg_at_k(ranked, {"a": 0}, 4) == 0.0


def test_unlabeled_counts_as_not_relevant():
    assert precision_at_k(["x", "a"], {"a": 1}, 2) == 0.5


def test_evaluate_compares_rankers():
    rows = []
    for i in range(12):
        rows.append({"status": "ok", "job": {"url": f"u{i}"}, "score_keyword": float(i),
                     "score_semantic": float(12 - i), "score_hybrid": 6.0})
    labels = {f"u{i}": (1 if i < 6 else 0) for i in range(12)}  # semanttinen järjestys on oikea
    res = evaluate(rows, labels, k=5, bootstrap=False)
    assert res["n_labels"] == 12 and res["n_relevant"] == 6
    assert res["rankers"]["semanttinen"]["p@k"] == 1.0
    assert res["rankers"]["avainsana"]["p@k"] == 0.0


def _prof():
    return {"locations": [{"name": "Vaasa", "weight": 20, "aliases": ["vaasa"]}], "remote_bonus": 3,
            "remote_terms": ["etätyö", "remote"], "skills": {"python": 3}, "skills_target": 12,
            "level_positive": ["junior"], "level_negative": ["senior"], "level_soft_negative": ["vähintään 2 vuo"],
            "field_title_terms": ["developer"], "exclude_title_terms": [], "it_title_terms": ["developer"],
            "it_body_terms": ["python"], "title_senior_terms": [], "years_cap_terms": [],
            "remote_anywhere": {"weight": 15, "terms": ["täysin etänä"]}}


def test_fully_remote_job_gets_location_points_outside_search_areas():
    import datetime as dt
    remote = jh.Job(title="Junior Python Developer", url="u1", location="Oulu", text="Työ tehdään täysin etänä. Python.")
    office = jh.Job(title="Junior Python Developer", url="u2", location="Oulu", text="Toimisto Oulussa. Python.")
    r1, r2 = (jh.score_job(j, _prof(), dt.date(2026, 11, 9)) for j in (remote, office))
    assert r1["area"] == "Etätyö (koko Suomi)" and r1["parts"]["alue"] >= 15
    assert r2["area"] is None and r2["parts"]["alue"] == 0


def test_two_years_requirement_is_a_mild_one_time_penalty():
    import datetime as dt
    base = jh.Job(title="Junior Python Developer", url="u", location="Vaasa", text="Python.")
    two = jh.Job(title="Junior Python Developer", url="u", location="Vaasa", text="Python. Vähintään 2 vuoden kokemus, vähintään 2 vuotta.")
    a, b = (jh.score_job(j, _prof(), dt.date(2026, 11, 9)) for j in (base, two))
    assert a["parts"]["taso"] - b["parts"]["taso"] == 3


def test_kuntarekry_source_reads_public_json(monkeypatch, tmp_path):
    seen = []
    data = {"total": 2, "jobs": [
        {"url": "/fi/tyopaikat/datainsinoori-1/", "title": "Datainsinööri", "organisation_title": "Tampereen kaupunki",
         "location_tags": ["Tampere"], "region": "Pirkanmaa"},
        {"url": "/fi/tyopaikat/ict-2/", "title": "ICT-asiantuntija", "organisation_title": "Hyvinkää", "location_tags": [], "region": "Uusimaa"}]}
    monkeypatch.setattr(jh, "http_get", lambda url, *a, **k: seen.append(url) or json.dumps(data))
    jobs = jh.search_kuntarekry({"search": {"kuntarekry_categories": ["it-tietohallinto"]}}, tmp_path, 0)
    assert seen == ["https://www.kuntarekry.fi/fi/tyopaikat/it-tietohallinto/?format=json&includeTotal=1&limit=200"]
    assert [(j.title, j.company, j.location, j.source) for j in jobs] == [
        ("Datainsinööri", "Tampereen kaupunki", "Tampere", "kuntarekry"), ("ICT-asiantuntija", "Hyvinkää", "Uusimaa", "kuntarekry")]
    assert jobs[0].url == "https://www.kuntarekry.fi/fi/tyopaikat/datainsinoori-1/"


def test_glued_paragraphs_are_split_but_camelcase_kept():
    out = jh._plain("Rakennetaan ympäristöäHaluatko mukaan?Korkeakoulutus muuttuu. JavaScript ja PostgreSQL.")
    assert "mukaan?\nKorkeakoulutus" in out and "JavaScript" in out and "PostgreSQL" in out
    assert "selain ei ole tuettu" in jh._JUNK_H1


def test_slug_title_and_filter():
    assert jh.slug_title("https://duunitori.fi/tyopaikat/tyo/tekninen-kiinteistonhoitaja-vantaa-scsav-20717318") == "tekninen kiinteistonhoitaja vantaa"
    p = {"field_title_terms": ["kehittäjä"], "it_title_terms": [], "level_positive": [], "search": {"sitemap_terms": ["it", "ai", "data"]}}
    assert jh.slug_relevant("junioriohjelmistokehittaja harjoittelija", p)       # ääkkösetön yhdyssanan osa
    assert jh.slug_relevant("it tukihenkilo", p) and jh.slug_relevant("ai engineer", p)
    assert not jh.slug_relevant("myyja vaasaan", p)
    assert not jh.slug_relevant("kitchen assistant", p) and not jh.slug_relevant("sairaanhoitaja", p)   # it/ai vain sanoina


def test_sitemap_reads_pages_until_empty(monkeypatch, tmp_path):
    pages = {jh.SITEMAP: "<url><loc>https://duunitori.fi/tyopaikat/tyo/junior-developer-abcde-1234567</loc></url>"
                         "<url><loc>https://duunitori.fi/tyopaikat/tyo/myyja-abcde-1234568</loc></url>",
             jh.SITEMAP + "?p=2": "<url><loc>https://duunitori.fi/tyopaikat/tyo/data-engineer-abcde-1234569</loc></url>",
             jh.SITEMAP + "?p=3": "<urlset></urlset>"}
    calls = []
    monkeypatch.setattr(jh, "http_get", lambda url, *a, **k: calls.append(url) or pages[url])
    prof = {"field_title_terms": [], "it_title_terms": [], "level_positive": ["junior"],
            "search": {"duunitori_mode": "sitemap", "queries": [], "areas": [], "sitemap_terms": ["data", "developer"]}}
    jobs = jh.search_duunitori(prof, tmp_path, 0)
    assert len(calls) == 3
    assert sorted(j.title for j in jobs) == ["data engineer", "junior developer"]


def test_location_from_posting_places_when_sitemap_gives_none():
    import datetime as dt
    j = jh.Job(title="Junior Python Developer", url="u", location="", text="Python.", detail={"places": ["Vaasa"]})
    r = jh.score_job(j, _prof(), dt.date(2026, 11, 9))
    assert r["area"] == "Vaasa" and r["parts"]["alue"] == 20


def test_relative_paths_in_env_resolve_inside_data_dir(monkeypatch):
    from rag import config
    monkeypatch.setenv("CV_PATH", "data/cv.md")
    monkeypatch.setenv("RAG_DB", "rag.sqlite")
    s = config.load()
    assert s.cv_path == config.DATA / "cv.md" and s.db_path == config.DATA / "rag.sqlite"
