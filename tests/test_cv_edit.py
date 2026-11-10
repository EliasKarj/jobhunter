import copy

from server import drafts

STORED = {"name": "Aino", "header": ["**Opiskelija**", "Vaasa | a@b.fi"], "summary": {"text": "Profiili.", "source": "generated"},
          "sections": [{"id": "S1", "title": "Projektit", "profile": False, "units": [], "entries": [
              {"id": "S1E1", "title": "Verkkolabra", "meta": "Docker | 2026", "included": True, "units": [
                  {"id": "S1E1B1", "kind": "bullet", "text": "Segmentoitu verkko.", "included": True}]}]},
                       {"id": "S2", "title": "Taidot", "profile": False, "entries": [], "units": [
                           {"id": "S2B1", "kind": "bullet", "text": "Python, SQL", "included": True}]}]}


def edit(**changes):
    e = copy.deepcopy(STORED)
    for f in changes.values():
        f(e)
    return e


def test_text_title_meta_header_and_new_lines_are_saved_and_marked():
    def ch(e):
        ent = e["sections"][0]["entries"][0]
        ent["title"], ent["meta"] = "Verkkolabra – verkkolabra", "Docker, Linux | 2026"
        ent["units"][0]["text"] = "Segmentoitu   Docker-verkko."
        ent["units"].append({"id": "NEW1", "kind": "bullet", "text": "Oma uusi rivi.", "included": True})
        e["sections"][1]["units"].append({"id": "NEW2", "text": "  ", "included": True})      # tyhjä: ei lisätä
        e["header"][0] = "**IT-opiskelija**"
    out = drafts.merge_cv_edit(STORED, edit(a=ch))
    ent = out["sections"][0]["entries"][0]
    assert ent["title"] == "Verkkolabra – verkkolabra" and ent["orig_title"] == "Verkkolabra"
    assert ent["meta"] == "Docker, Linux | 2026"
    u = ent["units"][0]
    assert u["text"] == "Segmentoitu Docker-verkko." and u["edited"] and u["orig"] == "Segmentoitu verkko."
    assert ent["units"][1] == {"id": "S1E1N1", "kind": "bullet", "text": "Oma uusi rivi.", "included": True, "edited": True, "orig": ""}
    assert len(out["sections"][1]["units"]) == 1
    assert out["header"][0] == "**IT-opiskelija**" and out["orig_header"][0] == "**Opiskelija**"
    assert [x[0]["id"] for x in drafts.edited_units(out)] == ["S1E1B1", "S1E1N1"]
    # toinen tallennus: lisätty rivi tunnetaan omalla tunnisteellaan, ei tuplaannu
    again = drafts.merge_cv_edit(out, copy.deepcopy(out))
    assert len(again["sections"][0]["entries"][0]["units"]) == 2


def test_unchanged_edit_marks_nothing_and_text_back_to_original_unmarks():
    out = drafts.merge_cv_edit(STORED, copy.deepcopy(STORED))
    assert drafts.edited_units(out) == [] and "orig_header" not in out
    def back(e):
        e["sections"][1]["units"][0]["text"] = "Python, SQL, C++"
    once = drafts.merge_cv_edit(STORED, edit(a=back))
    restored = copy.deepcopy(once)
    restored["sections"][1]["units"][0]["text"] = "Python, SQL"
    assert not drafts.merge_cv_edit(once, restored)["sections"][1]["units"][0]["edited"]


def test_structure_cannot_be_injected():
    e = edit()
    e["sections"].append({"id": "S9", "title": "Keksitty osio", "units": [{"id": "X", "text": "Ei", "included": True}], "entries": []})
    out = drafts.merge_cv_edit(STORED, e)
    assert [s["id"] for s in out["sections"]] == ["S1", "S2"]
