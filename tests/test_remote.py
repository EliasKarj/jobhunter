import dataclasses

import pytest
from fastapi.testclient import TestClient

import webapp
from rag import config
from server import app as srv

H = {"X-Requested-With": "jobhunter"}
TS_HOST = "kone.tailnet.ts.net"


@pytest.fixture
def client(monkeypatch):
    base = config.load()
    monkeypatch.setattr(config, "load", lambda: dataclasses.replace(base, remote_users=("me@github",), remote_hosts=(TS_HOST,)))
    webapp.CFG["port"] = 8765
    return TestClient(srv.app, base_url="http://127.0.0.1:8765")


def ts(user="me@github", host=f"{TS_HOST}"):
    return {"Tailscale-User-Login": user, "Host": host}


def test_local_works_and_is_not_remote(client):
    r = client.get("/api/ai/status")
    assert r.status_code == 200 and r.json()["remote"] is False


def test_allowed_tailscale_user_gets_in_and_is_marked_remote(client):
    r = client.get("/api/ai/status", headers=ts())
    assert r.status_code == 200 and r.json()["remote"] is True
    assert client.get("/api/ai/status", headers=ts(host="127.0.0.1:8765")).json()["remote"] is True   # serve voi välittää Hostin


def test_other_tailscale_user_and_unknown_host_are_rejected(client):
    assert client.get("/api/ai/status", headers=ts(user="someone@github")).status_code == 403
    assert client.get("/api/ai/status", headers=ts(host="evil.example")).status_code == 403
    assert client.get("/api/ai/status", headers={"Host": TS_HOST}).status_code == 403          # ei Tailscale-tunnistetta


def test_settings_and_extension_key_only_at_the_machine(client):
    assert client.get("/api/assist/key", headers=ts()).status_code == 403
    r = client.post("/api/ai/settings", json={"values": {"ANTHROPIC_API_KEY": "x"}}, headers={**H, **ts()})
    assert r.status_code == 403 and "koneen ääressä" in r.json()["error"]
    assert client.post("/api/projects/dismiss", json={"id": "nope"}, headers=ts()).status_code == 403   # CSRF-otsikko yhä vaaditaan


def test_no_remote_users_means_no_remote_access(monkeypatch):
    base = config.load()
    monkeypatch.setattr(config, "load", lambda: dataclasses.replace(base, remote_users=(), remote_hosts=(TS_HOST,)))
    webapp.CFG["port"] = 8765
    c = TestClient(srv.app, base_url="http://127.0.0.1:8765")
    assert c.get("/api/ai/status", headers=ts()).status_code == 403
