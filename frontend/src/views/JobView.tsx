import { Fragment, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, get, post } from "../api";
import { useJobState, useSetStatus } from "../actions";
import { DeadlineTag, ErrorNotice, Loading, Scores, SystemBanner, useToast } from "../components";
import { STATUS_LABEL, STATUS_ORDER, shortDate, useAiStatus, useApps, useKeys, useTitle, useTracking } from "../lib";
import type { App, Explanation, Ref, Row, Status } from "../types";
import { ProgressBar } from "../progress";

interface JobPayload {
  row: Row; app: App | null; state: { saved?: boolean; hidden?: boolean }; gone: string | null;
  insights: { matched?: (string | { name: string; weight: number })[]; missing?: string[]; experience?: string[]; languages?: string[]; mode?: string[] };
}
interface AiJob { clean_text: string; lang: string; posted: string; deadline: string }

/** Ilmoitusteksti, jossa selityksen viittaamat kohdat on korostettu ja numeroitu alaviitteiksi. */
function MarkedText({ text, refs, active }: { text: string; refs: Ref[]; active: string | null }) {
  const spans = refs.filter((r) => r.type === "ad" && r.start != null && r.end != null && r.end! > r.start!)
    .sort((a, b) => a.start! - b.start!);
  const out: ReactNode[] = [];
  let pos = 0;
  for (const r of spans) {
    const s = Math.max(r.start!, pos);
    if (s >= r.end!) continue;   // päällekkäisyys: aiempi korostus kattaa jo
    if (s > pos) out.push(text.slice(pos, s));
    out.push(<mark key={r.id} id={`ref-${r.id}`} className={active === r.id ? "on" : ""}>{text.slice(s, r.end!)}<sup>{r.id}</sup></mark>);
    pos = r.end!;
  }
  out.push(text.slice(pos));
  return <div className="ad-text">{out}</div>;
}

function RefButtons({ ids, refs, onAd, onProfile }: {
  ids: string[]; refs: Record<string, Ref>; onAd: (id: string) => void; onProfile: (id: string) => void;
}) {
  return <>{ids.filter((id) => refs[id]).map((id) => (
    <button key={id} className={"refbtn" + (id.startsWith("P") ? " p" : "")} onClick={() => (id.startsWith("A") ? onAd(id) : onProfile(id))}
            aria-label={id.startsWith("A") ? `Näytä ilmoituksen kohta ${id}` : `Näytä profiilin kohta ${id}`}>{id}</button>
  ))}</>;
}

/** Välimuistissa oleva selitys (ei generoi). Sama kysely ilmoitustekstin korostuksille ja reunapalstalle. */
function useExplain(url: string, enabled: boolean) {
  return useQuery({
    queryKey: ["explain", url], retry: false, enabled, staleTime: Infinity,
    queryFn: () => get<Explanation>("/api/explain", { url, cached_only: true }).catch((e) => {
      if (e instanceof ApiError && e.code === "not_found") return null;
      throw e;
    }),
  });
}

