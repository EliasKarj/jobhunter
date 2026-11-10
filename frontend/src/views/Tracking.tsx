import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { useUpdateApp } from "../actions";
import { ErrorNotice, Loading, useToast } from "../components";
import { STATUS_LABEL, STATUS_ORDER, daysAgo, shortDate, useApps, useKeys, useTitle, useTracking } from "../lib";
import type { App, Status } from "../types";

function allowedFrom(a: App, transitions: Record<Status, Status[]>): Status[] {
  const prev = a.history.length >= 2 ? a.history[a.history.length - 2].status : null;   // kumoa
  const out = new Set(transitions[a.status] || []);
  if (prev && prev !== a.status) out.add(prev);
  return [...out];
}

function AppDialog({ app, onClose, allowed }: { app: App | null; onClose: () => void; allowed: Status[] }) {
  const ref = useRef<HTMLDialogElement>(null);
  const toast = useToast();
  const qc = useQueryClient();
  const update = useUpdateApp(toast);
  const [f, setF] = useState<Partial<App>>({});
  const isNew = app?.id === "";
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (app) { setF(app); if (!d.open) d.showModal(); } else if (d.open) d.close();
  }, [app]);
  if (!app) return <dialog ref={ref} />;
  const field = (k: keyof App, label: string, type = "text", wide = false) => (
    <label className={"field" + (wide ? " wide" : "")}>{label}
      <input type={type} value={(f[k] as string) || ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>
  );
  const submit = async () => {
    const fields: Record<string, string> = {};
    for (const k of ["title", "company", "location", "url", "deadline", "applied_date", "next_date", "next_step", "notes", "contact_name", "contact_email", "salary"] as const)
      if (f[k] !== undefined && f[k] !== (app as App)[k]) fields[k] = f[k] as string;
    if (f.status && f.status !== app.status) fields.status = f.status;
    try {
      if (isNew) await post("/api/apps", { action: "create", fields: { ...fields, status: f.status || "interested" } });
      else if (Object.keys(fields).length) await update.mutateAsync({ id: app.id, fields: fields as Partial<App> });
      qc.invalidateQueries({ queryKey: ["apps"] }); qc.invalidateQueries({ queryKey: ["tracking"] });
      toast("Tallennettu");
      onClose();
    } catch (e) { toast((e as Error).message); }
  };
  const remove = async () => {
    if (!confirm(`Poistetaanko "${app.title}" seurannasta?`)) return;
    await post("/api/apps", { action: "delete", id: app.id });
    qc.invalidateQueries({ queryKey: ["apps"] });
    onClose();
  };
  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="app-title">
      <form method="dialog" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <h2 id="app-title">{isNew ? "Lisää hakemus käsin" : app.title}</h2>
        <div className="form-grid">
          {isNew && <>{field("title", "Tehtävä", "text", true)}{field("company", "Yritys")}{field("location", "Paikka")}{field("url", "Linkki ilmoitukseen", "text", true)}</>}
          <label className="field">Tila
            <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as Status })}>
              {STATUS_ORDER.map((s) => <option key={s} value={s} disabled={!isNew && s !== app.status && !allowed.includes(s)}>{STATUS_LABEL[s]}</option>)}
            </select></label>
          {field("deadline", "Hakuaika päättyy", "date")}
          {field("applied_date", "Haettu", "date")}
          {field("next_date", "Seuraava askel (pvm)", "date")}
          {field("next_step", "Seuraava askel", "text", true)}
          {field("contact_name", "Yhteyshenkilö")}
          {field("contact_email", "Sähköposti", "email")}
          <label className="field wide">Muistiinpanot
            <textarea rows={5} value={f.notes || ""} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
        </div>
        {!isNew && app.history.length > 0 && (
          <p className="muted mono" style={{ fontSize: 12.5 }}>
            {app.history.map((h) => `${STATUS_LABEL[h.status] || h.status} ${shortDate(h.at)}`).join(" → ")}</p>
        )}
        <div className="actions">
          <button className="btn primary" type="submit">Tallenna</button>
          <button className="btn" type="button" onClick={onClose}>Peruuta</button>
          {!isNew && app.url && <Link className="btn ghost" to={`/ilmoitus?url=${encodeURIComponent(app.url)}`} onClick={onClose}>Ilmoitus</Link>}
          {!isNew && app.url && <Link className="btn ghost" to={`/hakemus?url=${encodeURIComponent(app.url)}`} onClick={onClose}>Hakemuspaketti</Link>}
          {!isNew && <button className="btn ghost" type="button" onClick={remove} style={{ marginLeft: "auto", color: "var(--stamp)" }}>Poista</button>}
        </div>
      </form>
    </dialog>
  );
}

interface Outcome { kind: string; label: string; applied: number; responded: number; interview: number; interview_rate: number; coverage: number | null }

