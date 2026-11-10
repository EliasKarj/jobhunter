import datetime as dt

import numpy as np
import pytest

from rag import chat, explain, llm, package, verify
from rag.config import Settings
from rag.grounding import Grounding
from rag.profile import ProfileChunk
from rag.retrieve import Filters, Hit, parse_filters, search_terms
from server import tracking

# ------------------------------------------------------------ viitteet (chat)


def _sources(n=3):
    titles = ["Commissioning Manager", "ICT-asiantuntija, vt.13", "Metro Construction EHS Manager - Finland"]
    return [{"n": i, "job_id": f"duunitori:{i}", "title": titles[i - 1], "company": f"Firma {i}", "city": "Vaasa",
             "url": f"https://x/{i}", "posted": "2026-10-30", "source": "duunitori", "sim": 0.5,
             "chunks": [{"id": i, "text": f"Vaatimuksena Azure platform -osaaminen ja tiimityö {i}.", "start": 0,
                         "end": 10, "why": "sana"}]} for i in range(1, n + 1)]


def test_check_citations_finds_cited_invalid_and_uncited():
    a = ("Wapice hakee .NET-kehittäjää Vaasaan [2]. Verohallinto edellyttää Azurea [1][7].\n"
         "Lisäksi monessa paikassa arvostetaan ruotsin kielen taitoa ilman lähdettä.")
    r = chat.check_citations(a, 3)
    assert r["cited"] == [1, 2]
    assert r["invalid"] == [7]
    assert r["uncited"] == ["Lisäksi monessa paikassa arvostetaan ruotsin kielen taitoa ilman lähdettä."]
    assert "[7]" not in chat.strip_invalid(a, 3) and "[1]" in chat.strip_invalid(a, 3)


def test_not_found_sentence_is_not_flagged_as_uncited():
    assert chat.check_citations("En löytänyt ilmoituksia, jotka vaativat COBOLia Vaasassa.", 3)["uncited"] == []


def test_fix_citations_corrects_wrong_number_by_title():
    a = "* **Commissioning Manager, Microsoft [3]**: mainitsee Azure platformin"
    fixed, issues = chat.fix_citations(a, _sources())
    assert "[1]" in fixed and "[3]" not in fixed
    assert issues[0]["kind"] == "fixed" and issues[0]["from"] == 3 and issues[0]["to"] == 1


def test_fix_citations_flags_quote_missing_from_source():
    a = 'ICT-asiantuntija vaatii "viiden vuoden kokemusta Kubernetesista" [2].'
    _, issues = chat.fix_citations(a, _sources())
    assert any(i["kind"] == "quote_not_found" for i in issues)
    _, ok = chat.fix_citations('Ilmoitus mainitsee "Azure platform -osaaminen ja tiimityö 2" [2].', _sources())
    assert not any(i["kind"] == "quote_not_found" for i in ok)
    _, ell = chat.fix_citations('Ilmoitus: "Vaatimuksena Azure ... ja tiimityö 2" [2].', _sources())   # väliin jätetty kohta
    assert not any(i["kind"] == "quote_not_found" for i in ell)


def _hit(sim=0.6, why="merkitys"):
    return Hit("duunitori:1", "Junior Developer", "Firma", "Vaasa", "https://x/1", "2026-10-30", "duunitori", 0.03, sim,
               [{"id": 1, "text": "Haemme junior-kehittäjää. Azure on plussaa.", "start": 0, "end": 40, "why": why}])


def test_answer_without_relevant_hits_does_not_call_model():
    fake = llm.FakeLLM(["tätä ei saa käyttää"])
    evs = list(chat.answer("COBOL Utsjoella?", [], fake, lambda q: (Filters(), [_hit(sim=0.2)])))
    assert fake.calls == []
    done = evs[-1]
    assert done["type"] == "done" and done["found"] is False
    assert chat.NOT_FOUND in "".join(e["text"] for e in evs if e["type"] == "delta")


