from rag import claims, cvtailor as ct, panel
from tests.test_panel import CV, OK, make, plan

SRC = "Web app that loads GPX files. Set up CI/CD with GitHub Actions. Third-year IT student."


def test_inferred_claims_flagged_only_when_source_lacks_them():
    assert set(claims.unsupported_claims("Fast learner who led the team and improved performance.", SRC)) == {"led", "improv", "fast learner"}
    assert claims.unsupported_claims("Built apps, demonstrating a focus on data.", SRC) == ["demonstrat"]
    assert claims.unsupported_claims("Rakensin sovelluksen ja paransin sen nopeutta.", SRC) == ["paransi"]
    # lähde sanoo saman: sallittu
    assert claims.unsupported_claims("Improved CI speed.", SRC + " Improved CI speed by caching.") == []
    assert claims.unsupported_claims("Web app for GPX files with CI/CD (GitHub Actions).", SRC) == []


def test_rewrite_with_inferred_claim_rejected():
    ctx = "Web app that loads GPX files."
    r = ct.check_rewrite("Web app that loads GPX files.", "Production-grade web app that loads GPX files.", ctx)
    assert any("production" in x for x in r)
    assert not ct.check_rewrite("Web app that loads GPX files.", "Web app for loading GPX files.", ctx)


def test_summary_with_inferred_trait_falls_back_to_original():
    s = "Third-year IT student and fast learner with DevOps skills (Docker, Linux)."
    flags = ct.check_summary(s, ct.parse(CV))
    assert any("fast learner" in r for f in flags for r in f["reasons"])


def test_novel_words_go_to_fact_checker():
    p, _, llms = make({"tailor": [plan(summary="Third-year IT student with DevOps skills (Docker, Linux), very curious.")],
                       **{a: [OK] for a in panel.REVIEWERS}})
    p.run()
    user = llms["fact_checker"].calls[0]["messages"][0]["content"] if isinstance(llms["fact_checker"].calls[0], dict) \
        else str(llms["fact_checker"].calls[0])
    assert "NEW_WORDS" in user and "curious" in user


def test_cliches_flagged_unless_candidate_wrote_them():
    from rag.claims import cliches
    assert cliches("Built a RAG app, demonstrating a focus on data.", "Built a RAG app.") == ["demonstrating a"]
    assert cliches("Olen intohimoinen ohjelmoija nykypäivän maailmassa.", "") == ["intohimoinen", "nykypäivän"]
    assert cliches("Passionate about Rust.", "I am passionate about Rust.") == []    # hakijan oma sanavalinta
    assert cliches("Configured elevated privileges and navigation.", "") == []      # tekninen sana ei ole fraasi
    assert cliches("Tein sen \u2014 nopeasti.", "") == ["\u2014"]


def test_rewrite_with_cliche_is_rejected():
    from rag.cvtailor import check_rewrite
    orig = "Built a job search assistant with RAG."
    assert any("tekoälyn" in r for r in check_rewrite(orig, orig + " Leveraging RAG seamlessly.", orig))
