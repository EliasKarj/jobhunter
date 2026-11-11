import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { post } from "../api";
import { useJobState, useSetStatus } from "../actions";
import { DeadlineTag, ErrorNotice, Loading, useToast } from "../components";
import { STATUS_LABEL, STATUS_ORDER, daysAgo, shortDate, useAiStatus, useApps, useJobs, useKeys, useProjectSummary, useTitle } from "../lib";
import type { App, Row, Status } from "../types";
import { ProgressBar } from "../progress";

interface F {
  q: string; areas: string[]; sources: string[]; days: number; min: number; track: "all" | "none" | Status;
  saved: boolean; hidden: boolean; order: "combined" | "score" | "fit" | "new" | "deadline";
}
const DEFAULT: F = { q: "", areas: [], sources: [], days: 0, min: 0, track: "all", saved: false, hidden: false, order: "combined" };
const PAGE = 60;

function loadF(): F {
  try { return { ...DEFAULT, ...JSON.parse(localStorage.getItem("filters") || "{}") }; } catch { return DEFAULT; }
}

function posted(r: Row) { return r.job.detail?.posted || r.first_seen || ""; }
/** Uusi = haku löysi ilmoituksen tänään tai eilen JA se on julkaistu enintään 3 päivää sitten. Pelkkä löytöpäivä
 *  merkitsi uudeksi myös kuukausia vanhoja ilmoituksia, kun haku laajeni (esim. sivukartan ensimmäinen ajo). */
function isNew(r: Row) {
  if (!r.first_seen || (daysAgo(r.first_seen) ?? 99) > 1) return false;
  const p = r.job.detail?.posted;
  return !p || (daysAgo(p) ?? 0) <= 3;
}

/** Ilmoitus, jota haku ei löydä (esim. yrityksen omalta sivulta): pisteytetään samoin kuin muut. */
function ManualJobDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const empty = { title: "", company: "", location: "", url: "", deadline: "", text: "" };
  const [f, setF] = useState(empty);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) { setErr(null); d.showModal(); }
    if (!open && d.open) d.close();
  }, [open]);
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await post<{ ok: boolean; url?: string; duplicate?: string }>("/api/manual", { action: "add", fields: f });
      if (!r.ok && r.duplicate) { setErr("Sama ilmoitus on jo listalla."); return; }
      qc.invalidateQueries({ queryKey: ["jobs"] });
      toast("Ilmoitus lisätty ja pisteytetty");
      setF(empty); onClose();
      if (r.url) nav(`/ilmoitus?url=${encodeURIComponent(r.url)}`);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const field = (k: keyof typeof f, label: string, type = "text", wide = false) => (
    <label className={"field" + (wide ? " wide" : "")}>{label}
      <input type={type} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>
  );
  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="manual-title">
      <form method="dialog" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <h2 id="manual-title">Lisää ilmoitus käsin</h2>
        {err && <div className="notice error" role="alert"><p>{err}</p></div>}
        <div className="form-grid">
          {field("title", "Tehtävä", "text", true)}{field("company", "Yritys")}{field("location", "Paikkakunta")}
          {field("url", "Linkki (valinnainen)")}{field("deadline", "Hakuaika päättyy", "date")}
          <label className="field wide">Ilmoituksen teksti
            <textarea rows={8} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} placeholder="Liitä ilmoituksen koko teksti" /></label>
        </div>
        <div className="actions">
          <button className="btn primary" disabled={busy || !f.title.trim() || f.text.trim().length < 30}>{busy ? "Lisätään…" : "Lisää ja pisteytä"}</button>
          <button className="btn" type="button" onClick={onClose}>Peruuta</button>
        </div>
        {busy && <ProgressBar compact id="manual-add" estimate={15} label="Pisteytetään ja lisätään hakuun" />}
      </form>
    </dialog>
  );
}