def test_answer_streams_and_returns_sources_and_citations():
    fake = llm.FakeLLM(["Firma hakee junior-kehittäjää, Azure on plussaa [1]."])
    evs = list(chat.answer("junior Azure?", [], fake, lambda q: (Filters(cities=["Vaasa"]), [_hit()])))
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "filters" and "sources" in kinds and kinds[-1] == "done"
    assert sum(k == "delta" for k in kinds) > 1   # streamattu paloina
    assert evs[-1]["cited"] == [1] and evs[-1]["found"]
    assert "[1] Junior Developer" in fake.calls[0]["messages"][-1]["content"]   # lähde kontekstissa


def test_answer_reports_model_error():
    fake = llm.FakeLLM([llm.LLMError("Ollama ei vastaa")])
    evs = list(chat.answer("x", [], fake, lambda q: (Filters(), [_hit()])))
    assert evs[-1] == {"type": "error", "message": "Ollama ei vastaa"}


# ------------------------------------------------------------ suodattimet

CITIES = ["Helsinki", "Tampere", "Vaasa", "Espoo", "Turku"]


def test_parse_filters_city_inflection_negation_and_date():
    f = parse_filters("mitkä Vaasan paikat vaativat Azurea mutta eivät ruotsia?", CITIES, today=dt.date(2026, 11, 3))
    assert f.cities == ["Vaasa"] and f.exclude == ["ruotsia"] and f.since is None
    assert search_terms("mitkä Vaasan paikat vaativat Azurea mutta eivät ruotsia?", f) == ["azurea"]
    f = parse_filters("Python-töitä Helsingissä tai Turussa viime viikolla", CITIES, today=dt.date(2026, 11, 3))
    assert sorted(f.cities) == ["Helsinki", "Turku"] and f.since == "2026-10-27"
    assert parse_filters("jobs posted in the last 3 days", CITIES, today=dt.date(2026, 11, 3)).since == "2026-10-31"
    assert parse_filters("mitä DevOps-paikkoja on?", CITIES).describe() == []


# ------------------------------------------------------------ väitetarkistus

PROFILE = {"P1": "Hands-on DevOps skills (containers, Linux, CI/CD). Background in the security sector and as a "
                 "ESIM peacekeeper. Python, PostgreSQL, Microsoft Azure. Graduating in summer 2027.",
           "P2": "Lenkkiloki: JavaScript, Canvas, GitHub Actions"}
AD = {"A1": "Etsimme kehittäjää, jolla on kokemusta Kubernetesista ja SAP-järjestelmistä."}


def _status(sentence, **kw):
    c = verify.check(sentence, PROFILE, AD, **kw)[0]
    return c.status, c.reasons


def test_flags_invented_skill_number_and_concept():
    st, why = _status("Minulla on kolmen vuoden kokemus Kubernetesista ja Terraformista.")
    assert st == "flag" and any("Kubernetes" in r for r in why) and any("Terraform" in r for r in why)
    st, why = _status("Olen työskennellyt 5 vuotta Python-kehittäjänä.")
    assert st == "flag" and any("luku 5" in r for r in why)
    st, why = _status("Olen syventänyt osaamistani koneoppimisen parissa.")
    assert st == "flag" and any("koneoppi" in r for r in why)
    st, _ = _status("Olen käyttänyt Azure DevOpsia projekteissani.")
    assert st == "flag"   # Azure ja DevOps erikseen eivät todista Azure DevOps -työkalua


def test_accepts_grounded_claims_with_finnish_inflection():
    for s in ["Yhdistän DevOps-taidot ja Linux-osaamisen.", "Kokemukseni ESIM-rauhanturvaajana kehitti minua.",
              "Olen käyttänyt Pythonia ja PostgreSQL:ää omissa projekteissani.", "Valmistun kesällä 2027."]:
        assert _status(s)[0] == "ok", s


def test_ad_terms_allowed_only_outside_first_person():
    assert _status("Tehtävässä käytetään Kubernetesia ja SAP-järjestelmiä.")[0] == "ok"
    assert _status("Osaan käyttää Kubernetesia.")[0] == "flag"


def test_motivation_is_not_a_factual_claim():
    c = verify.check("Olen erityisen kiinnostunut tiimin toimintatapojen kehittämisestä.", PROFILE, AD,
                     embed=lambda xs: np.array([[1.0, 0.0]] * len(xs), dtype=np.float32),
                     vectors={"P1": np.array([0.0, 1.0], dtype=np.float32)})[0]
    assert c.kind == "motivation" and c.status == "ok"


