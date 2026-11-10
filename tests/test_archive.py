import json

from server import archive

CV = {"name": "Aino", "header": ["**Opiskelija**", "Vaasa"], "company": "Esimerkkivirasto", "job_title": "Harjoittelija (forensiikka)",
      "summary": {"text": "Profiiliteksti."},
      "sections": [{"title": "Profiili", "profile": True, "units": [{"kind": "para", "text": "x", "included": True}], "entries": []},
                   {"title": "Projektit", "profile": False, "units": [], "entries": [
                       {"title": "Verkkolabra", "meta": "Docker | 2026", "included": True, "units": [
                           {"kind": "bullet", "text": "Segmentoitu verkko.", "included": True},
                           {"kind": "bullet", "text": "Piilotettu rivi.", "included": False}]},
                       {"title": "Pois", "meta": "", "included": False, "units": []}]}]}


def test_markdown_has_only_included_content():
    md = archive.cv_markdown(CV)
    assert md.startswith("# Aino\n**Opiskelija**\nVaasa")
    assert "## Profiili\nProfiiliteksti." in md and "### Verkkolabra\nDocker | 2026\n- Segmentoitu verkko." in md
    assert "Piilotettu" not in md and "Pois" not in md


def test_every_version_archived_once_and_sent_marked(tmp_path):
    root = tmp_path / "a"
    r1 = archive.save("u", "fi", CV, root=root)
    assert r1 and (root / r1["file"]).exists()
    assert r1["file"].startswith("Esimerkkivirasto/Harjoittelija (forensiikka)/") and r1["file"].endswith("_fi.md")
    assert archive.save("u", "fi", CV, root=root) is None                  # sama sisältö: ei uutta tiedostoa
    changed = json.loads(json.dumps(CV)); changed["summary"]["text"] = "Uusi profiili."
    assert archive.save("u", "fi", changed, root=root)
    sent = archive.mark_sent("u", {"u": {"fi": changed}}, root=root)
    assert len(sent) == 1 and sent[0]["sent"] and sent[0]["file"].endswith("_HAETTU.md")
    assert [r["sent"] for r in archive.listing("u", root)] == [True, False, False]
    assert json.loads((root / sent[0]["file"].replace(".md", ".json")).read_text(encoding="utf-8"))["cv"]["summary"]["text"] == "Uusi profiili."


def test_save_cv_archives_automatically(tmp_path, monkeypatch):
    from server import drafts
    monkeypatch.setattr(drafts, "CVS", tmp_path / "cvs.json")
    drafts.save_cv("u", "en", CV)
    assert len(archive.listing("u")) == 1


def test_company_and_role_folders_are_safe_names(tmp_path):
    cv = {**CV, "company": 'A/B: "Oy"', "job_title": "Kehittäjä | Rust? "}
    r = archive.save("u2", "en", cv, root=tmp_path)
    assert r["file"].startswith("A B Oy/Kehittäjä Rust/")
    blank = archive.save("u3", "en", {**CV, "company": "", "job_title": ""}, root=tmp_path)
    assert blank["file"].startswith("Tuntematon yritys/Tuntematon tehtävä/")


def test_every_archived_version_gets_a_pdf_next_to_it(tmp_path, monkeypatch):
    from server import pdf
    calls = []
    monkeypatch.setattr(pdf, "ENABLED", True)
    monkeypatch.setattr(pdf, "render", lambda url, lang, out, port=8765, timeout=90: calls.append((url, lang, out)))
    monkeypatch.setattr(pdf.threading, "Thread", lambda target, **k: type("T", (), {"start": lambda self: target()})())
    r = archive.save("u", "fi", CV, root=tmp_path)
    assert calls == [("u", "fi", tmp_path / r["file"].replace(".md", ".pdf"))]


def test_pdf_browser_override(monkeypatch, tmp_path):
    from server import pdf
    exe = tmp_path / "chrome.exe"
    exe.write_text("")
    monkeypatch.setenv("PDF_BROWSER", str(exe))
    assert pdf.browser() == str(exe)
    monkeypatch.setenv("PDF_BROWSER", str(tmp_path / "puuttuu.exe"))
    assert pdf.browser() is None
