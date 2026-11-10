import json
import os
import subprocess
import time

import pytest

from rag import config, llm, projects

ME, OTHER = "me@example.com", "other@example.com"
CV = """# Matti Meikäläinen

## Projects

### Lenkkiloki – GPX analyzer
*JavaScript, GitHub Actions*

- Web app that loads GPX files.

## Education

- **IT** | ESAMK | 2023 – 2027
"""


def git(repo, *args, email=ME, date=None):
    env = {**os.environ, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_NAME": "x",
           "GIT_COMMITTER_NAME": "x"}
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def make_repo(root, name, files: dict, commits=3, email=ME, date="2026-01-10T12:00:00"):
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    for path, text in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    for i in range(commits):
        git(repo, "commit", "-q", "--allow-empty", "-m", f"Add feature {i}", email=email, date=date)
    return repo


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "code"
    cvp = tmp_path / "cv.md"
    cvp.write_text(CV, encoding="utf-8")
    old = time.mktime((2026, 3, 1, 0, 0, 0, 0, 0, -1))
    os.utime(cvp, (old, old))
    s = config.Settings(cv_path=cvp, db_path=tmp_path / "x.sqlite")
    st = {"own_emails": [ME], "aliases": {}, "ignore": [], "root": "", "llm": "default"}
    return root, s, st, tmp_path / "proposals.json"


def run(env, fake, **kw):
    root, s, st, path = env
    return projects.scan(s, llm=fake, root=root, settings=st, proposals_path=path, report_path=None,
                         progress=lambda m: None, **kw)


def test_detect_stack_from_files():
    files = {"package.json": json.dumps({"dependencies": {"react": "1", "next": "1"}, "devDependencies": {"vitest": "1"}}),
             "requirements.txt": "fastapi==0.1\npandas>=2\n", "Dockerfile": "", ".github/workflows/ci.yml": "",
             **{f"src/a{i}.ts": "" for i in range(6)}, "vite.config.js": ""}
    stack = projects.detect_stack(list(files), lambda f: files[f])
    assert stack[0] == "TypeScript" and "JavaScript" not in stack        # yksi konfiguraatiotiedosto ei riitä kieleksi
    for t in ("React", "Next.js", "Vitest", "FastAPI", "pandas", "Docker", "GitHub Actions"):
        assert t in stack


def test_scan_proposes_new_project_with_verified_draft_and_skips_others(env):
    root = env[0]
    make_repo(root, "budget-app", {"package.json": json.dumps({"dependencies": {"react": "1"}}),
                                   "README.md": "# Budget app\nReact app for tracking expenses with charts.",
                                   **{f"src/c{i}.jsx": "" for i in range(5)}})
    make_repo(root, "friends-repo", {"main.py": ""}, email=OTHER)
    make_repo(root / "secret", "hidden", {"a.py": ""})
    fake = llm.FakeLLM([{"title": "Budget app – expense tracker",
                         "bullets": ["Built a React app for tracking expenses with charts.",
                                     "Deployed the app on Kubernetes with Terraform."]}])
    res = run(env, fake)
    new = [p for p in res["proposals"] if p["kind"] == "new_project"]
    assert [p["repo"] for p in new] == ["budget-app"]
    ok, bad = new[0]["draft"]["bullets"]
    assert ok["flags"] == [] and any("kubernetes" in f.lower() for f in bad["flags"])
    assert {x["repo"] for x in res["skipped"]} == {"friends-repo"}
    assert "hidden" not in json.dumps(res)
    assert res["other_authors"] == {OTHER: 3}
    assert "Budget app" in fake.calls[0]["messages"][0]["content"]          # README meni lähteeksi


