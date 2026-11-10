import json
import threading

import pytest
from fastapi.testclient import TestClient

import webapp
from rag import config, cvtailor, llm, panel
from rag.llm import LLMError

CV = """# Matti Meikäläinen

**IT Engineering student**
Vaasa | matti@example.com | 040 123 4567

## Profile

Third-year IT student with DevOps skills (Docker, Linux) and full-stack development.

## Work Experience

### Warehouse Worker | Firma Oy
*2021 – 2023*

- Picking and packing orders.

## Projects

### Lenkkiloki – GPX analyzer
*JavaScript, GitHub Actions*

- Web app that loads GPX files.
- Set up CI/CD with GitHub Actions.

### Chess bot
*Python*

- Bot that plays chess.

## Education

- **Information Technology** | ESAMK | 2023 – 2027

## Technical Skills

- **Web:** React, JavaScript
- **Containers:** Docker, Kubernetes
"""
JOB = {"title": "Junior DevOps", "company": "Firma", "city": "Vaasa"}
SUMMARY = "Third-year IT student with DevOps skills (Docker, Linux)."
OK = {"verdict": "approve", "score": 8, "findings": [], "interview_questions": [], "summary": "Hyvä."}


def plan(**kw):
    p = {"section_order": ["S1", "S3", "S2", "S4", "S5"], "drop": [], "focus_skills": ["Docker"], "summary": SUMMARY,
         "rewrites": [], "declined_suggestions": []}
    p.update(kw)
    return p


def rej(quote="", sid="", cat="fabricated", fix="Poista väite."):
    return {"verdict": "reject", "score": 3, "summary": "Ei.", "interview_questions": [],
            "findings": [{"severity": "blocking", "category": cat, "quote": quote, "source_id": sid,
                          "issue": "Ei CV:ssä.", "fix": fix}]}


def make(replies: dict, cancel=None, on_event=None):
    llms = {a: llm.FakeLLM(list(replies.get(a, [])), name=f"fake:{a}") for a in ("tailor", *panel.REVIEWERS)}
    events = []

    def emit(t, m, d):
        events.append((t, m, d or {}))
        if on_event:
            on_event(t, d or {})
    p = panel.Panel(JOB, "Haemme Docker- ja Kubernetes-osaajaa.", CV, None, "en", lambda a: llms[a], emit, cancel)
    return p, events, llms


def all_ok(n=1):
    return {a: [OK] * n for a in panel.REVIEWERS}


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(panel, "BACKOFF", 0)


# ------------------------------------------------------------ kulku

def test_approved_on_first_round_and_event_order():
    p, events, llms = make({"tailor": [plan()], **all_ok()})
    res = p.run()
    assert res["panel"]["status"] == "approved" and res["panel"]["rounds"] == 1
    types = [t for t, _, _ in events]
    assert types[:5] == ["run_started", "facts_loaded", "tailor_started", "tailor_finished", "rules_checked"]
    assert types.count("review_started") == len(panel.REVIEWERS) and types.count("review_finished") == len(panel.REVIEWERS)
    assert "revision_started" not in types and "fallback_applied" not in types
    assert res["summary"] == {"text": SUMMARY, "source": "generated", "flags": [], "style": []}
    assert len(llms["tailor"].calls) == 1


def test_revision_fixes_blocking_issue_on_second_round():
    replies = all_ok(2)
    replies["fact_checker"] = [rej("expert in Kubernetes", "SUMMARY"), OK]
    p, events, llms = make({"tailor": [plan(summary=SUMMARY + " Expert in Kubernetes."), plan()], **replies})
    res = p.run()
    assert res["panel"]["status"] == "approved" and res["panel"]["rounds"] == 2
    rev = [d for t, _, d in events if t == "revision_started"]
    assert rev == [{"round": 1, "rejected_by": ["fact_checker"]}]
    second = llms["tailor"].calls[1]["messages"][0]["content"]
    assert "PANEL_FEEDBACK" in second and "expert in Kubernetes" in second and "PREVIOUS_PLAN" in second