def test_interest_in_posting_tech_is_not_a_skill_claim_but_mixed_claim_is():
    assert _status("Olen valmis oppimaan Kubernetesia ja SAP-järjestelmiä.")[0] == "ok"
    assert _status("Olen kiinnostunut tästä, ja minulla on kokemusta Kubernetesista.")[0] == "flag"


def test_lowercase_tool_names_from_posting_lists_are_checked():
    ad = ("Kokemusta Rust-ohjelmoinnista, esim. axum, sqlx ja egui. Hyvät vuorovaikutustaidot, tiimityö ja "
          "oma-aloitteisuus. Edut: lomamökit, autoetu ja liikuntaetu.")
    terms = verify.ad_terms(ad)
    assert {"axum", "sqlx", "egui"} <= set(terms) and not {"lomamökit", "autoetu", "tiimityö"} & set(terms)
    c = verify.check("Olen käyttänyt PostgreSQL:ää ja sqlx-kirjastoa.", PROFILE, {"A1": ad}, extra_terms=terms)[0]
    assert c.status == "flag" and "sqlx" in c.reasons[0]
    assert verify.check("Olen valmis opettelemaan axumia.", PROFILE, {"A1": ad}, extra_terms=terms)[0].status == "ok"
    # oppimiskohde ei ole väite, vaikka lauseessa on "osaamistani"; saman lauseen alun osaamisväite tarkistetaan
    assert verify.check("Olen valmis oppimaan Rustia ja axumia ja soveltamaan osaamistani.", PROFILE, {"A1": ad},
                        extra_terms=terms)[0].status == "ok"
    assert verify.check("Minulla ei ole vielä kokemusta Rust-ohjelmoinnista.", PROFILE, {"A1": ad})[0].status == "ok"
    c = verify.check("Osaan Kubernetesia ja olen valmis oppimaan Rustia.", PROFILE, {"A1": ad}, extra_terms=terms)[0]
    assert c.status == "flag" and len(c.reasons) == 1 and "Kubernetes" in c.reasons[0]


def test_placeholders_need_checking_not_flagging():
    st, why = _status("Olen käytettävissä [Aloituspäivämäärä] alkaen ja haen [Harjoitteluun/Junioriksi].")
    assert st == "check" and "täytettävän" in why[0]


def test_explain_recovers_refs_written_only_in_text():
    g = _grounding()
    r = explain.validate({"strengths": [{"text": "CI/CD-osaaminen (A1) (P1)", "ad": [], "profile": []}],
                          "gaps": [{"text": "Ei SAP-kokemusta (A1, must)", "ad": [], "severity": "must"}],
                          "verdict": "hyvä", "summary": ""}, g)
    assert r["strengths"] == [{"text": "CI/CD-osaaminen", "ad": ["A1"], "profile": ["P1"]}]
    assert r["gaps"][0] == {"text": "Ei SAP-kokemusta", "ad": ["A1"], "severity": "must"}


def test_weak_support_needs_checking():
    c = verify.check("Olen vastannut asiakkaiden koulutuksista ja tuesta pitkään.", PROFILE, AD,
                     embed=lambda xs: np.array([[1.0, 0.0]] * len(xs), dtype=np.float32),
                     vectors={"P1": np.array([0.0, 1.0], dtype=np.float32), "A1": np.array([1.0, 0.0], dtype=np.float32)})[0]
    assert c.status == "check" and c.source["id"] == "P1"   # minä-väitteen lähde haetaan vain profiilista


def _grounding():
    job = {"job_id": "duunitori:1", "url": "https://x/1", "title": "Junior DevOps Engineer", "company": "Firma Oy",
           "city": "Vaasa", "lang": "fi", "content_hash": "h1", "clean_text": AD["A1"]}
    prof = [ProfileChunk("cv", "cv.md#Profile", PROFILE["P1"]), ProfileChunk("cv", "cv.md#Projects", PROFILE["P2"])]
    return Grounding(job, {"A1": {"id": None, "text": AD["A1"], "start": 0, "end": len(AD["A1"])}},
                     {"P1": prof[0], "P2": prof[1]})