def test_existing_project_gets_new_tech_and_activity(env):
    root = env[0]
    repo = make_repo(root, "lenkkiloki", {"package.json": json.dumps({"devDependencies": {"vitest": "1"}}),
                                          **{f"src/m{i}.js": "" for i in range(5)}}, date="2026-02-01T12:00:00")
    git(repo, "commit", "-q", "--allow-empty", "-m", "Add pace chart", date="2026-04-01T12:00:00")
    res = run(env, llm.FakeLLM())
    kinds = {p["kind"]: p for p in res["proposals"]}
    assert "new_project" not in kinds
    assert kinds["new_tech"]["missing"] == ["Vitest"] and kinds["new_tech"]["cv_entry"].startswith("Lenkkiloki")
    assert kinds["activity"]["recent"] == ["Add pace chart"]


def test_dismissed_stays_dismissed_and_draft_is_cached(env):
    make_repo(env[0], "budget-app", {"README.md": "# Budget app", **{f"src/c{i}.py": "" for i in range(5)}})
    fake = llm.FakeLLM([{"title": "Budget app – tracker", "bullets": ["Built a budget app."]}])
    first = run(env, fake)
    pid = first["proposals"][0]["id"]
    assert projects.dismiss(pid, env[3])
    again = run(env, fake)
    assert again["proposals"][0]["status"] == "dismissed"
    assert len(fake.calls) == 1 and again["proposals"][0]["draft"]["title"] == "Budget app – tracker"


def test_alias_and_report(env):
    root, s, st, path = env
    make_repo(root, "gpx-tool", {**{f"src/m{i}.js": "" for i in range(5)}})
    st["aliases"] = {"gpx-tool": "Lenkkiloki"}
    res = run(env, llm.FakeLLM())
    assert not [p for p in res["proposals"] if p["kind"] == "new_project"]
    text = projects.report(res)
    assert "Mitään ei ole kirjoitettu CV:hen" in text


def test_requires_own_emails(env):
    root, s, st, path = env
    st["own_emails"] = []
    with pytest.raises(ValueError):
        run(env, llm.FakeLLM())


# ------------------------------------------------------------ hyväksyntä ja cv_fi.md:n synkronointi

FI = """# Matti Meikäläinen

## Projektit

### Lenkkiloki – GPX-analysaattori
*JavaScript, GitHub Actions*

- Verkkosovellus, joka lataa GPX-tiedostoja.

## Koulutus

- **IT** | ESAMK | 2023 – 2027
"""