export default function Dashboard() {
  useTitle("Etusivu");
  const toast = useToast();
  const nav = useNavigate();
  const jobs = useJobs();
  const apps = useApps();
  const status = useAiStatus();
  const proj = useProjectSummary();
  const [f, setF] = useState<F>(loadF);
  const [shown, setShown] = useState(PAGE);
  const [sel, setSel] = useState(0);
  const search = useRef<HTMLInputElement>(null);
  const [manual, setManual] = useState(false);
  const setStatus = useSetStatus(toast);
  const jobState = useJobState(toast);
  const mode = status.data?.scoring.mode;

  useEffect(() => { try { localStorage.setItem("filters", JSON.stringify(f)); } catch { /* ei tallennusta */ } }, [f]);
  useEffect(() => { setShown(PAGE); setSel(0); }, [f]);

  const appByUrl = useMemo(() => new Map((apps.data?.apps || []).map((a) => [a.url, a] as [string, App])), [apps.data]);
  const rows = jobs.data?.rows || [];
  const state = jobs.data?.state || {};

  const facets = useMemo(() => {
    const areas = new Map<string, number>(), sources = new Map<string, number>();
    for (const r of rows) {
      if (r.status !== "ok") continue;
      const a = r.area || r.job.location || "Muu";
      areas.set(a, (areas.get(a) || 0) + 1);
      sources.set(r.job.source, (sources.get(r.job.source) || 0) + 1);
    }
    const sort = (m: Map<string, number>) => [...m.entries()].sort((a, b) => b[1] - a[1]);
    return { areas: sort(areas), sources: sort(sources) };
  }, [rows]);

  const list = useMemo(() => {
    const q = f.q.trim().toLowerCase();
    const out = rows.filter((r) => {
      if (r.status !== "ok") return false;
      const st = state[r.job.url] || {};
      if (st.hidden && !f.hidden) return false;
      if (f.saved && !st.saved) return false;
      if (r.score < f.min) return false;
      if (f.areas.length && !f.areas.includes(r.area || r.job.location || "Muu")) return false;
      if (f.sources.length && !f.sources.includes(r.job.source)) return false;
      if (f.days) { const d = daysAgo(posted(r)); if (d === null || d > f.days) return false; }
      const app = appByUrl.get(r.job.url);
      if (f.track === "none" && app) return false;
      if (f.track !== "all" && f.track !== "none" && app?.status !== f.track) return false;
      if (q && !`${r.job.title} ${r.job.company} ${r.job.location}`.toLowerCase().includes(q)) return false;
      return true;
    });
    if (f.order === "new") out.sort((a, b) => posted(b).localeCompare(posted(a)) || b.score - a.score);
    else if (f.order === "deadline") out.sort((a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999"));
    else if (f.order === "combined") out.sort((a, b) => (b.score_combined ?? b.score) - (a.score_combined ?? a.score));
    else if (f.order === "fit") out.sort((a, b) => (b.llm?.fit ?? -1) - (a.llm?.fit ?? -1) || b.score - a.score);
    else out.sort((a, b) => b.score - a.score);
    return out;
  }, [rows, state, f, appByUrl]);

  const visible = list.slice(0, shown);
  const cur = visible[sel];
  const open = (r?: Row) => r && nav(`/ilmoitus?url=${encodeURIComponent(r.job.url)}`);
  const setTrack = (s: Status) => cur && setStatus.mutate({ url: cur.job.url, status: s, apps: apps.data?.apps || [] });

  useEffect(() => {
    document.getElementById(`row-${sel}`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);
  useEffect(() => {   // suodatinvalikot sulkeutuvat klikkaamalla muualle
    const on = (e: MouseEvent) => document.querySelectorAll<HTMLDetailsElement>(".filterbar details[open]")
      .forEach((d) => { if (!d.contains(e.target as Node)) d.open = false; });
    document.addEventListener("click", on);
    return () => document.removeEventListener("click", on);
  }, []);

  useKeys({
    j: () => setSel((i) => Math.min(i + 1, visible.length - 1)),
    k: () => setSel((i) => Math.max(i - 1, 0)),
    ArrowDown: () => setSel((i) => Math.min(i + 1, visible.length - 1)),
    ArrowUp: () => setSel((i) => Math.max(i - 1, 0)),
    Enter: () => open(cur),
    "/": () => search.current?.focus(),
    s: () => cur && jobState.mutate({ url: cur.job.url, saved: !state[cur.job.url]?.saved }),
    x: () => cur && jobState.mutate({ url: cur.job.url, hidden: !state[cur.job.url]?.hidden }),
    o: () => cur && window.open(cur.job.url, "_blank", "noopener"),
    ...Object.fromEntries(STATUS_ORDER.map((s, i) => [String(i + 1), () => setTrack(s)])),
  });

  const toggle = (k: "areas" | "sources", v: string) =>
    setF((x) => ({ ...x, [k]: x[k].includes(v) ? x[k].filter((y) => y !== v) : [...x[k], v] }));
  const changed = JSON.stringify(f) !== JSON.stringify(DEFAULT);

  if (jobs.error) return <ErrorNotice error={jobs.error} title="Ilmoituksia ei saatu ladattua" retry={() => jobs.refetch()} />;

  const strong = list.filter((r) => r.score >= 70).length;
  const fresh = list.filter(isNew).length;

  const hybClass = (v?: number) => (v == null ? "" : v >= 80 ? " strong" : v >= 60 ? " mid" : "");
  const areaLabel = f.areas.length === 0 ? "Kaikki kaupungit" : f.areas.length === 1 ? f.areas[0] : `${f.areas.length} kaupunkia`;

  const pn = proj.data?.new || [];
  return (
    <section aria-labelledby="list-title">
      {pn.length > 0 && (
        <div className="notice ok" role="status">
          <h3>{pn.length === 1 ? "Projektiskanneri löysi uuden ehdotuksen" : `Projektiskanneri löysi ${pn.length} uutta ehdotusta`}</h3>
          <p>{pn.slice(0, 4).map((p) => `${p.title} (${p.label})`).join(", ")}{pn.length > 4 ? ` ja ${pn.length - 4} muuta` : ""}.
            {" "}Ne tulevat CV:hen ja osaamisprofiiliin vasta, kun tarkistat ja hyväksyt ne.</p>
          <p><Link to="/profiili?tab=projektit">Tarkista projektit</Link></p>
        </div>
      )}
      <header className="page-head">
        <h1 id="list-title">Ilmoitukset</h1>
        <div className="search">
          <input ref={search} type="search" placeholder="Hae otsikosta, yrityksestä tai paikasta" value={f.q}
                 onChange={(e) => setF({ ...f, q: e.target.value })} aria-label="Hae ilmoituksista"
                 onKeyDown={(e) => { if (e.key === "Escape" || e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
          {!f.q && <kbd aria-hidden="true">/</kbd>}
        </div>
        <button className="btn" onClick={() => setManual(true)}>Lisää käsin</button>
        <Link className="btn primary" to="/kysy">Kysy ilmoituksista</Link>
      </header>
      <ManualJobDialog open={manual} onClose={() => setManual(false)} />

      <div className="filterbar" role="group" aria-label="Suodattimet">
        <details className={f.areas.length ? "on" : ""}>
          <summary>{areaLabel} ▾</summary>
          <div className="pop">
            {facets.areas.map(([a, n]) => (
              <label className="check" key={a}><input type="checkbox" checked={f.areas.includes(a)} onChange={() => toggle("areas", a)} />
                {a}<span className="n">{n}</span></label>
            ))}
            {f.areas.length > 0 && <button className="btn small ghost" onClick={() => setF({ ...f, areas: [] })}>Kaikki kaupungit</button>}
          </div>
        </details>
        <select className={f.days ? "on" : ""} value={f.days} onChange={(e) => setF({ ...f, days: Number(e.target.value) })} aria-label="Julkaistu">
          <option value={0}>Julkaistu: milloin tahansa</option><option value={1}>Julkaistu: tänään tai eilen</option>
          <option value={3}>Julkaistu: 3 päivän sisällä</option><option value={7}>Julkaistu: viikon sisällä</option><option value={14}>Julkaistu: 2 viikon sisällä</option>
        </select>
        <select className={f.min ? "on" : ""} value={f.min} onChange={(e) => setF({ ...f, min: Number(e.target.value) })} aria-label="Vähimmäispisteet">
          <option value={0}>Pisteet: kaikki</option><option value={50}>Pisteet ≥ 50</option><option value={60}>Pisteet ≥ 60</option>
          <option value={70}>Pisteet ≥ 70</option><option value={80}>Pisteet ≥ 80</option>
        </select>
        <select className={f.track !== "all" ? "on" : ""} value={f.track} onChange={(e) => setF({ ...f, track: e.target.value as F["track"] })} aria-label="Seurannan tila">
          <option value="all">Seuranta: kaikki</option><option value="none">Ei seurannassa</option>
          {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </select>
        {facets.sources.length > 1 && (
          <details className={f.sources.length ? "on" : ""}>
            <summary>Lähde ▾</summary>
            <div className="pop">
              {facets.sources.map(([s, n]) => (
                <label className="check" key={s}><input type="checkbox" checked={f.sources.includes(s)} onChange={() => toggle("sources", s)} />
                  {s}<span className="n">{n}</span></label>
              ))}
            </div>
          </details>
        )}
        <button className={"chip-btn" + (f.saved ? " on" : "")} aria-pressed={f.saved} onClick={() => setF({ ...f, saved: !f.saved })}>Tallennetut</button>
        <button className={"chip-btn" + (f.hidden ? " on" : "")} aria-pressed={f.hidden} onClick={() => setF({ ...f, hidden: !f.hidden })}>Näytä piilotetut</button>
        <select value={f.order} onChange={(e) => setF({ ...f, order: e.target.value as F["order"] })} aria-label="Järjestys">
          <option value="combined">Järjestys: yhdistelmä</option><option value="score">Järjestys: pisteet</option><option value="fit">Järjestys: sopivuus</option><option value="new">Järjestys: uusimmat</option><option value="deadline">Järjestys: hakuaika</option>
        </select>
        {changed && <button className="btn small ghost" onClick={() => setF(DEFAULT)}>Tyhjennä</button>}
        {!jobs.isLoading && (
          <span className="summary"><b>{list.length.toLocaleString("fi-FI")}</b> ilmoitusta · <b>{strong}</b> vahvaa
            {fresh > 0 && <> · <b>{fresh}</b> uutta</>}</span>
        )}
      </div>

      {jobs.isLoading ? <Loading lines={10} label="Ladataan ilmoituksia" /> : list.length === 0 ? (
        <div className="empty">Suodattimilla ei löydy ilmoituksia.<p>Kokeile laskea pisterajaa tai tyhjentää suodattimet.</p></div>
      ) : (
        <>
          <div className="cols" aria-hidden="true">
            <span>Ilmoitus <span className="hide-narrow" style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>· <kbd>j</kbd> <kbd>k</kbd> liikkuu, <kbd>1</kbd>–<kbd>8</kbd> tila, <kbd>?</kbd> ohje</span></span>
            <span title="Avainsanapisteet">Avain</span><span title="Semanttinen vastaavuus">Sem.</span>
            <span title={mode === "hybrid" ? "Yhdistetty piste (järjestys)" : "Yhdistetty piste"}>Hybr.</span>
            <span title="Kielimallin arvio: voisitko realistisesti tulla valituksi">Sopiv.</span>
          </div>
          <ol className="jobs" aria-label="Ilmoitukset pisteiden mukaan">
            {visible.map((r, i) => {
              const app = appByUrl.get(r.job.url);
              const st = state[r.job.url] || {};
              const fresh1 = isNew(r);
              const kw = r.score_keyword ?? r.score;
              return (
                <li key={r.job.url} id={`row-${i}`} className={"job-row" + (i === sel ? " sel" : "") + (st.hidden ? " hidden-job" : "")}
                    onClick={() => setSel(i)} onDoubleClick={() => open(r)} aria-current={i === sel ? "true" : undefined}>
                  <div style={{ minWidth: 0 }}>
                    <Link className="t" to={`/ilmoitus?url=${encodeURIComponent(r.job.url)}`}>{r.job.title}</Link>
                    <div className="meta">
                      <span className="co">{r.job.company || "Yritys ei tiedossa"}</span>
                      <span>{r.job.location}</span>
                      {posted(r) && <span className="mono" style={{ fontSize: 12 }}>{shortDate(posted(r))}</span>}
                      <span className="tagline">
                        {fresh1 && <span className="tag new">Uusi</span>}
                        {app && <span className="tag good">{STATUS_LABEL[app.status]}</span>}
                        {st.saved && !app && <span className="tag">Tallennettu</span>}
                        {jobs.data?.gone[r.job.url] && <span className="tag stamp">Poistunut</span>}
                        {r.llm && r.llm.level !== "junior" && <span className={"tag" + (r.llm.level === "senior" ? " stamp" : " warn")}>{r.llm.level === "senior" ? "Senior-taso" : "Vaatii kokemusta"}</span>}
                        <DeadlineTag iso={r.deadline} />
                      </span>
                      <button type="button" className="row-hide" title={st.hidden ? "Palauta listaan (x)" : "Ei kiinnosta: piilota listalta (x)"}
                              onClick={(e) => { e.stopPropagation(); jobState.mutate({ url: r.job.url, hidden: !st.hidden }); }}
                              onDoubleClick={(e) => e.stopPropagation()}>
                        {st.hidden ? "Palauta" : "Piilota"}</button>
                    </div>
                    {r.llm?.reason && <div className="why-line">{r.llm.reason}</div>}
                  </div>
                  <span className="sc" aria-label={`Avainsana ${Math.round(kw)}`}>{Math.round(kw)}</span>
                  <span className={"sc" + (r.score_semantic == null ? " na" : "")} aria-label="Semanttinen">
                    {r.score_semantic == null ? "–" : Math.round(r.score_semantic)}</span>
                  <span className="sc hy" aria-label="Hybridi">{Math.round(r.score_hybrid ?? r.score)}</span>
                  {r.llm ? <span className={"hyb" + hybClass(r.llm.fit)} aria-label="Sopivuus">{r.llm.fit}</span>
                    : <span className="sc na fit" title="Ei arvioitu: arvio tehdään kärjelle ja uusille ilmoituksille">–</span>}
                </li>
              );
            })}
          </ol>
          {shown < list.length && (
            <div className="pager"><button className="btn" onClick={() => setShown((n) => n + PAGE)}>
              Näytä lisää ({(list.length - shown).toLocaleString("fi-FI")} jäljellä)</button></div>
          )}
        </>
      )}
    </section>
  );
}
