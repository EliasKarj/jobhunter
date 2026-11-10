// CV:n arviointipaneeli: reaaliaikainen ajo (SSE), tulokset ja aiemmat ajot. Backend: rag/panel.py.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, post } from "../api";
import { useToast } from "../components";
import { ProgressBar } from "../progress";

export const AGENTS = ["fact_checker", "ai_screener", "recruiter", "tech_interviewer", "hiring_manager"] as const;
export const AGENT_LABEL: Record<string, string> = {
  tailor: "Räätälöijä", fact_checker: "Faktantarkistaja", ai_screener: "AI-seuloja", recruiter: "Rekrytoija",
  tech_interviewer: "Tekninen haastattelija", hiring_manager: "Esihenkilö",
};
const STAGES = ["Räätälöinti", "Sääntötarkistus", "Paneeli", "Korjaus", "Valmis"];

export interface Finding { severity: "blocking" | "suggestion"; category: string; quote: string; source_id: string; issue: string; fix: string }
export interface Review { agent: string; label: string; verdict: string; rejects: boolean; score: number; findings: Finding[]; interview_questions: string[]; summary: string }
export interface PanelInfo {
  status: "approved" | "needs_review"; rounds: number; run_id?: string; duration_s: number; cost?: number | null;
  usage?: { input: number; output: number }; reviews: Record<string, Review>; failed: Record<string, string>;
  remaining: (Finding & { agent: string; resolved?: string })[]; rule: Finding[]; interview_questions: string[];
  declined: string[]; fallback: { summary: boolean; reverted: string[] } | null; models: Record<string, string>;
  screening?: Screening;
}
interface Term { term: string; status: "evidenced" | "skills_only" | "dropped" | "missing"; ids: string[]; fixable_ids: string[]; form: string | null }
interface Requirement { requirement: string; kind: "must" | "nice"; status: "shown" | "weak" | "not_shown" | "lacking"; evidence: string }
export interface Screening {
  coverage: { terms: Term[]; have: number; evidenced: number; skills_only: number; dropped: number; missing: number; score: number | null } | null;
  decision?: "advance" | "maybe" | "reject" | null; score?: number; note?: string; requirements?: Requirement[];
}
interface Ev { seq: number; at: string; type: string; message: string; data: Record<string, any> }
interface RunRow { run_id: string; lang: string; created: string; finished?: string; status: string; cost?: number | null; usage: { input?: number; output?: number } }

const FINAL = new Set(["run_finished", "run_failed", "run_cancelled"]);
const RUN_STATUS: Record<string, string> = {
  running: "käynnissä", approved: "hyväksytty", needs_review: "vaatii tarkistusta", failed: "epäonnistui",
  cancelled: "peruutettu", interrupted: "keskeytyi",
};

function stageOf(evs: Ev[]): number {
  for (let i = evs.length - 1; i >= 0; i--) {
    const { type, data } = evs[i];
    if (FINAL.has(type) || type === "fallback_applied") return 4;
    if (type.startsWith("review_")) return 2;
    if (type === "revision_started") return 3;
    if (type === "rules_checked") return data.round ? 3 : 1;
    if (type.startsWith("tailor_")) return data.round ? 3 : 0;
  }
  return 0;
}

const clock = (iso: string) => iso.slice(11, 19);

