from rag import llm, review

CONTACT = {"email": "aino@example.com", "phone": "040 123 4567"}


def F(label, value="", **kw):
    return {"label": label, "value": value, **kw}


def fields(*xs):
    return review._clean_fields([{**x, "i": i} for i, x in enumerate(xs)])


def test_rules_find_form_problems():
    fs = fields(F("Linkki Linkedin -profiiliin", "linkedin.com/in/aino"), F("Sukunimi", "", required=True),
                F("Olen lukenut ja ymmärtänyt tietosuojaselosteen.", type="checkbox", checked=False, required=True),
                F("Sähköposti", "toinen@example.com"), F("Puhelinnumero", "+358 40 123 4567"),
                F("Hakemusteksti", "x" * 50, maxlength="40"), F("Postinumero", "651", invalid=True, error="Virheellinen muoto"))
    r = review.rule_checks(fs, CONTACT)
    must = {x["field"]: x for x in r["must_fix"]}
    assert must["Linkki Linkedin -profiiliin"]["fix"] == "https://linkedin.com/in/aino"
    assert "Sukunimi" in must and "Olen lukenut ja ymmärtänyt tietosuojaselosteen." in must
    assert "50 merkkiä, raja on 40" in must["Hakemusteksti"]["issue"]
    assert "Virheellinen muoto" in must["Postinumero"]["issue"]
    check = {x["field"] for x in r["check"]}
    assert "Sähköposti" in check and "Puhelinnumero" not in check     # +358 40 = 040: sama numero


def test_salary_is_never_reviewed():
    fs = fields(F("Palkkatoive (euro/kk)", "", required=True))
    assert review.rule_checks(fs, CONTACT) == {"must_fix": [], "check": []}
    assert "[hakijan oma tieto, ei arvioida]" in review._form_block(fs)
    raw = {"must_fix": [{"field": "Palkkatoive (euro/kk)", "kind": "muoto", "issue": "Liian matala", "quote": "",
                         "fix": "2500", "text": ""}], "improve": [], "check": []}
    out = review.vet(raw, fs, lambda t: {"flagged": 0})
    assert out["must_fix"] == [] and out["dropped"] == 1


def test_language_fix_needs_real_quote_and_suggestions_are_fact_checked():
    fs = fields(F("Hakemusteksti", "Uskon olevan hyötyä vianetsinnässä."))
    raw = {"must_fix": [
        {"field": "Hakemusteksti", "kind": "kieli", "issue": "Sana puuttuu", "quote": "Uskon olevan hyötyä",
         "fix": "Uskon tästä olevan hyötyä", "text": ""},
        {"field": "Hakemusteksti", "kind": "kieli", "issue": "Keksitty virhe", "quote": "lause jota ei ole",
         "fix": "korjaus", "text": ""}],
        "improve": [
        {"field": "Hakemusteksti", "kind": "sisältö", "issue": "Lisää asiakaspalvelu", "quote": "", "fix": "",
         "text": "Palvelin asiakkaita Airprolla."},
        {"field": "Hakemusteksti", "kind": "sisältö", "issue": "Lisää Kubernetes", "quote": "", "fix": "",
         "text": "Olen ylläpitänyt Kubernetes-klusteria kolme vuotta."}], "check": []}
    verify_fn = lambda t: {"flagged": int("Kubernetes" in t)}   # noqa: E731
    out = review.vet(raw, fs, verify_fn)
    assert [x["fix"] for x in out["must_fix"]] == ["Uskon tästä olevan hyötyä"]
    assert out["dropped"] == 1
    texts = {x["issue"].split(" (")[0]: x["text"] for x in out["improve"]}
    assert texts["Lisää asiakaspalvelu"] == "Palvelin asiakkaita Airprolla."
    assert texts["Lisää Kubernetes"] == ""                          # keksitty väite poistettiin ehdotuksesta


def test_text_checks_flag_unsupported_claims_and_style():
    long = "Olen erittäin motivoitunut ja ylläpitänyt FastAPI-taustapalvelua. " * 3
    fs = fields(F("Hakemusteksti", long))
    verify_fn = lambda t: {"claims": [{"text": "ylläpitänyt Kubernetesia", "status": "flag", "reasons": ["ei CV:ssä"]}]}  # noqa: E731
    r = review.text_checks(fs, verify_fn, "fi")
    assert r["check"] and "Kubernetesia" in r["check"][0]["issue"]
    style = r["improve"][0]["issue"]
    assert "erittäin motivoitunut" in style and "taustapalvelua → backend" in style