/** Lähetetyt hakemukset CV-tavan mukaan: auttaa näkemään, toimiiko paneelin CV oikeasti paremmin. */
function Outcomes() {
  const q = useQuery({ queryKey: ["stats"], queryFn: () => get<{ outcomes: Outcome[]; applied: number }>("/api/stats"), staleTime: 60_000 });
  const rows = q.data?.outcomes || [];
  if (!rows.length) return null;
  const total = rows.reduce((n, r) => n + r.applied, 0);
  return (
    <section className="pkg-section" aria-labelledby="outcomes-title" style={{ marginTop: 18 }}>
      <h2 id="outcomes-title">Mikä toimii</h2>
      <div style={{ overflowX: "auto" }}>
        <table className="data">
          <thead><tr><th>CV</th><th className="n">Haettu</th><th className="n">Vastaus</th><th className="n">Haastattelu</th><th className="n">Avainsanakattavuus</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.kind}><td>{r.label}</td><td className="n">{r.applied}</td><td className="n">{r.responded}</td>
              <td className="n">{r.interview} <span className="muted">({r.interview_rate} %)</span></td>
              <td className="n">{r.coverage === null ? "–" : `${Math.round(r.coverage * 100)} %`}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 13.5, margin: "6px 0 0" }}>Vastaus = haastattelu, tarjous tai hylkäys. CV-tapa luetaan ilmoitukselle tallennetusta CV:stä.
        {total < 20 && ` Hakemuksia on vasta ${total}, joten erot ovat vielä sattumaa; vertailusta tulee luotettavampi muutaman kymmenen hakemuksen jälkeen.`}</p>
    </section>
  );
}

const EMPTY: App = { id: "", status: "interested", title: "", company: "", location: "", url: "", source: "", notes: "", deadline: "",
  applied_date: "", next_date: "", next_step: "", contact_name: "", contact_email: "", salary: "", created: "", updated: "", score: null, history: [] };

