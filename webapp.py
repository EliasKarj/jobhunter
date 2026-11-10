#!/usr/bin/env python3
"""Työnhaku-webapp: ajaa jobhunterin taustalla ja näyttää tulokset selaimessa.

  python webapp.py              # http://127.0.0.1:8765, päivittyy itsestään 6 h välein
  python webapp.py --demo       # käyttää sample_jobs.json:ia (ei verkkoa)
  python webapp.py --every 3    # päivitysväli tunteina (0 = vain käsin)
"""
from __future__ import annotations
from datadir import DATA

import argparse
import datetime as dt
import json
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jobhunter as jh
from server import tracking

BASE = Path(__file__).resolve().parent
RES = DATA / "results"
STATIC = BASE / "static"
LATEST = RES / "latest.json"
STATE = RES / "state.json"      # tallennettu / haettu / piilotettu
SEEN = RES / "seen.json"        # milloin ilmoitus nähtiin ensimmäisen kerran

lock = threading.Lock()
status = {"running": False, "message": "", "error": None, "warning": None, "started": None}
CFG = {"demo": False, "port": 8765}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data, indent=None) -> None:
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------ Osaamisprofiili

write_lock = threading.Lock()
SKILLS = DATA / "skills.json"
SUGGEST = DATA / "ops_suggestions.json"
CATEGORIES = ["Ohjelmointikielet", "Web ja käyttöliittymät", "Taustajärjestelmät ja tietokannat",
              "Työkalut ja DevOps", "Pilvi, verkot ja tietoturva", "Sulautetut ja elektroniikka", "Muu"]
LEVELS = ["", "Opiskelen", "Perusteet", "Hyvä", "Vahva"]

# Yleisiä tekniikoita, joiden esiintymistä ilmoituksissa lasketaan ("puuttuvat taidot")
VOCAB = ["TypeScript", "JavaScript", "React", "Vue", "Angular", "Next.js", "Node.js", "Python", "Java",
         "C#", ".NET", "C++", "Kotlin", "Swift", "PHP", "Rust", "SQL", "PostgreSQL", "MySQL", "MongoDB",
         "Docker", "Kubernetes", "AWS", "Azure", "GCP", "Terraform", "Linux", "Git", "CI/CD", "Jenkins",
         "GitHub Actions", "REST", "GraphQL", "Microservices", "Scrum", "Agile", "Jira", "Figma", "HTML",
         "CSS", "Tailwind", "Spring", "Django", "Flask", "FastAPI", "Unity", "Unreal", "Matlab", "LabVIEW",
         "Power BI", "Tableau", "Power Platform", "SAP", "Salesforce", "Machine learning", "LLM", "DevOps",
         "Cypress", "Playwright", "Jest", "Selenium", "Kafka", "Redis", "Elasticsearch", "Firebase",
         "Supabase", "Prisma", "Embedded", "FPGA", "Tietoturva", "Testaus", "Pilvi"]


def clean_skills(raw) -> list[dict]:
    """Siivoa ja rajaa selaimelta tullut taitolista."""
    if not isinstance(raw, list) or len(raw) > 300:
        raise ValueError("skills")
    out, seen = [], set()
    for s in raw:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", "")).strip()[:60]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        aliases = s.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        aliases = [str(a).strip()[:40] for a in aliases[:12] if str(a).strip()]
        try:
            weight = max(0.0, min(5.0, float(s.get("weight", 1))))
        except (TypeError, ValueError):
            weight = 1.0
        out.append({
            "name": name, "aliases": aliases, "weight": round(weight * 2) / 2,
            "level": s.get("level") if s.get("level") in LEVELS else "",
            "category": s.get("category") if s.get("category") in CATEGORIES else "Muu",
            "source": str(s.get("source", ""))[:20],
        })
    return out


def current_skills() -> list[dict]:
    return _read(SKILLS, {"skills": []}).get("skills", [])


def ranked(rows: list[dict]) -> list[dict]:
    """SCORING_MODE (.env): keyword = nykyinen pisteytys sellaisenaan, semantic/hybrid käyttää RAG-indeksiä."""
    try:
        from rag.llmscore import attach_saved
        from rag.scoring import apply_mode
        return attach_saved(apply_mode(rows))
    except Exception as e:  # noqa: BLE001
        print(f"! semanttinen pisteytys ei käytössä: {e}", file=sys.stderr)
        return rows


def update_llm_scores() -> None:
    """Kärjen ja uusien ilmoitusten sopivuusarvio haun jälkeen (SCORE_PROVIDER). Virhe ei kaada hakua."""
    try:
        from rag import llmscore
        latest = _read(LATEST, None)
        if not latest or CFG["demo"]:
            return
        status["message"] = "Arvioidaan sopivuutta"
        llmscore.update(latest["rows"], progress=lambda d, n: status.update(message=f"Arvioidaan sopivuutta {d}/{n}"))
    except Exception as e:  # noqa: BLE001
        print(f"! sopivuusarvio epäonnistui: {e}", file=sys.stderr)
        status["warning"] = f"Sopivuusarvio epäonnistui ({e}). Muut pisteet ovat ajan tasalla."


def update_projects() -> None:
    """Projektiskanneri kerran viikossa haun jälkeen, omassa säikeessään (luonnokset vievät aikaa). Ei muuta CV:tä."""
    def run():
        try:
            from rag import projects
            projects.scan(progress=lambda m: None)
        except Exception as e:  # noqa: BLE001
            print(f"! projektiskannaus epäonnistui: {e}", file=sys.stderr)
    try:
        from rag import projects
        if CFG["demo"] or not projects.due():
            return
        threading.Thread(target=run, daemon=True, name="projects").start()
    except Exception as e:  # noqa: BLE001
        print(f"! projektiskannaus epäonnistui: {e}", file=sys.stderr)


def update_semantic_index() -> None:
    """Embeddaa uudet ilmoitukset hakuun liittyen (vain semantic/hybrid-tilassa, RAG_AUTO_INGEST=1)."""
    try:
        from rag import config as rag_config
        s = rag_config.load()
        if s.scoring_mode == "keyword" or not s.auto_ingest or CFG["demo"]:
            return
        from rag.ingest import update_index
        status["message"] = "Päivitetään semanttista indeksiä"
        update_index(s, progress=lambda m: status.update(message="Semanttinen indeksi:" + m.split(":", 1)[-1]))
    except Exception as e:  # noqa: BLE001
        print(f"! semanttisen indeksin päivitys epäonnistui: {e}", file=sys.stderr)
        status["warning"] = f"Semanttisen indeksin päivitys epäonnistui ({e}). Pisteet laskettiin avainsanoilla."


def unindexed_count() -> int:
    """Tuloksissa olevat ilmoitukset, joita ei ole semanttisessa indeksissä (esim. keskeytynyt haku)."""
    try:
        import sqlite3
        from rag import config as rag_config
        s = rag_config.load()
        if s.scoring_mode == "keyword" or not s.auto_ingest or CFG["demo"] or not s.db_path.exists():
            return 0
        urls = {r["job"]["url"] for r in _read(LATEST, {}).get("rows", []) if r.get("status") == "ok"}
        with sqlite3.connect(s.db_path) as db:
            have = {u for (u,) in db.execute("SELECT j.url FROM jobs j JOIN job_index i ON i.job_id = j.job_id")}
        return len(urls - have)
    except Exception as e:  # noqa: BLE001
        print(f"! indeksin tarkistus epäonnistui: {e}", file=sys.stderr)
        return 0


