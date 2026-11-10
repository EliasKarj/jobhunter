"""Projektiskanneri: ehdotuksia CV:n projektiosioon omista git-repoista. Ei koskaan kirjoita CV:tä.

1. Etsii git-repot juurikansiosta (oletus AABIGBOMBOCLAT; secret, node_modules ym. ohitetaan).
2. Tekijyys omista commiteista (data/projects.json: own_emails). Repot ilman omia committeja ohitetaan, ja muut
   tekijät raportoidaan, jotta oman vanhan osoitteen voi lisätä listaan.
3. Tekniikat päätellään säännöillä tiedostoista (package.json, requirements, Dockerfile, workflowt...), ei mallilla.
4. Ehdotukset:
   - new_project: repo, jota CV:ssä ei ole. Kielimalli luonnostelee 2-3 riviä README:n ja omien commit-viestien
     pohjalta; jokainen rivi tarkistetaan lähdetekstiä vasten (verify.check + tekniikat), ja liputetut näytetään.
   - new_tech: CV:ssä oleva projekti, jonka repossa on tekniikka, jota CV:n kohdassa ei mainita.
   - activity: CV:ssä olevaan projektiin on tullut omia committeja CV:n viimeisen muokkauksen jälkeen.
5. Tulos: data/project_proposals.json (hylätyt pysyvät hylättyinä) ja luettava raportti results/project_proposals.md.
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from . import config
from .cvtailor import _is, parse
from .verify import _present, check, tech_terms

PROMPT_VERSION = "projects-1"
SETTINGS = config.DATA / "projects.json"
PROPOSALS = config.DATA / "project_proposals.json"
REPORT = config.DATA / "results" / "project_proposals.md"
SKIP_DIRS = {"node_modules", ".next", "dist", "build", ".venv", "venv", "__pycache__", "graphify-out", "secret", ".git",
             "bin", "obj", "target", ".turbo", "coverage"}
MIN_OWN_COMMITS = 3

# ------------------------------------------------------------ tekniikoiden tunnistus (säännöt, ei mallia)

NPM = {"react": "React", "next": "Next.js", "typescript": "TypeScript", "electron": "Electron", "fastify": "Fastify",
       "express": "Express", "prisma": "Prisma", "@prisma/client": "Prisma", "vite": "Vite", "tailwindcss": "Tailwind CSS",
       "@supabase/supabase-js": "Supabase", "vitest": "Vitest", "jest": "Jest", "playwright": "Playwright",
       "@playwright/test": "Playwright", "vue": "Vue", "svelte": "Svelte", "three": "Three.js", "socket.io": "Socket.IO",
       "mongoose": "MongoDB", "pg": "PostgreSQL", "better-sqlite3": "SQLite", "sqlite3": "SQLite", "redis": "Redis",
       "@tanstack/react-query": "TanStack Query", "zod": "Zod", "drizzle-orm": "Drizzle", "react-native": "React Native",
       "expo": "Expo", "discord.js": "discord.js", "wrangler": "Cloudflare", "openai": "OpenAI API",
       "@anthropic-ai/sdk": "Claude API", "chart.js": "Chart.js", "d3": "D3"}
PY = {"fastapi": "FastAPI", "flask": "Flask", "django": "Django", "pandas": "pandas", "numpy": "NumPy",
      "torch": "PyTorch", "scikit-learn": "scikit-learn", "sqlalchemy": "SQLAlchemy", "psycopg2": "PostgreSQL",
      "psycopg2-binary": "PostgreSQL", "psycopg": "PostgreSQL", "pytest": "pytest", "requests": "requests",
      "beautifulsoup4": "Beautiful Soup", "selenium": "Selenium", "playwright": "Playwright", "discord.py": "discord.py",
      "sqlite-vec": "sqlite-vec", "sentence-transformers": "sentence-transformers", "anthropic": "Claude API",
      "openai": "OpenAI API", "ollama": "Ollama", "streamlit": "Streamlit", "matplotlib": "Matplotlib",
      "openpyxl": "openpyxl", "netmiko": "Netmiko", "paramiko": "Paramiko", "web3": "web3.py", "uvicorn": "Uvicorn"}
FILES = [(r"(^|/)Dockerfile$", "Docker"), (r"(^|/)(docker-)?compose\.ya?ml$", "Docker Compose"),
         (r"^\.github/workflows/.+\.ya?ml$", "GitHub Actions"), (r"^\.gitlab-ci\.yml$", "GitLab CI"),
         (r"(^|/)Jenkinsfile$", "Jenkins"), (r"\.tf$", "Terraform"), (r"(^|/)(k8s|kubernetes)/.+\.ya?ml$", "Kubernetes"),
         (r"\.csproj$", ".NET"), (r"(^|/)pom\.xml$", "Maven"), (r"(^|/)Cargo\.toml$", "Rust"), (r"(^|/)go\.mod$", "Go"),
         (r"(^|/)vercel\.json$", "Vercel"), (r"(^|/)wrangler\.(toml|jsonc?)$", "Cloudflare"), (r"\.sql$", "SQL"),
         (r"(^|/)AndroidManifest\.xml$", "Android"), (r"(^|/)supabase/", "Supabase")]
LANGS = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
         ".mjs": "JavaScript", ".cs": "C#", ".java": "Java", ".kt": "Kotlin", ".cpp": "C++", ".cc": "C++", ".c": "C",
         ".rs": "Rust", ".go": "Go", ".php": "PHP", ".rb": "Ruby", ".swift": "Swift", ".dart": "Dart", ".sh": "Bash",
         ".ps1": "PowerShell", ".xaml": "XAML", ".sol": "Solidity"}


def detect_stack(files: list[str], read) -> list[str]:
    """files: repon tiedostot (git ls-files). read(path) -> teksti tai ''. Palauttaa tekniikat järjestyksessä."""
    found: list[str] = []
    add = lambda x: found.append(x) if x not in found else None  # noqa: E731
    langs = Counter(LANGS[Path(f).suffix.lower()] for f in files if Path(f).suffix.lower() in LANGS)
    total = sum(langs.values()) or 1
    for lang, n in langs.most_common():
        if (n >= 5 and n / total >= 0.1) or n / total >= 0.3:   # ei pelkkien konfiguraatiotiedostojen takia
            add(lang)
    for f in files:
        if f.endswith("package.json") and "node_modules" not in f:
            try:
                pj = json.loads(read(f) or "{}")
            except json.JSONDecodeError:
                continue
            for dep in list(pj.get("dependencies", {})) + list(pj.get("devDependencies", {})):
                if dep in NPM:
                    add(NPM[dep])
        elif re.search(r"(^|/)(requirements[^/]*\.txt|pyproject\.toml)$", f):
            for line in (read(f) or "").lower().splitlines():
                name = re.split(r"[<>=~!\[;\s\"']", line.strip().lstrip("\"'"), maxsplit=1)[0]
                if name in PY:
                    add(PY[name])
    for pat, name in FILES:
        if any(re.search(pat, f) for f in files):
            add(name)
    return found


# ------------------------------------------------------------ git

def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    return r.stdout if r.returncode == 0 else ""


def discover(root: Path, ignore: list[str]) -> list[Path]:
    out = []

    def walk(d: Path, depth: int) -> None:
        if depth > 6:
            return
        try:
            kids = sorted(d.iterdir())
        except OSError:
            return
        if (d / ".git").exists() and d != root:
            out.append(d)
        for k in kids:
            if k.is_dir() and k.name not in SKIP_DIRS and not k.name.startswith(".") and k.name.lower() not in ignore:
                walk(k, depth + 1)
    walk(root, 0)
    return out


def repo_info(repo: Path, own: set[str]) -> dict | None:
    head = _git(repo, "rev-parse", "HEAD").strip()
    if not head:
        return None
    log = [line.split("\x1f") for line in _git(repo, "log", "--format=%ae\x1f%as\x1f%s").splitlines() if "\x1f" in line]
    mine = [(d, s) for e, d, s in log if e.lower() in own]
    others = Counter(e.lower() for e, _, _ in log if e.lower() not in own)
    files = [f for f in _git(repo, "ls-files").splitlines() if f]

    def read(f: str) -> str:
        p = repo / f
        try:
            return p.read_text(encoding="utf-8", errors="replace")[:20000] if p.stat().st_size < 400_000 else ""
        except OSError:
            return ""
    readme = next((read(f) for f in files if re.fullmatch(r"readme(\.\w+)?", f, re.I)), "")
    name = repo.name
    for f in ("package.json",):
        if f in files:
            try:
                name = json.loads(read(f)).get("name") or name
            except json.JSONDecodeError:
                pass
    return {"repo": repo.name, "path": str(repo), "head": head, "name": name, "files": len(files),
            "stack": detect_stack(files, read), "readme": readme[:4000],
            "commits": len(log), "own_commits": len(mine), "others": dict(others.most_common(5)),
            "first": min((d for d, _ in mine), default=""), "last": max((d for d, _ in mine), default=""),
            "subjects": [s for _, s in mine[:40]], "dates": [d for d, _ in mine]}


# ------------------------------------------------------------ vertailu CV:hen

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9åäö]", "", s.lower())


def cv_projects(cv_md: str) -> list[dict]:
    cv = parse(cv_md)
    out = []
    for s in cv.sections:
        if not _is(s.title, ("projects", "projektit")):
            continue
        for e in s.entries:
            out.append({"id": e.id, "title": e.title, "name": re.split(r"\s+[–-]\s+", e.title)[0].strip(),
                        "text": "\n".join([e.title, e.meta, *(u.text for u in e.units)])})
    return out


def match(info: dict, projects: list[dict], aliases: dict[str, str]) -> dict | None:
    alias = aliases.get(info["repo"].lower()) or aliases.get(info["repo"])
    keys = {_norm(info["repo"]), _norm(info["name"])}
    for p in projects:
        n = _norm(p["name"])
        if alias and _norm(alias) == n:
            return p
        if n and any(k and (k == n or (len(n) >= 5 and (n in k or k in n))
                            or difflib.SequenceMatcher(None, k, n).ratio() >= 0.85) for k in keys):
            return p
    return None


# ------------------------------------------------------------ luonnos uudelle projektille

DRAFT_SYSTEM = """You draft CV project entries for a student's own software project, based ONLY on the
project's README, its commit messages and the detected tech stack.

