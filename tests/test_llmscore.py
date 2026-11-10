from rag import llm, llmscore
from rag.clean import job_id
from rag.store import Store

CV = """# Matti Meikäläinen

**Student**
Vaasa | matti@example.com | 040 123 4567

## Profile

IT student with Docker skills.

## Work Experience

### Security Guard | Vartiopalvelu Oy
*2020*

Patrols.

## Projects

### Lenkkiloki – analyzer
*JavaScript*

- App.

## Technical Skills

- **Web:** React
"""


def _row(i, score, status="ok"):
    return {"status": status, "score": score, "job": {"url": f"https://x/{i}", "source": "duunitori", "title": f"Dev {i}",
                                                      "company": "Firma", "location": "Vaasa", "text": "Haemme kehittäjää."}}


def test_summary_is_anonymous_but_useful():
    s = llmscore.applicant_summary(CV)
    for private in ("Matti", "Meikäläinen", "matti@example.com", "040 123", "Vartiopalvelu"):
        assert private not in s
    for useful in ("IT student", "Security Guard", "Lenkkiloki", "React"):
        assert useful in s


def test_scores_top_and_labeled_once_and_saves_results():
    store = Store(":memory:")
    rows = [_row(1, 90), _row(2, 80), _row(3, 10), _row(4, 95, "expired")]
    fake = llm.FakeLLM([{"level": "senior", "fit": 150, "reason": "Vaatii vuosia."}] * 3)
    r = llmscore.run(rows, store, "tiivistelmä", fake, top=2, must={"https://x/3"})
    assert r == {"done": 3, "failed": 0, "total": 3}             # kärki 2 + merkitty, vanhentunut ei
    scores = llmscore.load_scores(store)
    assert scores[job_id("https://x/1", "duunitori")]["fit"] == 100   # rajattu 0-100
    again = llmscore.run(rows, store, "tiivistelmä", fake, top=2, must={"https://x/3"})
    assert again["total"] == 0 and len(fake.calls) == 3          # ei uudelleen maksua
    changed = llmscore.run(rows, store, "uusi profiili", llm.FakeLLM([{"level": "mid", "fit": 5, "reason": ""}] * 3), top=2,
                           must={"https://x/3"})
    assert changed["total"] == 3                                 # profiilin muutos arvioi uudelleen


def test_failure_does_not_stop_run_and_attach_keeps_score():
    store = Store(":memory:")
    rows = [_row(1, 90), _row(2, 80)]
    fake = llm.FakeLLM([llm.LLMError("raja täynnä"), {"level": "junior", "fit": 70, "reason": "Sopii."}])
    r = llmscore.run(rows, store, "t", fake, top=2)
    assert r["done"] == 1 and r["failed"] == 1
    llmscore.attach(rows, llmscore.load_scores(store))
    got = [x for x in rows if x["llm"]]
    assert len(got) == 1 and got[0]["llm"]["fit"] == 70 and got[0]["score_llm"] == 70
    assert got[0]["score"] in (90, 80)                           # pisteet ennallaan


def test_scoring_provider_is_separate_from_llm_provider():
    from rag.config import Settings
    assert llmscore.get_scorer(Settings(score_provider="off")) is None
    c = llmscore.get_scorer(Settings(llm_provider="ollama", score_provider="claude", anthropic_api_key="k"))
    assert isinstance(c, llm.ClaudeLLM)


def test_combined_averages_scores_and_ranks_unrated_below_rated_peers():
    rated = {"job": {"url": "a"}, "score_hybrid": 60}
    unrated = {"job": {"url": "b"}, "score_hybrid": 60}
    no_hybrid = {"job": {"url": "c"}}
    scores = {llmscore.job_id("a", ""): {"fit": 40, "level": "mid", "reason": "x"}}
    llmscore.attach([rated, unrated, no_hybrid], scores)
    assert rated["score_combined"] == 50
    assert unrated["score_combined"] == 30                       # ei arviota: hybridijärjestys säilyy, mutta arvioidut edellä
    assert no_hybrid["score_combined"] is None