def resume_index() -> None:
    """Käynnistyksessä: jos edellisen haun indeksointi keskeytyi (palvelin käynnistettiin uudelleen kesken),
    indeksoi puuttuvat taustalla, pisteytä uudelleen ja hae uusille sopivuusarvio. Näkyy kuin haku sivupalkissa."""
    n = unindexed_count()
    if not n:
        return
    with lock:
        if status["running"]:
            return
        status.update(running=True, error=None, warning=None, message=f"Jatketaan keskeytynyttä indeksointia ({n} ilmoitusta)",
                      started=dt.datetime.now().isoformat(timespec="seconds"))
    try:
        update_semantic_index()
        rescore()
        update_llm_scores()
        rescore()
        status["message"] = "Valmis"
    except Exception as e:  # noqa: BLE001
        status["error"] = str(e)
        status["message"] = "Indeksoinnin jatkaminen epäonnistui"
    finally:
        status["running"] = False


def rescore() -> None:
    """Pisteytä jo haetut ilmoitukset uudelleen nykyisellä profiililla ilman uutta hakua."""
    with write_lock:
        latest = _read(LATEST, None)
        manual = manual_jobs()
        if not latest:
            if not manual:
                return
            latest = {"updated": dt.datetime.now().isoformat(timespec="seconds"), "demo": CFG["demo"],
                      "partial": True, "rows": []}
        prof = jh.load_profile(DATA / "profile.json")
        first = {r["job"]["url"]: r.get("first_seen") for r in latest["rows"]}
        jobs = [jh.Job(**r["job"]) for r in latest["rows"]]
        have = {j.url for j in jobs}
        jobs += [m for m in manual if m.url not in have]   # käsin lisätyt säilyvät aina
        rows = ranked(jh.score_all(jobs, prof))
        today = dt.date.today().isoformat()
        for r in rows:
            r["first_seen"] = first.get(r["job"]["url"]) or today
        latest["rows"] = rows
        _write(LATEST, latest)


_gaps_cache: dict = {"key": None, "val": []}
_gaps_busy = threading.Lock()


def _gaps_key():
    try:
        st = LATEST.stat()
        latest = (st.st_mtime_ns, st.st_size)
    except OSError:
        latest = None
    known = tuple(sorted(s["name"].lower() for s in current_skills()))
    return (latest, known)


def _compute_gaps(limit: int = 8) -> list[dict]:
    latest = _read(LATEST, None) or {}
    rows = [r for r in latest.get("rows", []) if r.get("status") == "ok"]
    if len(rows) < 3:
        return []
    known = set()
    for s in current_skills():
        for t in [s["name"], *s.get("aliases", [])]:
            known.add(t.lower().rstrip("*"))
    blobs = [f'{r["job"]["title"]} {r["job"]["text"][:6000]}'.lower() for r in rows]  # teksti kerran, ei joka termille
    out = []
    for name in VOCAB:
        if name.lower() in known:
            continue
        pat = jh._term(name)
        n = sum(1 for b in blobs if pat.search(b))
        if n >= 2:
            out.append({"name": name, "count": n, "share": round(n / len(rows), 2)})
    out.sort(key=lambda g: -g["count"])
    return out[:limit]


def refresh_gaps() -> None:
    """Laske puuteanalyysi taustalla (isolla tulosjoukolla se kestää), tulos välimuistiin."""
    if not _gaps_busy.acquire(blocking=False):
        return
    try:
        key = _gaps_key()
        if _gaps_cache["key"] != key:
            _gaps_cache.update(key=key, val=_compute_gaps())
    except Exception as e:  # noqa: BLE001
        print(f"! puuteanalyysi epäonnistui: {e}", file=sys.stderr)
    finally:
        _gaps_busy.release()


def gaps(limit: int = 8) -> list[dict]:
    """Palauta välimuistissa oleva puuteanalyysi heti; päivitys käynnistyy taustalle tarvittaessa."""
    if _gaps_cache["key"] != _gaps_key():
        threading.Thread(target=refresh_gaps, daemon=True).start()
    return _gaps_cache["val"][:limit]


def skills_payload() -> dict:
    sug = _read(SUGGEST, {"suggestions": []})
    names = {s["name"].lower() for s in current_skills()}
    return {
        "skills": current_skills(), "categories": CATEGORIES, "levels": LEVELS,
        "suggestions": [s for s in sug.get("suggestions", []) if s["name"].lower() not in names],
        "ops_url": sug.get("ops_url", ""), "ops_note": sug.get("note", ""),
        "gaps": gaps(),
    }


# ------------------------------------------------------------ Ilmoitussivu
_LINE_SPLIT = re.compile(r"\n+|(?<=[.!?])\s+(?=[A-ZÅÄÖ])")
_YEARS = re.compile(r"\d+\s*(?:[-–]\s*\d+\s*)?\+?\s*(?:vuo(?:tta|den|si|sia|n)|v\.|years?|yrs)", re.I)
_EXP_WORDS = re.compile(r"kokemus|experience|vaadi|edellyt|required|vähintään|minimum", re.I)
_LANG = re.compile(r"suomen|suomi|finnish|englannin|englanti|english|ruotsin|ruotsi|swedish", re.I)
_LANG_CTX = re.compile(r"kiel|taito|language|fluent|sujuv|erinomai|vaadi|required|skills|speak|puhu|kirjall|written", re.I)
_MODE = re.compile(r"etätyö|etänä|remote|hybrid|hybridi|lähityö|toimistolla|on-?site|paikan päällä", re.I)


def _snips(text: str, pred, limit: int) -> list[str]:
    out = []
    for ln in _LINE_SPLIT.split(text):
        ln = ln.strip(" -•*✅\t")
        if 8 <= len(ln) <= 400 and pred(ln) and ln not in out:
            out.append(ln if len(ln) <= 220 else ln[:217] + "…")
            if len(out) >= limit:
                break
    return out


