"""Kielimallin sopivuusarvio hybridin kärjelle: lukee ilmoituksen kuin ihminen ja tunnistaa epäsuorat vaatimukset.

Hybridi karsii kaikki ilmoitukset nopeasti; kielimalli arvioi vain kärjen (SCORE_TOP), merkityt ilmoitukset ja uudet
ilmoitukset. Tulokset tallennetaan tauluun llm_scores, joten samaa ilmoitusta ei arvioida uudelleen.

Yksityisyys: mallille lähtee vain julkinen ilmoitus ja nimetön tiivistelmä (roolit ilman yrityksiä, taidot, projektit).
Nimeä, yhteystietoja ja työnantajia ei lähetetä, vaikka pisteytys käyttäisi Claudea ja LLM_PROVIDER olisi ollama.
"""
from __future__ import annotations

import datetime as dt
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config
from .clean import clean_text, job_id, sha1
from .cvtailor import _is, parse
from .llm import LLM, LLMError, get_llm
from .store import Store

PROMPT_VERSION = "score-1"
COMBINED_FIT_WEIGHT = 0.5   # evaluoitu 83 merkinnällä, ks. README
TABLE = """CREATE TABLE IF NOT EXISTS llm_scores (
    job_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, model TEXT, level TEXT, fit INTEGER, reason TEXT, scored_at TEXT)"""

SYSTEM = """Arvioit työpaikkailmoituksen sopivuutta alla kuvatulle hakijalle. Hakija hakee harjoittelu- tai junioritehtävää.

Palauta:
- level: junior | mid | senior. Ilmoituksen todellinen vaatimustaso. Lue myös epäsuorat ilmaisut ("vahvaa kokemusta",
  "työvuosia mittarissa", "itsenäisesti"), äläkä luota otsikon sanaan junior, jos teksti vaatii vuosien kokemusta.
- fit: 0-100. Voisiko tämä hakija realistisesti tulla valituksi? 80+ = sopii hyvin, 50-79 = kannattaa hakea,
  20-49 = epätodennäköinen, alle 20 = ei sovi.
- reason: yksi lyhyt lause suomeksi, jossa kerrot ratkaisevan syyn.

HAKIJA:
"""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["level", "fit", "reason"],
          "properties": {"level": {"type": "string", "enum": ["junior", "mid", "senior"]}, "fit": {"type": "integer"},
                         "reason": {"type": "string"}}}


def applicant_summary(cv_md: str) -> str:
    """Nimetön tiivistelmä CV:stä: ei nimeä, yhteystietoja eikä työnantajia."""
    cv = parse(cv_md)
    out = []
    for s in cv.sections:
        t = s.title.lower()
        if _is(t, ("profile", "profiili")):
            out += [u.text for u in s.units]
        elif _is(t, ("work experience", "työkokemus")):
            out.append("Työkokemus: " + "; ".join(e.title.split("|")[0].strip() for e in s.entries))
        elif _is(t, ("projects", "projektit")):
            out.append("Projektit: " + "; ".join(f"{e.title.split(' – ')[0]} ({e.meta})" for e in s.entries))
        elif _is(t, ("technical skills", "tekninen osaaminen", "education", "koulutus")):
            out += [u.text.replace("**", "") for u in s.units]
    text = clean_text("\n".join(out))   # puhelinnumerot ja sähköpostit pois varmuuden vuoksi
    return text.replace(cv.name, "Hakija")


def input_hash(job: dict, summary: str, model: str) -> str:
    return sha1(PROMPT_VERSION, summary, model, job.get("title") or "", (job.get("text") or "")[:6000])


def _ensure(store: Store) -> None:
    store.db.execute(TABLE)


def load_scores(store: Store) -> dict[str, dict]:
    _ensure(store)
    return {r["job_id"]: {"fit": r["fit"], "level": r["level"], "reason": r["reason"], "model": r["model"], "hash": r["input_hash"]}
            for r in store.db.execute("SELECT * FROM llm_scores")}


def combined(hybrid: float | None, fit: float | None) -> float | None:
    """Oletusjärjestyksen piste. Ilman arviota sopivuus on 0: hybridijärjestys säilyy, mutta arvioidut menevät edelle."""
    if hybrid is None:
        return None
    return (1 - COMBINED_FIT_WEIGHT) * hybrid + COMBINED_FIT_WEIGHT * (fit or 0)


