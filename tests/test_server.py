import json

import pytest
from fastapi.testclient import TestClient

import webapp
from server import app as srv
from server import drafts, envfile

H = {"X-Requested-With": "jobhunter"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "APPS", tmp_path / "applications.json")
    monkeypatch.setattr(webapp, "LATEST", tmp_path / "latest.json")
    monkeypatch.setattr(drafts, "DRAFTS", tmp_path / "drafts.json")
    (tmp_path / "latest.json").write_text(json.dumps({"updated": "2026-11-03T10:00:00", "rows": [
        {"job": {"url": "https://x/1", "title": "Junior Dev", "company": "Firma", "location": "Vaasa",
                 "source": "duunitori", "text": "Haemme junioria."}, "score": 70, "reasons": [], "status": "ok"}]}),
        encoding="utf-8")
    webapp.CFG["port"] = 8765
    return TestClient(srv.app, base_url="http://127.0.0.1:8765")


def test_rejects_foreign_host_and_missing_header(client):
    assert TestClient(srv.app, base_url="http://evil.example").get("/api/apps").status_code == 403
    assert client.post("/api/apps", json={"action": "create"}).status_code == 403
    assert client.post("/api/apps", json={"action": "create"}, headers=H).status_code == 200


def test_tracking_transitions_through_api(client):
    a = client.post("/api/apps", json={"action": "create", "job_url": "https://x/1"}, headers=H).json()["app"]
    assert a["status"] == "interested"
    bad = client.post("/api/apps", json={"action": "update", "id": a["id"], "fields": {"status": "offer"}}, headers=H)
    assert bad.status_code == 400 and "ei voi siirtyä" in bad.json()["error"]
    ok = client.post("/api/apps", json={"action": "update", "id": a["id"], "fields": {"status": "applied"}}, headers=H)
    assert ok.json()["app"]["status"] == "applied"
    info = client.get("/api/tracking").json()
    assert "no_response" in info["statuses"] and info["follow_up_days"] == 14


def test_drafts_save_keeps_history(client):
    drafts.save_generated({"url": "https://x/1", "title": "Junior Dev", "company": "Firma", "lang": "fi", "model": "fake",
                           "sections": [{"key": "cover_letter", "title": "Saatekirje", "text": "Hei", "edited": False,
                                         "verify": {"claims": [], "flagged": 0, "check": 0}}]})
    r = client.post("/api/drafts", json={"url": "https://x/1", "sections": [{"key": "cover_letter", "text": "Hei!"}]},
                    headers=H).json()
    assert r["sections"][0]["text"] == "Hei!" and r["sections"][0]["edited"] and r["history"][0]["sections"][0]["text"] == "Hei"
    assert client.get("/api/drafts", params={"url": "https://x/nope"}).status_code == 404


def test_env_update_validates(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# kommentti\nLLM_PROVIDER=ollama\nW_KEYWORD=0.6\n", encoding="utf-8")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert envfile.update({"LLM_PROVIDER": "claude", "W_SEMANTIC": "0.5"}, env) == ["LLM_PROVIDER", "W_SEMANTIC"]
    assert env.read_text(encoding="utf-8") == "# kommentti\nLLM_PROVIDER=claude\nW_KEYWORD=0.6\nW_SEMANTIC=0.5\n"
    with pytest.raises(ValueError):
        envfile.update({"LLM_PROVIDER": "gpt"}, env)
    with pytest.raises(ValueError):
        envfile.update({"DB_PATH": "/etc"}, env)


def test_cv_text_edit_saved_flagged_and_archived(client, tmp_path, monkeypatch):
    import copy
    from rag import cvtailor
    from server import archive
    monkeypatch.setattr(drafts, "CVS", tmp_path / "cvs.json")
    monkeypatch.setattr(cvtailor, "user_source", lambda s=None: "Docker Linux Verkkolabra 2026")
    cv = {"name": "Aino", "header": ["**Opiskelija**", "Vaasa"], "summary": {"text": "P.", "source": "original", "flags": []},
          "company": "Firma", "job_title": "Junior Dev", "lang": "fi",
          "sections": [{"id": "S1", "title": "Projektit", "profile": False, "units": [], "entries": [
              {"id": "S1E1", "title": "Verkkolabra", "meta": "2026", "included": True, "units": [
                  {"id": "S1E1B1", "kind": "bullet", "text": "Docker-verkko.", "included": True}]}]}]}
    drafts.save_cv("https://x/1", "fi", cv)
    e = copy.deepcopy(cv)
    e["sections"][0]["entries"][0]["units"][0]["text"] = "Docker-verkko Linuxilla."
    e["sections"][0]["entries"][0]["units"].append({"id": "NEW1", "text": "Kubernetes-klusteri 300 käyttäjälle.", "included": True})
    r = client.post("/api/cv/save", json={"url": "https://x/1", "lang": "fi", "cv": e}, headers=H)
    assert r.status_code == 200
    units = r.json()["sections"][0]["entries"][0]["units"]
    assert units[0]["edited"] and units[0]["flags"] == []
    assert units[1]["id"] == "S1E1N1" and any("Kubernetes" in f for f in units[1]["flags"]) and any("300" in f for f in units[1]["flags"])
    assert len(archive.listing("https://x/1")) == 2          # alkuperäinen ja muokattu versio arkistossa


def test_tailoring_a_cv_puts_job_in_tracking_and_extension_list(client, tmp_path, monkeypatch):
    from rag import cvtailor
    monkeypatch.setattr(drafts, "CVS", tmp_path / "cvs.json")
    monkeypatch.setattr(cvtailor, "tailor_url", lambda url, s, lang: {"name": "E", "header": [], "sections": [],
                                                                       "summary": {"text": "", "source": "original"},
                                                                       "company": "Firma", "job_title": "Junior Dev"})
    assert client.post("/api/cv", json={"url": "https://x/1", "lang": "fi"}, headers=H).status_code == 200
    apps = client.get("/api/apps").json()["apps"]
    assert [(a["url"], a["status"]) for a in apps] == [("https://x/1", "drafting")]


def test_style_samples_saved_by_id(client, tmp_path, monkeypatch):
    from rag import stylesamples
    monkeypatch.setattr(stylesamples, "PATH", tmp_path / "style.json")
    stylesamples._save({"samples": [{"id": "fs1-1", "generated": "Luonnos.", "mine": "", "updated": ""}]})
    assert client.get("/api/style").json()["samples"][0]["id"] == "fs1-1"
    r = client.post("/api/style/save", json={"id": "fs1-1", "mine": "Oma versio."}, headers=H)
    assert r.status_code == 200 and r.json()["mine"] == "Oma versio." and r.json()["updated"]
    assert client.post("/api/style/save", json={"id": "x", "mine": "a"}, headers=H).status_code == 404