def job_insights(row: dict) -> dict:
    """Ilmoituskohtainen erittely: mitä osaamisestasi osuu, mitä puuttuu, vaatimukset ja muut saman yrityksen paikat."""
    j = row["job"]
    text = j.get("text", "") or ""
    blob = f'{j.get("title", "")}\n{text}'.lower()
    prof = jh.load_profile(DATA / "profile.json")
    matched = [{"name": sk["name"], "weight": sk["weight"]}
               for sk in prof.get("skill_list", [])
               if sk["weight"] > 0 and any(jh._term(t).search(blob) for t in sk["terms"])]
    matched.sort(key=lambda m: -m["weight"])
    known = set()
    for s in current_skills():
        for t in [s["name"], *s.get("aliases", [])]:
            known.add(t.lower().rstrip("*"))
    missing = [n for n in VOCAB if n.lower() not in known and jh._term(n).search(blob)]
    company = (j.get("company") or "").strip().lower()
    same = []
    if company:
        for r in _read(LATEST, {}).get("rows", []):
            o = r["job"]
            if r.get("status") == "ok" and o["url"] != j["url"] and (o.get("company") or "").strip().lower() == company:
                same.append({"title": o["title"], "url": o["url"], "score": r["score"]})
        same.sort(key=lambda x: -x["score"])
    desc = re.sub(r"\n*Hakuaika umpeutuu \d\d\.\d\d\.\d{4}\s*$", "", text)
    return {
        "description": desc[:40000],
        "clean": bool(j.get("detail")),
        "matched": matched,
        "missing": missing,
        "experience": _snips(text, lambda s: bool(_YEARS.search(s)) or (bool(_EXP_WORDS.search(s)) and "kokemus" in s.lower()), 5),
        "languages": _snips(text, lambda s: bool(_LANG.search(s)) and bool(_LANG_CTX.search(s)), 4),
        "mode": _snips(text, lambda s: bool(_MODE.search(s)), 3),
        "same_company": same[:6],
    }


# ----------------------------------------------------------- Hakemusseuranta

APPS = DATA / "applications.json"   # ei results-kansiossa, jotta tulokset voi tyhjentää turvallisesti
app_lock = threading.Lock()
STATUSES = tracking.STATUSES
APP_TEXT = {"title": 200, "company": 120, "location": 120, "url": 500, "source": 30,
            "contact_name": 120, "contact_email": 160, "contact_phone": 40, "salary": 120,
            "next_step": 300, "notes": 10000, "posting_text": 30000}
APP_DATES = ("deadline", "applied_date", "next_date")


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _iso_date(v) -> str:
    v = str(v or "").strip()[:10]
    try:
        dt.date.fromisoformat(v)
        return v
    except ValueError:
        return ""


def _apps() -> list[dict]:
    apps = _read(APPS, {"apps": []}).get("apps", [])
    for a in apps:
        a["status"] = tracking.normalize(a.get("status"))
    return apps


def _save_apps(apps: list[dict]) -> None:
    _write(APPS, {"apps": apps}, indent=1)


def new_app() -> dict:
    now = _now()
    a = {"id": secrets.token_hex(6), "status": "interested", "created": now, "updated": now,
         "history": [{"status": "interested", "at": now}], "score": None, "reasons": [], "posting_saved": ""}
    for k in APP_TEXT:
        a[k] = ""
    for k in APP_DATES:
        a[k] = ""
    a["title"] = "Uusi hakemus"
    return a


def apply_fields(a: dict, fields: dict, initial: bool = False) -> None:
    """Kirjoita sallitut kentät hakemukseen rajoitettuina ja siivottuina."""
    for k, limit in APP_TEXT.items():
        if k not in fields:
            continue
        v = str(fields[k] if fields[k] is not None else "")[:limit]
        if k not in ("notes", "posting_text"):
            v = v.strip()
        if k == "url" and v and not re.match(r"https?://", v, re.I):
            v = ""
        if k == "title" and not v:
            v = a.get("title") or "Nimetön hakemus"
        if k == "posting_text" and v != a.get("posting_text"):
            a["posting_saved"] = _now()
        a[k] = v
    for k in APP_DATES:
        if k in fields:
            a[k] = _iso_date(fields[k])
    st = tracking.normalize(fields["status"]) if fields.get("status") else None
    if st and st != a.get("status"):
        if initial:  # uusi hakemus voidaan kirjata suoraan mihin tahansa tilaan (esim. aiemmin haettu)
            a["status"], a["history"] = st, [{"status": st, "at": _now()}]
            if st == "applied" and not a.get("applied_date"):
                a["applied_date"] = dt.date.today().isoformat()
        else:
            tracking.transition(a, st, _now())  # ValueError, jos siirtymä ei ole sallittu
        if st == "applied" and a.get("url"):   # lähetetty versio talteen merkinnällä HAETTU
            try:
                from server import archive
                archive.mark_sent(a["url"], _read(DATA / "cvs.json", {}))
            except OSError as e:
                print(f"! CV:n arkistointi epäonnistui: {e}", file=sys.stderr)
    a["updated"] = _now()


def app_from_job(url: str) -> dict | None:
    """Luo hakemus viimeisimmistä hakutuloksista ja tallenna ilmoituksen teksti mukaan."""
    latest = _read(LATEST, None) or {}
    row = next((r for r in latest.get("rows", []) if r["job"]["url"] == url), None)
    if not row:
        return None
    j = row["job"]
    a = new_app()
    text = (j.get("text") or "")[:APP_TEXT["posting_text"]]
    apply_fields(a, {"title": j.get("title", ""), "company": j.get("company", ""),
                     "location": j.get("location") or row.get("area") or "", "url": j["url"],
                     "source": j.get("source", ""), "posting_text": text})
    dl = jh._deadline(text)
    if dl:
        a["deadline"] = dl.isoformat()
    a["score"] = round(row.get("score", 0))
    a["reasons"] = list(row.get("reasons", []))[:20]
    a["posting_saved"] = _now()
    return a


def migrate_applied() -> None:
    """Vanha 'merkitty haetuksi' -merkintä siirretään kerran hakemusseurantaan."""
    state = _read(STATE, {})
    urls = [u for u, s in state.items() if s.get("applied")]
    if not urls:
        return
    with app_lock:
        apps = _apps()
        have = {a.get("url") for a in apps}
        moved = False
        for u in urls:
            if u in have:
                moved = True
            else:
                a = app_from_job(u)
                if a:
                    apply_fields(a, {"status": "applied"})
                    apps.append(a)
                    moved = True
            if moved:
                state[u].pop("applied", None)
                if not any(state[u].values()):
                    state.pop(u)
            moved = False
        _save_apps(apps)
    _write(STATE, state)


def save_rows(jobs, prof, partial: bool) -> None:
    """Pisteytä ja tallenna. Osatallennus yhdistetään edelliseen tulokseen, ettei vanha lista katoa."""
    have = {j.url for j in jobs}
    rows = ranked(jh.score_all(list(jobs) + [m for m in manual_jobs() if m.url not in have], prof))
    today = dt.date.today().isoformat()
    seen = _read(SEEN, {})
    for r in rows:
        r["first_seen"] = seen.setdefault(r["job"]["url"], today)
    _write(SEEN, seen)
    with write_lock:
        old = _read(LATEST, {}).get("rows", [])
        fresh = {r["job"]["url"] for r in rows}
        if partial:
            rows = rows + [r for r in old if r["job"]["url"] not in fresh]
        else:
            # seuratut (tallennetut/haetut) ilmoitukset säilyvät, vaikka ne katoaisivat hausta
            keep = _interest_urls()
            rows = rows + [r for r in old if r["job"]["url"] in keep and r["job"]["url"] not in fresh]
        _write(LATEST, {
            "updated": dt.datetime.now().isoformat(timespec="seconds"),
            "demo": CFG["demo"],
            "partial": partial,
            "rows": rows,
        })