function ExplainPanel({ url, onAd, active }: { url: string; onAd: (id: string) => void; active: string | null }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [openP, setOpenP] = useState<string | null>(null);
  const cached = useExplain(url, true);
  const run = async (refresh = false) => {
    setBusy(true); setErr(null);
    try {
      qc.setQueryData(["explain", url], await get<Explanation>("/api/explain", { url, refresh }));
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  useKeys({ e: () => !busy && run(false) });
  const ex = cached.data;
  if (cached.isLoading) return <Loading lines={4} />;
  if (cached.error) return <ErrorNotice error={cached.error} title="Selitystä ei voi näyttää" />;
  return (
    <section aria-labelledby="fit-title" aria-busy={busy}>
      <h2 id="fit-title">Sopivuus</h2>
      {err ? <ErrorNotice error={err} title="Selityksen luonti epäonnistui" retry={() => run(false)} /> : null}
      {!ex && !busy && (
        <>
          <p className="muted" style={{ fontSize: 15 }}>Kielimalli vertaa ilmoitusta CV:hen ja osaamisprofiiliin ja viittaa molempiin.</p>
          <button className="btn primary" onClick={() => run(false)}>Selitä sopivuus <kbd>e</kbd></button>
        </>
      )}
      {busy && <ProgressBar id="explain" llm={[12, 45]} label="Luodaan sopivuusselitystä ja tarkistetaan viitteet" />}
      {ex && (
        <>
          <div className={`verdict ${ex.verdict}`}>{ex.verdict[0].toUpperCase() + ex.verdict.slice(1)}</div>
          <p style={{ fontSize: 14, color: "var(--ink-2)" }}>{ex.summary}</p>
          <h3 className="kicker" style={{ marginTop: 10 }}>Vahvuudet</h3>
          {ex.strengths.length === 0 && <p className="muted">Ei lähteellisiä vahvuuksia.</p>}
          <ul className="points plus">
            {ex.strengths.map((s, i) => (
              <li key={i}>{s.text} <RefButtons ids={[...s.ad, ...s.profile]} refs={ex.refs} onAd={onAd}
                                              onProfile={(id) => setOpenP(openP === id ? null : id)} />
                {s.profile.includes(openP || "") && ex.refs[openP!] && (
                  <div className="prof-note"><span className="src">{openP} · {ex.refs[openP!].source_ref}</span>{ex.refs[openP!].text.slice(0, 400)}</div>
                )}
              </li>
            ))}
          </ul>
          <h3 className="kicker" style={{ marginTop: 14 }}>Puutteet</h3>
          <ul className="points minus">
            {ex.gaps.map((g, i) => (
              <li key={i}>{g.text} {g.severity === "must" && <span className="tag stamp">Vaatimus</span>}{" "}
                <RefButtons ids={g.ad} refs={ex.refs} onAd={onAd} onProfile={() => {}} /></li>
            ))}
          </ul>
          {ex.dropped.length > 0 && (
            <details style={{ marginTop: 10, fontSize: 14 }}>
              <summary className="muted">{ex.dropped.length} väitettä hylätty, koska profiilista ei löytynyt lähdettä</summary>
              <ul>{ex.dropped.map((d, i) => <li key={i}>{d.text}</li>)}</ul>
            </details>
          )}
          <p className="muted mono" style={{ fontSize: 12, marginTop: 10 }}>
            {ex.cached ? `Välimuistista · ${ex.created?.slice(0, 16).replace("T", " ")}` : "Juuri luotu"} · {ex.model}{" "}
            <button className="btn small ghost" onClick={() => run(true)} disabled={busy}>Luo uudelleen</button>
          </p>
          {active && <span className="sr" aria-live="polite">Korostettu kohta {active}</span>}
        </>
      )}
    </section>
  );
}

export default function JobView() {
  const [params] = useSearchParams();
  const url = params.get("url") || "";
  const nav = useNavigate();
  const toast = useToast();
  const apps = useApps();
  const tracking = useTracking();
  const status = useAiStatus();
  const setStatus = useSetStatus(toast);
  const jobState = useJobState(toast);
  const [active, setActive] = useState<string | null>(null);
  const qc = useQueryClient();
  const job = useQuery({ queryKey: ["job", url], queryFn: () => get<JobPayload>("/api/job", { url }), enabled: !!url });
  const ai = useQuery({ queryKey: ["ai-job", url], queryFn: () => get<AiJob>("/api/ai/job", { url }), enabled: !!url, retry: false });
  const ex = useExplain(url, !!ai.data).data;
  const refs = useMemo(() => Object.values(ex?.refs || {}), [ex]);
  const r = job.data?.row;
  useTitle(r?.job.title || "Ilmoitus");

  const app = (apps.data?.apps || []).find((a) => a.url === url) || null;
  const allowed: Status[] = app ? (tracking.data?.transitions[app.status] || []) : ["interested", "drafting", "applied"];
  const change = (s: Status) => setStatus.mutate({ url, status: s, apps: apps.data?.apps || [] });
  // Alkuperäinen ilmoitus uuteen välilehteen (lomakkeen täyttöä varten) ja CV-sivu tähän: CV tehdään sillä välin
  const openCv = () => {
    if (r?.job.source !== "manual" || /^https?:/.test(url)) window.open(url, "_blank", "noopener");
    nav(`/cv?url=${encodeURIComponent(url)}&lang=${ai.data?.lang === "fi" ? "fi" : "en"}&auto=1`);
  };
  const showAd = (id: string) => {
    setActive(id);
    document.getElementById(`ref-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  useKeys({
    Escape: () => nav(-1),
    o: () => window.open(url, "_blank", "noopener"),
    h: () => openCv(),
    s: () => jobState.mutate({ url, saved: !job.data?.state.saved }),
    x: () => jobState.mutate({ url, hidden: !job.data?.state.hidden }),
    ...Object.fromEntries(STATUS_ORDER.map((s, i) => [String(i + 1), () => {
      if (app ? allowed.includes(s) || s === app.status : true) change(s);
      else toast(`Tilasta ${STATUS_LABEL[app!.status]} ei voi siirtyä tilaan ${STATUS_LABEL[s]}`);
    }])),
  });

  if (!url) return <ErrorNotice error="Ilmoituksen osoite puuttuu" />;
  if (job.isLoading) return <Loading lines={12} label="Ladataan ilmoitusta" />;
  if (job.error) return <ErrorNotice error={job.error} title="Ilmoitusta ei voi avata" retry={() => job.refetch()} />;
  if (!r) return null;
  const d = r.job.detail || {};
  const text = ai.data?.clean_text || r.job.text || "";
  const indexed = !!ai.data;

  return (
    <article className="article">
      <header className="article-head">
        <span className="kicker">{r.area || r.job.location} · {r.job.source}</span>
        <h1>{r.job.title}</h1>
        <div className="byline">
          <span className="co">{r.job.company}</span>
          {d.places?.length ? <span>{d.places.join(", ")}</span> : <span>{r.job.location}</span>}
          {d.employment && <span>{d.employment}</span>}
          {d.posted && <span className="mono">julkaistu {shortDate(d.posted)}</span>}
          <DeadlineTag iso={r.deadline || d.deadline} />
          {job.data?.gone && <span className="tag stamp">Poistunut lähteestä {shortDate(job.data.gone)}</span>}
        </div>
        <div className="actions">
          <button className="btn primary" onClick={openCv}
                  title="Avaa alkuperäisen ilmoituksen uuteen välilehteen ja tekee CV:n sillä välin">Räätälöi CV <kbd>h</kbd></button>
          {r.job.source === "manual" ? (
            <button className="btn ghost" onClick={async () => {
              if (!confirm("Poistetaanko käsin lisätty ilmoitus?")) return;
              try { await post("/api/manual", { action: "delete", url }); qc.invalidateQueries({ queryKey: ["jobs"] }); toast("Poistettu"); nav("/"); }
              catch (e) { toast((e as Error).message); }
            }}>Poista käsin lisätty</button>
          ) : <a className="btn" href={url} target="_blank" rel="noreferrer">Alkuperäinen ↗</a>}
          <label className="sr" htmlFor="status">Seurannan tila</label>
          <select id="status" style={{ width: "auto" }} value={app?.status || ""} onChange={(e) => e.target.value && change(e.target.value as Status)}>
            <option value="" disabled={!!app}>{app ? "" : "Lisää seurantaan…"}</option>
            {STATUS_ORDER.map((s, i) => (
              <option key={s} value={s} disabled={!!app && s !== app.status && !allowed.includes(s)}>{i + 1} · {STATUS_LABEL[s]}</option>
            ))}
          </select>
          <Link className="btn ghost" to={`/hakemus?url=${encodeURIComponent(url)}`}
                title="Saatekirje ja valmiit vastaukset. Lomakkeen kysymyksiin laajennus kirjoittaa vastaukset ilman tätäkin.">Saatekirje</Link>
          <button className="btn ghost" aria-pressed={!!job.data?.state.saved} onClick={() => jobState.mutate({ url, saved: !job.data?.state.saved })}>
            {job.data?.state.saved ? "Tallennettu" : "Tallenna"} <kbd>s</kbd></button>
          <button className="btn ghost" aria-pressed={!!job.data?.state.hidden} title="Ei kiinnosta: ilmoitus piilotetaan listalta"
                  onClick={() => jobState.mutate({ url, hidden: !job.data?.state.hidden })}>
            {job.data?.state.hidden ? "Piilotettu" : "Piilota"} <kbd>x</kbd></button>
        </div>
      </header>

      <div>
        {!indexed && <p className="notice">Ilmoitus ei ole vielä semanttisessa indeksissä: selitys ja hakemuspaketti vaativat sen
          (päivittyy haun jälkeen, tai aja <code>python -m rag ingest</code>).</p>}
        <MarkedText text={text} refs={refs} active={active} />
      </div>

      <aside className="margin" aria-label="Arvio">
        <SystemBanner need={["llm"]} />
        {indexed && <ExplainPanel url={url} onAd={showAd} active={active} />}
        <section>
          <h2>Pisteet</h2>
          {r.llm && (
            <p style={{ margin: "0 0 10px", fontSize: 13.5 }}>
              <b style={{ fontWeight: 600 }}>Sopivuus {r.llm.fit}/100</b> · {{ junior: "junioritaso", mid: "vaatii kokemusta", senior: "senior-taso" }[r.llm.level]}<br />
              <span style={{ color: "var(--ink-2)" }}>{r.llm.reason}</span></p>
          )}
          <Scores row={r} mode={status.data?.scoring.mode} />
          <ul style={{ fontSize: 14, paddingLeft: 18, marginTop: 10 }}>{r.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </section>
        {(job.data?.insights.matched?.length || job.data?.insights.missing?.length) ? (
          <section>
            <h2>Tekniikat</h2>
            <table className="meta-table"><tbody>
              {job.data!.insights.matched?.length ? <tr><th>Osaat</th><td>{job.data!.insights.matched.map((m) => (typeof m === "string" ? m : m.name)).join(", ")}</td></tr> : null}
              {job.data!.insights.missing?.length ? <tr><th>Puuttuu</th><td>{job.data!.insights.missing.join(", ")}</td></tr> : null}
              {job.data!.insights.languages?.length ? <tr><th>Kielet</th><td>{job.data!.insights.languages.map((x, i) => <Fragment key={i}>{x}<br /></Fragment>)}</td></tr> : null}
            </tbody></table>
          </section>
        ) : null}
        {app && (
          <section>
            <h2>Seuranta</h2>
            <p><span className="tag good">{STATUS_LABEL[app.status]}</span> <span className="muted mono" style={{ fontSize: 13 }}>
              päivitetty {shortDate(app.updated)}</span></p>
            <Link to={`/seuranta?id=${app.id}`}>Muistiinpanot ja päivämäärät →</Link>
          </section>
        )}
      </aside>
    </article>
  );
}
