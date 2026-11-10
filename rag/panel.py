"""CV:n arviointipaneeli: räätälöijä + viisi arvioijaa (yksi niistä simuloi tekoälyseulontaa), enintään kaksi korjauskierrosta.

Räätälöijä ei kirjoita CV:tä vapaasti. Se tekee suunnitelman (järjestys, pois jätettävät, korostettavat taidot,
profiiliteksti ja rivien uudelleenmuotoilut), ja cvtailor.assemble kokoaa CV:n alkuperäisistä riveistä. Jokainen
muotoilu viittaa yhteen lähderiviin, ja säännöt hylkäävät sen ennen paneelia, jos siinä on jotain, mitä samassa
kohdassa ei ole. Paneeli arvioi kootun CV:n rinnakkain. Faktantarkistajalla on veto-oikeus.

Varma lopetus: jos faktantarkistaja hylkää vielä viimeisellä kierroksella, hylätyt muotoilut ja profiiliteksti
korvataan alkuperäisillä. Keksitty sisältö ei siis koskaan päädy lopulliseen CV:hen, eikä ajo jää silmukkaan.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import secrets
import sqlite3
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ValidationError, field_validator

from . import cvtailor as ct
from . import screening
from .claims import novel_words
from .llm import LLM, LLMError
from .style import STYLE
from .style import find as style_find
from .style import third_person_fi
from .verify import _present, tech_terms

PROMPTS = Path(__file__).with_name("prompts")
REVIEWERS = ("fact_checker", "ai_screener", "recruiter", "tech_interviewer", "hiring_manager")
LABELS = {"tailor": "Räätälöijä", "fact_checker": "Faktantarkistaja", "ai_screener": "AI-seuloja", "recruiter": "Rekrytoija",
          "tech_interviewer": "Tekninen haastattelija", "hiring_manager": "Esihenkilö"}
MAX_REVISIONS = 2
ATTEMPTS = 3
BACKOFF = 2.0          # sekuntia, kaksinkertaistuu yrityksittäin
MAX_TOKENS = 6000      # ajattelu kuluttaa samaa rajaa: liian pieni raja katkaisee JSONin


class Cancelled(Exception):
    pass


# ------------------------------------------------------------ skeemat

class Rewrite(BaseModel):
    source_id: str
    text: str
    reason: str = ""


class TailorPlan(BaseModel):
    section_order: list[str] = []
    drop: list[str] = []
    focus_skills: list[str] = []
    summary: str = ""
    rewrites: list[Rewrite] = []
    declined_suggestions: list[str] = []


class Finding(BaseModel):
    severity: Literal["blocking", "suggestion"]
    category: str = ""
    quote: str = ""
    source_id: str = ""
    issue: str
    fix: str = ""

    @field_validator("category", "quote", "source_id", "fix", mode="before")
    @classmethod
    def _none_empty(cls, v):
        return "" if v is None else v


class Requirement(BaseModel):
    requirement: str
    kind: Literal["must", "nice"]
    status: Literal["shown", "weak", "not_shown", "lacking"]
    evidence: str = ""

    @field_validator("evidence", mode="before")
    @classmethod
    def _none_empty(cls, v):
        return "" if v is None else v


class ReviewOutput(BaseModel):
    agent: str = ""
    verdict: Literal["approve", "reject"]
    score: int
    findings: list[Finding] = []
    interview_questions: list[str] = []
    summary: str = ""
    decision: Literal["advance", "maybe", "reject", ""] = ""     # vain AI-seuloja
    requirements: list[Requirement] = []

    @field_validator("score", mode="before")
    @classmethod
    def _clamp(cls, v):
        return max(1, min(10, int(round(float(v)))))


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


_S, _L = {"type": "string"}, {"type": "array", "items": {"type": "string"}}
PLAN_SCHEMA = _obj({"section_order": _L, "drop": _L, "focus_skills": _L, "summary": _S,
                    "rewrites": {"type": "array", "items": _obj({"source_id": _S, "text": _S, "reason": _S})},
                    "declined_suggestions": _L})
REVIEW_SCHEMA = _obj({
    "verdict": {"type": "string", "enum": ["approve", "reject"]}, "score": {"type": "integer"},
    "findings": {"type": "array", "items": _obj({"severity": {"type": "string", "enum": ["blocking", "suggestion"]},
                                                 "category": _S, "quote": _S, "source_id": _S, "issue": _S, "fix": _S})},
    "interview_questions": _L, "summary": _S})
SCREEN_SCHEMA = _obj({**REVIEW_SCHEMA["properties"],
                      "decision": {"type": "string", "enum": ["advance", "maybe", "reject"]},
                      "requirements": {"type": "array", "items": _obj({
                          "requirement": _S, "kind": {"type": "string", "enum": ["must", "nice"]},
                          "status": {"type": "string", "enum": ["shown", "weak", "not_shown", "lacking"]},
                          "evidence": _S})}})


def prompt(agent: str) -> str:
    return (PROMPTS / f"{agent}.md").read_text(encoding="utf-8").strip()


def _fmt(findings: list[Finding], who: str = "") -> str:
    out = []
    for f in findings:
        bits = [f"[{who}]" if who else "", f"[{f.severity}]", f"{f.category}: {f.issue}"]
        if f.quote:
            bits.append(f'| quote: "{f.quote}"')
        if f.source_id:
            bits.append(f"| id: {f.source_id}")
        if f.fix:
            bits.append(f"| fix: {f.fix}")
        out.append("- " + " ".join(b for b in bits if b))
    return "\n".join(out) or "(none)"


# ------------------------------------------------------------ paneeli

class Panel:
    def __init__(self, job: dict, ad_text: str, cv_md: str, fi_md: str | None, lang: str,
                 llm_for: Callable[[str], LLM], emit: Callable[..., None], cancel: threading.Event | None = None,
                 embed=None, parallel: bool = True, notes: dict[str, str] | None = None):
        if lang == "fi" and not fi_md:
            raise ValueError("Suomenkielistä pohjaa (data/cv_fi.md) ei ole. Tee se ensin tavallisella räätälöinnillä.")
        self.job, self.ad, self.cv_md, self.fi_md, self.lang = job, ad_text, cv_md, fi_md, lang
        self.slang = "fi" if lang == "fi" else "en"
        self.llm_for, self._emit, self.cancel = llm_for, emit, cancel or threading.Event()
        self.embed, self.parallel = embed, parallel
        self.notes = notes or {}
        self.usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self._context: str | None = None
        self.usage_by_model: dict[str, dict] = {}   # räätälöijä ja arvioijat voivat olla eri malleja (eri hinta)
        self.models: dict[str, str] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------- apuvälineet
    def emit(self, typ: str, message: str, **data) -> None:
        self._emit(typ, message, data)

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    def fresh(self) -> tuple[ct.CV, ct.CV]:
        """(en, base): kokoaminen muuttaa CV-olioita, joten jokainen kokoaminen jäsentää uudelleen."""
        en = ct.parse(self.cv_md)
        return en, (ct.parse(self.fi_md) if self.lang == "fi" else en)

    def _add_usage(self, llm, before: dict) -> None:
        after = getattr(llm, "usage", None) or {}
        with self._lock:
            per = self.usage_by_model.setdefault(getattr(llm, "name", "?"), {k: 0 for k in self.usage})
            for k in self.usage:
                d = max(0, int(after.get(k, 0)) - int(before.get(k, 0)))
                self.usage[k] += d
                per[k] += d

    def call(self, agent: str, system, user: str, schema: dict, model: type[BaseModel], on_start=None):
        """Yksi agenttikutsu: API-virheissä enintään 3 yritystä (backoff), virheellisessä JSONissa yksi korjauspyyntö."""
        llm = self.llm_for(agent)
        with self._lock:
            self.models[agent] = llm.name
        msgs = [{"role": "user", "content": user}]
        fixed = False
        for attempt in range(1, ATTEMPTS + 1):
            self.check_cancel()
            before = dict(getattr(llm, "usage", None) or {})
            try:
                raw = llm.complete_json(system, msgs, schema, max_tokens=MAX_TOKENS, on_start=on_start)
            except LLMError as e:
                self._add_usage(llm, before)
                if attempt == ATTEMPTS:
                    raise
                self.emit("retry", f"{LABELS[agent]}: {e} (yritys {attempt + 1}/{ATTEMPTS})",
                          agent=agent, attempt=attempt + 1, reason=str(e))
                if self.cancel.wait(BACKOFF * 2 ** (attempt - 1)):
                    raise Cancelled() from None
                continue
            self._add_usage(llm, before)
            try:
                return model.model_validate(raw)
            except ValidationError as e:
                if fixed or attempt == ATTEMPTS:
                    raise LLMError(f"{LABELS[agent]}: vastaus ei vastannut skeemaa korjauspyynnönkään jälkeen.") from e
                fixed = True
                why = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])
                self.emit("retry", f"{LABELS[agent]}: virheellinen JSON ({why}), yritys {attempt + 1}/{ATTEMPTS}",
                          agent=agent, attempt=attempt + 1, reason=why)
                msgs = msgs + [{"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                               {"role": "user", "content": f"Your reply did not match the schema: {why}. Return the full JSON again, corrected."}]
        raise LLMError(f"{LABELS[agent]}: ei vastausta")

    def _notes(self) -> str:
        """Käyttäjän lisätiedot projekteista (oma osuus ym.): taustatietoa arvioon, ei CV:hen kirjoitettavaa."""
        if not self.notes:
            return ""
        return ("\n\nCANDIDATE_NOTES (the candidate's own notes about their role in projects; context for judging what "
                "they can defend, never to be written into the CV as new facts):\n"
                + "\n".join(f"- {k}: {v}" for k, v in self.notes.items()))

    def _posting(self) -> str:
        j = self.job
        return f"JOB POSTING: {j.get('title')} | {j.get('company')} | {j.get('city')}\n{self.ad[:6000]}"

    # -------------------------------------------------- räätälöijä ja kokoaminen
    def context(self) -> str:
        """Koko ajon yhteinen alkuosa (räätälöijä ja kaikki arvioijat, kaikki kierrokset): välimuistissa.
        Sama tavujono joka kutsussa, joten siihen ei saa tulla ajokohtaisia muuttujia."""
        if self._context is None:
            self._context = (f"CV LANGUAGE: {'Finnish' if self.slang == 'fi' else 'English'}\n\n{self._posting()}\n\n"
                             f"SOURCE_FACTS:\n{ct.outline(self.fresh()[1])}{self._notes()}")
        return self._context

    def tailor(self, prev: TailorPlan | None, feedback: str, rule: list[Finding]) -> TailorPlan:
        user = "Make the tailoring plan for the JOB POSTING above, using only SOURCE_FACTS."
        if prev is not None:
            user = (f"PREVIOUS_PLAN:\n{prev.model_dump_json()}\n\nPANEL_FEEDBACK:\n{feedback}"
                    f"\n\nRULE_FINDINGS:\n{_fmt(rule)}")
        system = [self.context(), prompt("tailor") + "\n\n" + STYLE[self.slang]]
        return self.call("tailor", system, user, PLAN_SCHEMA, TailorPlan)

    def build(self, plan: TailorPlan, original_summary: bool = False) -> dict:
        en, base = self.fresh()
        text = None if original_summary or not plan.summary.strip() else plan.summary.strip()
        third = third_person_fi(text) if text and self.slang == "fi" else []
        third += [f"text addressed to screening software: {g}" for g in screening.gaming(text or "")]
        sections, changes, summary, skills, rejected = ct.assemble(
            base, en, plan.model_dump(), None if third else text, self.ad, self.lang, self.embed,
            rewrites=[r.model_dump() for r in plan.rewrites])
        rule: list[Finding] = []
        if third:   # kolmas persoona hylätään säännöllä kuten keksitty väite: CV:hen jää alkuperäinen profiili
            summary.update(rejected=text, flags=[{"text": text, "reasons": [r if r.startswith("text addressed") else f"third person: {r}" for r in third]}])
        if summary.get("rejected"):
            rule.append(Finding(severity="suggestion", category="rule_summary", quote=summary["rejected"], source_id="SUMMARY",
                                issue="Profile text rejected by rule check and replaced with the original: "
                                      + "; ".join(r for f in summary["flags"] for r in f["reasons"]),
                                fix="Write a profile text that uses only facts in the CV, in the first person without "
                                    "the pronoun (Finnish: \"Rakennan…\", \"Valmistun…\")."))
        for r in rejected:
            rule.append(Finding(severity="suggestion", category="rule_rewrite", quote=r["text"], source_id=r["source_id"],
                                issue="Rewrite rejected by rule check and kept as the original: " + "; ".join(r["reasons"]),
                                fix="Use only facts from the same line or job/project, or leave the line as it is."))
        written = [("SUMMARY", summary["text"])] if summary["source"] == "generated" else []
        written += [(c["id"], c["after"]) for c in changes if c["kind"] == "rewrite"]
        for sid, t in written:
            for phrase in style_find(t, self.slang):
                rule.append(Finding(severity="suggestion", category="style", quote=phrase, source_id=sid,
                                    issue="Phrase sounds generic or machine-written.", fix="Use plain, concrete wording."))
        cv = ct.to_dict(base, sections, summary, self.lang)
        cov = screening.coverage(self.ad, cv, base, ct.full_text(en) if base is not en else "")
        rule += [Finding(severity="suggestion", **f) for f in screening.findings(cov)]
        return {"cv": cv, "coverage": cov, "changes": changes, "summary": summary, "skills": skills, "rule": rule, "base": base,
                "rewritten": {c["id"] for c in changes if c["kind"] == "rewrite"}}

    def render(self, built: dict) -> str:
        """Koottu CV arvioijille tunnisteineen. Tähän paikkaan kirjoitetut kohdat merkitään."""
        cv, mark = built["cv"], " (rewritten)"
        out = []
        for s in cv["sections"]:
            out.append(f"## {s['title']}")
            if s["profile"]:
                gen = built["summary"]["source"] == "generated"
                out.append(f"[SUMMARY] {built['summary']['text']}{mark if gen else ''}")
                continue
            out += [f"[{u['id']}] {u['text']}{mark if u['id'] in built['rewritten'] else ''}" for u in s["units"] if u["included"]]
            for e in s["entries"]:
                if not e["included"]:
                    continue
                out.append(f"[{e['id']}] ### {e['title']}" + (f" ({e['meta']})" if e["meta"] else ""))
                out += [f"  [{u['id']}] {u['text']}{mark if u['id'] in built['rewritten'] else ''}"
                        for u in e["units"] if u["included"]]
        return "\n".join(out)

    # -------------------------------------------------- arvioijat
    @staticmethod
    def candidate(cv_text: str, built: dict) -> str:
        """Kierroksen yhteinen osa kaikille arvioijille (välimuistissa saman kierroksen ajan)."""
        return (f"TAILORED_CV (line ids in square brackets and the marker \"(rewritten)\" are annotations for "
                f"reviewers; they are not printed on the real CV):\n{cv_text}\n\nRULE_FINDINGS:\n{_fmt(built['rule'])}")

    def review(self, agent: str, cv_text: str, built: dict, on_start=None) -> ReviewOutput:
        user = "Review the TAILORED_CV above against the JOB POSTING and SOURCE_FACTS as instructed."
        if agent == "fact_checker":
            user = f"NEW_WORDS:\n{self.new_words(built)}\n\n" + user
        system = [self.context(), self.candidate(cv_text, built), prompt(agent)]
        r = self.call(agent, system, user, SCREEN_SCHEMA if agent == "ai_screener" else REVIEW_SCHEMA, ReviewOutput,
                      on_start=on_start)
        r.agent = agent
        if agent == "ai_screener":
            # puuttuva vaatimus on tieto hakijasta: se ei voi estää hyväksyntää
            lacking = {q.requirement.lower() for q in r.requirements if q.status == "lacking"}
            for f in r.findings:
                if f.severity == "blocking" and any(x and (x in f.issue.lower() or x in f.quote.lower()) for x in lacking):
                    f.severity = "suggestion"
        if agent != "fact_checker":
            # muut arvioijat eivät voi estää hyväksyntää vaatimalla taitoa, jota CV:ssä ei ole
            have = ct.full_text(self.fresh()[0]).lower() + "\n" + ct.full_text(built["base"]).lower()
            for f in r.findings:
                missing = [t for t in tech_terms(f.fix) if not _present(t, have)]
                if f.severity == "blocking" and missing:
                    f.severity = "suggestion"
                    f.category = (f.category + " " if f.category else "") + f"(vaatisi CV:stä puuttuvan taidon: {', '.join(missing)})"
        return r

    def new_words(self, built: dict) -> str:
        """Kirjoitettujen kohtien sanat, joita faktapohjassa ei ole: faktantarkistaja käy jokaisen läpi."""
        en, base = self.fresh()
        src = ct.full_text(base) + ("\n" + ct.full_text(en) if base is not en else "")
        rows = []
        if built["summary"]["source"] == "generated":
            rows.append(("SUMMARY", built["summary"]["text"]))
        rows += [(c["id"], c["after"]) for c in built["changes"] if c["kind"] == "rewrite"]
        out = [f"- {sid}: {', '.join(w)}" for sid, t in rows if (w := novel_words(t, src))]
        return "\n".join(out) or "(none)"

    @staticmethod
    def rejects(agent: str, r: ReviewOutput) -> bool:
        blocking = any(f.severity == "blocking" for f in r.findings)
        return blocking or (agent == "fact_checker" and r.verdict == "reject")

    def review_all(self, rnd: int, built: dict) -> tuple[dict[str, ReviewOutput], dict[str, str]]:
        text = self.render(built)
        results: dict[str, ReviewOutput] = {}
        failed: dict[str, str] = {}

        warmed = threading.Event()

        def one(agent: str, on_start=None) -> ReviewOutput:
            self.emit("review_started", f"{LABELS[agent]} arvioi…", round=rnd, agent=agent)
            return self.review(agent, text, built, on_start=on_start)

        ex = ThreadPoolExecutor(len(REVIEWERS) if self.parallel else 1)
        # Ensimmäinen arvioija kirjoittaa yhteisen alkuosan välimuistiin; muut käynnistetään vasta, kun malli on
        # käsitellyt sen syötteen, jotta ne lukevat välimuistista eivätkä kirjoita kukin omaansa.
        first = ex.submit(one, REVIEWERS[0], warmed.set)
        futs = {first: REVIEWERS[0]}
        try:
            if self.parallel:
                while not warmed.wait(0.2) and not first.done():
                    self.check_cancel()
            futs.update({ex.submit(one, a): a for a in REVIEWERS[1:]})
            pending = set(futs)
            while pending:
                self.check_cancel()
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for f in done:
                    a = futs[f]
                    try:
                        r = f.result()
                    except Cancelled:
                        raise
                    except Exception as e:  # noqa: BLE001  (yksi kaatunut arvioija ei kaada ajoa)
                        failed[a] = str(e)
                        self.emit("review_failed", f"{LABELS[a]} ei saanut arviota valmiiksi: {e}", round=rnd, agent=a)
                        continue
                    results[a] = r
                    nb = sum(x.severity == "blocking" for x in r.findings)
                    verdict = "hylkää" if self.rejects(a, r) else "hyväksyy"
                    self.emit("review_finished", f"{LABELS[a]}: {verdict}, {r.score}/10, {nb} estävää",
                              round=rnd, agent=a, verdict="reject" if self.rejects(a, r) else "approve",
                              score=r.score, blocking=nb, findings=len(r.findings))
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return results, failed

    # -------------------------------------------------- varma lopetus
    def fallback(self, plan: TailorPlan, built: dict, review: ReviewOutput) -> tuple[TailorPlan, bool, list[str], list[Finding]]:
        """Faktantarkistajan estävät löydökset: kyseiset muotoilut ja profiiliteksti alkuperäisiksi.
        Palauttaa (suunnitelma, profiili alkuperäiseksi, palautetut rivit, löydökset alkuperäisessä tekstissä)."""
        rws = {r.source_id: r for r in plan.rewrites}
        summary_out, reverted, untouched = False, [], []
        gen_summary = built["summary"]["text"].lower() if built["summary"]["source"] == "generated" else ""
        for f in review.findings:
            if f.severity != "blocking":
                continue
            q, hit = f.quote.strip().lower(), False
            for sid, r in list(rws.items()):
                if f.source_id == sid or (q and q in r.text.lower()):
                    rws.pop(sid)
                    reverted.append(sid)
                    hit = True
            if gen_summary and (f.source_id.upper() == "SUMMARY" or (q and q in gen_summary)):
                summary_out = hit = True
            if not hit:
                untouched.append(f)
        if review.verdict == "reject" and not reverted and not summary_out and gen_summary and not untouched:
            summary_out = True     # hylkäys ilman paikannettavaa löydöstä: varmuuden vuoksi alkuperäinen profiili
        return plan.model_copy(update={"rewrites": list(rws.values())}), summary_out, reverted, untouched

    @staticmethod
    def screening(built: dict, r: ReviewOutput | None) -> dict:
        """Lopullisen CV:n seulonta-arvio: koodin avainsanakattavuus ja AI-seulojan päätös vaatimuksittain."""
        out = {"coverage": built.get("coverage")}
        if r is not None:
            out.update(decision=r.decision or None, score=r.score, note=r.summary,
                       requirements=[q.model_dump() for q in r.requirements])
        return out

    # -------------------------------------------------- kulku
    def run(self) -> dict:
        t0 = time.time()
        self.emit("run_started", f"Räätälöidään: {self.job.get('title')} ({self.job.get('company')})",
                  lang=self.lang, agents=list(REVIEWERS))
        en, base = self.fresh()
        lines = sum(len(s.units) + sum(len(e.units) for e in s.entries) for s in base.sections)
        self.emit("facts_loaded", f"Faktapohja: {'cv_fi.md' if self.lang == 'fi' else 'cv.md'}, {lines} riviä",
                  source="cv_fi.md" if self.lang == "fi" else "cv.md", lines=lines)
        plan: TailorPlan | None = None
        feedback, rule = "", []
        rounds: list[dict] = []
        reviews: dict[str, ReviewOutput] = {}
        failed: dict[str, str] = {}
        rejecting: list[str] = []
        built: dict = {}
        for rnd in range(MAX_REVISIONS + 1):
            if rnd:
                self.emit("revision_started", f"Korjauskierros {rnd}/{MAX_REVISIONS}: "
                          + ", ".join(LABELS[a] for a in rejecting) + " löysi estäviä ongelmia",
                          round=rnd, rejected_by=rejecting)
            self.emit("tailor_started", "Räätälöijä tekee suunnitelmaa…" if not rnd else "Räätälöijä korjaa löydöksiä…", round=rnd)
            plan = self.tailor(plan, feedback, rule)
            built = self.build(plan)
            n_rw = sum(c["kind"] == "rewrite" for c in built["changes"])
            self.emit("tailor_finished", f"Suunnitelma valmis: {len(built['changes'])} muutosta, {n_rw} muotoiltua riviä",
                      round=rnd, changes=len(built["changes"]), rewrites=n_rw)
            rule = built["rule"]
            self.emit("rules_checked", f"Sääntötarkistus: {len(rule)} huomiota", round=rnd,
                      findings=[f.model_dump() for f in rule])
            self.check_cancel()
            reviews, failed = self.review_all(rnd, built)
            rejecting = [a for a in REVIEWERS if a in reviews and self.rejects(a, reviews[a])]
            rounds.append({"round": rnd, "plan": plan.model_dump(), "cv": built["cv"], "changes": built["changes"],
                           "rule": [f.model_dump() for f in rule], "failed": failed,
                           "reviews": {a: r.model_dump() for a, r in reviews.items()}})
            if not rejecting:
                break
            merged = [_fmt([f for f in reviews[a].findings if f.severity == "blocking"], a) for a in rejecting]
            merged += [_fmt([f for f in r.findings if f.severity == "suggestion"], a) for a, r in reviews.items()]
            feedback = "\n".join(m for m in merged if m != "(none)")

        fb = None
        if "fact_checker" in rejecting:
            plan2, summary_out, reverted, untouched = self.fallback(plan, built, reviews["fact_checker"])
            if summary_out or reverted:
                built = self.build(plan2, original_summary=summary_out)
                plan = plan2
            fb = {"summary": summary_out, "reverted": reverted, "untouched": [f.model_dump() for f in untouched]}
            what = (["profiiliteksti"] if summary_out else []) + reverted
            self.emit("fallback_applied", "Faktantarkistajan löydökset korjattiin palauttamalla alkuperäinen: "
                      + (", ".join(what) or "ei korjattavaa tekstiä (löydökset koskevat CV:n alkuperäistä tekstiä)"),
                      summary=summary_out, reverted=reverted)

        approved = not rejecting and not failed and "fact_checker" in reviews
        remaining = [{"agent": a, **f.model_dump()} for a in rejecting for f in reviews[a].findings if f.severity == "blocking"]
        final_text = self.render(built).lower()
        for f in remaining:     # lainattu kohta ei ole enää lopullisessa CV:ssä (sääntö tai varma lopetus poisti sen)
            if f["quote"].strip() and f["quote"].strip().lower() not in final_text:
                f["resolved"] = "korvattu alkuperäisellä"
        questions = next((r["reviews"]["tech_interviewer"]["interview_questions"] for r in reversed(rounds)
                          if "tech_interviewer" in r["reviews"]), [])
        res = dict(built["cv"])
        res.update(job_title=self.job.get("title"), company=self.job.get("company"), changes=built["changes"],
                   focus_skills=built["skills"], source="cv_fi.md" if self.lang == "fi" else "cv.md", review_required=False,
                   translation={}, model=self.models.get("tailor", ""), created=dt.datetime.now().isoformat(timespec="seconds"))
        res["panel"] = {
            "status": "approved" if approved else "needs_review", "rounds": len(rounds),
            "reviews": {a: {**r.model_dump(), "label": LABELS[a], "rejects": a in rejecting} for a, r in reviews.items()},
            "failed": failed, "remaining": remaining, "fallback": fb, "rule": [f.model_dump() for f in built["rule"]],
            "interview_questions": questions, "declined": plan.declined_suggestions if plan else [],
            "models": dict(self.models), "duration_s": round(time.time() - t0, 1), "history": rounds,
            "screening": self.screening(built, reviews.get("ai_screener")),
        }
        return res


# ------------------------------------------------------------ ajojen tallennus ja taustaajo

RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS cv_runs (
    run_id TEXT PRIMARY KEY, url TEXT NOT NULL, lang TEXT NOT NULL, created TEXT NOT NULL, finished TEXT,
    status TEXT NOT NULL, models TEXT, usage TEXT, cost REAL, error TEXT);
CREATE INDEX IF NOT EXISTS cv_runs_url ON cv_runs(url);
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, at TEXT NOT NULL, type TEXT NOT NULL, message TEXT NOT NULL,
    data TEXT NOT NULL, PRIMARY KEY (run_id, seq));
"""
FINAL = {"approved", "needs_review", "failed", "cancelled", "interrupted"}
ACTIVE: dict[str, threading.Event] = {}


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class RunLog:
    """Ajot ja tapahtumat SQLiteen (sama tietokanta kuin muulla sovelluksella, oma yhteys per kutsu)."""

    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(RUN_SCHEMA)

    @contextlib.contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def create(self, url: str, lang: str) -> str:
        run_id = secrets.token_hex(8)
        with self._conn() as c:
            c.execute("INSERT INTO cv_runs (run_id, url, lang, created, status) VALUES (?,?,?,?,?)",
                      (run_id, url, lang, _now(), "running"))
        return run_id

    def emit(self, run_id: str, typ: str, message: str, data: dict | None = None) -> int:
        with self._lock, self._conn() as c:
            seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id=?", (run_id,)).fetchone()[0]
            c.execute("INSERT INTO run_events VALUES (?,?,?,?,?,?)",
                      (run_id, seq, _now(), typ, message, json.dumps(data or {}, ensure_ascii=False, default=str)))
        return seq

    def finish(self, run_id: str, status: str, models: dict | None = None, usage: dict | None = None,
               cost: float | None = None, error: str | None = None) -> None:
        with self._conn() as c:
            c.execute("UPDATE cv_runs SET status=?, finished=?, models=?, usage=?, cost=?, error=? WHERE run_id=?",
                      (status, _now(), json.dumps(models or {}), json.dumps(usage or {}), cost, error, run_id))

    @staticmethod
    def _run(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["models"], d["usage"] = json.loads(d["models"] or "{}"), json.loads(d["usage"] or "{}")
        if d["status"] == "running" and d["run_id"] not in ACTIVE:
            d["status"] = "interrupted"     # palvelin käynnistyi uudelleen kesken ajon
        return d

    def get(self, run_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM cv_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._run(r) if r else None

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT seq, at, type, message, data FROM run_events WHERE run_id=? AND seq>? ORDER BY seq",
                             (run_id, after)).fetchall()
        return [{"seq": r["seq"], "at": r["at"], "type": r["type"], "message": r["message"], "data": json.loads(r["data"])}
                for r in rows]

    def list(self, url: str, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM cv_runs WHERE url=? ORDER BY created DESC LIMIT ?", (url, limit)).fetchall()
        return [self._run(r) for r in rows]


def panel_llm(s, agent: str) -> LLM:
    """Räätälöijä = kirjoitusmalli. Arvioijat = PANEL_MODEL (tyhjä: CLAUDE_MODEL); faktantarkistaja ajattelee enemmän."""
    from .llm import ClaudeLLM, get_llm
    if agent == "tailor":
        return get_llm(s, "writing")
    if s.llm_provider == "claude":
        return ClaudeLLM(s.panel_model or s.claude_model, s.anthropic_api_key,
                         effort="medium" if agent == "fact_checker" else "low")
    return get_llm(s, "explain")


# $ / miljoona tokenia (syöte, tuloste, välimuistin luku). Muut mallit: PANEL_PRICE_IN/OUT ja luku 0,1 ×.
PRICES = {"claude-haiku-5-5": (0.10, 0.50, 0.01), "claude-sonnet-5-5": (2.0, 10.0, 0.20),
          "claude-opus-5-5": (4.0, 20.0, 0.20), "claude-fable-5-1": (10.0, 50.0, 0.25)}


def _price(model: str, s) -> tuple[float, float, float] | None:
    m = model.split(":", 1)[-1]
    if m in PRICES:
        return PRICES[m]
    if s.panel_price_in or s.panel_price_out:
        return s.panel_price_in, s.panel_price_out, 0.1 * s.panel_price_in
    return None


def cost_of(usage: dict, s, by_model: dict[str, dict] | None = None) -> float | None:
    """Arvioitu hinta dollareina. by_model: käyttö malleittain (räätälöijä ja arvioijat voivat olla eri malleja).
    Välimuistin kirjoitus 1,25 × syötteen hinta (5 min). None, jos jonkin mallin hintaa ei tiedetä."""
    parts = by_model or {"": usage}
    total = 0.0
    for model, u in parts.items():
        p = _price(model, s) if model else ((s.panel_price_in, s.panel_price_out, 0.1 * s.panel_price_in)
                                            if (s.panel_price_in or s.panel_price_out) else None)
        if p is None:
            return None
        pin, pout, pread = p
        total += (u.get("input", 0) * pin + u.get("cache_read", 0) * pread + 1.25 * u.get("cache_write", 0) * pin
                  + u.get("output", 0) * pout) / 1e6
    return round(total, 4)


def start(url: str, lang: str, s=None, log: RunLog | None = None, on_result: Callable[[dict], None] | None = None) -> str:
    """Käynnistä paneeliajo taustasäikeessä. Palauttaa run_id:n heti."""
    from . import config
    s = s or config.load()
    log = log or RunLog(s.db_path)
    run_id = log.create(url, lang)
    cancel = threading.Event()
    ACTIVE[run_id] = cancel
    threading.Thread(target=_work, args=(run_id, url, lang, s, log, cancel, on_result), daemon=True,
                     name=f"panel-{run_id}").start()
    return run_id


def cancel(run_id: str) -> bool:
    ev = ACTIVE.get(run_id)
    if ev:
        ev.set()
    return ev is not None


def _load_notes() -> dict[str, str]:
    try:
        from .projects import load_notes
        return load_notes()
    except Exception:  # noqa: BLE001  (lisätiedot eivät ole välttämättömiä)
        return {}


def _work(run_id, url, lang, s, log: RunLog, cancel_ev: threading.Event, on_result) -> None:
    emit = lambda typ, msg, data=None: log.emit(run_id, typ, msg, data)  # noqa: E731
    panel: Panel | None = None
    t0 = time.time()
    try:
        from . import service
        from .grounding import find_job
        from .scoring import _embedder_factory
        job = find_job(service.store(s), url)
        fp = ct.fi_path(s.cv_path)
        fi_md = fp.read_text(encoding="utf-8") if lang == "fi" and fp.exists() else None
        panel = Panel(job, job.get("clean_text") or "", s.cv_path.read_text(encoding="utf-8"), fi_md, lang,
                      lambda a: panel_llm(s, a), emit, cancel_ev, _embedder_factory(s)().embed_queries,
                      parallel=s.llm_provider != "ollama", notes=_load_notes())
        res = panel.run()
        cost = cost_of(panel.usage, s, panel.usage_by_model)
        res["panel"].update(run_id=run_id, usage=dict(panel.usage), cost=cost, duration_s=round(time.time() - t0, 1))
        if on_result:
            on_result(res)
        st = res["panel"]["status"]
        tokens = sum(panel.usage.values())
        emit("run_finished", ("Valmis: hyväksytty" if st == "approved" else "Valmis: vaatii tarkistusta")
             + f" ({res['panel']['duration_s']:.0f} s, {tokens} tokenia" + (f", noin ${cost:.3f})" if cost is not None else ")"),
             {"status": st, "duration_s": res["panel"]["duration_s"], "usage": panel.usage, "cost": cost})
        log.finish(run_id, st, panel.models, panel.usage, cost)
    except Cancelled:
        emit("run_cancelled", "Ajo peruutettiin.")
        log.finish(run_id, "cancelled", panel.models if panel else {}, panel.usage if panel else {})
    except Exception as e:  # noqa: BLE001
        msg = str(e) if isinstance(e, (LLMError, ValueError, LookupError)) else f"Odottamaton virhe: {type(e).__name__}: {e}"
        emit("run_failed", msg)
        log.finish(run_id, "failed", panel.models if panel else {}, panel.usage if panel else {}, error=msg)
    finally:
        ACTIVE.pop(run_id, None)
