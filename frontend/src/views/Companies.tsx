import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { ErrorNotice, Loading, useToast } from "../components";
import { shortDate, useTitle } from "../lib";

interface Company {
  id: string; name: string; careers_url: string; notes: string; added: string; checked?: string;
  days_since_check: number | null; open_count: number; open_jobs: { title: string; url: string; score: number }[];
}
interface Payload { companies: Company[]; suggested: { name: string; careers_url: string; group: string }[] }

const STALE_DAYS = 7;

export default function Companies() {
  useTitle("Yritykset");
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["companies"], queryFn: () => get<Payload>("/api/companies") });
  const [form, setForm] = useState({ name: "", careers_url: "", notes: "" });
  const [editing, setEditing] = useState<string | null>(null);
  const [notes, setNotes] = useState("");

  const act = async (body: Record<string, unknown>, msg: string) => {
    try {
      const r = await post<Payload>("/api/companies", body);
      qc.setQueryData(["companies"], r);
      qc.invalidateQueries({ queryKey: ["jobs"] });   // seurattu yritys vaikuttaa pisteisiin
      toast(msg);
      return true;
    } catch (e) { toast((e as Error).message); return false; }
  };

  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNotice error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const sorted = [...d.companies].sort((a, b) => (b.days_since_check ?? 999) - (a.days_since_check ?? 999));
  const due = d.companies.filter((c) => c.days_since_check === null || c.days_since_check > STALE_DAYS).length;

  return (
    <section aria-labelledby="co-title">
      <div className="section-head">
        <h1 id="co-title">Seurattavat yritykset</h1>
        <span className="muted" style={{ fontSize: 13 }}>{d.companies.length} yritystä · {due} tarkistamatta yli {STALE_DAYS} päivään</span>
      </div>
      <div className="pkg">
        <div>
          {sorted.length === 0 && <div className="empty">Ei seurattavia yrityksiä.<p>Lisää yritys oikealta tai ota käyttöön valmis lista.</p></div>}
          {sorted.map((c) => {
            const stale = c.days_since_check === null || c.days_since_check > STALE_DAYS;
            return (
              <article key={c.id} className="pkg-section">
                <header>
                  <h2>{c.name}</h2>
                  <div className="actions" style={{ marginTop: 0 }}>
                    <span className={"tag" + (stale ? " stamp" : " good")}>
                      {c.checked ? `Tarkistettu ${shortDate(c.checked)} (${c.days_since_check} pv)` : "Ei tarkistettu"}</span>
                    {c.careers_url && <a className="btn small" href={c.careers_url} target="_blank" rel="noreferrer">Rekrysivu ↗</a>}
                    <button className="btn small" onClick={() => act({ action: "check", id: c.id }, `${c.name}: tarkistettu tänään`)}>Merkitse tarkistetuksi</button>
                    <button className="btn small ghost" onClick={() => { setEditing(editing === c.id ? null : c.id); setNotes(c.notes || ""); }}>Muistiinpanot</button>
                    <button className="btn small ghost" onClick={() => confirm(`Poistetaanko ${c.name} seurannasta?`) && act({ action: "delete", id: c.id }, "Poistettu")}>Poista</button>
                  </div>
                </header>
                {editing === c.id ? (
                  <form onSubmit={async (e) => { e.preventDefault(); if (await act({ action: "update", id: c.id, fields: { notes } }, "Tallennettu")) setEditing(null); }}>
                    <label className="field">Muistiinpanot<textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
                    <div className="actions"><button className="btn small primary">Tallenna</button></div>
                  </form>
                ) : c.notes ? <p style={{ margin: "0 0 8px", fontSize: 13.5, color: "var(--ink-2)", whiteSpace: "pre-wrap" }}>{c.notes}</p> : null}
                {c.open_count > 0 ? (
                  <>
                    <div className="kicker" style={{ marginBottom: 4 }}>Avoimia ilmoituksia haussa: {c.open_count}</div>
                    <ul className="jobs">
                      {c.open_jobs.map((j) => (
                        <li key={j.url} className="job-row" style={{ gridTemplateColumns: "minmax(0,1fr) 64px", padding: "6px 4px" }}>
                          <Link className="t" to={`/ilmoitus?url=${encodeURIComponent(j.url)}`} style={{ fontSize: 13.5 }}>{j.title}</Link>
                          <span className={"hyb" + (j.score >= 80 ? " strong" : j.score >= 60 ? " mid" : "")}>{j.score}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : <p className="muted" style={{ margin: 0, fontSize: 13 }}>Ei avoimia ilmoituksia viimeisimmässä haussa. Tarkista rekrysivu.</p>}
              </article>
            );
          })}
        </div>
        <aside className="margin" aria-label="Lisää yritys">
          <section>
            <h2>Lisää yritys</h2>
            <form style={{ display: "grid", gap: 10 }} onSubmit={async (e) => {
              e.preventDefault();
              if (await act({ action: "add", fields: form }, `${form.name} lisätty`)) setForm({ name: "", careers_url: "", notes: "" });
            }}>
              <label className="field">Nimi<input type="text" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
              <label className="field">Rekrysivun osoite<input type="text" placeholder="https://" value={form.careers_url} onChange={(e) => setForm({ ...form, careers_url: e.target.value })} /></label>
              <label className="field">Muistiinpanot<textarea rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
              <button className="btn primary" disabled={!form.name.trim()}>Lisää</button>
            </form>
            <p className="muted" style={{ fontSize: 12.5, marginBottom: 0 }}>Seurattujen yritysten ilmoitukset saavat pisteissä pienen lisän.</p>
          </section>
          {d.suggested.length > 0 && (
            <section>
              <h2>Valmis lista ({d.suggested.length})</h2>
              <ul className="points" style={{ gap: 4 }}>
                {d.suggested.slice(0, 12).map((s) => (
                  <li key={s.name} style={{ paddingLeft: 0, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                    <span>{s.name} <span className="muted" style={{ fontSize: 12 }}>· {s.group}</span></span>
                    <button className="btn small" onClick={() => act({ action: "add", fields: { name: s.name, careers_url: s.careers_url } }, `${s.name} lisätty`)}>Lisää</button>
                  </li>
                ))}
              </ul>
              <div className="actions"><button className="btn small" onClick={() => act({ action: "add_catalog" }, "Valmis lista lisätty")}>Lisää kaikki</button></div>
            </section>
          )}
        </aside>
      </div>
    </section>
  );
}