def test_max_two_revisions_then_fallback_to_original_summary():
    bad = SUMMARY + " Built microservices in production."
    replies = all_ok(3)
    replies["fact_checker"] = [rej("Built microservices in production", "SUMMARY")] * 3
    p, events, llms = make({"tailor": [plan(summary=bad)] * 3, **replies})
    res = p.run()
    assert len(llms["tailor"].calls) == 3 and res["panel"]["rounds"] == 3
    assert [d["round"] for t, _, d in events if t == "revision_started"] == [1, 2]
    assert any(t == "fallback_applied" for t, _, _ in events)
    assert res["summary"]["source"] == "original" and "microservices" not in res["summary"]["text"]
    assert res["panel"]["status"] == "needs_review"
    assert res["panel"]["remaining"][0]["resolved"] == "korvattu alkuperäisellä"


def test_fact_checker_veto_blocks_approval_even_if_others_approve():
    replies = all_ok(3)
    replies["fact_checker"] = [{**OK, "verdict": "reject"}] * 3
    p, _, _ = make({"tailor": [plan()] * 3, **replies})
    res = p.run()
    assert res["panel"]["status"] == "needs_review"
    assert res["panel"]["reviews"]["fact_checker"]["rejects"] is True
    assert res["summary"]["source"] == "original"      # paikantamaton hylkäys: varmuuden vuoksi alkuperäinen


def test_other_reviewer_cannot_block_by_demanding_missing_skill():
    replies = all_ok()
    replies["recruiter"] = [rej("", "", "missing_keyword", fix="Add Rust experience to the summary.")]
    p, _, _ = make({"tailor": [plan()], **replies})
    res = p.run()
    assert res["panel"]["status"] == "approved"
    f = res["panel"]["reviews"]["recruiter"]["findings"][0]
    assert f["severity"] == "suggestion" and "rust" in f["category"].lower()


def test_invalid_json_is_retried_once_with_error():
    replies = all_ok()
    replies["recruiter"] = [{"verdict": "maybe"}, OK]
    p, events, llms = make({"tailor": [plan()], **replies})
    res = p.run()
    assert res["panel"]["status"] == "approved"
    assert any(t == "retry" and d.get("agent") == "recruiter" for t, _, d in events)
    assert "did not match the schema" in llms["recruiter"].calls[1]["messages"][-1]["content"]


def test_api_error_retries_and_failed_reviewer_means_needs_review():
    replies = all_ok()
    replies["tech_interviewer"] = [LLMError("raja täynnä"), OK]
    replies["hiring_manager"] = [LLMError("a"), LLMError("b"), LLMError("c")]
    p, events, _ = make({"tailor": [plan()], **replies})
    res = p.run()
    assert sum(t == "retry" for t, _, _ in events) == 3
    assert "hiring_manager" in res["panel"]["failed"] and res["panel"]["status"] == "needs_review"
    assert any(t == "review_failed" for t, _, _ in events)


def test_cancel_during_panel_stops_run():
    ev = threading.Event()
    p, events, _ = make({"tailor": [plan()], **all_ok()}, cancel=ev,
                        on_event=lambda t, d: ev.set() if t == "review_started" else None)
    with pytest.raises(panel.Cancelled):
        p.run()
    assert not any(t == "revision_started" for t, _, _ in events)


# ------------------------------------------------------------ muotoilut ja muutoslista

def test_valid_rewrite_is_applied_and_listed_with_source():
    rw = [{"source_id": "S3E1B1", "text": "Web app that loads and charts GPX files.", "reason": "selkeämpi"}]
    p, _, _ = make({"tailor": [plan(rewrites=rw)], **all_ok()})
    res = p.run()
    ch = [c for c in res["changes"] if c["kind"] == "rewrite"]
    assert ch == [{"kind": "rewrite", "id": "S3E1B1", "before": "Web app that loads GPX files.",
                   "after": "Web app that loads and charts GPX files.", "reason": "selkeämpi",
                   "text": "Muotoiltu: Web app that loads and charts GPX files."}]
    texts = [u["text"] for s in res["sections"] for e in s["entries"] for u in e["units"]]
    assert "Web app that loads and charts GPX files." in texts


@pytest.mark.parametrize("sid,text,why", [
    ("S3E1B1", "Web app that loads GPX files, deployed on Kubernetes.", "kubernetes"),   # uusi tekniikka
    ("S3E1B1", "Web app that loads 500 GPX files.", "uusi luku"),
    ("S3E2B1", "Bot that plays chess, built with GitHub Actions.", "github actions"),     # toisen projektin fakta
    ("S2E1", "Senior Warehouse Lead | Firma Oy", "ei saa muotoilla"),                    # nimike
    ("S4B1", "**Information Technology, MSc** | ESAMK | 2023 – 2027", "ei saa muotoilla"),
    ("S5B1", "**Web:** React, JavaScript, Vue", "ei saa muotoilla"),
])
def test_rule_rejects_unsafe_rewrites(sid, text, why):
    cv = cvtailor.parse(CV)
    changes, rejected = cvtailor.apply_rewrites(cv, [{"source_id": sid, "text": text}])
    assert not changes and len(rejected) == 1
    assert why in " ".join(rejected[0]["reasons"]).lower()
    assert text not in cvtailor.full_text(cv)