/** Käynnissä olevan (tai juuri valmistuneen) ajon näkymä: vaihepalkki, arvioijat, loki ja peruutus. */
export function PanelRun({ runId, slow, onDone, onClose }: { runId: string; slow: boolean; onDone: (type: string) => void; onClose: () => void }) {
  const toast = useToast();
  const [evs, setEvs] = useState<Ev[]>([]);
  const [now, setNow] = useState(Date.now());
  const logRef = useRef<HTMLOListElement>(null);
  const stick = useRef(true);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    setEvs([]);
    // EventSource lähettää Last-Event-ID:n itse uudelleenyhdistäessä, joten loki jatkuu siitä mihin jäi
    const es = new EventSource(`/api/cv/runs/${runId}/events`);
    es.onmessage = (m) => {
      const ev: Ev = JSON.parse(m.data);
      setEvs((xs) => (xs.some((x) => x.seq === ev.seq) ? xs : [...xs, ev]));
      if (FINAL.has(ev.type)) doneRef.current(ev.type);
    };
    es.addEventListener("end", () => es.close());
    return () => es.close();
  }, [runId]);

  const finished = evs.find((e) => FINAL.has(e.type));
  useEffect(() => {
    if (finished) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [finished]);
  useLayoutEffect(() => {
    const el = logRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [evs]);

  const stage = stageOf(evs);
  // edistyminen kierroksen sisällä: suunnitelma 40 %, jokainen valmis arvioija 15 %; kierroksia enintään 3
  const inRound = (() => {
    const mine = evs.filter((e) => (e.data.round ?? 0) === Math.max(0, ...evs.map((x) => x.data.round ?? 0)));
    const reviewed = mine.filter((e) => e.type === "review_finished" || e.type === "review_failed").length;
    return (mine.some((e) => e.type === "tailor_finished") ? 0.4 : 0.1) + reviewed * (0.6 / AGENTS.length);
  })();
  const round = Math.max(0, ...evs.map((e) => e.data.round ?? 0));
  const agent = (a: string) => {
    const mine = evs.filter((e) => e.data.agent === a && (e.data.round ?? 0) === round);
    const last = mine[mine.length - 1];
    if (!last) return { cls: "", text: "odottaa" };
    if (last.type === "review_finished") return { cls: last.data.verdict === "approve" ? "ok" : "bad", text: `${last.data.verdict === "approve" ? "hyväksyy" : "hylkää"} · ${last.data.score}/10` };
    if (last.type === "review_failed") return { cls: "bad", text: "epäonnistui" };
    return { cls: "busy", text: "arvioi…" };
  };
  const t0 = evs[0] ? Date.parse(evs[0].at) : now;
  const t1 = finished ? Date.parse(finished.at) : now;
  const secs = Math.max(0, Math.round((t1 - t0) / 1000));
  const cancel = async () => {
    try { await post(`/api/cv/runs/${runId}/cancel`, {}); toast("Peruutetaan…"); } catch (e) { toast((e as Error).message); }
  };

  return (
    <section className="panel-run no-print" aria-labelledby="run-title">
      <div className="panel-run-head">
        <h2 id="run-title">Arviointipaneeli {round > 0 && <span className="muted">· kierros {round + 1}/3</span>}</h2>
        <span className="mono muted" aria-label="Kulunut aika">{Math.floor(secs / 60)}:{String(secs % 60).padStart(2, "0")}</span>
        {!finished ? <button className="btn small" onClick={cancel}>Peruuta</button> : <button className="btn small ghost" onClick={onClose}>Sulje</button>}
      </div>
      {slow && !finished && <p className="stale" style={{ margin: "0 0 8px" }}>Paikallinen malli arvioi peräkkäin: ajo kestää useita minuutteja.</p>}
      {!finished && <ProgressBar compact label={round > 0 ? `Korjauskierros ${round}/2` : "Ensimmäinen kierros"}
        value={Math.min(1, inRound)} detail="Jos arvioijat löytävät estäviä ongelmia, räätälöijä korjaa ne (enintään kaksi korjauskierrosta)." />}
      <ol className="stages" aria-label="Vaiheet">
        {STAGES.map((s, i) => (
          <li key={s} className={i < stage || (finished && i === 4) ? "done" : i === stage ? "now" : ""} aria-current={i === stage && !finished ? "step" : undefined}>{s}</li>
        ))}
      </ol>
      <div className="agents">
        {AGENTS.map((a) => {
          const st = agent(a);
          return (
            <div key={a} className={"agent " + st.cls}>
              <b>{AGENT_LABEL[a]}</b>
              <span>{st.cls === "busy" && <span className="spinner" />} {st.text}</span>
            </div>
          );
        })}
      </div>
      <ol className="runlog" ref={logRef} aria-live="polite"
          onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 30; }}>
        {evs.map((e) => (
          <li key={e.seq} className={/failed|cancelled/.test(e.type) ? "bad" : e.type === "retry" || e.type === "fallback_applied" ? "warn" : ""}>
            <time>{clock(e.at)}</time> {e.message}
          </li>
        ))}
        {!evs.length && <li className="muted">Yhdistetään…</li>}
      </ol>
    </section>
  );
}

function FindingItem({ f, who }: { f: Finding & { resolved?: string }; who?: string }) {
  return (
    <li className={f.severity === "blocking" && !f.resolved ? "st-flag" : ""}>
      {who && <b>{who}: </b>}
      {f.issue}
      {f.quote && <div className="src">”{f.quote}”{f.source_id && <span className="mono"> · {f.source_id}</span>}</div>}
      {f.fix && <div className="why">Korjaus: {f.fix}</div>}
      {f.resolved && <div className="why">Ratkaistu: {f.resolved}</div>}
    </li>
  );
}