Return:
- title: "<Project name> – <short description, max 6 words>"
- bullets: 2-3 CV lines in English, past tense, CV style without pronouns (e.g. "Built…", "Set up…").

Rules:
- Use only facts in the sources. No numbers, users, results or technologies that the sources do not state.
- Describe what was built and with what. Do not claim impact, scale, production use or team leadership unless
  the sources say so.
- Plain and concrete. No buzzwords ("robust", "seamless", "leveraging", "cutting-edge")."""
DRAFT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title", "bullets"],
                "properties": {"title": {"type": "string"}, "bullets": {"type": "array", "items": {"type": "string"}}}}


def source_text(info: dict) -> str:
    return (f"PROJECT: {info['repo']} ({info['name']})\nSTACK: {', '.join(info['stack'])}\n\n"
            f"README:\n{info['readme'] or '(none)'}\n\nCOMMIT MESSAGES:\n" + "\n".join(f"- {s}" for s in info["subjects"]))


def verify_bullet(text: str, src: str, name: str = "") -> list[str]:
    low = src.lower()
    reasons = [f"\"{t}\" ei esiinny lähteissä" for t in tech_terms(text) if not _present(t, low)]
    reasons += [r for c in check(text, {"SRC": src}, {}, name, about_applicant=True) if c.status == "flag" for r in c.reasons]
    return list(dict.fromkeys(reasons))


def draft(info: dict, llm) -> dict:
    src = source_text(info)
    raw = llm.complete_json(DRAFT_SYSTEM, [{"role": "user", "content": src}], DRAFT_SCHEMA, max_tokens=4000)
    title = str(raw.get("title") or info["repo"]).strip()
    bullets = [{"text": b.strip(), "flags": verify_bullet(b.strip(), src, info["repo"])}
               for b in (raw.get("bullets") or [])[:3] if str(b).strip()]
    return {"title": title, "bullets": bullets, "model": getattr(llm, "name", "")}


# ------------------------------------------------------------ asetukset, ajo ja raportti

def _period(first: str, last: str) -> str:
    if not first:
        return ""
    f, l = first[:7], last[:7]
    return f if f == l else f"{f} – {l}"


def load_settings(path: Path = SETTINGS) -> dict:
    """data/projects.json: own_emails, aliases (repo -> CV:n projektin nimi), ignore (repojen nimiä), root, llm."""
    default = {"own_emails": [], "aliases": {}, "ignore": [], "root": "", "llm": "default"}
    if path.exists():
        return {**default, **json.loads(path.read_text(encoding="utf-8"))}
    git_email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True).stdout.strip()
    default["own_emails"] = [e for e in [git_email] if e]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
    return default


def _pid(kind: str, repo: str, detail: str = "") -> str:
    return hashlib.sha1(f"{kind}|{repo}|{detail}".encode()).hexdigest()[:10]


def scan(s: config.Settings | None = None, llm=None, root: Path | None = None, settings: dict | None = None,
         proposals_path: Path = PROPOSALS, report_path: Path | None = REPORT, use_llm: bool = True,
         progress=print, step=None) -> dict:
    """step(tehty, kaikki, repo): edistyminen käyttöliittymälle."""
    s = s or config.load()
    st = settings if settings is not None else load_settings()
    own = {e.lower() for e in st.get("own_emails", [])}
    if not own:
        raise ValueError("data/projects.json: own_emails on tyhjä. Lisää git-commitien sähköpostiosoitteesi.")
    root = Path(root or st.get("root") or config.BASE.parents[3])
    old = json.loads(proposals_path.read_text(encoding="utf-8")) if proposals_path.exists() else {}
    old_by_id = {p["id"]: p for p in old.get("proposals", [])}
    cv_md = s.cv_path.read_text(encoding="utf-8")
    cv_changed = dt.datetime.fromtimestamp(s.cv_path.stat().st_mtime).date().isoformat()
    projects = cv_projects(cv_md)
    ignore = [x.lower() for x in st.get("ignore", [])]
    out, skipped = [], []
    if use_llm and llm is None and st.get("llm") != "off":
        from .llm import OllamaLLM, get_llm
        llm = OllamaLLM(s.ollama_model, s.ollama_url) if st.get("llm") == "ollama" else get_llm(s, "writing")
    repos = discover(root, ignore)
    for i, repo in enumerate(repos):
        if step:
            step(i, len(repos), repo.name)
        info = repo_info(repo, own)
        if info is None:
            continue
        if info["own_commits"] == 0:
            skipped.append({"repo": info["repo"], "path": info["path"], "why": "ei omia committeja",
                            "authors": info["others"]})
            continue
        hit = match(info, projects, st.get("aliases", {}))
        base = {"repo": info["repo"], "path": info["path"], "stack": info["stack"], "period": _period(info["first"], info["last"]),
                "own_commits": info["own_commits"], "commits": info["commits"], "others": info["others"], "head": info["head"]}
        if hit is None:
            if info["own_commits"] < MIN_OWN_COMMITS:
                skipped.append({"repo": info["repo"], "path": info["path"], "why": f"vain {info['own_commits']} omaa committia",
                                "authors": info["others"]})
                continue
            pid = _pid("new_project", info["repo"])
            prev = old_by_id.get(pid, {})
            d = prev.get("draft") if prev.get("head") == info["head"] and prev.get("prompt") == PROMPT_VERSION else None
            if d is None and llm is not None:
                progress(f"  luonnostellaan: {info['repo']}")
                try:
                    d = draft(info, llm)
                except Exception as e:  # noqa: BLE001  (yksi repo ei kaada skannausta)
                    d = {"title": info["repo"], "bullets": [], "error": str(e)}
            out.append({**base, "id": pid, "kind": "new_project", "draft": d, "prompt": PROMPT_VERSION,
                        "status": prev.get("status", "open")})
            continue
        missing = [t for t in info["stack"] if not _present(t, hit["text"].lower())]
        if missing:
            pid = _pid("new_tech", info["repo"], ",".join(sorted(missing)))
            out.append({**base, "id": pid, "kind": "new_tech", "cv_entry": hit["title"], "cv_id": hit["id"],
                        "missing": missing, "status": old_by_id.get(pid, {}).get("status", "open")})
        recent = [subj for d, subj in zip(info["dates"], info["subjects"]) if d > cv_changed]
        if recent:
            pid = _pid("activity", info["repo"], info["head"])
            out.append({**base, "id": pid, "kind": "activity", "cv_entry": hit["title"], "cv_id": hit["id"],
                        "since": cv_changed, "recent": recent[:15], "n_recent": len(recent),
                        "status": old_by_id.get(pid, {}).get("status", "open")})
    # löytöhetki: ilmoitus näyttää ehdotukset, jotka ovat tulleet sen jälkeen, kun käyttäjä viimeksi katsoi niitä.
    # Ennen tätä kenttää tehdyt ehdotukset saavat edellisen skannauksen ajan.
    now = dt.datetime.now().isoformat(timespec="seconds")
    for p in out:
        prev = old_by_id.get(p["id"])
        p["found"] = (prev.get("found") or old.get("generated") or now) if prev else now
    authors = Counter()
    for p in out + skipped:
        authors.update(p.get("others") or p.get("authors") or {})
    res = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "root": str(root), "cv_changed": cv_changed,
           "own_emails": sorted(own), "other_authors": dict(authors.most_common(15)), "proposals": out, "skipped": skipped}
    proposals_path.parent.mkdir(parents=True, exist_ok=True)
    proposals_path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report(res), encoding="utf-8")
    return res


KIND_LABEL = {"new_project": "uusi projekti", "new_tech": "uusia tekniikoita", "activity": "uutta työtä"}


def summary(path: Path = PROPOSALS, settings_path: Path = SETTINGS) -> dict:
    """Avoimet ehdotukset ja niistä uudet (löydetty viimeisen katselun jälkeen): sivupalkin ja etusivun ilmoitus."""
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"proposals": []}
    seen = load_settings(settings_path).get("seen_at") or ""
    open_ = [p for p in data.get("proposals", []) if p.get("status") == "open"]
    new = [{"id": p["id"], "repo": p["repo"], "kind": p["kind"], "label": KIND_LABEL.get(p["kind"], p["kind"]),
            "title": ((p.get("draft") or {}).get("title") or p.get("cv_entry") or p["repo"]), "found": p.get("found", "")}
           for p in open_ if not seen or (p.get("found") or "") > seen]   # ei koskaan katsottu: kaikki avoimet
    return {"open": len(open_), "new": new, "seen_at": seen, "generated": data.get("generated")}


def mark_seen(settings_path: Path = SETTINGS) -> str:
    """Käyttäjä avasi ehdotukset: nykyiset eivät ole enää uusia. Palauttaa edellisen katseluhetken."""
    st = load_settings(settings_path)
    prev = st.get("seen_at") or ""
    st["seen_at"] = dt.datetime.now().isoformat(timespec="seconds")
    settings_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return prev


def dismiss(pid: str, path: Path = PROPOSALS) -> bool:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"proposals": []}
    hit = False
    for p in data["proposals"]:
        if p["id"] == pid:
            p["status"], hit = "dismissed", True
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return hit


def report(res: dict) -> str:
    open_ = [p for p in res["proposals"] if p["status"] == "open"]
    L = [f"# Projektiehdotukset CV:hen", "",
         f"Skannattu {res['generated'][:16].replace('T', ' ')} · juuri `{res['root']}` · CV muokattu {res['cv_changed']}.",
         "Mitään ei ole kirjoitettu CV:hen. Hyväksy ehdotus siirtämällä se itse `data/cv.md`:hen, "
         "tai piilota se: `python -m rag projects --dismiss <id>`.", ""]
    for kind, head in (("new_project", "Uudet projektit"), ("new_tech", "Uusia tekniikoita CV:n projekteissa"),
                       ("activity", "Muutoksia CV:n päivityksen jälkeen")):
        items = [p for p in open_ if p["kind"] == kind]
        if not items:
            continue
        L += [f"## {head} ({len(items)})", ""]
        for p in items:
            meta = f"`{p['id']}` · {p['own_commits']}/{p['commits']} omaa committia · {p['period']} · `{p['path']}`"
            if p["others"]:
                meta += " · muut tekijät: " + ", ".join(f"{e} ({n})" for e, n in p["others"].items())
            if kind == "new_project":
                d = p.get("draft") or {}
                L += [f"### {d.get('title') or p['repo']}", f"*{', '.join(p['stack'])} | {p['period']}*", ""]
                for b in d.get("bullets", []):
                    L.append(f"- {b['text']}" + (f"  \n  ⚠ {'; '.join(b['flags'])}" if b["flags"] else ""))
                if d.get("error"):
                    L.append(f"- (luonnos epäonnistui: {d['error']})")
                if not d:
                    L.append("- (ei luonnosta: kielimalli pois päältä)")
            elif kind == "new_tech":
                L += [f"### {p['cv_entry']}", f"Repossa, mutta ei CV:n kohdassa: **{', '.join(p['missing'])}**"]
            else:
                L += [f"### {p['cv_entry']}", f"{p['n_recent']} omaa committia {p['since']} jälkeen:"]
                L += [f"- {x}" for x in p["recent"]]
            L += ["", meta, ""]
    if not open_:
        L += ["Ei avoimia ehdotuksia.", ""]
    if res["other_authors"]:
        L += ["## Muut commit-osoitteet", "",
              "Jos jokin näistä on oma vanha osoitteesi, lisää se tiedoston `data/projects.json` listaan `own_emails`:", ""]
        L += [f"- {e} ({n} committia)" for e, n in res["other_authors"].items()]
        L.append("")
    if res["skipped"]:
        L += ["## Ohitetut repot", ""] + [f"- {x['repo']}: {x['why']}" for x in res["skipped"]] + [""]
    return "\n".join(L)


def due(days: int = 7, path: Path = PROPOSALS) -> bool:
    if not path.exists():
        return True
    try:
        g = json.loads(path.read_text(encoding="utf-8")).get("generated", "")
        return dt.datetime.now() - dt.datetime.fromisoformat(g) > dt.timedelta(days=days)
    except (ValueError, json.JSONDecodeError):
        return True


# ------------------------------------------------------------ hyväksyntä: ehdotus CV:hen (käyttäjän pyynnöstä)

NOTES = config.DATA / "project_notes.json"
BACKUPS = config.DATA / "backups"
_SEASONS = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
            6: "Summer", 7: "Summer", 8: "Summer", 9: "Autumn", 10: "Autumn", 11: "Autumn"}


def season(period: str) -> str:
    """'2026-08' -> 'Summer 2026'; '2025-09 – 2026-06' -> 'Autumn 2025 – Summer 2026' (CV:n omaan tyyliin)."""
    parts = [p.strip() for p in period.split("–") if p.strip()]
    out = []
    for p in parts:
        y, m = p.split("-")[:2]
        out.append(f"{_SEASONS[int(m)]} {y}")
    out = list(dict.fromkeys(out))
    return " – ".join(out)


def default_meta(p: dict) -> str:
    stack = ", ".join(p.get("stack", [])[:6])
    when = season(p.get("period", "")) if p.get("period") else ""
    return " | ".join(x for x in (stack, when) if x)


def _section_bounds(lines: list[str], names) -> tuple[int, int] | None:
    start = next((i for i, l in enumerate(lines) if l.startswith("## ") and _is(l[3:].strip(), names)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def add_project(md: str, title: str, meta: str, bullets: list[str]) -> str:
    """Uusi projekti projektiosion alkuun (CV:n muu teksti ennallaan)."""
    title, meta = title.strip().replace("\n", " "), meta.strip().strip("*").replace("\n", " ")
    bullets = [b.strip().replace("\n", " ") for b in bullets if b.strip()]
    if not title or not bullets:
        raise ValueError("Projektilta puuttuu nimi tai rivit")
    lines = md.splitlines()
    b = _section_bounds(lines, ("projects", "projektit"))
    if b is None:
        raise ValueError("CV:stä ei löydy projektiosiota (## Projects)")
    i = b[0] + 1
    while i < b[1] and not lines[i].strip():
        i += 1
    block = [f"### {title}"] + ([f"*{meta}*"] if meta else []) + [""] + [f"- {x}" for x in bullets] + [""]
    return "\n".join(lines[:i] + block + lines[i:]) + "\n"


def add_tech(md: str, entry_title: str, techs: list[str]) -> str:
    """Tekniikat CV:n kohdan meta-riville ('*A, B | Autumn 2026*' -> '*A, B, C | Autumn 2026*')."""
    techs = [t.strip() for t in techs if t.strip()]
    if not techs:
        raise ValueError("Valitse vähintään yksi tekniikka")
    lines = md.splitlines()
    i = next((k for k, l in enumerate(lines) if l.strip() == f"### {entry_title}"), None)
    if i is None:
        raise ValueError(f"CV:stä ei löydy kohtaa: {entry_title}")
    j = i + 1
    if j < len(lines) and re.fullmatch(r"\*[^*].*\*", lines[j].strip()):
        meta = lines[j].strip().strip("*").strip()
        stack, sep, rest = meta.partition(" | ")
        have = [x.strip() for x in stack.split(",") if x.strip()] if sep or "," in stack or not re.search(r"\d", stack) else []
        if not sep and re.search(r"\d", stack):          # pelkkä aika: tekniikat eteen
            lines[j] = f"*{', '.join(techs)} | {stack}*"
        else:
            new = have + [t for t in techs if t.lower() not in {h.lower() for h in have}]
            lines[j] = f"*{', '.join(new)}{sep}{rest}*"
    else:
        lines.insert(j, f"*{', '.join(techs)}*")
    return "\n".join(lines) + "\n"


def _write_cv(s, new_md: str) -> str:
    """Varmuuskopio backups/-kansioon ja atominen kirjoitus. Palauttaa varmuuskopion polun."""
    from . import cvsync
    cvsync.snapshot(s)                       # suomennokset muistiin ennen kuin rivien tunnisteet siirtyvät
    parse(new_md)                            # rikkinäistä markdownia ei kirjoiteta
    BACKUPS.mkdir(exist_ok=True)
    bak = BACKUPS / f"cv-{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    bak.write_text(s.cv_path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = s.cv_path.with_suffix(".tmp")
    tmp.write_text(new_md, encoding="utf-8")
    tmp.replace(s.cv_path)
    return str(bak)


def accept(pid: str, edits: dict, s=None, path: Path = PROPOSALS, skills: list[str] | None = None) -> dict:
    """Hyväksy ehdotus: new_project ja new_tech kirjoitetaan cv.md:hen (käyttäjän muokkaamana), activity merkitään
    käsitellyksi. Palauttaa tekniikat, joita osaamisprofiilissa ei vielä ole."""
    s = s or config.load()
    data = json.loads(path.read_text(encoding="utf-8"))
    p = next((x for x in data["proposals"] if x["id"] == pid), None)
    if p is None:
        raise LookupError("Ehdotusta ei löydy. Skannaa uudelleen.")
    if p["status"] != "open":
        raise ValueError("Ehdotus on jo käsitelty")
    md = s.cv_path.read_text(encoding="utf-8")
    techs: list[str] = []
    backup = None
    if p["kind"] == "new_project":
        d = p.get("draft") or {}
        title = str(edits.get("title") or d.get("title") or p["repo"])
        meta = str(edits.get("meta") if edits.get("meta") is not None else default_meta(p))
        bullets = edits.get("bullets") if edits.get("bullets") is not None else [b["text"] for b in d.get("bullets", [])]
        backup = _write_cv(s, add_project(md, title, meta, [str(b) for b in bullets]))
        techs = p.get("stack", [])
        if str(edits.get("note") or "").strip():
            save_note(title, str(edits["note"]))
    elif p["kind"] == "new_tech":
        techs = [t for t in (edits.get("techs") or p["missing"]) if t in p["missing"]]
        backup = _write_cv(s, add_tech(md, p["cv_entry"], techs))
    p["status"] = "accepted"
    p["accepted"] = dt.datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    known = {x.lower() for x in (skills or [])}
    return {"ok": True, "kind": p["kind"], "backup": backup, "new_skills": [t for t in techs if t.lower() not in known]}


def load_notes(path: Path | None = None) -> dict[str, str]:
    path = path or NOTES            # haetaan kutsuhetkellä (testit vaihtavat polun)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_note(entry_title: str, note: str, path: Path | None = None) -> dict[str, str]:
    """Oma osuus / lisätieto projektista. Ei näy CV:ssä; paneeli käyttää sitä taustatietona."""
    path = path or NOTES
    notes = load_notes(path)
    note = note.strip()[:1000]
    if note:
        notes[entry_title] = note
    else:
        notes.pop(entry_title, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    return notes


def add_own_email(email: str, path: Path = SETTINGS) -> list[str]:
    st = load_settings(path)
    email = email.strip().lower()
    if "@" not in email:
        raise ValueError("Virheellinen osoite")
    if email not in [e.lower() for e in st["own_emails"]]:
        st["own_emails"].append(email)
    path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return st["own_emails"]
