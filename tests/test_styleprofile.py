import json

from rag import llm, style, styleprofile, stylesamples

SAMPLES = {"samples": [
    {"id": "a-1", "lang": "fi", "question": "Miksi haet?", "generated": "Haen tehtävään. Kiitos hakemukseni käsittelystä.",
     "mine": "Mielestäni tehtävä sopii minulle. Olen innokas oppimaan lisää.", "updated": "2026-11-09T21:00:00"},
    {"id": "b-1", "lang": "fi", "question": "Kerro itsestäsi", "generated": "Olen opiskelija.", "mine": "", "updated": ""}]}


def test_profile_built_from_edited_pairs_only_and_used_in_prompt(tmp_path):
    stylesamples._save(SAMPLES)
    fake = llm.FakeLLM([{"rules": ["Kirjoita menneessä aikamuodossa.", "Älä käytä kohteliaisuuslopetuksia."]}])
    p = styleprofile.build(llm=fake)
    assert p["samples"] == 1 and "MINE:\nMielestäni" in fake.calls[0]["messages"][0]["content"]
    assert "Olen opiskelija" not in fake.calls[0]["messages"][0]["content"]          # muokkaamaton ei mukana
    assert "Never write a rule that would let the model claim skills" in fake.calls[0]["system"]
    block = styleprofile.prompt_block("fi")
    assert "menneessä aikamuodossa" in block and "Mielestäni tehtävä sopii minulle" in block and "älä kopioi" in block


def test_own_phrases_are_not_flagged_as_cliches():
    stylesamples._save(SAMPLES)
    assert "innokas oppimaan" not in [x.lower() for x in style.find("Olen innokas oppimaan uutta.", "fi")]


def test_discreet_name_rule_and_flag(monkeypatch, tmp_path):
    prof = tmp_path / "profile.json"
    prof.write_text(json.dumps({"discreet_names": {"Konepaja": {"fi": "suuren teollisuusyrityksen toimeksianto", "en": "x"}}}),
                    encoding="utf-8")
    monkeypatch.setattr(styleprofile.config, "DATA", tmp_path)
    assert "Konepaja" in styleprofile.names_rule("fi") and "suuren teollisuusyrityksen" in styleprofile.names_rule("fi")
    monkeypatch.setattr(style, "_discreet", lambda: ["Konepaja"])
    assert "Konepaja" in style.find("Tein Konepajan toimeksiannon.", "fi")