def test_package_flags_invented_claims_and_leaves_salary_to_user():
    fake = llm.FakeLLM([{"cover_letter": "Hei! Olen DevOps-osaaja ja käytän Linuxia päivittäin. "
                                         "Minulla on 4 vuoden kokemus Kubernetesista. [P1]\n\nTerveisin Aino",
                         "why_apply": "Haluan oppia lisää Firma Oy:n tiimissä.",
                         "strengths": "Paineensietokyky ESIM-palveluksesta ja CI/CD-osaaminen."}])
    p = package.build_package(_grounding(), fake, "fi", "Aino", cv_full="")
    secs = {s["key"]: s for s in p["sections"]}
    assert secs["salary"]["text"] == package.SALARY_PLACEHOLDER["fi"]
    assert "Älä kirjoita palkkatoivetta" in fake.calls[0]["system"]   # mallia ei pyydetä palkkatoiveeseen
    cl = secs["cover_letter"]["verify"]
    assert cl["flagged"] == 1
    flagged = [c for c in cl["claims"] if c["status"] == "flag"][0]
    assert "Kubernetes" in flagged["text"] and secs["cover_letter"]["text"][flagged["start"]:flagged["end"]] == flagged["text"]
    assert "[P1]" not in secs["cover_letter"]["text"]
    assert secs["strengths"]["verify"]["flagged"] == 0


def test_posting_title_does_not_ground_a_skill_claim():
    g = _grounding()
    g.job["title"] = "Junioriohjelmistokehittäjä — Rust ja tekoälysovellukset"
    res = package.verify_section("Olen hankkinut käytännön kokemusta Rust-ohjelmoinnista omissa projekteissani.",
                                 g, cv_full="", name="Aino")
    assert res["flagged"] == 1 and "Rust" in res["claims"][0]["reasons"][0]
    ok = package.verify_section("Tehtävässä kehitetään Rust-sovelluksia.", g, cv_full="", name="Aino")
    assert ok["flagged"] == 0   # tehtävän kuvaus saa mainita otsikon tekniikan


def test_language_follows_posting_unless_chosen():
    g = _grounding()
    assert package.choose_lang(g, None) == "fi" and package.choose_lang(g, "en") == "en"
    g.job["lang"] = "en"
    assert package.choose_lang(g, None) == "en"


# ------------------------------------------------------------ sopivuusselitys


def test_explain_validates_refs_and_uses_cache():
    from rag.store import Store
    store = Store(":memory:")
    g = _grounding()
    raw = {"strengths": [{"text": "DevOps-tausta (A1), profiilissa CI/CD (P1).", "ad": ["A1"], "profile": ["P1", "P9"]},
                         {"text": "Kubernetes-osaaminen", "ad": ["A1"], "profile": []}],
           "gaps": [{"text": "SAP-kokemus puuttuu", "ad": ["A1", "A7"], "severity": "must"}],
           "verdict": "kohtalainen", "summary": "Hyvä pohja."}
    fake = llm.FakeLLM([raw])
    r = explain.explain(g, fake, store)
    assert r["strengths"] == [{"text": "DevOps-tausta, profiilissa CI/CD.", "ad": ["A1"], "profile": ["P1"]}]
    assert r["dropped"][0]["text"] == "Kubernetes-osaaminen"     # vahvuus ilman profiililähdettä
    assert r["gaps"][0]["ad"] == ["A1"] and set(r["refs"]) == {"A1", "P1"}
    again = explain.explain(g, fake, store)
    assert again["cached"] and len(fake.calls) == 1
    explain.explain(g, llm.FakeLLM([raw]), store, refresh=True)   # pakotettu uusi generointi toimii


# ------------------------------------------------------------ seuranta


def _app(status="interested", hist=None, at="2026-10-01T10:00:00"):
    return {"id": "a1", "title": "Dev", "status": status, "history": hist or [{"status": status, "at": at}]}


