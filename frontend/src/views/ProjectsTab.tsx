// Profiili → Projektit: skannerin ehdotukset (rag/projects.py), hyväksyntä CV:hen, lisätiedot ja cv_fi.md:n synkronointi.
import { createContext, useContext, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { ErrorNotice, Loading, useToast } from "../components";
import { ProgressBar } from "../progress";

interface Bullet { text: string; flags: string[] }
interface Proposal {
  id: string; kind: "new_project" | "new_tech" | "activity"; repo: string; path: string; stack: string[]; period: string;
  own_commits: number; commits: number; others: Record<string, number>; status: "open" | "dismissed" | "accepted";
  draft?: { title: string; bullets: Bullet[]; error?: string; model?: string } | null; default_meta?: string;
  cv_entry?: string; missing?: string[]; recent?: string[]; n_recent?: number; since?: string; found?: string;
}
interface Payload {
  generated?: string; proposals: Proposal[]; other_authors?: Record<string, number>; skipped?: { repo: string; why: string }[];
  scan: { running: boolean; error: string | null; done?: number; total?: number; repo?: string }; own_emails: string[]; notes: Record<string, string>; cv_projects: string[];
}
interface Sync { exists: boolean; in_sync: boolean; missing: { id: string; en: string }[]; restructure?: number }
interface Tr { orig: string; text: string; reasons: string[] }

/** Edellinen katseluhetki: sen jälkeen löydetyt ehdotukset merkitään uusiksi. null = ei vielä tiedossa. */
const SeenSince = createContext<string | null>(null);
function NewTag({ p }: { p: Proposal }) {
  const since = useContext(SeenSince);
  if (since === null || !p.found || p.found <= since) return null;
  return <span className="tag" style={{ marginLeft: 8, verticalAlign: "middle" }}>Uusi</span>;
}

function SkillOffer({ names, onDone }: { names: string[]; onDone: () => void }) {
  const toast = useToast();
  const [pick, setPick] = useState<string[]>(names);
  const add = async () => {
    try {
      const r = await post<{ added: string[] }>("/api/projects/skills", { names: pick });
      toast(r.added.length ? `Osaamiseen lisätty: ${r.added.join(", ")}` : "Ei uusia taitoja");
      onDone();
    } catch (e) { toast((e as Error).message); }
  };
  return (
    <div className="notice ok" role="status">
      <p style={{ margin: "0 0 6px" }}>Näitä tekniikoita ei ole vielä osaamisprofiilissa. Lisätäänkö ne (paino 1, voit tarkentaa Osaaminen-välilehdellä)?</p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 8 }}>
        {names.map((n) => (
          <label key={n} style={{ fontSize: 13.5 }}><input type="checkbox" checked={pick.includes(n)}
            onChange={() => setPick(pick.includes(n) ? pick.filter((x) => x !== n) : [...pick, n])} /> {n}</label>
        ))}
      </div>
      <div className="actions" style={{ marginTop: 0 }}>
        <button className="btn small primary" onClick={add} disabled={!pick.length}>Lisää osaamiseen</button>
        <button className="btn small ghost" onClick={onDone}>Ei nyt</button>
      </div>
    </div>
  );
}

