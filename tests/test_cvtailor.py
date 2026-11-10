from rag import cvtailor, llm
from server import drafts

CV_MD = """# Matti Meikäläinen

**IT Engineering student**
Vaasa | matti@example.com | 040 123 4567

## Profile

Third-year IT student with DevOps skills (Docker, Linux) and full-stack development.

## Work Experience

### Warehouse Worker | Firma Oy
*2021 – 2023*

Picking and packing orders.

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

JOB = {"title": "Junior DevOps Engineer", "company": "Firma", "city": "Vaasa"}


def test_parse_gives_stable_ids():
    cv = cvtailor.parse(CV_MD)
    assert cv.name == "Matti Meikäläinen" and len(cv.header) == 2
    proj = cv.sections[2]
    assert proj.id == "S3" and [e.id for e in proj.entries] == ["S3E1", "S3E2"]
    assert proj.entries[0].meta == "JavaScript, GitHub Actions"
    assert [u.id for u in proj.entries[0].units] == ["S3E1B1", "S3E1B2"]
    assert cvtailor.to_markdown(cv, {}).count("### ") == 3


def test_plan_only_selects_and_text_stays_verbatim():
    plan = {"section_order": ["S1", "S3", "S5", "S2", "S99"], "drop": ["S3E2", "S4B1", "S2E1"],
            "focus_skills": ["Kubernetes", "Terraform"], "summary": "IT student with DevOps skills (Docker, Linux)."}
    res = cvtailor.tailor(CV_MD, JOB, "Docker ja Kubernetes", llm.FakeLLM([plan]))
    titles = [s["title"] for s in res["sections"]]
    assert titles[:3] == ["Profile", "Projects", "Technical Skills"]
    proj = next(s for s in res["sections"] if s["title"] == "Projects")
    assert [e["included"] for e in proj["entries"]] == [True, False]
    edu = next(s for s in res["sections"] if s["title"] == "Education")
    assert all(u["included"] for u in edu["units"])          # koulutusta ei jätetä pois
    work = next(s for s in res["sections"] if s["title"] == "Work Experience")
    assert work["entries"][0]["included"]                   # osion ainoaa kohtaa ei poisteta
    assert res["focus_skills"] == ["Kubernetes"]            # Terraform ei ole CV:ssä
    skills = next(s for s in res["sections"] if s["title"] == "Technical Skills")
    assert skills["units"][0]["text"].startswith("**Containers:**")   # korostettu taito ensin
    originals = {u.text for s in cvtailor.parse(CV_MD).sections for e in s.entries for u in e.units}
    assert {u["text"] for e in proj["entries"] for u in e["units"]} <= originals
    assert res["summary"]["source"] == "generated"


def test_invented_summary_falls_back_to_original():
    plan = {"section_order": [], "drop": [], "focus_skills": [], "summary": "IT student with 3 years of Rust experience."}
    res = cvtailor.tailor(CV_MD, JOB, "", llm.FakeLLM([plan]))
    assert res["summary"]["source"] == "original"
    assert res["summary"]["text"].startswith("Third-year IT student")
    reasons = " ".join(r for f in res["summary"]["flags"] for r in f["reasons"])
    assert "Rust" in reasons and "luku 3" in reasons


def test_translation_check_catches_changed_terms_and_numbers():
    assert cvtailor.check_translation("Set up CI/CD with GitHub Actions in 2026.",
                                      "Rakensi CI/CD:n GitHub Actionsilla vuonna 2026.") == []
    assert cvtailor.check_translation("Team of four, 2026", "Neljän hengen tiimi, 2025") == ["luvut eivät täsmää"]
    r = cvtailor.check_translation("Full-stack development", "Täyspinnoitteinen kehitys")
    assert any("full-stack" in x.lower() for x in r)
    assert any("Kubernetes" in x for x in cvtailor.check_translation("Docker", "Docker ja Kubernetes"))


def test_finnish_without_base_needs_review_and_with_base_is_extractive(tmp_path):
    plan = {"section_order": [], "drop": [], "focus_skills": [], "summary": "IT student with DevOps skills."}
    cv = cvtailor.parse(CV_MD)
    units = cvtailor.translation_units(cv)
    units["SUMMARY"] = "x"
    fake_tr = {k: v for k, v in units.items()}
    fake_tr["S2E1P1"] = "Tilausten keräilyä ja pakkaamista."
    res = cvtailor.tailor(CV_MD, JOB, "", llm.FakeLLM([plan, fake_tr]), lang="fi")
    assert res["review_required"] and res["translation"]["S2E1P1"]["text"] == "Tilausten keräilyä ja pakkaamista."
    fi_md = cvtailor.to_markdown(cv, {k: v["text"] for k, v in res["translation"].items()})
    plan_fi = {"section_order": [], "drop": [], "focus_skills": [], "summary": "IT-opiskelija, jolla on DevOps-taitoja."}
    res2 = cvtailor.tailor(CV_MD, JOB, "", llm.FakeLLM([plan_fi]), lang="fi", fi_md=fi_md)
    assert not res2["review_required"] and res2["source"] == "cv_fi.md"
    work = next(s for s in res2["sections"] if s["title"] == "Työkokemus")
    assert work["entries"][0]["units"][0]["text"] == "Tilausten keräilyä ja pakkaamista."


def _long_cv() -> str:
    work = "".join(f"### Role {i} | Firma {i}\n*20{i:02d}*\n\nDescription of the work number {i}.\n\n" for i in range(5))
    bullets = "".join(f"- Line {j} about building web services with Docker and Linux in practice.\n" for j in range(4))
    projects = "".join(f"### Project {i}\n*Python*\n\n{bullets}\n" for i in range(9))
    return (f"# Matti\n\n**Student**\nVaasa | m@x.fi\n\n## Profile\n\nIT student with Docker skills.\n\n## Work Experience\n\n{work}"
            f"## Projects\n\n### Kube lab\n*Kubernetes, Docker*\n\n{bullets}\n{projects}## Education\n\n- **IT** | ESAMK\n\n"
            "## Technical Skills\n\n- **Containers:** Docker, Kubernetes\n\n## Languages\n\nFinnish\n")


def test_tailored_cv_fits_one_page_and_keeps_most_relevant():
    plan = {"section_order": [], "drop": [], "focus_skills": ["Kubernetes"], "summary": "IT student with Docker skills."}
    res = cvtailor.tailor(_long_cv(), JOB, "Haemme Kubernetes-osaajaa", llm.FakeLLM([plan]))
    cv = cvtailor.parse(_long_cv())
    proj = next(s for s in res["sections"] if s["title"] == "Projects")
    kept = [e for e in proj["entries"] if e["included"]]
    assert len(kept) <= cvtailor.MAX_PROJECTS and kept[0]["title"] == "Kube lab"     # osuvin ensin
    assert all(any(u["included"] for u in e["units"]) for e in kept)                  # jokaisella vähintään rivi
    work = next(s for s in res["sections"] if s["title"] == "Work Experience")
    assert all(e["included"] for e in work["entries"])                                # työhistorian nimikkeet säilyvät
    assert any(c["kind"] == "fit" for c in res["changes"])
    by_id = {s.id: s for s in cv.sections}
    for s in res["sections"]:
        o = by_id[s["id"]]
        for e, oe in zip(sorted(s["entries"], key=lambda x: x["id"]), sorted(o.entries, key=lambda x: x.id)):
            oe.included = e["included"]
            for u, ou in zip(e["units"], oe.units):
                ou.included = u["included"]
    assert cvtailor.estimate_lines(cv, cv.sections, "x") <= cvtailor.PAGE_LINES


def test_answers_keep_only_known_fields(tmp_path):
    p = tmp_path / "answers.json"
    drafts.save_answers({"salary": " 3000 € ", "hack": "x"}, p)
    vals = {f["key"]: f["value"] for f in drafts.get_answers(p)["fields"]}
    assert vals["salary"] == "3000 €" and "hack" not in vals and vals["start_date"] == ""


def test_cv_edit_saves_users_own_text_marked_as_edited(tmp_path):
    """Käyttäjä saa muokata tekstiä itse (oma teksti, ei kielimallin keksimää); muutos merkitään ja tarkistetaan."""
    stored = {"sections": [{"id": "S1", "units": [], "entries": [{"id": "S1E1", "included": True, "title": "T",
              "units": [{"id": "S1E1B1", "text": "alkuperäinen", "included": True}]}]}], "summary": {"text": "a", "source": "generated"}}
    edit = {"sections": [{"id": "S1", "entries": [{"id": "S1E1", "included": False, "units": [{"id": "S1E1B1", "text": "KEKSITTY"}]}]}],
            "summary": {"text": "b"}}
    out = drafts.merge_cv_edit(stored, edit)
    e = out["sections"][0]["entries"][0]
    assert e["included"] is False and e["units"][0]["text"] == "KEKSITTY"
    assert e["units"][0]["edited"] and e["units"][0]["orig"] == "alkuperäinen"
    assert out["summary"] == {"text": "b", "source": "edited"}
    drafts.save_cv("u", "fi", out, tmp_path / "cvs.json")
    assert drafts.get_cv("u", "fi", tmp_path / "cvs.json")["summary"]["text"] == "b"


def test_finnish_base_is_measured_as_is_but_translation_gets_growth(monkeypatch):
    seen = []
    monkeypatch.setattr(cvtailor, "fit_one_page", lambda *a: seen.append(a[-1]) or [])
    plan = {"section_order": [], "drop": [], "focus_skills": [], "summary": "Opiskelija."}
    cvtailor.tailor(CV_MD, JOB, "", llm.FakeLLM([plan]), lang="fi", fi_md=CV_MD)
    cvtailor.tailor(CV_MD, JOB, "", llm.FakeLLM([plan, {}]), lang="fi")
    assert seen == ["en", "fi"]


def test_third_person_finnish_summary_falls_back_to_original():
    fi_md = CV_MD.replace("Third-year IT student with DevOps skills", "Kolmannen vuoden opiskelija, rakennan DevOps-ratkaisuja")
    plan = {"section_order": [], "drop": [], "focus_skills": [], "summary": "Rakentaa Docker-ratkaisuja."}
    fix = {"summary": "Rakentaa edelleen Docker-ratkaisuja."}            # uudelleenkirjoitus ei korjaa
    res = cvtailor.tailor(CV_MD, JOB, "", llm.FakeLLM([plan, fix]), lang="fi", fi_md=fi_md)
    assert res["summary"]["source"] == "original" and res["summary"]["text"].startswith("Kolmannen vuoden opiskelija, rakennan")
    assert "kolmas persoona" in res["summary"]["flags"][0]["reasons"][0]