def test_tracking_allows_forward_and_undo_but_not_jumps():
    a = _app()
    tracking.transition(a, "drafting", "2026-10-02T10:00:00")
    tracking.transition(a, "applied", "2026-10-03T10:00:00")
    assert a["applied_date"] == "2026-10-03"
    assert tracking.can_transition(a, "drafting")          # kumoa: takaisin edelliseen
    assert not tracking.can_transition(a, "interested")    # kaksi askelta taaksepäin ei
    tracking.transition(a, "no_response")
    tracking.transition(a, "interview")                    # vastaus tuli myöhässä
    with pytest.raises(ValueError):
        tracking.transition(_app("rejected"), "offer")
    with pytest.raises(ValueError):
        tracking.transition(_app("interested"), "interview")
    assert tracking.normalize("planned") == "interested" and tracking.normalize("withdrawn") == "withdrawn"


def test_follow_up_reminder_after_two_weeks():
    applied = _app("applied", at="2026-10-20T09:00:00")
    assert tracking.reminders([applied], dt.date(2026, 11, 2)) == []
    r = tracking.reminders([applied], dt.date(2026, 11, 3))
    assert r[0]["kind"] == "follow_up" and r[0]["days"] == 14
    interview = _app("interview", at="2026-09-01T09:00:00")
    assert tracking.reminders([interview], dt.date(2026, 11, 3)) == []   # vain haetuille


# ------------------------------------------------------------ provider


def test_provider_switch_and_errors():
    assert isinstance(llm.get_llm(Settings(llm_provider="ollama", ollama_model="gemma3:12b")), llm.OllamaLLM)
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        llm.get_llm(Settings(llm_provider="claude"))
    c = llm.get_llm(Settings(llm_provider="claude", anthropic_api_key="sk-test", claude_model="claude-haiku-5-5",
                             claude_model_writing="claude-sonnet-5-5"), "writing")
    assert isinstance(c, llm.ClaudeLLM) and c.model == "claude-sonnet-5-5"
    assert llm.get_llm(Settings(llm_provider="claude", anthropic_api_key="k"), "chat").model == "claude-haiku-5-5"
    with pytest.raises(ValueError):
        Settings(llm_provider="gpt").validate()


def test_ollama_unreachable_gives_readable_error():
    o = llm.OllamaLLM("gemma3:12b", "http://127.0.0.1:9", timeout=2)
    with pytest.raises(llm.LLMError, match="Ollama ei vastaa"):
        list(o.stream("s", [{"role": "user", "content": "hei"}]))


def test_settings_from_env(monkeypatch):
    from rag import config
    monkeypatch.setattr(config, "_load_env", lambda: None)
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-5-5")
    s = config.load()
    assert s.llm_provider == "claude" and "anthropic_api_key" not in s.describe()


def test_free_form_application_field_gets_letter_length_and_finnish_wording():
    fake = llm.FakeLLM([{"answer": "Haen tehtävään, koska CI/CD kiinnostaa."}] * 2)
    package.answer_question(_grounding(), fake, "Vapaamuotoinen hakemuksesi tehtävään", "fi", "Aino", cv_full="",
                            fi_cv="Rakensin CI/CD-putken GitHub Actionsilla.")
    sys_, user = fake.calls[0]["system"], fake.calls[0]["messages"][0]["content"]
    assert "180-280 sanaa" in sys_ and "SUJUVUUS" in sys_
    assert "SUOMENKIELINEN CV" in user and "GitHub Actionsilla" in user
    package.answer_question(_grounding(), fake, "Mikä on vahvuutesi?", "fi", "Aino", cv_full="")
    assert "40-100 sanaa" in fake.calls[1]["system"] and "SUOMENKIELINEN CV" not in fake.calls[1]["messages"][0]["content"]


def test_dynamic_analysis_is_a_term_not_a_cliche():
    from rag import style
    from rag.claims import cliches
    assert style.find("En ole tehnyt staattista tai dynaamista analyysia.", "fi") == []
    assert style.find("Toimin dynaamisessa tiimissä.", "fi") != []
    assert style.find("I have not done dynamic analysis.", "en") == []
    assert cliches("staattisesti tai dynaamisesti", "") == [] and cliches("dynaamisessa ympäristössä", "") != []