/** cv.md muuttui: käännä vain uudet ja muuttuneet rivit ja tallenna suomenkieliseen pohjaan. */
function SyncFi() {
  const toast = useToast();
  const qc = useQueryClient();
  const sync = useQuery({ queryKey: ["cv-sync"], queryFn: () => get<Sync>("/api/cv/sync") });
  const [tr, setTr] = useState<Record<string, Tr> | null>(null);
  const [lines, setLines] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  if (!sync.data?.exists || sync.data.in_sync) return null;
  const n = sync.data.missing.length;
  const translate = async () => {
    setBusy(true);
    try {
      const r = await post<{ translation: Record<string, Tr> }>("/api/cv/sync/translate", {});
      setTr(r.translation);
      setLines(Object.fromEntries(Object.entries(r.translation).map(([k, v]) => [k, v.text])));
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true);
    try {
      await post("/api/cv/sync/save", { lines });
      toast("Suomenkielinen pohja päivitetty");
      setTr(null);
      qc.invalidateQueries({ queryKey: ["cv-sync"] });
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <section className="pkg-section" aria-labelledby="sync-title">
      <h2 id="sync-title">Suomenkielinen pohja on jäljessä</h2>
      {!tr ? (
        <>
          <p style={{ fontSize: 14 }}>{n > 0 ? <><b>{n} riviä</b> cv.md:ssä on uusia tai muuttuneita. Muut rivit tulevat aiemmista suomennoksista,
            joten käännetään vain nämä.</> : "Rivien järjestys on muuttunut. Tallenna, niin pohja kootaan uudelleen aiemmista suomennoksista."}</p>
          {n > 0 && <ul className="points" style={{ gap: 2, marginBottom: 10 }}>{sync.data.missing.slice(0, 8).map((m) => (
            <li key={m.id} style={{ paddingLeft: 0, fontSize: 13 }}><span className="mono muted">{m.id}</span> {m.en}</li>))}</ul>}
          <div className="actions" style={{ marginTop: 0 }}>
            {n > 0 ? <button className="btn primary" onClick={translate} disabled={busy}>{busy ? "Käännetään…" : "Käännä uudet rivit"}</button>
              : <button className="btn primary" onClick={save} disabled={busy}>Kokoa pohja uudelleen</button>}
          </div>
          {busy && <ProgressBar id="cv-sync-translate" llm={[8 + n * 1.2, 20 + n * 4]} label={`Käännetään ${n} riviä ja tarkistetaan käännökset`} />}
        </>
      ) : (
        <>
          <p style={{ fontSize: 14 }}>Tarkista käännökset ja tallenna. Epäilyttävät rivit on merkitty.</p>
          <ul className="claims">
            {Object.entries(tr).sort((a, b) => b[1].reasons.length - a[1].reasons.length).map(([k, v]) => (
              <li key={k} className={v.reasons.length ? "st-flag" : ""}>
                <div className="src" style={{ marginBottom: 3 }}>{v.orig}</div>
                <textarea rows={Math.min(4, Math.ceil(v.orig.length / 70) + 1)} value={lines[k] || ""} aria-label={`Suomennos: ${v.orig.slice(0, 40)}`}
                          onChange={(e) => setLines({ ...lines, [k]: e.target.value })} />
                {v.reasons.length > 0 && <div className="why">{v.reasons.join(" · ")}</div>}
              </li>
            ))}
          </ul>
          <div className="actions"><button className="btn primary" onClick={save} disabled={busy}>Tallenna pohjaan</button>
            <button className="btn ghost" onClick={() => setTr(null)}>Peruuta</button></div>
        </>
      )}
    </section>
  );
}

function NewProject({ p, onAccepted, onDismiss }: { p: Proposal; onAccepted: (skills: string[]) => void; onDismiss: () => void }) {
  const toast = useToast();
  const d = p.draft;
  const [title, setTitle] = useState(d?.title || p.repo);
  const [meta, setMeta] = useState(p.default_meta || "");
  const [bullets, setBullets] = useState<string[]>(d?.bullets.map((b) => b.text) || [""]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const flagged = d?.bullets.filter((b, i) => b.flags.length && bullets[i] === b.text) || [];
  const accept = async () => {
    if (flagged.length && !confirm(`${flagged.length} riviä on merkitty: niissä on jotain, mitä README:stä tai commit-viesteistä ei löydy. Lisätäänkö silti?`)) return;
    setBusy(true);
    try {
      const r = await post<{ new_skills: string[] }>("/api/projects/accept", { id: p.id, title, meta, bullets: bullets.filter((b) => b.trim()), note });
      toast("Projekti lisätty CV:hen");
      onAccepted(r.new_skills);
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <details className="proposal">
      <summary>
        <b>{d?.title || p.repo}</b><NewTag p={p} />
        <span className="muted" style={{ fontSize: 12.5 }}>{p.stack.slice(0, 5).join(", ")} · {p.period} · {p.own_commits} omaa committia</span>
        {d?.bullets.some((b) => b.flags.length) && <span className="tag warn">tarkista</span>}
      </summary>
      <label className="field"><span>Nimi ja kuvaus</span><input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <label className="field"><span>Tekniikat ja ajankohta</span><input value={meta} onChange={(e) => setMeta(e.target.value)} /></label>
      <div className="field"><span>Rivit (englanniksi, kuten cv.md)</span>
        {bullets.map((b, i) => {
          const orig = d?.bullets[i];
          return (
            <div key={i} style={{ display: "grid", gap: 2, marginBottom: 6 }}>
              <textarea rows={2} value={b} aria-label={`Rivi ${i + 1}`} onChange={(e) => setBullets(bullets.map((x, j) => (j === i ? e.target.value : x)))} />
              {orig && orig.flags.length > 0 && b === orig.text && <span className="why" style={{ color: "var(--danger)", fontSize: 12.5 }}>⚠ {orig.flags.join("; ")}</span>}
            </div>
          );
        })}
        <div><button className="btn small ghost" onClick={() => setBullets([...bullets, ""])}>+ Rivi</button></div>
      </div>
      <label className="field"><span>Oma osuus / lisätieto (ei näy CV:ssä, paneeli käyttää taustatietona)</span>
        <textarea rows={2} value={note} placeholder="esim. tehty tekoälyn kanssa opiskeluprojektina; tein backendin itse" onChange={(e) => setNote(e.target.value)} /></label>
      <p className="muted mono" style={{ fontSize: 12, margin: "4px 0 8px" }}>
        {p.repo} · {p.own_commits}/{p.commits} omaa committia{Object.keys(p.others).length > 0 && ` · muut: ${Object.entries(p.others).map(([e, n]) => `${e} (${n})`).join(", ")}`}
        {d?.model && ` · luonnos: ${d.model}`}</p>
      {d?.error && <p className="stale">Luonnos epäonnistui: {d.error}</p>}
      <div className="actions" style={{ marginTop: 0 }}>
        <button className="btn primary small" onClick={accept} disabled={busy || !title.trim() || !bullets.some((b) => b.trim())}>Lisää CV:hen</button>
        <button className="btn ghost small" onClick={onDismiss}>Piilota</button>
      </div>
    </details>
  );
}

function NewTech({ p, onAccepted, onDismiss }: { p: Proposal; onAccepted: (skills: string[]) => void; onDismiss: () => void }) {
  const toast = useToast();
  const [pick, setPick] = useState<string[]>(p.missing || []);
  const accept = async () => {
    try {
      const r = await post<{ new_skills: string[] }>("/api/projects/accept", { id: p.id, techs: pick });
      toast("Tekniikat lisätty CV:hen");
      onAccepted(r.new_skills);
    } catch (e) { toast((e as Error).message); }
  };
  return (
    <article className="proposal">
      <h3>{p.cv_entry}<NewTag p={p} /></h3>
      <p style={{ fontSize: 13.5, margin: "0 0 6px" }}>Repossa on tekniikoita, joita CV:n kohdassa ei mainita. Valitut lisätään kohdan tekniikkariville:</p>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
        {(p.missing || []).map((t) => (
          <label key={t} style={{ fontSize: 13.5 }}><input type="checkbox" checked={pick.includes(t)}
            onChange={() => setPick(pick.includes(t) ? pick.filter((x) => x !== t) : [...pick, t])} /> {t}</label>
        ))}
      </div>
      <div className="actions" style={{ marginTop: 0 }}>
        <button className="btn primary small" onClick={accept} disabled={!pick.length}>Lisää CV:hen</button>
        <button className="btn ghost small" onClick={onDismiss}>Piilota</button>
      </div>
    </article>
  );
}

function Activity({ p, onDismiss }: { p: Proposal; onDismiss: () => void }) {
  return (
    <article className="proposal">
      <h3>{p.cv_entry}<NewTag p={p} /></h3>
      <p style={{ fontSize: 13.5, margin: "0 0 4px" }}>{p.n_recent} omaa committia CV:n päivityksen ({p.since}) jälkeen. Päivitä kohtaa cv.md:ssä, jos tuli jotain olennaista:</p>
      <ul className="points" style={{ gap: 2, marginBottom: 8 }}>{(p.recent || []).map((r, i) => <li key={i} style={{ paddingLeft: 0, fontSize: 13 }}>{r}</li>)}</ul>
      <button className="btn small" onClick={onDismiss}>Käsitelty</button>
    </article>
  );
}

function Notes({ entries, notes }: { entries: string[]; notes: Record<string, string> }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [draft, setDraft] = useState(notes);
  useEffect(() => setDraft(notes), [notes]);
  const save = async (title: string) => {
    try { await post("/api/projects/notes", { title, note: draft[title] || "" }); toast("Tallennettu"); qc.invalidateQueries({ queryKey: ["projects"] }); }
    catch (e) { toast((e as Error).message); }
  };
  return (
    <section className="pkg-section" aria-labelledby="notes-title">
      <h2 id="notes-title">Oma osuus projekteissa</h2>
      <p style={{ fontSize: 14 }}>Ei näy CV:ssä. Arviointipaneeli käyttää näitä taustatietona: se ei esimerkiksi anna kuvata tekoälyn kanssa opiskeltua projektia itse suunnitelluksi.</p>
      <div style={{ display: "grid", gap: 10 }}>
        {entries.map((t) => (
          <label key={t} className="field"><span>{t}</span>
            <div style={{ display: "flex", gap: 8, alignItems: "start" }}>
              <textarea rows={1} style={{ flex: 1 }} value={draft[t] || ""} onChange={(e) => setDraft({ ...draft, [t]: e.target.value })} />
              <button className="btn small" onClick={() => save(t)} disabled={(draft[t] || "") === (notes[t] || "")}>Tallenna</button>
            </div>
          </label>
        ))}
      </div>
    </section>
  );
}

export default function ProjectsTab() {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["projects"], queryFn: () => get<Payload>("/api/projects"),
    refetchInterval: (query) => (query.state.data?.scan.running ? 1500 : false) });
  const [skills, setSkills] = useState<string[]>([]);
  const [since, setSince] = useState<string | null>(null);
  useEffect(() => {   // välilehti avattu: ehdotukset on nähty, ilmoitus sivupalkista ja etusivulta pois
    post<{ previous: string }>("/api/projects/seen", {})
      .then((r) => { setSince(r.previous); qc.invalidateQueries({ queryKey: ["project-summary"] }); })
      .catch(() => setSince(null));
  }, [qc]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["projects"] }); qc.invalidateQueries({ queryKey: ["cv-sync"] }); };
  const scan = async () => { try { await post("/api/projects/scan", {}); refresh(); } catch (e) { toast((e as Error).message); } };
  const dismiss = async (id: string) => { try { await post("/api/projects/dismiss", { id }); refresh(); } catch (e) { toast((e as Error).message); } };
  const addEmail = async (email: string) => {
    try { await post("/api/projects/email", { email }); toast("Lisätty omiin osoitteisiin. Skannataan uudelleen…"); await scan(); }
    catch (e) { toast((e as Error).message); }
  };
  const accepted = (names: string[]) => { refresh(); if (names.length) setSkills(names); };
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNotice error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const open = d.proposals.filter((p) => p.status === "open");
  const group = (k: Proposal["kind"]) => open.filter((p) => p.kind === k);
  return (
    <SeenSince.Provider value={since}>
    <div style={{ display: "grid", gap: 16 }}>
      <section className="pkg-section">
        <div className="section-head" style={{ marginBottom: 6 }}>
          <h2>Projektiehdotukset</h2>
          <button className="btn small" onClick={scan} disabled={d.scan.running}>{d.scan.running ? "Skannataan…" : "Skannaa nyt"}</button>
        </div>
        {d.scan.running && <ProgressBar id="projects-scan" estimate={60}
          label={d.scan.total ? `Skannataan repoja ${(d.scan.done || 0) + 1}/${d.scan.total}` : "Etsitään repoja"}
          value={d.scan.total ? (d.scan.done || 0) / d.scan.total : null}
          detail={d.scan.repo ? `${d.scan.repo} (uusille projekteille kirjoitetaan luonnos, mikä vie hetken)` : undefined} />}
        <p style={{ fontSize: 14, margin: 0 }}>Ehdotukset tulevat omista git-repoistasi (skannataan viikoittain haun jälkeen). Mikään ei mene CV:hen ilman hyväksyntääsi,
          ja cv.md:stä otetaan varmuuskopio ennen jokaista muutosta.
          {d.generated && <span className="muted"> Viimeksi {d.generated.slice(0, 16).replace("T", " ")}.</span>}</p>
        {d.scan.error && <p className="stale">Skannaus epäonnistui: {d.scan.error}</p>}
      </section>
      {skills.length > 0 && <SkillOffer names={skills} onDone={() => setSkills([])} />}
      <SyncFi />
      {!open.length && <div className="empty">Ei avoimia ehdotuksia.</div>}
      {group("new_project").length > 0 && (
        <section className="pkg-section" aria-labelledby="np-title">
          <h2 id="np-title">Uudet projektit ({group("new_project").length})</h2>
          <p className="muted" style={{ fontSize: 13.5, marginTop: 0 }}>Kielimallin luonnos README:n ja omien commit-viestien pohjalta. Muokkaa ennen lisäämistä.</p>
          <div style={{ display: "grid", gap: 12 }}>{group("new_project").map((p) => <NewProject key={p.id} p={p} onAccepted={accepted} onDismiss={() => dismiss(p.id)} />)}</div>
        </section>
      )}
      {group("new_tech").length > 0 && (
        <section className="pkg-section" aria-labelledby="nt-title">
          <h2 id="nt-title">Uusia tekniikoita ({group("new_tech").length})</h2>
          <div style={{ display: "grid", gap: 12 }}>{group("new_tech").map((p) => <NewTech key={p.id} p={p} onAccepted={accepted} onDismiss={() => dismiss(p.id)} />)}</div>
        </section>
      )}
      {group("activity").length > 0 && (
        <section className="pkg-section" aria-labelledby="ac-title">
          <h2 id="ac-title">Muutoksia CV:n päivityksen jälkeen ({group("activity").length})</h2>
          <div style={{ display: "grid", gap: 12 }}>{group("activity").map((p) => <Activity key={p.id} p={p} onDismiss={() => dismiss(p.id)} />)}</div>
        </section>
      )}
      <Notes entries={d.cv_projects} notes={d.notes} />
      {d.other_authors && Object.keys(d.other_authors).length > 0 && (
        <section className="pkg-section" aria-labelledby="au-title">
          <h2 id="au-title">Commit-osoitteet</h2>
          <p style={{ fontSize: 14 }}>Omat: <span className="mono" style={{ fontSize: 12.5 }}>{d.own_emails.join(", ")}</span>.
            Jos jokin alla olevista on sinun vanha osoitteesi, lisää se, niin sen commitit lasketaan omiksi.</p>
          <ul className="points" style={{ gap: 4 }}>
            {Object.entries(d.other_authors).map(([e, n]) => (
              <li key={e} style={{ paddingLeft: 0, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span className="mono" style={{ fontSize: 12.5 }}>{e}</span><span className="muted" style={{ fontSize: 12.5 }}>{n} committia</span>
                <button className="btn small ghost" onClick={() => confirm(`Ovatko osoitteen ${e} commitit sinun?`) && addEmail(e)}>Minun</button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
    </SeenSince.Provider>
  );
}