def test_rejected_rewrite_becomes_rule_finding_for_tailor_and_panel():
    rw = [{"source_id": "S3E2B1", "text": "Bot that plays chess, built with GitHub Actions.", "reason": "x"}]
    p, _, llms = make({"tailor": [plan(rewrites=rw)], **all_ok()})
    res = p.run()
    assert any(f["category"] == "rule_rewrite" for f in res["panel"]["rule"])
    assert "RULE_FINDINGS" in llms["fact_checker"].calls[0]["system"]
    assert "built with GitHub Actions" not in json.dumps(res["sections"])


def test_finnish_requires_base():
    with pytest.raises(ValueError):
        panel.Panel(JOB, "", CV, None, "fi", lambda a: llm.FakeLLM(), lambda *a: None)


# ------------------------------------------------------------ tallennus ja SSE

@pytest.fixture
def log(tmp_path):
    return panel.RunLog(tmp_path / "runs.sqlite")


def test_runlog_events_and_interrupted_status(log):
    rid = log.create("https://x/1", "en")
    for i in range(3):
        log.emit(rid, "tick", f"viesti {i}", {"i": i})
    assert [e["seq"] for e in log.events(rid, after=1)] == [2, 3]
    assert log.get(rid)["status"] == "interrupted"     # ei käynnissä tässä prosessissa
    log.finish(rid, "approved", {"tailor": "fake"}, {"input": 10, "output": 5}, 0.01)
    assert log.list("https://x/1")[0]["status"] == "approved"


def test_sse_replays_from_last_event_id(log, monkeypatch):
    from server import app as srv
    rid = log.create("https://x/1", "en")
    for t in ("run_started", "tailor_started", "tailor_finished", "run_finished"):
        log.emit(rid, t, t)
    log.finish(rid, "approved")
    monkeypatch.setattr(srv, "_runlog", lambda: log)
    webapp.CFG["port"] = 8765
    c = TestClient(srv.app, base_url="http://127.0.0.1:8765")
    body = c.get(f"/api/cv/runs/{rid}/events", headers={"Last-Event-ID": "2"}).text
    assert "id: 2\n" not in body and "id: 3\n" in body and "id: 4\n" in body and "event: end" in body
    assert c.get(f"/api/cv/runs/{rid}").json()["run"]["status"] == "approved"
    assert c.get("/api/cv/runs/nope").status_code == 404


def test_background_run_saves_result_and_finishes(log, monkeypatch, tmp_path):
    from rag import service
    cvp = tmp_path / "cv.md"
    cvp.write_text(CV, encoding="utf-8")
    s = config.Settings(cv_path=cvp, db_path=tmp_path / "runs.sqlite", embed_backend="fake")
    fakes = {"tailor": llm.FakeLLM([plan()]), **{a: llm.FakeLLM([OK]) for a in panel.REVIEWERS}}
    monkeypatch.setattr(panel, "panel_llm", lambda s, a: fakes[a])
    monkeypatch.setattr(service, "store", lambda s: None)
    import rag.grounding as grounding
    monkeypatch.setattr(grounding, "find_job", lambda st, url: {**JOB, "url": url, "clean_text": "Docker"})
    saved = []
    rid = panel.start("https://x/1", "en", s, log, on_result=saved.append)
    for _ in range(100):
        if log.get(rid)["status"] != "interrupted" and rid not in panel.ACTIVE:
            break
        threading.Event().wait(0.05)
    run = log.get(rid)
    assert run["status"] == "approved", log.events(rid)
    assert saved and saved[0]["panel"]["run_id"] == rid
    assert log.events(rid)[-1]["type"] == "run_finished"


# ------------------------------------------------------------ oikea API (pytest -m live)

