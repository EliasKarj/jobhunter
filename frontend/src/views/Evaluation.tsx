import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { ErrorNotice, Loading, useToast } from "../components";
import { fmtNum, useKeys, useTitle } from "../lib";
import type { EvalRun } from "../types";
import { ProgressBar } from "../progress";

interface EvalPayload { runs: EvalRun[]; labels: { url: string; title: string; label: string }[]; counts: Record<string, number> }
interface Cand { url: string; title: string; company: string; location: string; text: string; posted?: string }
// evaluate.py tallentaa rankingit suomenkielisillä avaimilla
const NAMES: Record<string, string> = { avainsana: "Avainsana", semanttinen: "Semanttinen", hybridi: "Hybridi", kielimalli: "Kielimalli", "yhdistelmä": "Yhdistelmä" };
const KEYS = ["avainsana", "semanttinen", "hybridi", "kielimalli", "yhdistelmä"];

function Results({ run }: { run: EvalRun }) {
  const rankers = Object.entries(run.rankers);
  const best = Math.max(...rankers.map(([, m]) => m["ndcg@k"]));
  const sweep = run.sweep || [];
  const top = Math.max(...sweep.map((s) => s["ndcg@k"]), 0.01);
  return (
    <>
      <p className="muted" style={{ fontSize: 14 }}>
        {run.at.slice(0, 16).replace("T", " ")} · {run.n_labels} merkintää ({run.n_relevant} relevanttia) · indeksi <span className="mono">{run.config.config_id}</span> ·
        painot {run.config.w_keyword}/{run.config.w_semantic}{run.note ? ` · ${run.note}` : ""}</p>
      <div style={{ overflowX: "auto" }}>
        <table className="data">
          <thead><tr><th>Ranking</th><th className="n">P@10</th><th className="n">NDCG@10</th><th className="n">95 % väli</th><th className="n">Koko korpus</th></tr></thead>
          <tbody>{rankers.map(([k, m]) => (
            <tr key={k} className={m["ndcg@k"] === best ? "best" : ""}>
              <td>{NAMES[k] || k}</td><td className="n">{fmtNum(m["p@k"], 2)}</td><td className="n">{fmtNum(m["ndcg@k"], 2)}</td>
              <td className="n">{m.ndcg_ci ? `${fmtNum(m.ndcg_ci[0], 2)}–${fmtNum(m.ndcg_ci[1], 2)}` : "–"}</td>
              <td className="n" title="NDCG koko korpuksen top-10:stä; kattavuus = osuus top-10:stä, joka on merkitty">
                {fmtNum(m["full_ndcg@k"], 2)} <span className="muted">({Math.round((m.full_coverage || 0) * 100)} %)</span></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {sweep.length > 0 && (
        <figure style={{ margin: "20px 0 0" }}>
          <figcaption className="kicker" style={{ marginBottom: 6 }}>Hybridin NDCG@10 semanttisen painon mukaan</figcaption>
          <div className="sweep" role="img" aria-label={sweep.map((s) => `semanttinen ${s.w_semantic}: ${s["ndcg@k"]}`).join(", ")}>
            {sweep.map((s) => (
              <div key={s.w_semantic} className={s["ndcg@k"] === top ? "best" : ""} style={{ height: `${(s["ndcg@k"] / top) * 100}%` }}>
                <span>{fmtNum(s["ndcg@k"], 2)}</span></div>
            ))}
          </div>
          <div className="sweep-x" aria-hidden="true">{sweep.map((s) => <span key={s.w_semantic}>{s.w_semantic}</span>)}</div>
        </figure>
      )}
    </>
  );
}

function Labeler({ review }: { review: boolean }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [i, setI] = useState(0);
  const cands = useQuery({
    queryKey: ["eval-cands", review], staleTime: Infinity,
    queryFn: async () => (review ? { candidates: (await get<{ items: (Cand & { label: number })[] }>("/api/eval/review")).items }
      : await get<{ candidates: (Cand & { label?: number })[] }>("/api/eval/candidates", { n: 30 })),
  });
  const list = cands.data?.candidates || [];
  const c = list[i];
  const label = async (v: 0 | 1 | 2) => {
    if (!c) return;
    try {
      await post("/api/eval/label", { url: c.url, label: v });
      toast(`Merkitty ${v}`);
      setI((x) => x + 1);
      qc.invalidateQueries({ queryKey: ["eval"] });
    } catch (e) { toast((e as Error).message); }
  };
  useKeys({ "2": () => label(2), "1": () => label(1), "0": () => label(0), s: () => setI((x) => x + 1) });
  if (cands.isLoading) return <><ProgressBar id="eval-candidates" estimate={40} label="Pisteytetään ehdokkaita kaikilla rankingeilla" /><Loading lines={5} /></>;
  if (cands.error) return <ErrorNotice error={cands.error} retry={() => cands.refetch()} />;
  if (!c) return <div className="empty">Ehdokkaat on käyty läpi.<p><button className="btn" onClick={() => { setI(0); cands.refetch(); }}>Hae lisää ehdokkaita</button></p></div>;
  return (
    <div className="label-card" aria-live="polite">
      <span className="kicker">{review ? `Tarkistus ${i + 1} / ${list.length} · aiempi merkintä ${c.label}` : `Ehdokas ${i + 1} / ${list.length}`} · pisteitä ei näytetä</span>
      <h3 style={{ margin: "4px 0" }}>{c.title}</h3>
      <div className="byline"><span className="co">{c.company}</span><span>{c.location}</span></div>
      <div className="ex">{c.text}</div>
      <div className="label-keys">
        <button className="btn primary" onClick={() => label(2)}>Erittäin relevantti <kbd>2</kbd></button>
        <button className="btn" onClick={() => label(1)}>Relevantti <kbd>1</kbd></button>
        <button className="btn" onClick={() => label(0)}>Ei relevantti <kbd>0</kbd></button>
        <button className="btn ghost" onClick={() => setI((x) => x + 1)}>Ohita <kbd>s</kbd></button>
        <a className="btn ghost" href={c.url} target="_blank" rel="noreferrer">Alkuperäinen ↗</a>
      </div>
    </div>
  );
}

export default function Evaluation() {
  useTitle("Evaluointi");
  const toast = useToast();
  const qc = useQueryClient();
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [labeling, setLabeling] = useState<false | "new" | "review">(false);
  const ev = useQuery({ queryKey: ["eval"], queryFn: () => get<EvalPayload>("/api/eval") });
  const run = async () => {
    setRunning(true); setErr(null);
    try { await post("/api/eval/run", { note: "käyttöliittymästä" }); toast("Evaluointi valmis"); qc.invalidateQueries({ queryKey: ["eval"] }); }
    catch (e) { setErr(e); } finally { setRunning(false); }
  };
  const runs = ev.data?.runs || [];
  const last = runs[runs.length - 1];
  return (
    <div className="two">
      <section aria-labelledby="eval-title">
        <div className="section-head">
          <h1 id="eval-title" style={{ fontSize: 28 }}>Evaluointi</h1>
          <button className="btn small primary" onClick={run} disabled={running}>{running ? "Lasketaan…" : "Aja evaluointi"}</button>
        </div>
        {running && <ProgressBar id="eval-run" estimate={60} label="Lasketaan rankingit ja painovertailu" />}
        {err ? <ErrorNotice error={err} /> : null}
        {ev.isLoading && <Loading />}
        {ev.error && <ErrorNotice error={ev.error} />}
        {last ? <Results run={last} /> : ev.data && <p className="muted">Ei vielä ajoja.</p>}
        {runs.length > 1 && (
          <>
            <h2 style={{ fontSize: 19, marginTop: 26 }}>Aiemmat ajot</h2>
            <div style={{ overflowX: "auto" }}>
              <table className="data">
                <thead><tr><th>Aika</th><th>Asetukset</th><th className="n">Merk.</th><th className="n">Avain</th><th className="n">Sem.</th><th className="n">Hybr.</th><th className="n">Kiel.</th><th className="n">Yhd.</th></tr></thead>
                <tbody>{[...runs].reverse().slice(0, 15).map((r) => (
                  <tr key={r.at}><td className="mono" style={{ fontSize: 13 }}>{r.at.slice(5, 16).replace("T", " ")}</td>
                    <td style={{ fontSize: 13 }}>{r.config.config_id.replace("local_", "")} · {r.config.w_keyword}/{r.config.w_semantic} · {r.config.hybrid_method}</td>
                    <td className="n">{r.n_labels}</td>
                    {KEYS.map((k) => <td key={k} className="n">{fmtNum(r.rankers[k]?.["ndcg@k"], 2)}</td>)}</tr>
                ))}</tbody>
              </table>
            </div>
            <p className="muted" style={{ fontSize: 13 }}>Taulukossa NDCG@10 merkityillä ilmoituksilla.</p>
          </>
        )}
      </section>
      <section aria-labelledby="label-title">
        <div className="section-head">
          <h2 id="label-title">Relevanssimerkinnät</h2>
          <span className="mono muted" style={{ fontSize: 13 }}>
            {ev.data && `${ev.data.labels.length} merkintää · 2: ${ev.data.counts["2"]} · 1: ${ev.data.counts["1"]} · 0: ${ev.data.counts["0"]}`}</span>
        </div>
        <p style={{ fontSize: 15 }}>Ehdokkaat poimitaan jokaisen rankingin kärjestä sekä satunnaisesti, ja ne näytetään sekoitettuna, jotta
          merkintä ei suosi mitään menetelmää. Merkitse ainakin 0-merkintöjä: kun lähes kaikki on relevanttia, P@10 ei erottele.</p>
        {labeling ? <Labeler review={labeling === "review"} /> : (
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="btn primary" onClick={() => setLabeling("review")}>Tarkista aiemmat merkinnät</button>
            <button className="btn" onClick={() => setLabeling("new")}>Merkitse uusia</button>
          </div>
        )}
      </section>
    </div>
  );
}