export default function Tracking() {
  useTitle("Seuranta");
  const toast = useToast();
  const apps = useApps();
  const info = useTracking();
  const update = useUpdateApp(toast);
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<"board" | "list">(() => (localStorage.getItem("track-view") as "list") || "board");
  const [drag, setDrag] = useState<App | null>(null);
  const [sel, setSel] = useState(0);
  const [editing, setEditing] = useState<App | null>(null);
  useEffect(() => { try { localStorage.setItem("track-view", view); } catch { /* */ } }, [view]);

  const list = useMemo(() => [...(apps.data?.apps || [])].sort(
    (a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) || b.updated.localeCompare(a.updated)), [apps.data]);
  useEffect(() => {
    const id = params.get("id");
    if (id && apps.data) { const a = apps.data.apps.find((x) => x.id === id); if (a) setEditing(a); }
  }, [params, apps.data]);
  const transitions = info.data?.transitions || ({} as Record<Status, Status[]>);
  const move = (a: App, s: Status) => {
    if (s === a.status) return;
    if (!allowedFrom(a, transitions).includes(s)) { toast(`Tilasta ${STATUS_LABEL[a.status]} ei voi siirtyä tilaan ${STATUS_LABEL[s]}`); return; }
    update.mutate({ id: a.id, fields: { status: s } }, { onSuccess: () => toast(`${a.title}: ${STATUS_LABEL[s]}`) });
  };
  const cur = list[sel];
  useKeys({
    j: () => setSel((i) => Math.min(i + 1, list.length - 1)), k: () => setSel((i) => Math.max(i - 1, 0)),
    Enter: () => cur && setEditing(cur), n: () => setEditing({ ...EMPTY }),
    ...Object.fromEntries(STATUS_ORDER.map((s, i) => [String(i + 1), () => cur && move(cur, s)])),
  }, !editing);
  useEffect(() => { document.getElementById(`app-${cur?.id}`)?.focus({ preventScroll: false }); }, [sel]);  // eslint-disable-line

  if (apps.error) return <ErrorNotice error={apps.error} retry={() => apps.refetch()} />;
  if (apps.isLoading || info.isLoading) return <Loading lines={8} />;
  const rem = info.data?.reminders || [];
  const card = (a: App) => {
    const i = list.indexOf(a);
    const r = rem.find((x) => x.id === a.id);
    return (
      <li key={a.id}>
        <div id={`app-${a.id}`} className={"card" + (i === sel ? " sel" : "")} tabIndex={0} draggable role="button"
             aria-label={`${a.title}, ${a.company}, ${STATUS_LABEL[a.status]}. Enter avaa, numerot vaihtavat tilan.`}
             onDragStart={(e) => { setDrag(a); e.dataTransfer.setData("text/plain", a.id); }} onDragEnd={() => setDrag(null)}
             onFocus={() => setSel(i)} onClick={() => setEditing(a)}>
          <div className="t">{a.title}</div>
          <div className="m">{a.company}{a.location ? ` · ${a.location}` : ""}</div>
          <div className="m mono">{a.applied_date ? `haettu ${shortDate(a.applied_date)}` : `lisätty ${shortDate(a.created)}`}
            {a.deadline && a.status in { interested: 1, drafting: 1 } ? ` · haku ${shortDate(a.deadline)}` : ""}</div>
          {r && <div className="due">{r.kind === "follow_up" ? `${r.days} pv ilman vastausta` : r.text}</div>}
        </div>
      </li>
    );
  };

  return (
    <section aria-labelledby="track-title">
      <div className="section-head">
        <h1 id="track-title" style={{ fontSize: 28 }}>Hakemusten seuranta</h1>
        <div className="actions" style={{ marginTop: 0 }}>
          <div role="group" aria-label="Näkymä" style={{ display: "flex" }}>
            <button className="btn small" aria-pressed={view === "board"} onClick={() => setView("board")}>Taulu</button>
            <button className="btn small" aria-pressed={view === "list"} onClick={() => setView("list")}>Lista</button>
          </div>
          <button className="btn small" onClick={() => setEditing({ ...EMPTY })}>Lisää käsin <kbd>n</kbd></button>
          <a className="btn small ghost" href="/api/export/kalenteri.ics">Kalenteriin (.ics)</a>
          <a className="btn small ghost" href="/api/export/hakemukset.csv">CSV</a>
        </div>
      </div>
      {rem.length > 0 && (
        <ul className="reminders" aria-label="Muistutukset">
          {rem.map((r, i) => (
            <li key={i}><span className="d">{r.kind === "follow_up" ? `haettu ${shortDate(r.date)}` : shortDate(r.date)}</span>
              <b>{r.title}</b><span className="muted">{r.company}</span><span>{r.text}</span>
              <button className="btn small ghost" onClick={() => setEditing(list.find((a) => a.id === r.id) || null)}>Avaa</button></li>
          ))}
        </ul>
      )}
      {list.length === 0 ? (
        <div className="empty">Seurannassa ei ole vielä hakemuksia.
          <p>Valitse etusivulta ilmoitus ja paina numeronäppäintä 1 (Kiinnostava) tai luo hakemuspaketti. Voit myös lisätä hakemuksen käsin.</p></div>
      ) : view === "board" ? (
        <div className="board">
          {STATUS_ORDER.map((s) => {
            const items = list.filter((a) => a.status === s);
            const ok = drag ? drag.status !== s && allowedFrom(drag, transitions).includes(s) : false;
            return (
              <section key={s} className={"col" + (drag ? (ok ? " drop-ok" : drag.status === s ? "" : " drop-no") : "")} aria-label={STATUS_LABEL[s]}
                       onDragOver={(e) => { if (ok) e.preventDefault(); }}
                       onDrop={(e) => { e.preventDefault(); if (drag) move(drag, s); setDrag(null); }}>
                <h3><span>{STATUS_ORDER.indexOf(s) + 1} · {STATUS_LABEL[s]}</span><span className="mono">{items.length}</span></h3>
                <ul>{items.map(card)}</ul>
              </section>
            );
          })}
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data">
            <thead><tr><th>Tehtävä</th><th>Tila</th><th>Haettu</th><th>Seuraava askel</th><th className="n">Päivitetty</th></tr></thead>
            <tbody>
              {list.map((a, i) => (
                <tr key={a.id} className={i === sel ? "best" : ""}>
                  <td><button className="btn ghost small" style={{ padding: 0, fontWeight: 600, fontSize: 14 }} onClick={() => setEditing(a)}>{a.title}</button>
                    <div className="muted" style={{ fontSize: 13.5 }}>{a.company}</div></td>
                  <td><select value={a.status} aria-label={`Tila: ${a.title}`} onChange={(e) => move(a, e.target.value as Status)} style={{ width: "auto", minHeight: 32 }}>
                    {STATUS_ORDER.map((s) => <option key={s} value={s} disabled={s !== a.status && !allowedFrom(a, transitions).includes(s)}>{STATUS_LABEL[s]}</option>)}
                  </select></td>
                  <td className="mono">{shortDate(a.applied_date)}{a.status === "applied" && a.applied_date ? <span className="muted"> ({daysAgo(a.applied_date)} pv)</span> : null}</td>
                  <td>{a.next_step}{a.next_date && <span className="mono muted"> {shortDate(a.next_date)}</span>}</td>
                  <td className="n">{shortDate(a.updated)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted" style={{ fontSize: 14, marginTop: 14 }}>
        Raahaa kortti sarakkeeseen tai valitse kortti ja paina numeroa 1–8. Vain sallitut siirtymät hyväksytään, ja edelliseen tilaan voi aina palata.
        Haetusta tulee muistutus, jos vastausta ei ole kuulunut {info.data?.follow_up_days} päivään.</p>
      <Outcomes />
      <AppDialog app={editing} allowed={editing ? allowedFrom(editing, transitions) : []}
                 onClose={() => { setEditing(null); if (params.get("id")) setParams({}); }} />
    </section>
  );
}