@pytest.mark.live
def test_live_fact_checker_rejects_fabrications():
    s = config.load()
    assert s.anthropic_api_key, "live-testi vaatii ANTHROPIC_API_KEY-avaimen"
    p = panel.Panel(JOB, "Haemme Kubernetes- ja Rust-osaajaa.", CV, None, "en", lambda a: panel.panel_llm(s.with_(llm_provider="claude"), a),
                    lambda *a: None)
    built = p.build(panel.TailorPlan(**plan()))
    text = p.render(built) + (
        "\n[S3E1B3] Led a team of five engineers building a Kubernetes platform in Rust. (rewritten)"
        "\n[S2E1] ### Senior Logistics Manager | Firma Oy (2021 – 2023)"
        "\n[S3E2B2] Built the chess bot's CI/CD pipeline with GitHub Actions and deployed it to production. (rewritten)")
    r = p.call("fact_checker", panel.prompt("fact_checker"),
               f"{p._posting()}\n\nSOURCE_FACTS:\n{cvtailor.outline(p.fresh()[1])}\n\nTAILORED_CV:\n{text}\n\nRULE_FINDINGS:\n(none)",
               panel.REVIEW_SCHEMA, panel.ReviewOutput)
    quotes = " ".join(f.quote.lower() + " " + f.issue.lower() for f in r.findings if f.severity == "blocking")
    assert r.verdict == "reject"
    assert "rust" in quotes or "kubernetes" in quotes
    assert "senior" in quotes or "manager" in quotes
    assert "chess" in quotes or "production" in quotes


def test_panel_rejects_third_person_finnish_summary():
    fi_md = CV.replace("Third-year IT student with DevOps skills", "Kolmannen vuoden opiskelija, rakennan DevOps-ratkaisuja")
    llms = {"tailor": llm.FakeLLM([plan(summary="Rakentaa Docker-ratkaisuja (Docker, Linux).")]),
            **{a: llm.FakeLLM([OK]) for a in panel.REVIEWERS}}
    p = panel.Panel(JOB, "Docker", CV, fi_md, "fi", lambda a: llms[a], lambda *a: None)
    res = p.run()
    assert res["summary"]["source"] == "original" and "rakennan" in res["summary"]["text"]
    assert any(f["category"] == "rule_summary" and "third person" in f["issue"] for f in res["panel"]["rule"])


# ------------------------------------------------------------ välimuisti (prompt caching)

def test_agents_share_byte_identical_cached_prefix():
    seen = {}

    class Spy(llm.FakeLLM):
        def complete_json(self, system, messages, schema, max_tokens=3000, on_start=None):
            seen.setdefault(self.name, []).append(system)
            return super().complete_json(system, messages, schema, max_tokens, on_start)
    llms = {a: Spy([plan()] if a == "tailor" else [OK], name=a) for a in ("tailor", *panel.REVIEWERS)}
    p = panel.Panel(JOB, "Haemme Docker- ja Kubernetes-osaajaa.", CV, None, "en", lambda a: llms[a],
                    lambda *x: None)
    assert p.run()["panel"]["status"] == "approved"
    ctx = seen["tailor"][0][0]
    assert "SOURCE_FACTS" in ctx and "JOB POSTING" in ctx
    revs = [seen[a][0] for a in panel.REVIEWERS]
    assert all(s[0] == ctx for s in revs)                 # sama alkuosa kuin räätälöijällä
    assert len({s[1] for s in revs}) == 1                 # sama CV-osa kaikilla arvioijilla
    assert len({s[2] for s in revs}) == len(revs)         # oma ohje viimeisenä


def test_claude_args_mark_all_but_last_segment_for_cache():
    c = llm.ClaudeLLM.__new__(llm.ClaudeLLM)
    c.model, c.effort = "m", "low"
    blocks = c._args(["a", "b", "c"], [], 10)["system"]
    assert [("cache_control" in b) for b in blocks] == [True, True, False]
    assert c._args("plain", [], 10)["system"] == "plain"
    assert llm.system_text(["a", "", "b"]) == "a\n\nb"


def test_cost_is_priced_per_model():
    class S:
        panel_price_in = panel_price_out = 0.0
    by = {"claude:claude-fable-5-1": {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0},
          "claude:claude-haiku-5-5": {"input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000, "cache_write": 0}}
    assert panel.cost_of({}, S, by) == round(10.0 + 0.10 + 0.50 + 0.01, 4)
    assert panel.cost_of({"input": 1}, S) is None                         # tuntematon hinta: ei arvausta
    assert panel.cost_of({}, S, {"claude:claude-x": {"input": 1}}) is None
