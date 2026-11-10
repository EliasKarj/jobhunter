from server import tracking



def test_outcomes_group_sent_applications_by_cv_kind():
    def app(url, *sts, applied="2026-10-01"):
        return {"url": url, "status": sts[-1], "applied_date": applied, "history": [{"status": s} for s in sts]}
    apps = [app("a", "applied", "interview"), app("b", "applied", "rejected"), app("c", "applied"),
            app("d", "interested", applied="")]                       # ei lähetetty: ei mukana
    cvs = {"a": {"fi": {"panel": {"status": "approved", "screening": {"coverage": {"score": 0.8}}}}},
           "b": {"en": {"panel": {"status": "approved", "screening": {"coverage": {"score": 0.6}}}}},
           "c": {"en": {"summary": {}}}, "d": {"en": {}}}
    out = {g["kind"]: g for g in tracking.outcomes(apps, cvs)}
    assert set(out) == {"panel", "tailored"}
    assert out["panel"]["applied"] == 2 and out["panel"]["responded"] == 2 and out["panel"]["interview"] == 1
    assert out["panel"]["interview_rate"] == 50 and out["panel"]["coverage"] == 0.7
    assert out["tailored"]["applied"] == 1 and out["tailored"]["coverage"] is None