const DECISION: Record<string, [string, string]> = {
  advance: ["Etenee rekrytoijalle", "good"], maybe: ["Rajatapaus", "warn"], reject: ["Karsiutuu", "stamp"],
};
const REQ: Record<string, [string, string]> = {
  shown: ["näkyy", "good"], weak: ["heikosti", "warn"], not_shown: ["ei näy, vaikka sinulla on", "stamp"], lacking: ["puuttuu sinulta", ""],
};
const TERM: Record<string, string> = { evidenced: "näkyy käytössä", skills_only: "vain taitolistassa", dropped: "jäi pois CV:stä", missing: "ei sinulla" };

/** Tekoälyseulonnan arvio: kulkeeko CV automaattisen esikarsinnan läpi ihmiselle. */
function ScreeningResult({ sc }: { sc: Screening }) {
  const cov = sc.coverage;
  const reqs = sc.requirements || [];
  const d = sc.decision ? DECISION[sc.decision] : null;
  const groups = cov ? (["evidenced", "skills_only", "dropped", "missing"] as const).map((k) => [k, cov.terms.filter((t) => t.status === k)] as const).filter(([, ts]) => ts.length) : [];
  return (
    <section aria-labelledby="scr-title">
      <h2 id="scr-title">Tekoälyseulonta</h2>
      <p style={{ margin: "0 0 8px", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        {d && <span className={"tag " + d[1]}>{d[0]}</span>}
        {sc.score != null && <span className="mono">{sc.score}/10</span>}
        {cov?.score != null && <span className="muted" style={{ fontSize: 12.5 }}
          title="Ilmoituksen tekniikoista, jotka sinulla on: käytössä näkyvä = 1, vain taitolistassa = ½">
          Avainsanat {Math.round(cov.score * 100)} % ({cov.evidenced + cov.skills_only}/{cov.have})</span>}
      </p>
      {sc.note && <p style={{ fontSize: 13, margin: "0 0 8px" }}>{sc.note}</p>}
      {reqs.length > 0 && (
        <ul className="reqs">
          {reqs.map((q, i) => (
            <li key={i}>
              <span className="mono muted">{q.kind === "must" ? "vaatimus" : "eduksi"}</span>
              <span>{q.requirement}{q.evidence && <span className="muted"> · {q.evidence}</span>}</span>
              <span className={"tag " + REQ[q.status][1]}>{REQ[q.status][0]}</span>
            </li>
          ))}
        </ul>
      )}
      {groups.length > 0 && (
        <details className="review" style={{ marginTop: 6 }}>
          <summary><b>Ilmoituksen tekniikat</b></summary>
          {groups.map(([k, ts]) => (
            <p key={k} style={{ fontSize: 13, margin: "6px 0 0" }}><b>{TERM[k]}:</b> {ts.map((t) => t.term + (t.form ? ` (CV: ${t.form})` : "")).join(", ")}</p>
          ))}
          <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>Tekniikoita, joita sinulla ei ole, ei lisätä CV:hen. Ne kertovat, kuinka hyvin paikka sopii.</p>
        </details>
      )}
    </section>
  );
}

/** Valmiin ajon tulokset CV-näkymän sivupalkissa. */
export function PanelResult({ panel }: { panel: PanelInfo }) {
  const toast = useToast();
  const open = panel.remaining.filter((f) => !f.resolved);
  const copyQs = async () => {
    try { await navigator.clipboard.writeText(panel.interview_questions.map((q) => `- ${q}`).join("\n")); toast("Kysymykset kopioitu"); }
    catch { toast("Kopiointi ei onnistunut"); }
  };
  const tokens = panel.usage ? panel.usage.input + panel.usage.output : null;
  return (
    <>
      <section aria-labelledby="pr-title">
        <h2 id="pr-title">Paneelin arvio</h2>
        <p style={{ margin: "0 0 8px", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span className={"tag " + (panel.status === "approved" ? "good" : "warn")}>{panel.status === "approved" ? "Hyväksytty" : "Vaatii tarkistusta"}</span>
          <span className="muted" style={{ fontSize: 12.5 }}>{panel.rounds} kierros{panel.rounds > 1 ? "ta" : ""} · {Math.round(panel.duration_s)} s
            {tokens !== null && ` · ${tokens.toLocaleString("fi-FI")} tokenia`}{panel.cost != null && ` · n. $${panel.cost.toFixed(3)}`}</span>
        </p>
        {panel.fallback && (panel.fallback.summary || panel.fallback.reverted.length > 0) && (
          <p className="stale" style={{ margin: "0 0 8px" }}>Faktantarkistajan löydösten takia alkuperäiseksi palautettiin: {[
            ...(panel.fallback.summary ? ["profiiliteksti"] : []), ...panel.fallback.reverted].join(", ")}.</p>
        )}
        {Object.keys(panel.failed).length > 0 && (
          <p className="stale" style={{ margin: "0 0 8px" }}>Ei arviota: {Object.keys(panel.failed).map((a) => AGENT_LABEL[a]).join(", ")}</p>
        )}
        {open.length > 0 && (
          <>
            <h3 className="kicker" style={{ margin: "10px 0 4px" }}>Ratkaisematta</h3>
            <ul className="claims">{open.map((f, i) => <FindingItem key={i} f={f} who={AGENT_LABEL[f.agent]} />)}</ul>
          </>
        )}
        <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
          {AGENTS.filter((a) => panel.reviews[a]).map((a) => {
            const r = panel.reviews[a];
            return (
              <details key={a} className="review">
                <summary>
                  <b>{r.label}</b>
                  <span className={"tag " + (r.rejects ? "stamp" : "good")}>{r.rejects ? "hylkää" : "hyväksyy"}</span>
                  <span className="mono">{r.score}/10</span>
                </summary>
                <p style={{ fontSize: 13, margin: "6px 0" }}>{r.summary}</p>
                {r.findings.length > 0
                  ? <ul className="claims">{r.findings.map((f, i) => <FindingItem key={i} f={f} />)}</ul>
                  : <p className="muted" style={{ fontSize: 13, margin: 0 }}>Ei huomautettavaa.</p>}
              </details>
            );
          })}
        </div>
        {panel.declined.length > 0 && (
          <details className="review" style={{ marginTop: 6 }}>
            <summary><b>Toteuttamatta jätetyt ehdotukset</b></summary>
            <ul className="points" style={{ gap: 4, marginTop: 6 }}>{panel.declined.map((d, i) => <li key={i} style={{ paddingLeft: 0 }}>{d}</li>)}</ul>
          </details>
        )}
      </section>
      {panel.screening && <ScreeningResult sc={panel.screening} />}
      {panel.interview_questions.length > 0 && (
        <section aria-labelledby="iq-title">
          <h2 id="iq-title">Haastattelukysymykset</h2>
          <ol style={{ margin: "0 0 8px", paddingLeft: 18, fontSize: 13.5, display: "grid", gap: 4 }}>
            {panel.interview_questions.map((q, i) => <li key={i}>{q}</li>)}
          </ol>
          <button className="btn small" onClick={copyQs}>Kopioi</button>
        </section>
      )}
    </>
  );
}

/** Ilmoituksen aiemmat paneeliajot; lokin voi lukea jälkikäteen. */
export function RunHistory({ url, refreshKey }: { url: string; refreshKey: number }) {
  const runs = useQuery({ queryKey: ["cv-runs", url, refreshKey], queryFn: () => get<{ runs: RunRow[] }>("/api/cv/runs", { url }) });
  const [open, setOpen] = useState<string | null>(null);
  const log = useQuery({ queryKey: ["cv-run", open], enabled: !!open, queryFn: () => get<{ events: Ev[] }>(`/api/cv/runs/${open}`) });
  const list = runs.data?.runs || [];
  if (!list.length) return null;
  return (
    <section aria-labelledby="rh-title">
      <h2 id="rh-title">Aiemmat paneeliajot</h2>
      <ul className="points" style={{ gap: 6 }}>
        {list.map((r) => (
          <li key={r.run_id} style={{ paddingLeft: 0 }}>
            <button className="linkish" aria-expanded={open === r.run_id} onClick={() => setOpen(open === r.run_id ? null : r.run_id)}>
              {r.created.slice(5, 16).replace("T", " ")} · {r.lang} · {RUN_STATUS[r.status] || r.status}
            </button>
            {r.cost != null && <span className="muted mono" style={{ fontSize: 12 }}> · ${r.cost.toFixed(3)}</span>}
            {open === r.run_id && (
              <ol className="runlog" style={{ maxHeight: 220, marginTop: 6 }}>
                {(log.data?.events || []).map((e) => <li key={e.seq}><time>{clock(e.at)}</time> {e.message}</li>)}
                {log.isLoading && <li className="muted">Ladataan…</li>}
              </ol>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