def refresh() -> None:
    """Hae, pisteytä ja tallenna. Ajetaan omassa säikeessään."""
    with lock:
        if status["running"]:
            return
        status.update(running=True, error=None, warning=None, message="Aloitetaan",
                      started=dt.datetime.now().isoformat(timespec="seconds"))
    try:
        try:
            prof = jh.load_profile(DATA / "profile.json")
        except (OSError, ValueError) as e:
            raise RuntimeError(f"profile.json tai skills.json puuttuu tai on virheellinen ({e})") from e
        if CFG["demo"]:
            data = _read(BASE / "sample_jobs.json", [])
            jobs = [jh.Job(**d) for d in data]
        else:
            def checkpoint(done_jobs):
                save_rows(done_jobs, prof, partial=True)  # keskeytys ei hukkaa jo luettuja
            jobs = jh.collect_jobs(prof, 1.5, lambda m: status.update(message=m), checkpoint)
            if not jobs:
                raise RuntimeError("Hakuun ei tullut yhtään ilmoitusta. Tarkista verkkoyhteys "
                                   "ja että Duunitori ja Jobly aukeavat selaimessa.")
        status["message"] = "Pisteytetään"
        prev_n = sum(1 for r in _read(LATEST, {}).get("rows", []) if r["job"].get("source") != "manual")
        suspicious = (not CFG["demo"]) and prev_n >= 200 and len(jobs) < 0.5 * prev_n
        if suspicious:
            # Haku palautti selvästi vähemmän kuin ennen: älä korvaa vanhoja tuloksia, vaan yhdistä
            status["warning"] = (f"Haku palautti vain {len(jobs)} ilmoitusta, kun edellisellä kerralla niitä oli {prev_n}. "
                                 "Vanhat tulokset säilytettiin. Duunitorin sivu on voinut muuttua tai verkko oli hidas. "
                                 "Kokeile hakua uudelleen.")
        save_rows(jobs, prof, partial=suspicious)
        update_semantic_index()
        rescore()  # jos profiilia muokattiin haun aikana (ja uudet embeddingit mukaan)
        update_llm_scores()   # tarvitsee ajantasaisen hybridijärjestyksen
        rescore()
        update_projects()
        if not CFG["demo"]:
            status["message"] = "Tarkistetaan seuratut ilmoitukset"
            fresh = {j.url for j in jobs}
            update_gone(_interest_urls() - fresh)
            g = _read(GONE, {})
            for u in fresh & set(g):
                g.pop(u)
            _write(GONE, g)
        status["message"] = "Valmis"
    except Exception as e:  # noqa: BLE001
        status["error"] = str(e)
        status["message"] = "Päivitys epäonnistui"
    finally:
        status["running"] = False


def start_refresh() -> bool:
    if status["running"]:
        return False
    threading.Thread(target=refresh, daemon=True).start()
    return True


def scheduler(every_h: float) -> None:
    while True:
        try:
            backup_data()
        except OSError:
            pass
        latest = _read(LATEST, None)
        stale = True
        if latest:
            age = dt.datetime.now() - dt.datetime.fromisoformat(latest["updated"])
            stale = every_h > 0 and age.total_seconds() > every_h * 3600
        if stale and not status["running"] and (latest is None or every_h > 0):
            refresh()
            if status["error"]:
                time.sleep(600)  # älä yritä virheen jälkeen uudestaan heti
        time.sleep(60)


# ------------------------------------------------------------ Käsin lisätyt ilmoitukset

MANUAL = DATA / "manual_jobs.json"   # käyttäjän omaa dataa: ei results-kansiossa
manual_lock = threading.Lock()


def manual_jobs() -> list:
    out = []
    for d in _read(MANUAL, {"jobs": []}).get("jobs", []):
        try:
            out.append(jh.Job(**d))
        except TypeError:
            continue
    return out


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w ]", " ", (s or "").lower()).split())


def manual_action(body: dict) -> dict:
    with manual_lock:
        data = _read(MANUAL, {"jobs": []})
        jobs = data.get("jobs", [])
        if body.get("action") == "delete":
            url = str(body.get("url", ""))
            data["jobs"] = [j for j in jobs if j["url"] != url]
            if len(data["jobs"]) == len(jobs):
                raise ValueError("Ilmoitusta ei löydy")
            _write(MANUAL, data, indent=1)
            latest = _read(LATEST, None)
            if latest:
                latest["rows"] = [r for r in latest["rows"] if r["job"]["url"] != url]
                with write_lock:
                    _write(LATEST, latest)
            return {"ok": True}
        f = body.get("fields") if isinstance(body.get("fields"), dict) else {}
        title = " ".join(str(f.get("title", "")).split())[:200]
        text = str(f.get("text", ""))[:40000].strip()
        company = " ".join(str(f.get("company", "")).split())[:120]
        location = " ".join(str(f.get("location", "")).split())[:120]
        url = str(f.get("url", "")).strip()[:500]
        if not title or len(text) < 30:
            raise ValueError("Anna otsikko ja ilmoituksen teksti (vähintään muutama lause)")
        if url and not re.match(r"https?://", url, re.I):
            raise ValueError("Linkin pitää alkaa https://")
        # kaksoisilmoitusten tarkistus: sama osoite tai sama otsikko + yritys
        rows = _read(LATEST, {}).get("rows", []) + [{"job": j} for j in jobs]
        for r in rows:
            o = r["job"]
            if (url and o["url"] == url) or (_norm(o.get("title")) == _norm(title) and _norm(o.get("company")) == _norm(company)):
                return {"ok": False, "duplicate": o["url"]}
        deadline = _iso_date(f.get("deadline"))
        if deadline:
            d = dt.date.fromisoformat(deadline)
            text += f"\nHakuaika umpeutuu {d.strftime('%d.%m.%Y')}"
        if not url:
            url = "manual://" + secrets.token_hex(6)
        job = {"title": title, "url": url, "company": company, "location": location, "text": text,
               "source": "manual",
               "detail": {"company": company, "company_url": "", "posted": dt.date.today().isoformat(),
                          "deadline": deadline, "employment": "", "places": [location] if location else [], "salary": ""}}
        if len(jobs) >= 300:
            raise ValueError("Käsin lisättyjä ilmoituksia on jo 300")
        jobs.insert(0, job)
        _write(MANUAL, {"jobs": jobs}, indent=1)
    rescore()
    return {"ok": True, "url": url}


# ------------------------------------------------------------ Palaute pisteytykselle

_STOP = {"with", "from", "helsinki", "vaasa", "tampere", "espoo", "vantaa", "oyj", "finland", "suomi", "suomessa",
         "oulu", "turku", "useita", "paikkakuntia", "etsimme", "haemme", "hakee", "työntekijä", "työntekijöitä",
         "tehtäviin", "ammattilaisia", "osaajia", "ammattilainen", "avoimet", "määräaikainen", "vakituinen"}


