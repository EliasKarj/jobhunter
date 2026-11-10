"""FastAPI-backend: uudet generatiiviset rajapinnat + vanhat /api/*-rajapinnat sellaisinaan (webapp.api_get/api_post).

  python webapp.py              # käynnistää tämän (uvicorn, http://127.0.0.1:8765)
  python webapp.py --legacy     # vanha http.server ilman generatiivisia ominaisuuksia

Vain paikallinen käyttö: Host-otsikon on oltava 127.0.0.1/localhost (DNS rebinding), ja muuttavat pyynnöt
vaativat X-Requested-With: jobhunter -otsikon (muut sivustot eivät voi lähettää sitä ilman CORS-esitarkistusta).
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import webapp  # noqa: E402
from rag import config  # noqa: E402
from rag.llm import LLMError  # noqa: E402
from server import assist, drafts, envfile, tracking  # noqa: E402

DIST = BASE / "frontend" / "dist"
app = FastAPI(title="jobhunter", docs_url=None, redoc_url=None, openapi_url=None)


def _err(code: int, msg: str, kind: str = "error") -> JSONResponse:
    return JSONResponse({"error": msg, "code": kind}, status_code=code)


# Etäkäytössä ei sallita: .env-asetukset ja API-avain, selainlaajennuksen avain ja laajennuksen rajapinta.
REMOTE_BLOCKED = ("/api/ai/settings", "/api/assist/")


def remote_access(request: Request, port: int) -> tuple[bool, str | None]:
    """(etäpyyntö, hylkäyksen syy). Etäkäyttö kulkee vain Tailscale serven kautta: se välittää liikenteen
    127.0.0.1:een ja lisää otsikon Tailscale-User-Login tailnetin käyttäjästä. Selain ei voi lisätä tätä otsikkoa
    toiselta sivustolta (vaatisi CORS-esitarkistuksen), eikä palvelin kuuntele muita osoitteita kuin 127.0.0.1."""
    host = (request.headers.get("host") or "").lower()
    user = (request.headers.get("tailscale-user-login") or "").strip().lower()
    local = host in {f"127.0.0.1:{port}", f"localhost:{port}"}
    if not user:
        return False, None if local else "forbidden"
    s = config.load()
    name = host.split(":")[0].rstrip(".")
    if not local and name not in s.remote_hosts:
        return True, "forbidden"
    if user not in s.remote_users:
        return True, "Tämä Tailscale-käyttäjä ei saa käyttää jobhunteria (REMOTE_USERS)."
    if request.url.path.startswith(REMOTE_BLOCKED):
        return True, "Tämä toiminto on käytettävissä vain koneen ääressä."
    return True, None


@app.middleware("http")
async def local_only(request: Request, call_next):
    port = webapp.CFG["port"]
    remote, why = remote_access(request, port)
    if why:
        return _err(403, why, "forbidden")
    request.state.remote = remote
    if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "jobhunter":
        return _err(403, "forbidden")
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _guard(fn):
    """Yhtenäiset virheet: puuttuva indeksi 409, kielimalli 502, puuttuva ilmoitus 404."""
    from rag.service import IndexMissing
    try:
        return fn()
    except IndexMissing as e:
        return _err(409, str(e), "index_missing")
    except LLMError as e:
        return _err(502, str(e), "llm")
    except LookupError as e:
        return _err(404, str(e), "not_found")
    except ValueError as e:
        return _err(400, str(e), "bad_request")


# ------------------------------------------------------------ tila ja asetukset

def _ollama_models(url: str) -> list[str] | None:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=1.5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:  # noqa: BLE001
        return None


def _index_info(s: config.Settings) -> dict:
    if not s.db_path.exists():
        return {"exists": False, "configs": [], "active": s.config_id}
    from rag.store import Store
    st = Store(s.db_path)
    try:
        cfgs = []
        for r in st.db.execute("SELECT config_id, backend, model, chunk_size, chunk_overlap, created FROM index_configs"):
            n = st.count("job_index", "WHERE config_id=?", (r["config_id"],))
            cfgs.append({**dict(r), "jobs": n})
        return {"exists": True, "active": s.config_id, "configs": cfgs, "jobs": st.count("jobs"),
                "active_ok": any(c["config_id"] == s.config_id for c in cfgs)}
    finally:
        st.close()


@app.get("/api/ai/status")
def ai_status(request: Request = None):
    s = config.load()
    models = _ollama_models(s.ollama_url) if s.llm_provider == "ollama" else None
    llm = {"provider": s.llm_provider,
           "model": s.ollama_model if s.llm_provider == "ollama" else s.claude_model,
           "writing_model": s.claude_model_writing or s.claude_model if s.llm_provider == "claude" else s.ollama_model}
    if s.llm_provider == "ollama":
        llm["reachable"] = models is not None
        llm["model_available"] = bool(models) and any(m == s.ollama_model or m.split(":")[0] == s.ollama_model
                                                      for m in models)
        llm["models"] = models or []
    else:
        llm["key_set"] = bool(s.anthropic_api_key)
    return {"llm": llm, "index": _index_info(s), "remote": bool(getattr(getattr(request, "state", None), "remote", False)),
            "scoring": {"mode": s.scoring_mode, "w_keyword": s.w_keyword, "w_semantic": s.w_semantic,
                        "method": s.hybrid_method, "aggregation": s.aggregation},
            "embed": {"backend": s.embed_backend, "model": s.embed_model, "chunk_size": s.chunk_size,
                      "chunk_overlap": s.chunk_overlap}}


@app.post("/api/ai/settings")
async def ai_settings(request: Request):
    body = await request.json()
    values = dict(body.get("values") or {})
    if body.get("index"):   # aktiivinen indeksi valitaan olemassa olevista
        info = _index_info(config.load())
        c = next((c for c in info["configs"] if c["config_id"] == body["index"]), None)
        if not c:
            return _err(400, "Tuntematon indeksi", "bad_request")
        values.update(EMBED_MODEL=c["model"], CHUNK_SIZE=str(c["chunk_size"]), CHUNK_OVERLAP=str(c["chunk_overlap"]))
    try:
        changed = envfile.update(values)
        config.load()   # tarkistaa kokonaisuuden (esim. painot eivät molemmat nollia)
    except ValueError as e:
        return _err(400, str(e), "bad_request")
    if set(changed) & {"SCORING_MODE", "W_KEYWORD", "W_SEMANTIC", "HYBRID_METHOD", "EMBED_MODEL", "CHUNK_SIZE",
                       "CHUNK_OVERLAP"}:
        threading.Thread(target=webapp.rescore, daemon=True).start()
    return {"ok": True, "changed": changed, "status": ai_status(request)}


@app.get("/api/ai/job")
def ai_job(url: str):
    """Indeksoidun ilmoituksen siivottu teksti: selitysten viitteiden merkkivälit osoittavat tähän tekstiin."""
    s = config.load()
    if not s.db_path.exists():
        return _err(409, "Semanttista indeksiä ei ole", "index_missing")
    from rag.store import Store
    st = Store(s.db_path)
    try:
        r = st.db.execute("SELECT job_id, clean_text, lang, posted, deadline, places FROM jobs WHERE url=?", (url,)).fetchone()
        if not r:
            return _err(404, "Ilmoitus ei ole vielä indeksissä", "not_found")
        return {**dict(r), "places": json.loads(r["places"] or "[]")}
    finally:
        st.close()


# ------------------------------------------------------------ chat

def _sse(gen):
    for ev in gen:
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    body = await request.json()
    q = str(body.get("question") or "").strip()[:1000]
    if not q:
        return _err(400, "Kysymys puuttuu", "bad_request")
    history = [{"role": m["role"], "content": str(m["content"])[:4000]} for m in (body.get("history") or [])[-6:]
               if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")]

    def events():
        from rag import service
        from rag.chat import answer
        try:
            s = config.load()
            retrieve = service.retriever(s)
            llm = service.llm("chat", s)
        except service.IndexMissing as e:
            yield {"type": "error", "message": str(e), "code": "index_missing"}
            return
        except LLMError as e:
            yield {"type": "error", "message": str(e), "code": "llm"}
            return
        yield from answer(q, history, llm, retrieve)

    return StreamingResponse(_sse(events()), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------ sopivuusselitys ja hakemuspaketti

@app.get("/api/explain")
def explain_endpoint(url: str, refresh: bool = False, cached_only: bool = False):
    from rag.explain import explain_url
    return _guard(lambda: explain_url(url, config.load(), refresh=refresh, cached_only=cached_only))


def _ensure_app(url: str) -> None:
    """CV:n räätälöinti tai hakemuspaketin luonti siirtää ilmoituksen seurantaan tilaan 'Hakemus työn alla'.
    Samalla ilmoitus tulee selainlaajennuksen hakemuslistaan (lista = paketit + seuranta)."""
    with webapp.app_lock:
        apps = webapp._apps()
        a = next((x for x in apps if x.get("url") == url), None)
        if a is None:
            a = webapp.app_from_job(url)
            if not a:
                return
            webapp.apply_fields(a, {"status": "drafting"}, initial=True)
            apps.insert(0, a)
        elif a["status"] == "interested":
            tracking.transition(a, "drafting")
        else:
            return
        webapp._save_apps(apps)


@app.post("/api/package")
async def package_endpoint(request: Request):
    body = await request.json()
    url, lang = str(body.get("url") or ""), body.get("lang")

    def run():
        from rag.package import package_url
        pkg = package_url(url, config.load(), lang=lang if lang in ("fi", "en") else None)
        salary = next((f["value"] for f in drafts.get_answers()["fields"] if f["key"] == "salary"), "")
        if salary:   # käyttäjän itse kirjoittama vakiovastaus, ei kielimallin
            next(s for s in pkg["sections"] if s["key"] == "salary")["text"] = salary
        rec = drafts.save_generated(pkg)
        _ensure_app(url)
        return {"url": url, **rec}
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_guard, run)


@app.get("/api/drafts")
def drafts_endpoint(url: str | None = None):
    if url is None:
        return {"drafts": drafts.all_drafts()}
    d = drafts.get(url)
    return {"url": url, **d} if d else _err(404, "Luonnosta ei ole vielä tehty", "not_found")


@app.post("/api/drafts")
async def drafts_save(request: Request):
    body = await request.json()
    url, sections = str(body.get("url") or ""), body.get("sections")
    if not isinstance(sections, list):
        return _err(400, "sections puuttuu", "bad_request")

    def run():
        secs = sections
        if body.get("verify"):
            from rag.package import reverify
            secs = reverify(url, [dict(x) for x in sections if isinstance(x, dict)], config.load())
        return {"url": url, **drafts.save_edit(url, secs)}
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_guard, run)


@app.get("/api/answers")
def answers_get():
    return drafts.get_answers()


@app.post("/api/answers")
async def answers_save(request: Request):
    body = await request.json()
    return drafts.save_answers(body.get("values") or {})


# ------------------------------------------------------------ selainlaajennus

def _ext_ok(request: Request) -> bool:
    return secrets.compare_digest(request.headers.get("x-jobhunter-key", ""), assist.extension_key())


@app.get("/api/assist/key")
def assist_key():
    """Vain oma käyttöliittymä lukee tämän: muut sivustot eivät saa vastausta luettavakseen (ei CORS-otsikoita)."""
    return {"key": assist.extension_key()}


@app.post("/api/assist/context")
async def assist_context(request: Request):
    if not _ext_ok(request):
        return _err(401, "Väärä laajennusavain", "key")
    body = await request.json()
    items = [{"url": d["url"], "title": d.get("title"), "company": d.get("company"), "package": True} for d in drafts.all_drafts()]
    have = {i["url"] for i in items}
    items += [{"url": a["url"], "title": a.get("title"), "company": a.get("company"), "package": False}
              for a in webapp._apps() if a.get("url") and a["url"] not in have]
    return {"items": items, "guess": assist.match_job(items, str(body.get("title") or ""), str(body.get("host") or ""))}


@app.post("/api/assist/suggest")
async def assist_suggest(request: Request):
    if not _ext_ok(request):
        return _err(401, "Väärä laajennusavain", "key")
    body = await request.json()
    s = config.load()
    cv = s.cv_path.read_text(encoding="utf-8") if s.cv_path.exists() else ""
    url = str(body.get("url") or "")
    return assist.suggest(str(body.get("question") or "")[:500], drafts.get_answers()["fields"],
                          drafts.get(url) if url else None, assist.contact_info(cv))


@app.post("/api/assist/generate")
async def assist_generate(request: Request):
    if not _ext_ok(request):
        return _err(401, "Väärä laajennusavain", "key")
    body = await request.json()
    url, question = str(body.get("url") or ""), str(body.get("question") or "").strip()[:500]
    if not url or not question:
        return _err(400, "Valitse ensin hakemus palkin valikosta", "bad_request")
    from rag.package import answer_url
    from starlette.concurrency import run_in_threadpool
    # saman lomakkeen muut kysymykset ja jo kirjoitetut vastaukset: projektit jaetaan kenttien kesken, ei toistoa
    others = [str(q)[:300] for q in (body.get("others") or []) if isinstance(q, str) and q.strip() and q.strip() != question][:15]
    written = [{"question": str(w.get("question") or "")[:300], "answer": str(w.get("answer") or "")[:3000]}
               for w in (body.get("written") or []) if isinstance(w, dict) and str(w.get("answer") or "").strip()][:8]
    return await run_in_threadpool(_guard, lambda: answer_url(url, question, config.load(), others, written))


# ------------------------------------------------------------ räätälöity CV

@app.get("/api/cv")
def cv_get(url: str, lang: str = "en"):
    cv = drafts.get_cv(url, lang)
    return cv if cv else _err(404, "CV:tä ei ole vielä räätälöity tälle ilmoitukselle", "not_found")


@app.post("/api/cv")
async def cv_make(request: Request):
    body = await request.json()
    url, lang = str(body.get("url") or ""), "fi" if body.get("lang") == "fi" else "en"

    def run():
        from rag.cvtailor import tailor_url
        cv = drafts.save_cv(url, lang, tailor_url(url, config.load(), lang))
        _ensure_app(url)
        return cv
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_guard, run)


@app.post("/api/cv/save")
async def cv_save(request: Request):
    body = await request.json()
    url, lang = str(body.get("url") or ""), "fi" if body.get("lang") == "fi" else "en"
    stored = drafts.get_cv(url, lang)
    if not stored:
        return _err(404, "CV:tä ei löydy", "not_found")

    def run():
        from rag.cvtailor import check_user_line, recheck_summary, user_source
        s = config.load()
        cv = drafts.merge_cv_edit(stored, body.get("cv") or {})
        if cv["summary"].get("source") == "edited":
            cv["summary"]["flags"] = recheck_summary(cv["summary"]["text"], s, lang)
        src = user_source(s)
        for u, _where in drafts.edited_units(cv):   # omat rivit: liputetaan, ei estetä
            u["flags"] = check_user_line(u["text"], src)
        return drafts.save_cv(url, lang, cv)
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_guard, run)


@app.get("/api/cv/base")
def cv_base_info():
    from rag.cvtailor import fi_path
    fp = fi_path(config.load().cv_path)
    return {"exists": fp.exists(), "path": fp.name}


@app.post("/api/cv/base")
async def cv_base(request: Request):
    """Tarkistettu suomennos (id -> teksti) tallennetaan pohjaksi data/cv_fi.md."""
    body = await request.json()
    lines = {str(k): str(v) for k, v in (body.get("lines") or {}).items()}
    from rag import cvsync
    from rag.cvtailor import save_fi_base

    def run():
        s = config.load()
        res = save_fi_base(lines, s, overwrite=bool(body.get("overwrite")))
        cvsync.snapshot(s, force=True)      # suomennokset muistiin rivin sisällön mukaan
        return res
    return _guard(run)


# ------------------------------------------------------------ CV:n arviointipaneeli (rag/panel.py)

def _runlog():
    from rag.panel import RunLog
    return RunLog(config.load().db_path)


@app.post("/api/cv/runs")
async def cv_run_start(request: Request):
    """Käynnistää paneeliajon taustalla ja palauttaa run_id:n heti. Samalle ilmoitukselle ja kielelle yksi ajo kerrallaan."""
    from rag import panel
    body = await request.json()
    url, lang = str(body.get("url") or ""), "fi" if body.get("lang") == "fi" else "en"
    if not url:
        return _err(400, "Ilmoituksen osoite puuttuu", "bad_request")
    log = _runlog()
    running = next((r for r in log.list(url) if r["lang"] == lang and r["status"] == "running"), None)
    if running:
        return {"run_id": running["run_id"], "existing": True}
    run_id = panel.start(url, lang, config.load(), log, on_result=lambda res: drafts.save_cv(url, lang, res))
    _ensure_app(url)
    return {"run_id": run_id, "existing": False}


@app.get("/api/cv/runs")
def cv_runs(url: str):
    return {"runs": _runlog().list(url)}


@app.get("/api/cv/runs/{run_id}")
def cv_run(run_id: str):
    """Tila ja kaikki tapahtumat (pollaus, jos SSE ei toimi, ja vanhan ajon loki)."""
    log = _runlog()
    run = log.get(run_id)
    return {"run": run, "events": log.events(run_id)} if run else _err(404, "Ajoa ei löydy", "not_found")


@app.get("/api/cv/runs/{run_id}/events")
def cv_run_events(run_id: str, request: Request, after: int = 0):
    """Tapahtumat SSE:nä. Last-Event-ID (tai ?after=) jatkaa siitä, mihin jäätiin. Heartbeat 15 s välein."""
    from rag.panel import FINAL
    log = _runlog()
    if not log.get(run_id):
        return _err(404, "Ajoa ei löydy", "not_found")
    try:
        start_seq = int(request.headers.get("last-event-id") or after)
    except ValueError:
        start_seq = after

    def gen():
        last, beat = start_seq, time.time()
        while True:
            evs = log.events(run_id, last)
            for ev in evs:
                last = ev["seq"]
                yield f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if not evs and log.get(run_id)["status"] in FINAL:
                yield "event: end\ndata: {}\n\n"
                return
            if time.time() - beat >= 15:
                yield ": heartbeat\n\n"
                beat = time.time()
            time.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/cv/runs/{run_id}/cancel")
def cv_run_cancel(run_id: str):
    from rag.panel import cancel
    return {"cancelled": cancel(run_id)}


# ------------------------------------------------------------ projektiehdotukset (rag/projects.py) ja cv_fi.md:n synkronointi

_scan_state: dict = {"running": False, "error": None, "done": 0, "total": 0, "repo": ""}


def _scan_bg():
    from rag import projects
    try:
        projects.scan(progress=lambda m: None, step=lambda i, n, r: _scan_state.update(done=i, total=n, repo=r))
        _scan_state["error"] = None
    except Exception as e:  # noqa: BLE001
        _scan_state["error"] = str(e)
    finally:
        _scan_state["running"] = False


@app.get("/api/projects")
def projects_get():
    from rag import projects
    data = json.loads(projects.PROPOSALS.read_text(encoding="utf-8")) if projects.PROPOSALS.exists() else {"proposals": []}
    st = projects.load_settings()
    for p in data["proposals"]:
        if p["kind"] == "new_project":
            p["default_meta"] = projects.default_meta(p)
    from rag.cvtailor import parse
    cv_md = config.load().cv_path.read_text(encoding="utf-8")
    entries = [p["title"] for p in projects.cv_projects(cv_md)]
    return {**data, "scan": dict(_scan_state), "own_emails": st["own_emails"], "notes": projects.load_notes(),
            "cv_projects": entries, "llm": st.get("llm", "default"), "name": parse(cv_md).name}


@app.get("/api/style")
def style_get():
    from rag import stylesamples, styleprofile
    return {"samples": stylesamples.listing(), "profile": styleprofile.load()}


@app.post("/api/style/profile")
async def style_profile_build():
    """Tyyliprofiili uudelleen muokatuista näytteistä (kirjoitusmalli, noin 0,03 $)."""
    from rag import styleprofile
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_guard, styleprofile.build)


@app.post("/api/style/save")
async def style_save(request: Request):
    from rag import stylesamples
    body = await request.json()
    return _guard(lambda: stylesamples.save_mine(str(body.get("id") or ""), str(body.get("mine") or "")))


@app.get("/api/projects/summary")
def projects_summary():
    from rag import projects
    return {**projects.summary(), "scan": {"running": _scan_state["running"]}}


@app.post("/api/projects/seen")
def projects_seen():
    from rag import projects
    return {"previous": projects.mark_seen()}


@app.post("/api/projects/scan")
def projects_scan():
    if not _scan_state["running"]:
        _scan_state.update(running=True, error=None, done=0, total=0, repo="")
        threading.Thread(target=_scan_bg, daemon=True, name="projects-scan").start()
    return dict(_scan_state)


@app.post("/api/projects/accept")
async def projects_accept(request: Request):
    from rag import projects
    body = await request.json()
    skills = [s["name"] for s in webapp.current_skills()]
    return _guard(lambda: projects.accept(str(body.get("id") or ""), body, config.load(), skills=skills))


@app.post("/api/projects/dismiss")
async def projects_dismiss(request: Request):
    from rag import projects
    body = await request.json()
    return {"ok": projects.dismiss(str(body.get("id") or ""))}


@app.post("/api/projects/notes")
async def projects_notes(request: Request):
    from rag import projects
    body = await request.json()
    title = str(body.get("title") or "").strip()
    if not title:
        return _err(400, "Projektin nimi puuttuu", "bad_request")
    return {"notes": projects.save_note(title, str(body.get("note") or ""))}


@app.post("/api/projects/email")
async def projects_email(request: Request):
    from rag import projects
    body = await request.json()
    return _guard(lambda: {"own_emails": projects.add_own_email(str(body.get("email") or ""))})


@app.post("/api/projects/skills")
async def projects_skills(request: Request):
    """Hyväksytyn projektin tekniikat osaamisprofiiliin (paino 1, taso tyhjä: käyttäjä tarkentaa Osaaminen-välilehdellä)."""
    body = await request.json()
    names = [str(n).strip() for n in (body.get("names") or []) if str(n).strip()][:30]
    cur = webapp.current_skills()
    have = {s["name"].lower() for s in cur}
    added = [n for n in names if n.lower() not in have]
    if added:
        skills = webapp.clean_skills(cur + [{"name": n, "weight": 1, "source": "projektit"} for n in added])
        webapp._write(webapp.SKILLS, {"skills": skills}, indent=2)
        threading.Thread(target=webapp.rescore, daemon=True).start()
    return {"added": added}


@app.get("/api/cv/sync")
def cv_sync_status():
    from rag import cvsync
    return cvsync.status(config.load())


@app.post("/api/cv/sync/translate")
async def cv_sync_translate(request: Request):
    """Käännä vain cv.md:n uudet ja muuttuneet rivit (muut tulevat muistista). Palauttaa tarkistettavat rivit."""
    def run():
        from rag import cvsync, service
        from rag.scoring import _embedder_factory
        s = config.load()
        return {"translation": cvsync.translate_missing(s, service.llm("writing", s), _embedder_factory(s)().embed_queries)}
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_guard, run)


@app.post("/api/cv/sync/save")
async def cv_sync_save(request: Request):
    from rag import cvsync
    body = await request.json()
    lines = {str(k): str(v) for k, v in (body.get("lines") or {}).items()}
    return _guard(lambda: cvsync.save(config.load(), lines))


# ------------------------------------------------------------ evaluointi ja merkinnät

_rows_cache: dict = {"at": 0.0, "rows": None, "key": None}
_rows_lock = threading.Lock()


def _scored_rows(s: config.Settings) -> list[dict]:
    from rag.__main__ import scored_rows
    key = (s.config_id, s.w_keyword, s.w_semantic, s.hybrid_method, s.aggregation, s.agg_k)
    with _rows_lock:
        if _rows_cache["rows"] is None or _rows_cache["key"] != key or time.time() - _rows_cache["at"] > 600:
            _rows_cache.update(rows=scored_rows(s), at=time.time(), key=key)
        return _rows_cache["rows"]


@app.get("/api/eval")
def eval_get():
    from rag.evaluate import load_runs
    from rag.label import LABELS, _load_full
    labels = list(_load_full(LABELS).values())
    return {"runs": load_runs()[-50:], "labels": labels,
            "counts": {str(k): sum(1 for x in labels if str(x.get("label")) == str(k)) for k in (0, 1, 2)}}


@app.post("/api/eval/run")
async def eval_run(request: Request):
    body = await request.json()

    def run():
        from rag.evaluate import evaluate, save_run, sweep
        from rag.label import load_labels
        s = config.load()
        labels = load_labels()
        if not labels:
            raise ValueError("Ei merkintöjä: merkitse ensin ilmoituksia")
        rows = _scored_rows(s)
        res = evaluate(rows, labels)
        extra = {"sweep": sweep(rows, labels, s)}
        return save_run(res, s, str(body.get("note") or "")[:200], extra)
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_guard, run)


@app.get("/api/eval/candidates")
def eval_candidates(n: int = 20):
    def run():
        from rag.label import candidates, load_labels
        rows = _scored_rows(config.load())
        out = []
        for r in candidates(rows, set(load_labels()))[:max(1, min(n, 60))]:
            j = r["job"]
            out.append({"url": j["url"], "title": j["title"], "company": j.get("company"), "location": j.get("location"),
                        "text": j.get("text") or "", "posted": j.get("posted") or (j.get("detail") or {}).get("posted")})
        return {"candidates": out}
    return _guard(run)


@app.get("/api/eval/review")
def eval_review():
    """Aiemmat merkinnät uudelleen arvioitaviksi koko tekstillä. Ensin ne, joissa merkintä ja kielimalli ovat eniten eri mieltä.
    Mallin arviota ei näytetä, jotta uusi merkintä on oma."""
    from rag.label import LABELS, _load_full
    labels = _load_full(LABELS)
    out = []
    for r in webapp._read(webapp.LATEST, {}).get("rows", []):
        lab = labels.get(r["job"]["url"])
        if not lab:
            continue
        j = r["job"]
        fit = (r.get("llm") or {}).get("fit")
        gap = abs(int(lab["label"]) * 45 - fit) if fit is not None else -1
        out.append({"url": j["url"], "title": j["title"], "company": j.get("company"), "location": j.get("location"),
                    "text": j.get("text") or "", "label": int(lab["label"]), "gap": gap})
    return {"items": sorted(out, key=lambda x: -x["gap"])}


@app.post("/api/eval/label")
async def eval_label(request: Request):
    from rag.label import save_label
    body = await request.json()
    try:
        label = int(body.get("label"))
        if label not in (0, 1, 2):
            raise ValueError
    except (TypeError, ValueError):
        return _err(400, "label pitää olla 0, 1 tai 2", "bad_request")
    url = str(body.get("url") or "")
    row = next((r for r in webapp._read(webapp.LATEST, {}).get("rows", []) if r["job"]["url"] == url), None)
    if not row:
        return _err(404, "Ilmoitusta ei löydy", "not_found")
    save_label(row["job"], label, str(body.get("note") or "")[:300])
    return {"ok": True}


# ------------------------------------------------------------ seuranta

@app.get("/api/tracking")
def tracking_info():
    apps = webapp._apps()
    return {"statuses": tracking.STATUSES, "labels": tracking.LABELS, "final": sorted(tracking.FINAL),
            "transitions": {k: sorted(v) for k, v in tracking.TRANSITIONS.items()},
            "reminders": tracking.reminders(apps), "follow_up_days": tracking.FOLLOW_UP_DAYS}


# ------------------------------------------------------------ vanhat rajapinnat

def _legacy_response(code: int, obj) -> Response:
    if isinstance(obj, webapp.Raw):
        headers = {"Content-Disposition": f'attachment; filename="{obj.filename}"'} if obj.filename else {}
        return Response(obj.body, status_code=code, media_type=obj.ctype, headers=headers)
    return JSONResponse(obj, status_code=code)


@app.get("/api/{rest:path}")
def legacy_get(rest: str, request: Request):
    q: dict[str, list[str]] = {}
    for k, v in request.query_params.multi_items():
        q.setdefault(k, []).append(v)
    return _legacy_response(*webapp.api_get("/api/" + rest, q))


@app.post("/api/{rest:path}")
async def legacy_post(rest: str, request: Request):
    raw = await request.body()
    from starlette.concurrency import run_in_threadpool
    return _legacy_response(*(await run_in_threadpool(webapp.api_post, "/api/" + rest, raw)))


# ------------------------------------------------------------ käyttöliittymä

@app.get("/vanha")
def legacy_ui():
    """Vanha käyttöliittymä: osaamisprofiilin, hakuasetusten ja yritysseurannan muokkaus."""
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/{rest:path}")
def spa(rest: str):
    if DIST.exists():
        f = (DIST / rest).resolve()
        if rest and f.is_file() and DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-store"})
    legacy = BASE / "static" / "index.html"   # uusi käyttöliittymä ei ole vielä käännetty
    return FileResponse(legacy, headers={"Cache-Control": "no-store"})


def serve(port: int = 8765, every: float = 6) -> None:
    import uvicorn
    webapp.CFG["port"] = port
    webapp.startup()
    threading.Thread(target=webapp.scheduler, args=(every,), daemon=True).start()
    print(f"Työnhaku käynnissä: http://127.0.0.1:{port}  (Ctrl+C lopettaa)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