def attach(rows: list[dict], scores: dict[str, dict]) -> list[dict]:
    """Lisää riveille arvio ja yhdistelmäpisteen. Hybridipisteisiin se ei koske."""
    for r in rows:
        j = r["job"]
        x = scores.get(job_id(j.get("url", ""), j.get("source", "")))
        r["llm"] = {k: x[k] for k in ("fit", "level", "reason")} if x else None
        r["score_llm"] = x["fit"] if x else None
        r["score_combined"] = combined(r.get("score_hybrid"), r["score_llm"])
    return rows


def pick(rows: list[dict], top: int, must: set[str], scores: dict, summary: str, model: str) -> list[dict]:
    """Arvioitavat: hybridin kärki ja merkityt ilmoitukset, joista ei ole ajantasaista arviota."""
    ok = sorted((r for r in rows if r.get("status") == "ok"), key=lambda r: -r.get("score", 0))
    chosen = {r["job"]["url"]: r for r in ok[:top]}
    chosen.update({r["job"]["url"]: r for r in ok if r["job"]["url"] in must})
    out = []
    for r in chosen.values():
        j = r["job"]
        old = scores.get(job_id(j["url"], j.get("source", "")))
        if not old or old["hash"] != input_hash(j, summary, model):
            out.append(r)
    return out


def score_one(job: dict, summary: str, llm: LLM) -> dict:
    user = f"{job.get('title')} | {job.get('company')} | {job.get('location')}\n{(job.get('text') or '')[:6000]}"
    raw = llm.complete_json(SYSTEM + summary, [{"role": "user", "content": user}], SCHEMA, max_tokens=1000)
    fit = max(0, min(100, int(raw.get("fit") or 0)))
    level = raw.get("level") if raw.get("level") in ("junior", "mid", "senior") else "mid"
    return {"fit": fit, "level": level, "reason": str(raw.get("reason") or "")[:300]}


def run(rows: list[dict], store: Store, summary: str, llm: LLM, top: int, must: set[str] | None = None, workers: int = 1,
        progress=None) -> dict:
    """Arvioi puuttuvat. Palauttaa {done, failed, skipped}. Tallentaa jokaisen tuloksen heti: keskeytys ei hukkaa tehtyä."""
    scores = load_scores(store)
    todo = pick(rows, top, must or set(), scores, summary, llm.name)
    done = failed = 0
    now = dt.datetime.now().isoformat(timespec="seconds")
    with ThreadPoolExecutor(max(1, workers)) as ex:
        futs = {ex.submit(score_one, r["job"], summary, llm): r["job"] for r in todo}
        for f in as_completed(futs):
            j = futs[f]
            try:
                x = f.result()
            except (LLMError, ValueError, TypeError) as e:
                failed += 1
                print(f"! arvio epäonnistui ({j.get('title')}): {e}", file=sys.stderr)
                continue
            store.db.execute("INSERT OR REPLACE INTO llm_scores VALUES (?,?,?,?,?,?,?)",
                             (job_id(j["url"], j.get("source", "")), input_hash(j, summary, llm.name), llm.name, x["level"],
                              x["fit"], x["reason"], now))
            store.db.commit()
            done += 1
            if progress and done % 25 == 0:
                progress(done, len(todo))
    return {"done": done, "failed": failed, "total": len(todo)}


def get_scorer(s: config.Settings) -> LLM | None:
    if s.score_provider == "off":
        return None
    return get_llm(s.with_(llm_provider=s.score_provider), "chat")


def update(rows: list[dict], s: config.Settings | None = None, progress=None) -> dict | None:
    """Arvioi kärki ja merkityt. None, jos pisteytys on pois päältä tai CV/indeksi puuttuu."""
    from .label import load_labels
    s = s or config.load()
    llm = get_scorer(s)
    if llm is None or not s.cv_path.exists() or not s.db_path.exists():
        return None
    store = Store(s.db_path)
    try:
        return run(rows, store, applicant_summary(s.cv_path.read_text(encoding="utf-8")), llm, s.score_top, set(load_labels()),
                   s.score_workers if s.score_provider == "claude" else 1, progress)
    finally:
        store.close()


def attach_saved(rows: list[dict], s: config.Settings | None = None) -> list[dict]:
    s = s or config.load()
    if not s.db_path.exists():
        return rows
    store = Store(s.db_path)
    try:
        return attach(rows, load_scores(store))
    finally:
        store.close()