def feedback_suggestions() -> dict:
    """Ehdotuksia siitä, mitä piilottaminen ja tallentaminen kertovat. Vaatii riittävästi valintoja."""
    state = _read(STATE, {})
    prof = _read(PROFILE, {})
    rows = {r["job"]["url"]: r for r in _read(LATEST, {}).get("rows", [])}
    hidden = [rows[u] for u, v in state.items() if v.get("hidden") and u in rows]
    liked = [rows[u] for u, v in state.items() if (v.get("saved") or v.get("applied")) and u in rows]
    out = {"hidden": len(hidden), "liked": len(liked), "exclude": [], "block": [], "watch": []}
    if len(hidden) + len(liked) < 8:
        return out
    def toks(r):
        return {t for t in re.findall(r"[a-zäöå][a-zäöå\-]{3,}", r["job"]["title"].lower()) if t not in _STOP}
    known = [t.lower() for k in ("field_title_terms", "it_title_terms", "level_positive", "exclude_title_terms")
             for t in prof.get(k, [])]
    hc: dict = {}
    lc: dict = {}
    for r in hidden:
        for t in toks(r):
            hc[t] = hc.get(t, 0) + 1
    for r in liked:
        for t in toks(r):
            lc[t] = lc.get(t, 0) + 1
    for t, n in sorted(hc.items(), key=lambda x: -x[1]):
        if n >= 3 and lc.get(t, 0) == 0 and not any(t in k or k in t for k in known):
            out["exclude"].append({"term": t, "count": n})
    blocked = {c.lower() for c in prof.get("block_companies", [])}
    watched = {c["name"].lower() for c in _companies()}
    cc: dict = {}
    for r in hidden:
        c = (r["job"].get("company") or "").strip()
        if c:
            cc[c] = cc.get(c, 0) + 1
    out["block"] = [{"company": c, "count": n} for c, n in cc.items()
                    if n >= 3 and c.lower() not in blocked and not any(c.lower() == (x["job"].get("company") or "").lower() for x in liked)]
    lk: dict = {}
    for r in liked:
        c = (r["job"].get("company") or "").strip()
        if c:
            lk[c] = lk.get(c, 0) + 1
    out["watch"] = [{"company": c, "count": n} for c, n in lk.items()
                    if n >= 2 and not any(c.lower() in w or w in c.lower() for w in watched)]
    out["exclude"] = out["exclude"][:8]
    return out


# ------------------------------------------------------------ Asetukset

PROFILE = DATA / "profile.json"
LIST_FIELDS = ["remote_terms", "level_positive", "level_negative", "title_senior_terms", "field_title_terms",
               "it_title_terms", "it_body_terms", "exclude_title_terms", "block_companies"]


def _strs(v, lower: bool, maxn: int = 400, maxlen: int = 100) -> list[str]:
    out, seen = [], set()
    for x in v if isinstance(v, list) else []:
        t = " ".join(str(x).split())[:maxlen]
        if lower:
            t = t.lower()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:maxn]


def _num(v, lo, hi, default, integer=False):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    x = max(lo, min(hi, x))
    return int(round(x)) if integer else round(x, 2)


def settings_payload() -> dict:
    prof = _read(PROFILE, {})
    s = prof.get("search", {})
    b = s.get("broad", {})
    out = {
        "queries": s.get("queries", []), "areas": s.get("areas", []),
        "broad_enabled": bool(b.get("enabled", False)), "max_pages": b.get("max_pages", 100),
        "max_jobs": b.get("max_jobs", 5000), "workers": s.get("workers", 4),
        "locations": prof.get("locations", []), "remote_bonus": prof.get("remote_bonus", 3),
        "skills_target": prof.get("skills_target", 12), "weekly_goal": prof.get("weekly_goal", 5),
    }
    for k in LIST_FIELDS:
        out[k] = prof.get(k, [])
    return out