def test_llm_review_prompt_contains_form_and_rules():
    fake = llm.FakeLLM([{"verdict": "valmis", "summary": "ok", "must_fix": [], "improve": [], "check": []}])
    fs = fields(F("Hakemusteksti", "Teksti"), F("Palkkatoive", "2000"))
    review.llm_review(fs, "Ilmoitus", "CV", {"must_fix": [{"field": "Sukunimi", "issue": "tyhjä"}]}, fake)
    msg = fake.calls[0]["messages"][0]["content"]
    assert "Hakemusteksti: Teksti" in msg and "2000" not in msg and "Sukunimi: tyhjä" in msg
    assert "Palkkatoiveeseen ei oteta kantaa" in fake.calls[0]["system"]


def test_must_fix_only_for_real_errors_and_own_phrase_kept():
    fs = fields(F("Hakemusteksti", "Olen innokas oppimaan näistä lisää. Teksti jatkuu."))
    raw = {"must_fix": [
        {"field": "Hakemusteksti", "kind": "kieli", "issue": "Täytefraasi", "quote": "Olen innokas oppimaan näistä lisää.",
         "fix": "Haluan oppia.", "text": ""},
        {"field": "Hakemusteksti", "kind": "sisältö", "issue": "Lisää esimerkki", "quote": "", "fix": "", "text": ""}],
        "improve": [], "check": [{"field": "Hakemusteksti", "kind": "", "issue": "Ristiriita CV:n kanssa", "quote": "Teksti",
                                 "fix": "", "text": ""}]}
    out = review.vet(raw, fs, lambda t: {"flagged": 0})
    assert out["must_fix"] == [] and out["dropped"] == 1               # oma ilmaus jää rauhaan
    assert [x["issue"] for x in out["improve"]] == ["Lisää esimerkki"]  # sisältö ei ole pakollinen korjaus
    assert out["check"][0]["quote"] == ""                              # tyhjä korjaus: ei lainausta


def test_dates_do_not_split_sentences_and_amk_is_known():
    from rag.verify import _present, sentences
    s = sentences("Pystyn aloittamaan 11.1.2027 ja työskentelemään kokoaikaisesti. Käytän Node.js:ää.")
    assert s[0][2] == "Pystyn aloittamaan 11.1.2027 ja työskentelemään kokoaikaisesti."
    assert _present("AMK", "tietotekniikka, insinööri | vaasan ammattikorkeakoulu (vamk)")


def test_review_endpoint_rejects_wrong_key(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server import app as srv, assist
    monkeypatch.setattr(assist, "KEY_FILE", tmp_path / "extension.key")
    import webapp
    webapp.CFG["port"] = 8765
    c = TestClient(srv.app, base_url="http://127.0.0.1:8765")
    h = {"X-Requested-With": "jobhunter"}
    for key in ("wrong", "väärä".encode()):   # ei-ASCII tavuina, kuten curl lähettää
        assert c.post("/api/assist/review", json={"url": "u", "fields": []}, headers={**h, "X-Jobhunter-Key": key}).status_code == 401
    ok = {**h, "X-Jobhunter-Key": assist.extension_key()}
    assert c.post("/api/assist/review", json={"fields": []}, headers=ok).status_code == 400   # hakemus valitsematta


def test_review_uses_review_model():
    from rag.config import Settings
    from rag.llm import get_llm
    s = Settings(llm_provider="claude", anthropic_api_key="x", claude_model="claude-haiku-5-5",
                 claude_model_writing="claude-sonnet-5-5")
    assert get_llm(s, "review").name.endswith("claude-opus-5-5")
    assert get_llm(s.with_(review_model=""), "review").name.endswith("claude-sonnet-5-5")
    assert get_llm(s, "writing").name.endswith("claude-sonnet-5-5")


def test_form_block_shows_room_left():
    fs = fields(F("Aiempi työkokemus", "x" * 550, maxlength="600"))
    assert "[merkkiraja 600, käytetty 550]" in review._form_block(fs)
