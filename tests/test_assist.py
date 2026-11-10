import pytest
from fastapi.testclient import TestClient

import webapp
from rag import llm, package
from server import app as srv
from server import assist, drafts
from tests.test_generative import _grounding

CV = "# Matti Meikäläinen\n\n**Student**\nVaasa, Finland | matti@example.com | 040 123 4567\n\n## Profile\n\nText\n"
ANSWERS = [{"key": "salary", "label": "Palkkatoive", "value": "3000 €"}, {"key": "start_date", "label": "Aloitus", "value": ""}]
DRAFT = {"sections": [{"key": "why_apply", "title": "Miksi haet?", "text": "Koska tehtävä kiinnostaa.", "verify": {"flagged": 1, "check": 0}}]}


@pytest.mark.parametrize("q,key", [
    ("Palkkatoive (€/kk)", "salary"), ("What are your salary expectations?", "salary"),
    ("Milloin voit aloittaa?", "start_date"), ("Onko sinulla ajokortti?", "drivers_license"),
    ("Miksi haet tätä paikkaa?", "why_apply"), ("Why should we hire you?", "why_you"),
    ("Miksi juuri sinut pitäisi valita?", "why_you"), ("Kerro itsestäsi", "about_me"),
    ("Mitkä ovat vahvuutesi?", "strengths"), ("What is your biggest weakness?", "develop"),
    ("Saatekirje", "cover_letter"), ("LinkedIn-profiili", "links"),
    ("Sähköposti *", "email"), ("Etunimi", "first_name"), ("Sukunimi", "last_name"),
])
def test_classifies_common_portal_questions(q, key):
    assert assist.classify(q)[1] == key


def test_unknown_question_is_not_forced_to_a_category():
    assert assist.classify("Kuvaile kokemustasi asiakaspalvelusta") is None


def test_contact_info_comes_from_cv_header():
    c = assist.contact_info(CV)
    assert c == {"full_name": "Matti Meikäläinen", "first_name": "Matti", "last_name": "Meikäläinen",
                 "email": "matti@example.com", "phone": "040 123 4567", "city": "Vaasa"}


def test_personal_facts_never_go_to_the_model():
    r = assist.suggest("Palkkatoive", ANSWERS, None, {})
    assert r["suggestions"][0]["text"] == "3000 €" and not r["can_generate"]
    empty = assist.suggest("Milloin voit aloittaa?", ANSWERS, DRAFT, {})
    assert empty["suggestions"] == [] and not empty["can_generate"] and "Vakiovastaukset" in empty["hint"]


def test_package_answer_carries_its_flags_and_open_question_can_be_generated():
    r = assist.suggest("Miksi haet tätä paikkaa?", ANSWERS, DRAFT, {})
    assert r["suggestions"][0]["source"] == "Hakemuspaketti" and r["suggestions"][0]["flagged"] == 1
    other = assist.suggest("Kuvaile kokemustasi asiakaspalvelusta", ANSWERS, DRAFT, {})
    assert other["suggestions"] == [] and other["can_generate"]


def test_generated_answer_is_fact_checked():
    fake = llm.FakeLLM([{"answer": "Olen käyttänyt Terraformia kolme vuotta tuotannossa."}])
    r = package.answer_question(_grounding(), fake, "Kuvaile kokemustasi?", "fi", "Aino", "")
    assert r["flagged"] == 1 and any("Terraform" in x for x in r["reasons"])


def test_match_job_by_company_in_page():
    items = [{"url": "a", "company": "Konepaja Oyj"}, {"url": "b", "company": "Firma Oy"}]
    assert assist.match_job(items, "Apply - Konepaja careers", "x.com") == "a"
    assert assist.match_job(items, "Hakemus", "recruit.firma.fi") == "b"
    assert assist.match_job(items, "Hakemus", "laura.fi") is None


def test_assist_requires_key(tmp_path, monkeypatch):
    monkeypatch.setattr(assist, "KEY_FILE", tmp_path / "extension.key")
    monkeypatch.setattr(drafts, "ANSWERS", tmp_path / "answers.json")
    webapp.CFG["port"] = 8765
    c = TestClient(srv.app, base_url="http://127.0.0.1:8765")
    h = {"X-Requested-With": "jobhunter"}
    assert c.post("/api/assist/suggest", json={"question": "Palkkatoive"}, headers=h).status_code == 401
    key = c.get("/api/assist/key").json()["key"]
    ok = c.post("/api/assist/suggest", json={"question": "Palkkatoive"}, headers={**h, "X-Jobhunter-Key": key})
    assert ok.status_code == 200 and ok.json()["match"] == "salary"
    assert (tmp_path / "extension.key").read_text(encoding="utf-8") == key