def test_promises_on_applicants_behalf_are_flagged():
    g = _grounding()
    for t in ("Suostun henkilöturvallisuusselvityksen tekemiseen.", "Olen kirjoilla ESAMKissa koko harjoittelun ajan.",
              "Opin parhaiten rakentamalla itse."):
        assert package.verify_section(t, g, cv_full="", name="Aino")["flagged"] == 1, t


def test_answer_sees_other_questions_and_written_answers():
    fake = llm.FakeLLM([{"answer": "Haen tehtävään."}])
    package.answer_question(_grounding(), fake, "Vapaamuotoinen hakemuksesi tehtävään", "fi", "Aino", cv_full="",
                            others=["Mitä projekteja olet tehnyt?"],
                            written=[{"question": "Mitä projekteja olet tehnyt?", "answer": "Verkkolabra-verkkolaboratorio."},
                                     {"question": "tyhjä", "answer": "  "}])
    user = fake.calls[0]["messages"][0]["content"]
    assert "LOMAKKEEN MUUT KYSYMYKSET" in user and "Mitä projekteja olet tehnyt?" in user
    assert "JO KIRJOITETUT VASTAUKSET" in user and "Verkkolabra-verkkolaboratorio." in user and "[tyhjä]" not in user
    assert "Älä toista" in user
    assert package.form_context("fi", [], []) == ""


def test_combine_self_description_is_a_style_issue_but_technical_use_is_not():
    from rag import style
    assert style.find("Yhdistän käytännön DevOps-osaamisen full-stack-kehitykseen.", "fi")
    assert style.find("Hän yhdistää full-stack-osaamisen ja DevOpsin.", "fi")
    assert not style.find("Kysely yhdistää kaksi taulua ja yhdisti datan raportiksi.", "fi")
    assert style.find("I combine hands-on DevOps skills with full-stack development.", "en")
    assert style.find("A student combining practical cloud experience with web work.", "en")
    assert not style.find("The query combines two tables.", "en")


def test_spelling_variants_and_full_stack_cover_front_and_back_end():
    from rag.verify import _present
    cv = "teen full-stack-kehitystä. web ja backend: react"
    assert all(_present(t, cv) for t in ("back end", "Back-end", "backend", "front end", "frontend", "Full stack"))
    assert not _present("front end", "web ja backend: react")          # ilman full-stackia front end ei löydy
    assert not _present("Kubernetes", cv)


def test_posting_asking_for_one_project_gets_one_with_one_retry():
    g = _grounding()
    g.ad["A1"]["text"] += " Lähetä lyhyt hakemus. Kerro yhdestä rakentamastasi ratkaisusta: mitä teit itse ja mitä opit."
    cv = "## Projects\n### Verkkolabra – lab\n- Docker.\n### Jobhunter – search\n- Python.\n"
    fake = llm.FakeLLM([{"answer": "Verkkolabra ja Jobhunter ovat projektejani."}, {"answer": "Verkkolabraissa rakensin Docker-verkon."}])
    r = package.answer_question(g, fake, "Vapaamuotoinen hakemuksesi tehtävään", "fi", "Aino", cv_full=cv)
    assert "Kerro yhdestä rakentamastasi ratkaisusta" in fake.calls[0]["system"] and "KERRO VAIN YHDESTÄ" in fake.calls[0]["system"]
    assert len(fake.calls) == 2 and "mainitaan 2 projektia" in fake.calls[1]["messages"][-1]["content"]
    assert r["text"] == "Verkkolabraissa rakensin Docker-verkon." and not any("yhtä projektia" in x for x in r["reasons"])


def test_application_instructions_not_forced_on_unrelated_questions():
    g = _grounding()
    g.ad["A1"]["text"] += " Kerro yhdestä rakentamastasi ratkaisusta."
    fake = llm.FakeLLM([{"answer": "Python ja SQL."}])
    package.answer_question(g, fake, "Mitä ohjelmointikieliä hallitset?", "fi", "Aino", cv_full="")
    assert "HAKUOHJEET" not in fake.calls[0]["system"]