def save_settings(body: dict) -> dict:
    with write_lock:
        prof = _read(PROFILE, None)
        if not isinstance(prof, dict):
            raise ValueError("profile.json puuttuu tai on virheellinen")
        try:
            (DATA / "profile.json.bak").write_text(PROFILE.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
        s = prof.setdefault("search", {})
        if "queries" in body:
            s["queries"] = _strs(body["queries"], False, 80, 120)
        if "areas" in body:
            s["areas"] = _strs(body["areas"], False, 15, 60)
        b = s.setdefault("broad", {})
        if "broad_enabled" in body:
            b["enabled"] = bool(body["broad_enabled"])
        if "max_pages" in body:
            b["max_pages"] = _num(body["max_pages"], 1, 300, 100, True)
        if "max_jobs" in body:
            b["max_jobs"] = _num(body["max_jobs"], 100, 20000, 5000, True)
        if "workers" in body:
            s["workers"] = _num(body["workers"], 1, 8, 4, True)
        if "locations" in body and isinstance(body["locations"], list):
            locs = []
            for l in body["locations"][:20]:
                if not isinstance(l, dict):
                    continue
                name = " ".join(str(l.get("name", "")).split())[:60]
                if not name:
                    continue
                al = _strs(l.get("aliases"), True, 12, 60) or [name.lower()]
                locs.append({"name": name, "weight": _num(l.get("weight"), 0, 20, 10), "aliases": al})
            prof["locations"] = locs
        if "remote_bonus" in body:
            prof["remote_bonus"] = _num(body["remote_bonus"], 0, 10, 3)
        if "skills_target" in body:
            prof["skills_target"] = _num(body["skills_target"], 1, 50, 12, True)
        if "weekly_goal" in body:
            prof["weekly_goal"] = _num(body["weekly_goal"], 0, 100, 5, True)
        for k in LIST_FIELDS:
            if k in body:
                prof[k] = _strs(body[k], True)
        _write(PROFILE, prof, indent=2)
    rescore()
    return settings_payload()


# ------------------------------------------------------------ Varmuuskopio

BACKUPS = DATA / "backups"
BACKUP_FILES = [DATA / "applications.json", DATA / "skills.json", DATA / "profile.json",
                DATA / "companies.json", DATA / "manual_jobs.json", STATE, DATA / "drafts.json", DATA / "cvs.json", DATA / "answers.json"]


def backup_data(manual: bool = False) -> str | None:
    """Kopioi käyttäjän omat tiedot backups/-kansioon. Päivittäinen kopio tehdään kerran päivässä."""
    name = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S") if manual else dt.date.today().isoformat()
    dest = BACKUPS / name
    if dest.exists() and not manual:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in BACKUP_FILES:
        if f.exists():
            (dest / f.name).write_bytes(f.read_bytes())
            n += 1
    if n == 0:
        try:
            dest.rmdir()
        except OSError:
            pass
        return None
    dirs = sorted(d for d in BACKUPS.iterdir() if d.is_dir())
    for d in dirs[:-14] if len(dirs) > 14 else []:   # säilytä 14 uusinta
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    return name


def list_backups() -> list[dict]:
    if not BACKUPS.exists():
        return []
    return [{"name": d.name, "files": sorted(f.name for f in d.iterdir())}
            for d in sorted((d for d in BACKUPS.iterdir() if d.is_dir()), reverse=True)]


# ------------------------------------------------------------ Viennit

def _csv_cell(v) -> str:
    s = "" if v is None else str(v).replace("\r", " ").replace("\n", " ")
    if s[:1] in ("=", "+", "-", "@", "\t"):
        s = "'" + s          # estä kaavojen suoritus Excelissä
    return '"' + s.replace('"', '""') + '"'


def _csv(header: list[str], rows: list[list]) -> bytes:
    lines = [";".join(_csv_cell(h) for h in header)]
    lines += [";".join(_csv_cell(c) for c in r) for r in rows]
    return ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8")   # BOM: Excel lukee ääkköset oikein


def export_apps_csv() -> bytes:
    cols = ["title", "company", "location", "status", "applied_date", "deadline", "next_date", "next_step",
            "contact_name", "contact_email", "contact_phone", "salary", "score", "url", "created", "notes"]
    head = ["Otsikko", "Yritys", "Paikka", "Tila", "Haettu", "Hakuaika päättyy", "Seuraava päivä",
            "Seuraava askel", "Yhteyshenkilö", "Sähköposti", "Puhelin", "Palkka", "Pisteet", "Linkki",
            "Luotu", "Muistiinpanot"]
    return _csv(head, [[a.get(c, "") for c in cols] for a in _apps()])


def export_jobs_csv() -> bytes:
    rows = _read(LATEST, {}).get("rows", [])
    state = _read(STATE, {})
    out = []
    for r in sorted(rows, key=lambda r: -r.get("score", 0)):
        if r.get("status") != "ok":
            continue
        j = r["job"]
        d = j.get("detail") or {}
        st = state.get(j["url"], {})
        out.append([round(r.get("score", 0)), j.get("title"), j.get("company"), r.get("area") or j.get("location"),
                    r.get("deadline") or "", d.get("employment", ""), r.get("first_seen") or "",
                    "kyllä" if st.get("saved") else "", "kyllä" if st.get("applied") else "", j["url"]])
    return _csv(["Pisteet", "Otsikko", "Yritys", "Alue", "Hakuaika päättyy", "Työsuhde", "Nähty ensin",
                 "Tallennettu", "Haettu", "Linkki"], out)


def _ics_esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def export_ics() -> bytes:
    """Kalenteriviennti: hakuajat ja seuraavat askeleet (koko päivän tapahtumat)."""
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//jobhunter//FI", "CALSCALE:GREGORIAN"]
    for a in _apps():
        items = []
        if a.get("deadline") and a.get("status") in ("interested", "drafting"):
            items.append(("dl", a["deadline"], f"Hakuaika päättyy: {a.get('title', '')}"))
        if a.get("next_date") and a.get("status") not in tracking.FINAL:
            label = a.get("next_step") or "Seuraava askel"
            items.append(("nx", a["next_date"], f"{label}: {a.get('title', '')}"))
        for kind, day, summary in items:
            try:
                d = dt.date.fromisoformat(day)
            except ValueError:
                continue
            nxt = d + dt.timedelta(days=1)
            desc = f"{a.get('company', '')}\n{a.get('url', '')}".strip()
            out += ["BEGIN:VEVENT", f"UID:{a['id']}-{kind}@jobhunter", f"DTSTAMP:{now}",
                    f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{nxt.strftime('%Y%m%d')}",
                    f"SUMMARY:{_ics_esc(summary)}", f"DESCRIPTION:{_ics_esc(desc)}", "END:VEVENT"]
    out.append("END:VCALENDAR")
    return ("\r\n".join(out) + "\r\n").encode("utf-8")


# ------------------------------------------------------------ Tilastot

def stats() -> dict:
    apps = _apps()
    counts = {s: 0 for s in STATUSES}
    applied = responded = 0
    for a in apps:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
        hist = {h.get("status") for h in a.get("history", [])}
        was_applied = bool(hist & {"applied", "interview", "offer"}) or bool(a.get("applied_date"))
        if was_applied:
            applied += 1
            if hist & {"interview", "offer", "rejected"}:
                responded += 1
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    weeks = []
    for i in range(7, -1, -1):
        start = monday - dt.timedelta(weeks=i)
        end = start + dt.timedelta(days=7)
        n = sum(1 for a in apps if a.get("applied_date") and start.isoformat() <= a["applied_date"] < end.isoformat())
        weeks.append({"start": start.isoformat(), "count": n})
    goal = _read(PROFILE, {}).get("weekly_goal", 5)
    cvs = _read(DATA / "cvs.json", {})
    return {"outcomes": tracking.outcomes(apps, cvs if isinstance(cvs, dict) else {}),
            "counts": counts, "total": len(apps), "applied": applied, "responded": responded,
            "rate": round(100 * responded / applied) if applied else None,
            "weeks": weeks, "this_week": weeks[-1]["count"], "goal": goal}


# ------------------------------------------------------------ Yritykset

COMPANIES = DATA / "companies.json"
# Tarkistetut rekrysivut (avattu 8.10.2026). Ryhmä: Vaasa = paikalliset, Iso = suuret yritykset
CATALOG = [
    ("Wärtsilä", "https://www.wartsila.com/careers", "Vaasa"),
    ("ABB", "https://careers.abb/", "Vaasa"),
    ("Danfoss", "https://www.danfoss.com/en/about-danfoss/careers/", "Vaasa"),
    ("Wapice", "https://wapice.com/careers", "Vaasa"),
    ("Vaasan kaupunki", "https://duunitori.fi/tyopaikat/yritys/vaasan-kaupunki", "Vaasa"),
    ("Tietoevry", "https://careers.tieto.com/", "IT"),
    ("Digia", "https://digia.com/en/careers/", "IT"),
    ("Solita", "https://www.solita.fi/en/careers/", "IT"),
    ("Reaktor", "https://www.reaktor.com/careers", "IT"),
    ("Futurice", "https://www.futurice.com/careers", "IT"),
    ("Gofore", "https://gofore.com/en/careers/", "IT"),
    ("CGI", "https://www.cgi.com/fi/fi/ura", "IT"),
    ("Accenture", "https://www.accenture.com/fi-en/careers", "IT"),
    ("Supercell", "https://supercell.com/en/careers/", "IT"),
    ("Nokia", "https://www.nokia.com/about-us/careers/", "Iso"),
    ("Fortum", "https://www.fortum.com/about-us/careers", "Iso"),
    ("Elisa", "https://elisa.com/corporate/careers/", "Iso"),
    ("Telia", "https://www.teliacompany.com/en/careers/", "Iso"),
    ("Nordea", "https://www.nordea.com/en/careers", "Iso"),
    ("Valmet", "https://www.valmet.com/about-us/careers/", "Iso"),
    ("Konecranes", "https://konecranes.careers/", "Iso"),
]
comp_lock = threading.Lock()


def _companies() -> list[dict]:
    return _read(COMPANIES, {"companies": []}).get("companies", [])


def companies_payload() -> dict:
    rows = [r for r in _read(LATEST, {}).get("rows", []) if r.get("status") == "ok"]
    out = []
    for c in _companies():
        key = c["name"].lower()
        mine = sorted((r for r in rows if key in (r["job"].get("company") or "").lower()),
                      key=lambda r: -r.get("score", 0))
        try:
            days = (dt.date.today() - dt.date.fromisoformat(c.get("checked", ""))).days if c.get("checked") else None
        except ValueError:
            days = None
        out.append({**c, "days_since_check": days, "open_jobs": [{"title": r["job"]["title"], "url": r["job"]["url"],
                                        "score": round(r.get("score", 0))} for r in mine[:6]],
                    "open_count": len(mine)})
    have = {c["name"].lower() for c in _companies()}
    return {"companies": out, "suggested": [{"name": n, "careers_url": u, "group": g} for n, u, g in CATALOG if n.lower() not in have]}


def company_action(body: dict) -> dict:
    with comp_lock:
        cs = _companies()
        action = body.get("action")
        f = body.get("fields") if isinstance(body.get("fields"), dict) else {}
        name = " ".join(str(f.get("name", "")).split())[:100]
        link = str(f.get("careers_url", "")).strip()[:500]
        if link and not re.match(r"https?://", link, re.I):
            link = ""
        notes = str(f.get("notes", ""))[:2000]
        if action == "add":
            if not name:
                raise ValueError("Yrityksen nimi puuttuu")
            if len(cs) >= 200:
                raise ValueError("Yrityksiä on jo 200")
            if any(c["name"].lower() == name.lower() for c in cs):
                raise ValueError("Yritys on jo listalla")
            cs.insert(0, {"id": secrets.token_hex(5), "name": name, "careers_url": link, "notes": notes,
                          "added": dt.date.today().isoformat()})
        elif action == "check":
            c = next((x for x in cs if x["id"] == body.get("id")), None)
            if not c:
                raise ValueError("Yritystä ei löydy")
            c["checked"] = dt.date.today().isoformat()
        elif action == "add_catalog":
            have = {c["name"].lower() for c in cs}
            for n, u, g in CATALOG:
                if n.lower() not in have and len(cs) < 200:
                    cs.append({"id": secrets.token_hex(5), "name": n, "careers_url": u, "notes": "",
                               "added": dt.date.today().isoformat()})
        elif action in ("update", "delete"):
            c = next((x for x in cs if x["id"] == body.get("id")), None)
            if not c:
                raise ValueError("Yritystä ei löydy")
            if action == "delete":
                cs = [x for x in cs if x is not c]
            else:
                if name:
                    c["name"] = name
                if "careers_url" in f:
                    c["careers_url"] = link
                if "notes" in f:
                    c["notes"] = notes
        else:
            raise ValueError("Tuntematon toiminto")
        _write(COMPANIES, {"companies": cs}, indent=1)
    return companies_payload()


# ------------------------------------------------------------ Poistuneet ilmoitukset

GONE = RES / "gone.json"      # url -> päivä jolloin havaittiin poistuneeksi


def check_gone(url: str):
    """True = ilmoitus on poistunut (404/410 tai sivulla ei ole enää ilmoitusta), False = auki, None = ei tietoa."""
    if CFG["demo"] or not url.startswith("https://duunitori.fi/"):
        return None
    try:
        html = jh.http_get(url, DATA / "cache", 1.5, max_age_h=0)
    except urllib.error.HTTPError as e:
        return True if e.code in (404, 410) else None
    except Exception:  # noqa: BLE001
        return None
    if jh.parse_posting(html):
        return False
    low = html.lower()
    if re.search(r"ei ole enää haettavissa|ilmoitus on päättynyt|ilmoitusta ei löydy|ei enää haettavissa", low):
        return True
    return None


def _interest_urls() -> set[str]:
    urls = {u for u, v in _read(STATE, {}).items() if v.get("saved") or v.get("applied")}
    urls |= {a["url"] for a in _apps() if a.get("url") and a.get("status") not in tracking.FINAL}
    return urls


def update_gone(missing: set[str]) -> None:
    """Tarkista seuratuista ilmoituksista ne, jotka puuttuvat uudesta haustuloksesta."""
    gone = _read(GONE, {})
    today = dt.date.today().isoformat()
    for u in list(missing)[:80]:
        r = check_gone(u)
        if r is True:
            gone.setdefault(u, today)
        elif r is False:
            gone.pop(u, None)
    _write(GONE, gone)



class Raw:
    """Muu kuin JSON-vastaus (tiedostovienti)."""

    def __init__(self, body: bytes, ctype: str, filename: str | None = None):
        self.body, self.ctype, self.filename = body, ctype, filename


BODY_LIMITS = {"/api/apps": 150_000, "/api/state": 10_000}


def _body(raw: bytes, path: str) -> dict:
    if len(raw) > BODY_LIMITS.get(path, 200_000):
        raise ValueError("liian suuri pyyntö")
    body = json.loads(raw or b"{}")
    if not isinstance(body, dict):
        raise ValueError("Virheellinen pyyntö")
    return body


def api_get(path: str, query: dict[str, list[str]]):
    """Vanhat GET-rajapinnat. Palauttaa (status, dict | Raw). Käyttävät sekä http.server että FastAPI."""
    if path == "/api/jobs":
        latest = _read(LATEST, {"updated": None, "rows": [], "demo": CFG["demo"]})
        # Listanäkymä ei tarvitse ilmoitustekstejä: ne haetaan ilmoitussivulle erikseen
        latest["rows"] = [{**r, "job": {k: v for k, v in r["job"].items() if k != "text"}} for r in latest.get("rows", [])]
        latest["state"] = _read(STATE, {})
        latest["gone"] = _read(GONE, {})
        return 200, latest
    if path == "/api/job":
        url = (query.get("url") or [""])[0]
        row = next((r for r in _read(LATEST, {}).get("rows", []) if r["job"]["url"] == url), None)
        if not row:
            return 404, {"error": "Ilmoitusta ei löytynyt. Se on voinut poistua tuloksista uuden haun jälkeen."}
        app = next((a for a in _apps() if a.get("url") == url), None)
        return 200, {"row": row, "insights": job_insights(row), "state": _read(STATE, {}).get(url, {}), "app": app,
                     "gone": _read(GONE, {}).get(url)}
    if path == "/api/status":
        return 200, status
    if path == "/api/settings":
        return 200, settings_payload()
    if path == "/api/feedback":
        return 200, feedback_suggestions()
    if path == "/api/stats":
        return 200, stats()
    if path == "/api/companies":
        return 200, companies_payload()
    if path == "/api/backups":
        return 200, {"backups": list_backups()}
    if path.startswith("/api/export/"):
        kind = path.rsplit("/", 1)[-1]
        files = {"hakemukset.csv": (export_apps_csv, "text/csv; charset=utf-8"),
                 "ilmoitukset.csv": (export_jobs_csv, "text/csv; charset=utf-8"),
                 "kalenteri.ics": (export_ics, "text/calendar; charset=utf-8")}
        if kind not in files:
            return 404, {"error": "not found"}
        fn, ctype = files[kind]
        return 200, Raw(fn(), ctype, f"{dt.date.today().isoformat()}-{kind}")
    if path == "/api/skills":
        return 200, skills_payload()
    if path == "/api/apps":
        return 200, {"apps": _apps(), "statuses": STATUSES, "labels": tracking.LABELS,
                     "transitions": {k: sorted(v) for k, v in tracking.TRANSITIONS.items()},
                     "reminders": tracking.reminders(_apps())}
    return 404, {"error": "not found"}


def _apps_action(body: dict):
    action = body.get("action")
    fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
    with app_lock:
        apps = _apps()
        if action == "create":
            url = str(body.get("job_url") or "")
            if url:
                existing = next((a for a in apps if a.get("url") == url), None)
                if existing:
                    return 200, {"ok": True, "app": existing}
                a = app_from_job(url)
                if not a:
                    return 404, {"error": "Ilmoitusta ei löydy viimeisimmistä hauista. Päivitä haku tai lisää hakemus käsin."}
            else:
                a = new_app()
            apply_fields(a, fields, initial=True)
            if len(apps) >= 500:
                return 400, {"error": "Hakemuksia on jo 500"}
            apps.insert(0, a)
        elif action in ("update", "delete"):
            a = next((x for x in apps if x.get("id") == body.get("id")), None)
            if not a:
                return 404, {"error": "Hakemusta ei löydy"}
            if action == "update":
                try:
                    apply_fields(a, fields)
                except ValueError as e:
                    return 400, {"error": str(e)}
            else:
                apps = [x for x in apps if x is not a]
        else:
            return 400, {"error": "Tuntematon toiminto"}
        _save_apps(apps)
    return 200, {"ok": True, "app": None if action == "delete" else a}


def api_post(path: str, raw: bytes):
    """Vanhat POST-rajapinnat. raw = pyynnön runko. Palauttaa (status, dict)."""
    if path == "/api/refresh":
        started = start_refresh()
        return (202 if started else 200), {"started": started}
    if path == "/api/backup":
        name = backup_data(manual=True)
        return 200, {"ok": bool(name), "name": name, "backups": list_backups()}
    try:
        body = _body(raw, path)
    except ValueError:
        return 400, {"error": "Virheellinen pyyntö"}
    if path == "/api/apps":
        return _apps_action(body)
    if path in ("/api/settings", "/api/companies", "/api/recheck", "/api/manual"):
        try:
            if path == "/api/settings":
                return 200, {"ok": True, "settings": save_settings(body)}
            if path == "/api/manual":
                return 200, manual_action(body)
            if path == "/api/companies":
                res = company_action(body)
                threading.Thread(target=rescore, daemon=True).start()  # seurattu yritys vaikuttaa pisteisiin
                return 200, {"ok": True, **res}
            url = str(body.get("url", ""))
            if url not in _interest_urls() and not any(r["job"]["url"] == url for r in _read(LATEST, {}).get("rows", [])):
                return 404, {"error": "Ilmoitusta ei löydy"}
            res = check_gone(url)
            g = _read(GONE, {})
            if res is True:
                g.setdefault(url, dt.date.today().isoformat())
            elif res is False:
                g.pop(url, None)
            _write(GONE, g)
            return 200, {"ok": True, "result": res, "gone": g.get(url)}
        except ValueError as e:
            return 400, {"error": str(e)}
    if path == "/api/skills":
        try:
            skills = clean_skills(body["skills"])
        except (ValueError, KeyError, TypeError):
            return 400, {"error": "bad request"}
        _write(SKILLS, {"skills": skills}, indent=2)
        rescore()
        return 200, {"ok": True, "count": len(skills)}
    if path == "/api/state":
        url = str(body.get("url") or "")
        if not url:
            return 400, {"error": "bad request"}
        state = _read(STATE, {})
        cur = state.get(url, {})
        for k in ("saved", "applied", "hidden"):
            if k in body:
                cur[k] = bool(body[k])
        if any(cur.values()):
            state[url] = cur
        else:
            state.pop(url, None)
        _write(STATE, state)
        return 200, {"ok": True}
    return 404, {"error": "not found"}


def startup() -> None:
    """Käynnistyksen kertaluonteiset työt (molemmille palvelimille)."""
    RES.mkdir(exist_ok=True)
    migrate_applied()
    try:
        backup_data()
    except OSError as e:
        print(f"! varmuuskopio epäonnistui: {e}", file=sys.stderr)
    try:
        rescore()  # uudet pisteytyssäännöt koskevat heti jo haettuja ilmoituksia, ei uutta hakua tarvita
    except Exception as e:  # noqa: BLE001
        print(f"! uudelleenpisteytys epäonnistui: {e}", file=sys.stderr)
    threading.Thread(target=refresh_gaps, daemon=True).start()
    threading.Thread(target=resume_index, daemon=True, name="resume-index").start()


class Handler(BaseHTTPRequestHandler):
    """Vanha palvelin (python webapp.py --legacy). Uusi palvelin: server/app.py (FastAPI)."""
    server_version = "jobhunter"

    def log_message(self, *a):  # hiljainen loki
        pass

    # Vain paikallinen käyttö: hylkää vieraat Host-otsikot (DNS rebinding)
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{CFG['port']}", f"localhost:{CFG['port']}"}

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              filename: str | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _reply(self, code: int, obj) -> None:
        if isinstance(obj, Raw):
            return self._send(code, obj.body, obj.ctype, obj.filename)
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._reply(403, {"error": "forbidden"})
        parts = urllib.parse.urlsplit(self.path)
        if parts.path in ("/", "/index.html"):
            f = STATIC / "index.html"
            if not f.exists():
                return self._reply(500, {"error": "index.html puuttuu (static/index.html)"})
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        self._reply(*api_get(parts.path, urllib.parse.parse_qs(parts.query)))

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._reply(403, {"error": "forbidden"})
        # Vaatii mukautetun otsikon: muut sivustot eivät voi lähettää tätä ilman CORS-esitarkistusta
        if self.headers.get("X-Requested-With") != "jobhunter":
            return self._reply(403, {"error": "forbidden"})
        try:
            n = min(int(self.headers.get("Content-Length", "0")), 300_000)
        except ValueError:
            n = 0
        self._reply(*api_post(self.path.split("?")[0], self.rfile.read(n)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--every", type=float, default=6, help="päivitysväli tunteina, 0 = vain käsin")
    ap.add_argument("--demo", action="store_true", help="käytä sample_jobs.json:ia")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--legacy", action="store_true", help="vanha http.server + static/index.html (ei chattia ym.)")
    a = ap.parse_args()
    CFG.update(demo=a.demo, port=a.port)
    if sys.stdout is None or sys.stderr is None:   # pythonw (taustakäynnistys) ei anna konsolia: kirjoita lokiin
        log = open(DATA / "jobhunter.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    url = f"http://127.0.0.1:{a.port}"
    if not a.legacy:
        from server.app import serve
        if not a.no_browser:
            threading.Timer(2.0, lambda: webbrowser.open(url)).start()
        return serve(a.port, a.every)
    startup()
    threading.Thread(target=scheduler, args=(a.every,), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"Työnhaku käynnissä: {url}  (Ctrl+C lopettaa)")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nLopetetaan")


if __name__ == "__main__":
    # server.app tuo tämän moduulin nimellä "webapp": ilman tätä siitä tulisi toinen kopio, jonka CFG jää
    # oletuksiin (--demo ja --port eivät välittyisi palvelimelle)
    sys.modules.setdefault("webapp", sys.modules[__name__])
    main()