@pytest.fixture
def cvenv(tmp_path, monkeypatch):
    cvp = tmp_path / "cv.md"
    cvp.write_text(CV, encoding="utf-8")
    (tmp_path / "cv_fi.md").write_text(FI, encoding="utf-8")
    monkeypatch.setattr(projects, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(projects, "NOTES", tmp_path / "notes.json")
    s = config.Settings(cv_path=cvp, db_path=tmp_path / "x.sqlite")
    props = tmp_path / "proposals.json"
    props.write_text(json.dumps({"proposals": [
        {"id": "p1", "kind": "new_project", "repo": "budget", "status": "open", "stack": ["React", "Vite"], "period": "2025-04",
         "draft": {"title": "Budget app – expense tracker", "bullets": [{"text": "Built a React app.", "flags": []}]}},
        {"id": "p2", "kind": "new_tech", "repo": "lenkkiloki", "status": "open", "cv_entry": "Lenkkiloki – GPX analyzer",
         "missing": ["Vitest", "Canvas"]},
        {"id": "p3", "kind": "activity", "repo": "lenkkiloki", "status": "open", "cv_entry": "Lenkkiloki – GPX analyzer"}]}),
        encoding="utf-8")
    return s, props, tmp_path


def test_accept_new_project_writes_cv_with_backup_and_keeps_finnish_by_content(cvenv):
    from rag import cvsync, cvtailor
    s, props, tmp = cvenv
    assert cvsync.snapshot(s)
    res = projects.accept("p1", {"bullets": ["Built a React app for tracking expenses."], "note": "Tein yksin."}, s, props,
                          skills=["react"])
    md = s.cv_path.read_text(encoding="utf-8")
    assert md.index("### Budget app – expense tracker") < md.index("### Lenkkiloki")
    assert "*React, Vite | Spring 2025*" in md and "- Built a React app for tracking expenses." in md
    assert res["new_skills"] == ["Vite"] and list((tmp / "backups").iterdir())
    assert projects.load_notes(tmp / "notes.json") == {"Budget app – expense tracker": "Tein yksin."}
    st = cvsync.status(s)                         # tunnisteet siirtyivät, mutta vanhat suomennokset löytyvät
    assert not st["in_sync"]
    assert {m["en"] for m in st["missing"]} == {"Budget app – expense tracker", "React, Vite | Kevät 2025",
                                                "Built a React app for tracking expenses."}
    out = cvsync.save(s, {m["id"]: "FI " + m["en"] for m in st["missing"]})
    fi = (tmp / "cv_fi.md").read_text(encoding="utf-8")
    assert "Verkkosovellus, joka lataa GPX-tiedostoja." in fi and "FI Built a React app" in fi
    assert cvsync.status(s)["in_sync"] and not out["left_english"]
    assert cvtailor.parse(fi).sections[0].entries[1].title == "Lenkkiloki – GPX-analysaattori"


def test_accept_new_tech_extends_meta_and_activity_only_marks(cvenv):
    s, props, _ = cvenv
    projects.accept("p2", {"techs": ["Vitest"]}, s, props)
    assert "*JavaScript, GitHub Actions, Vitest*" in s.cv_path.read_text(encoding="utf-8")
    before = s.cv_path.read_text(encoding="utf-8")
    projects.accept("p3", {}, s, props)
    assert s.cv_path.read_text(encoding="utf-8") == before
    with pytest.raises(ValueError):
        projects.accept("p3", {}, s, props)       # jo käsitelty


def test_add_tech_handles_time_only_meta():
    md = "## Work\n\n### Dev | Firma\n*2021 – 2023*\n\n- Did things.\n"
    assert "*Docker | 2021 – 2023*" in projects.add_tech(md, "Dev | Firma", ["Docker"])


def test_season_formatting():
    assert projects.season("2026-08") == "Summer 2026"
    assert projects.season("2025-09 – 2026-06") == "Autumn 2025 – Summer 2026"


def test_user_edit_to_finnish_base_is_remembered(cvenv):
    from rag import cvsync
    s, _, tmp = cvenv
    cvsync.snapshot(s)
    fp = tmp / "cv_fi.md"
    fp.write_text(FI.replace("lataa GPX-tiedostoja", "lukee GPX-tiedostoja"), encoding="utf-8")
    assert cvsync.status(s)["in_sync"]
    pairs = json.loads((tmp / "cv_fi_map.json").read_text(encoding="utf-8"))["pairs"]
    assert pairs["Web app that loads GPX files."] == "Verkkosovellus, joka lukee GPX-tiedostoja."


def test_new_proposals_notify_until_seen(env, tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps(env[2]), encoding="utf-8")
    make_repo(env[0], "budget-app", {"README.md": "# Budget app", **{f"src/c{i}.py": "" for i in range(5)}})
    fake = llm.FakeLLM([{"title": "Budget app", "bullets": ["Built a budget app."]}] * 2)
    run(env, fake)
    s1 = projects.summary(env[3], sp)
    assert s1["open"] == 1 and [n["repo"] for n in s1["new"]] == ["budget-app"]
    assert s1["new"][0]["label"] == "uusi projekti"
    time.sleep(1.1)                                   # aikaleimat sekunnin tarkkuudella
    assert projects.mark_seen(sp) == ""
    assert projects.summary(env[3], sp)["new"] == []
    first_found = json.loads(env[3].read_text(encoding="utf-8"))["proposals"][0]["found"]
    time.sleep(1.1)
    make_repo(env[0], "chat-app", {"README.md": "# Chat", **{f"src/c{i}.py": "" for i in range(5)}})
    res = run(env, fake)                              # vanha säilyttää löytöhetken, uusi on uusi
    assert next(p for p in res["proposals"] if p["repo"] == "budget-app")["found"] == first_found
    assert [n["repo"] for n in projects.summary(env[3], sp)["new"]] == ["chat-app"]
