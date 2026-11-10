import { Fragment, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, get, post } from "../api";
import { ErrorNotice, Loading, SystemBanner, useToast } from "../components";
import { useAiStatus, useTitle } from "../lib";
import { PanelResult, PanelRun, RunHistory, type PanelInfo } from "./CvPanel";
import { ProgressBar } from "../progress";

interface Unit { id: string; kind: "bullet" | "para"; text: string; included: boolean; edited?: boolean; orig?: string; flags?: string[] }
interface Entry { id: string; title: string; meta: string; included: boolean; units: Unit[] }
/** Muokkaustilan toiminnot: tekstit muokataan suoraan paperilla. */
interface Editor {
  unit: (id: string, text: string) => void; entry: (id: string, k: "title" | "meta", v: string) => void;
  header: (i: number, v: string) => void; summary: (v: string) => void; add: (parentId: string) => void;
}
interface Section { id: string; title: string; profile: boolean; units: Unit[]; entries: Entry[] }
interface Tr { orig: string; text: string; reasons: string[] }
interface Cv {
  name: string; header: string[]; sections: Section[]; lang: "fi" | "en"; job_title: string; company: string;
  summary: { text: string; source: "generated" | "original" | "edited"; flags: { text: string; reasons: string[] }[]; rejected?: string; style?: string[] };
  changes: { kind: string; text: string; id?: string; before?: string; after?: string; reason?: string }[];
  focus_skills: string[]; review_required: boolean; translation: Record<string, Tr>;
  source: string; model: string; created: string; updated?: string; panel?: PanelInfo;
}

/** **lihavointi** ja korostettavat taidot (vain esitys: teksti on CV:stä sellaisenaan). */
function rich(text: string, focus: string[]): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((p, i) =>
    p.startsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : <Fragment key={i}>{highlight(p, focus)}</Fragment>);
}
/** Yhteystietorivi: sähköposti ja verkko-osoitteet linkeiksi. Teksti pysyy näkyvänä (github.com/…), jotta
 *  seulontaohjelma lukee sen PDF:stä, ja linkki toimii PDF:ssä klikattavana. */
function contact(text: string): ReactNode[] {
  const re = /([\w.+-]+@[\w-]+\.[\w.]+|(?:https?:\/\/)?(?:www\.)?(?:github\.com|gitlab\.com|linkedin\.com)\/[\w\-./]+)/g;
  return text.split(re).map((p, i) => {
    if (i % 2 === 0) return <Fragment key={i}>{p}</Fragment>;
    const href = p.includes("@") && !p.includes("/") ? `mailto:${p}` : p.startsWith("http") ? p : `https://${p}`;
    return <a key={i} href={href}>{p.replace(/^https?:\/\/(www\.)?/, "")}</a>;
  });
}
function highlight(text: string, focus: string[]): ReactNode {
  if (!focus.length) return text;
  // vain kokonaiset sanat: "Git" ei korosta "GitHub"-sanan alkua
  const re = new RegExp(`(?<![\\w.])(${focus.map((f) => f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![\\w])`, "gi");
  return text.split(re).map((p, i) => (i % 2 ? <span key={i} className="cv-focus">{p}</span> : p));
}

/** PDF:n oletusnimi tulee sivun otsikosta: "Etunimi Sukunimi CV" näkyy seulontajärjestelmässä ja rekrytoijalle. */
function printPdf(cv: Cv) {
  const prev = document.title;
  document.title = `${cv.name} CV${cv.lang === "fi" ? "" : " (EN)"}`.replace(/[\/:*?"<>|]/g, "");
  const restore = () => { document.title = prev; window.removeEventListener("afterprint", restore); };
  window.addEventListener("afterprint", restore);
  window.print();
}

/** Muokattava teksti paperilla: kasvaa sisällön mukaan ja näyttää samalta kuin tuloste. */
function Ed({ value, onChange, label, cls = "" }: { value: string; onChange: (v: string) => void; label: string; cls?: string }) {
  return <textarea className={"cv-edit " + cls} rows={1} value={value} aria-label={label}
                   onChange={(e) => onChange(e.target.value.replace(/\n/g, " "))} />;
}

function Paper({ cv, edit, toggle, onFit, ed }: { cv: Cv; edit: boolean; toggle: (id: string) => void; onFit: (fill: number) => void; ed: Editor }) {
  const ref = useRef<HTMLElement>(null);
  const [over, setOver] = useState(false);
  useLayoutEffect(() => {
    if (edit) return;   // muokkaustilassa näkyvät myös pois jätetyt kohdat
    const measure = () => {
      const el = ref.current;
      const secs = el?.querySelectorAll(".cv-section");
      if (!el || !secs?.length) return;
      const box = el.getBoundingClientRect();
      const bottom = secs[secs.length - 1].getBoundingClientRect().bottom - box.top + parseFloat(getComputedStyle(el).paddingBottom);
      const fill = bottom / (box.width * 297 / 210);   // esikatselu on A4 millilleen
      setOver(fill > 1);
      onFit(fill);
    };
    measure();
    document.fonts?.ready.then(measure);
  }, [cv, edit, onFit]);
  const box = (id: string, on: boolean, label: string) => edit && (
    <input type="checkbox" className="no-print cv-check" checked={on} onChange={() => toggle(id)} aria-label={`${on ? "Jätä pois" : "Ota mukaan"}: ${label}`} />
  );
  const show = (on: boolean) => edit || on;
  const text = (u: Unit) => edit
    ? <Ed value={u.text} onChange={(v) => ed.unit(u.id, v)} label={`Rivi: ${u.text.slice(0, 40)}`} cls={u.edited ? "mine" : ""} />
    : rich(u.text, cv.focus_skills);
  const add = (parent: string) => edit && (
    <button type="button" className="btn small ghost no-print cv-add" onClick={() => ed.add(parent)}>+ rivi</button>);
  return (
    <article className="cv-paper" lang={cv.lang} ref={ref}>
      {over && !edit && <div className="cv-pagebreak no-print" aria-hidden="true"><span>Sivu 1 päättyy</span></div>}
      <header className="cv-head">
        <h1>{cv.name}</h1>
        {cv.header.map((h, i) => <div key={i} className={i === 0 ? "cv-role" : "cv-contact"}>
          {edit ? <Ed value={h} onChange={(v) => ed.header(i, v)} label={i === 0 ? "Otsikkorivi" : "Yhteystiedot"} />
            : i === 0 ? rich(h, []) : contact(h)}</div>)}
      </header>
      {cv.sections.map((s) => {
        const units = s.profile ? [] : s.units.filter((u) => show(u.included));
        const entries = s.entries.filter((e) => show(e.included));
        if (!s.profile && !units.length && !entries.length) return null;
        return (
          <section key={s.id} className="cv-section">
            <h2>{s.title}</h2>
            {s.profile && (edit ? <Ed value={cv.summary.text} onChange={ed.summary} label="Profiiliteksti" cls="cv-summary" />
              : <p className="cv-summary">{cv.summary.text}</p>)}
            {units.length > 0 && (units.every((u) => u.kind === "bullet") ? (
              <ul>{units.map((u) => <li key={u.id} className={u.included ? "" : "cv-off"}>{box(u.id, u.included, u.text)}{text(u)}</li>)}</ul>
            ) : units.map((u) => <p key={u.id} className={u.included ? "" : "cv-off"}>{box(u.id, u.included, u.text)}{text(u)}</p>))}
            {!s.profile && !s.entries.length && add(s.id)}
            {entries.map((e) => (
              <div key={e.id} className={"cv-entry" + (e.included ? "" : " cv-off")}>
                <div className="cv-entry-head">{box(e.id, e.included, e.title)}
                  {edit ? <><Ed value={e.title} onChange={(v) => ed.entry(e.id, "title", v)} label="Otsikko" cls="cv-h3" />
                            <Ed value={e.meta} onChange={(v) => ed.entry(e.id, "meta", v)} label="Tekniikat ja aika" cls="cv-meta" /></>
                    : <><h3>{e.title}</h3>{e.meta && <span className="cv-meta">{e.meta}</span>}</>}</div>
                {e.units.filter((u) => show(u.included) && (e.included || edit)).some((u) => u.kind === "bullet") ? (
                  <ul>{e.units.filter((u) => show(u.included)).map((u) => (
                    <li key={u.id} className={u.included ? "" : "cv-off"}>{box(u.id, u.included, u.text)}{text(u)}</li>))}</ul>
                ) : e.units.filter((u) => show(u.included)).map((u) => (
                  <p key={u.id} className={u.included ? "" : "cv-off"}>{box(u.id, u.included, u.text)}{text(u)}</p>))}
                {add(e.id)}
              </div>
            ))}
          </section>
        );
      })}
    </article>
  );
}

function Review({ cv, onSaved }: { cv: Cv; onSaved: () => void }) {
  const toast = useToast();
  const items = Object.entries(cv.translation).filter(([k]) => k !== "SUMMARY")
    .sort((a, b) => b[1].reasons.length - a[1].reasons.length);
  const [lines, setLines] = useState<Record<string, string>>(() => Object.fromEntries(items.map(([k, v]) => [k, v.text])));
  const [busy, setBusy] = useState(false);
  const flagged = items.filter(([, v]) => v.reasons.length).length;
  const save = async (overwrite = false) => {
    setBusy(true);
    try {
      const r = await post<{ path: string; warnings: Record<string, string[]> }>("/api/cv/base", { lines, overwrite });
      const w = Object.keys(r.warnings).length;
      toast(`Suomenkielinen pohja tallennettu${w ? ` (${w} riviä varoituksin)` : ""}`);
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.message.includes("on jo olemassa") && confirm(`${e.message}\n\nKorvataanko?`)) { setBusy(false); return save(true); }
      toast((e as Error).message);
    } finally { setBusy(false); }
  };
  return (
    <section aria-labelledby="rev-title">
      <h2 id="rev-title">Tarkista suomennos</h2>
      <p style={{ fontSize: 13, marginTop: 0 }}>Kone käänsi koko CV:n. Korjaa rivit ja tallenna ne <b>suomenkieliseksi pohjaksi</b>. Sen jälkeen
        suomenkieliset CV:t kootaan tarkistetusta pohjasta, eikä mitään käännetä enää.
        {flagged > 0 && <> <b style={{ color: "var(--danger)" }}>{flagged} riviä</b> on merkitty epäilyttäviksi, ja ne ovat listan alussa.</>}</p>
      <ul className="claims" style={{ maxHeight: "60vh", overflowY: "auto", paddingRight: 4 }}>
        {items.map(([k, v]) => (
          <li key={k} className={v.reasons.length ? "st-flag" : ""}>
            <div className="src" style={{ marginBottom: 3 }}>{v.orig}</div>
            <textarea rows={Math.min(5, Math.ceil(v.orig.length / 60) + 1)} value={lines[k]} aria-label={`Suomennos: ${v.orig.slice(0, 40)}`}
                      onChange={(e) => setLines({ ...lines, [k]: e.target.value })} />
            {v.reasons.length > 0 && <div className="why">{v.reasons.join(" · ")}</div>}
          </li>
        ))}
      </ul>
      <div className="actions"><button className="btn primary" onClick={() => save(false)} disabled={busy}>{busy ? "Tallennetaan…" : "Tallenna pohjaksi"}</button></div>
    </section>
  );
}

/** Käyttäjän itse muuttamat tai lisäämät rivit (tallennetusta versiosta). */
function mine(cv: Cv): Unit[] {
  return cv.sections.flatMap((s) => [...s.units, ...s.entries.flatMap((e) => e.units)]).filter((u) => u.edited);
}

export default function CvView() {
  const [params, setParams] = useSearchParams();
  const url = params.get("url") || "";
  const lang = params.get("lang") === "fi" ? "fi" : "en";
  const toast = useToast();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState<Cv | null>(null);
  const [fill, setFill] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["cv", url, lang], enabled: !!url, retry: false,
    queryFn: () => get<Cv>("/api/cv", { url, lang }).catch((e) => { if (e instanceof ApiError && e.code === "not_found") return null; throw e; }),
  });
  const base = useQuery({ queryKey: ["cv-base"], queryFn: () => get<{ exists: boolean; path: string }>("/api/cv/base") });
  const ai = useAiStatus();
  const [runId, setRunId] = useState<string | null>(null);
  const [runsKey, setRunsKey] = useState(0);
  // käynnissä oleva ajo jatkuu, vaikka sivu ladattaisiin uudelleen
  useEffect(() => {
    setRunId(null);
    if (!url) return;
    get<{ runs: { run_id: string; lang: string; status: string }[] }>("/api/cv/runs", { url })
      .then((r) => { const live = r.runs.find((x) => x.lang === lang && x.status === "running"); if (live) setRunId(live.run_id); })
      .catch(() => { /* ajohistoria ei ole välttämätön */ });
  }, [url, lang]);
  const panelBlocked = lang === "fi" && !base.data?.exists;
  // ?auto=1 (ilmoitussivun Räätälöi CV): jos CV:tä ei vielä ole, paneeli käynnistyy itse. Olemassa olevaa ei ajeta uudelleen.
  const autoStarted = useRef(false);
  useEffect(() => {
    if (params.get("auto") !== "1" || autoStarted.current || !q.isSuccess || base.isLoading) return;
    autoStarted.current = true;
    const next = new URLSearchParams(params); next.delete("auto"); setParams(next, { replace: true });
    if (q.data === null && !runId && !panelBlocked) startPanel();
  }, [q.isSuccess, q.data, base.isLoading]);  // eslint-disable-line
  const startPanel = async () => {
    setErr(null);
    try { setRunId((await post<{ run_id: string }>("/api/cv/runs", { url, lang })).run_id); }
    catch (e) { setErr(e); }
  };
  const runDone = (type: string) => {
    setRunsKey((k) => k + 1);
    if (type === "run_finished") { qc.invalidateQueries({ queryKey: ["cv", url, lang] }); toast("Paneeli valmis"); }
  };
  // ?edit=1 avaa suoraan muokkaustilaan (linkki suoraan muokkaukseen)
  useEffect(() => { setDraft(q.data ? structuredClone(q.data) : null); setEdit(params.get("edit") === "1"); }, [q.data]);  // eslint-disable-line
  const cv = draft;
  useTitle(cv ? `CV: ${cv.job_title}` : "Räätälöity CV");

  const generate = async () => {
    setBusy(true); setErr(null);
    try { qc.setQueryData(["cv", url, lang], await post<Cv>("/api/cv", { url, lang })); toast("CV räätälöity"); }
    catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const toggle = (id: string) => {
    if (!cv) return;
    const flip = <T extends { id: string; included: boolean }>(x: T): T => (x.id === id ? { ...x, included: !x.included } : x);
    setDraft({ ...cv, sections: cv.sections.map((s) => ({ ...s, units: s.units.map(flip),
      entries: s.entries.map((e) => ({ ...flip(e), units: e.units.map(flip) })) })) });
  };
  const mapUnits = (f: (u: Unit) => Unit) => cv && setDraft({ ...cv, sections: cv.sections.map((s) => ({ ...s, units: s.units.map(f),
    entries: s.entries.map((e) => ({ ...e, units: e.units.map(f) })) })) });
  const ed: Editor = {
    unit: (id, text) => mapUnits((u) => (u.id === id ? { ...u, text } : u)),
    entry: (id, k, v) => cv && setDraft({ ...cv, sections: cv.sections.map((s) => ({ ...s,
      entries: s.entries.map((e) => (e.id === id ? { ...e, [k]: v } : e)) })) }),
    header: (i, v) => cv && setDraft({ ...cv, header: cv.header.map((h, j) => (j === i ? v : h)) }),
    summary: (v) => cv && setDraft({ ...cv, summary: { ...cv.summary, text: v } }),
    add: (parent) => {
      if (!cv) return;
      const line: Unit = { id: `NEW${Date.now()}`, kind: "bullet", text: "", included: true };
      setDraft({ ...cv, sections: cv.sections.map((s) => (s.id === parent ? { ...s, units: [...s.units, line] } : { ...s,
        entries: s.entries.map((e) => (e.id === parent ? { ...e, units: [...e.units, line] } : e)) })) });
    },
  };
  const save = async () => {
    if (!cv) return;
    setBusy(true);
    try {
      const r = await post<Cv>("/api/cv/save", { url, lang, cv });
      qc.setQueryData(["cv", url, lang], r);
      const flagged = mine(r).filter((u) => u.flags?.length).length + (r.summary.source === "edited" && r.summary.flags?.length ? 1 : 0);
      toast(flagged ? `Tallennettu. ${flagged} muokattua kohtaa sisältää asioita, joita CV:ssäsi ei ole: katso Omat muokkaukset` : "Tallennettu");
      setEdit(false);
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  const dirty = !!cv && !!q.data && JSON.stringify(cv) !== JSON.stringify(q.data);
  if (!url) return <ErrorNotice error="Ilmoituksen osoite puuttuu" />;

  return (
    <div className="pkg cv-layout">
      <div>
        <div className="no-print" style={{ padding: "16px 0 14px", borderBottom: "1px solid var(--line)", marginBottom: 16, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ flex: 1 }}>
            <span className="kicker">Räätälöity CV</span>
            <h1 style={{ fontSize: 20, margin: "4px 0 0" }}>{cv?.job_title || "…"}</h1>
          </div>
          <div role="group" aria-label="Kieli" style={{ display: "flex" }}>
            <button className="btn small" aria-pressed={lang === "en"} onClick={() => setParams({ url, lang: "en" })}>English</button>
            <button className="btn small" aria-pressed={lang === "fi"} onClick={() => setParams({ url, lang: "fi" })}>Suomi</button>
          </div>
        </div>
        <div className="no-print"><SystemBanner need={["llm", "index"]} /></div>
        {err ? <div className="no-print"><ErrorNotice error={err} title="Räätälöinti epäonnistui" /></div> : null}
        {(q.isLoading || busy && !cv) && <Loading lines={10} />}
        {busy && <div className="no-print" role="status">{lang === "fi" && !base.data?.exists
          ? <ProgressBar id="cv-translate" llm={[90, 180]} label="Valitaan sisältöä ja käännetään koko CV suomeksi" />
          : <ProgressBar id={`cv-tailor-${lang}`} llm={[30, 60]} label="Valitaan sisältöä ja kirjoitetaan profiilitekstiä"
                         detail="Profiiliteksti tarkistetaan CV:tä vasten ja sovitetaan yhdelle sivulle." />}</div>}
        {q.isSuccess && !cv && !busy && (
          <div className="notice ok no-print">
            <h3>CV:tä ei ole vielä räätälöity tälle paikalle</h3>
            <p>Kielimalli valitsee CV:stä tähän paikkaan sopivimmat kohdat, järjestää osiot ja kirjoittaa lyhyen profiilitekstin.
              Muu teksti kootaan CV:stäsi sanasta sanaan, joten mitään ei keksitä. Profiiliteksti tarkistetaan, ja jos siinä on väite ilman lähdettä, käytetään alkuperäistä.</p>
            {lang === "fi" && (base.data?.exists
              ? <p>Suomenkielinen versio kootaan tarkistetusta pohjastasi ({base.data.path}).</p>
              : <p><b>Suomenkielistä pohjaa ei vielä ole.</b> Ensimmäisellä kerralla kone kääntää koko CV:n, ja sinä tarkistat käännöksen rivi riviltä. Tarkistettu käännös tallennetaan pohjaksi.</p>)}
            <div className="actions">
              <button className="btn primary" onClick={generate}>Räätälöi CV</button>
              <button className="btn" onClick={startPanel} disabled={!!runId || panelBlocked}
                      title={panelBlocked ? "Tee ensin suomenkielinen pohja tavallisella räätälöinnillä" : undefined}>Räätälöi ja arvioi paneelilla</button>
            </div>
            <p className="muted" style={{ fontSize: 13 }}>Paneelissa neljä arvioijaa (faktantarkistaja, rekrytoija, tekninen haastattelija ja esihenkilö)
              lukee CV:n, ja räätälöijä korjaa löydökset enintään kahdesti. Kestää noin minuutin tai pari.</p>
          </div>
        )}
        {runId && <PanelRun runId={runId} slow={ai.data?.llm.provider === "ollama"} onDone={runDone} onClose={() => setRunId(null)} />}
        {cv && <div className="cv-scroll"><Paper cv={cv} edit={edit} toggle={toggle} onFit={setFill} ed={ed} /></div>}
      </div>

      {cv && (
        <aside className="margin no-print" aria-label="CV:n toiminnot">
          {cv.review_required ? <Review cv={cv} onSaved={() => { qc.invalidateQueries({ queryKey: ["cv-base"] }); generate(); }} /> : null}
          <section>
            <h2>Tulostus</h2>
            {fill !== null && (
              <>
                <div className={"fit-bar" + (fill > 1 ? " over" : "")} aria-hidden="true"><i style={{ width: `${Math.min(100, fill * 100)}%` }} /></div>
                <p style={{ fontSize: 13, margin: "0 0 8px", color: fill > 1 ? "var(--danger)" : "var(--ink-2)" }}>
                  {fill > 1 ? `Ylittää yhden sivun (${Math.round(fill * 100)} %). Jätä kohtia pois Muokkaa-tilassa.`
                    : `Mahtuu yhdelle sivulle (täyttö ${Math.round(fill * 100)} %).`}</p>
              </>
            )}
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Valitse tulostusikkunassa ”Tallenna PDF-tiedostona” ja poista selaimen ylä- ja alatunnisteet.</p>
            <div className="actions" style={{ marginTop: 0 }}>
              <button className="btn primary" onClick={() => printPdf(cv)} disabled={dirty}>Tulosta / tallenna PDF</button>
              {dirty && <span className="stale">Tallenna muutokset ensin</span>}
            </div>
          </section>
          <section>
            <h2>Profiiliteksti</h2>
            {cv.summary.source === "original" && cv.summary.rejected && (
              <div className="claims" style={{ marginBottom: 8 }}><li className="st-flag" style={{ listStyle: "none" }}>
                <b>Räätälöity teksti hylättiin:</b> {cv.summary.rejected}
                <div className="why">{cv.summary.flags.flatMap((f) => f.reasons).join(" · ")}</div></li></div>
            )}
            {edit ? <p className="muted" style={{ fontSize: 13.5, margin: 0 }}>Muokkaa profiilitekstiä suoraan CV:ssä.</p> : <p style={{ fontSize: 13.5, margin: 0, color: "var(--ink-2)" }}>{{ generated: "Kirjoitettu tähän paikkaan ja tarkistettu", original: "Alkuperäinen CV:stä", edited: "Muokattu käsin" }[cv.summary.source]}
              {cv.summary.flags?.length > 0 && cv.summary.source === "edited" && <b style={{ color: "var(--danger)" }}> · väitteitä ilman lähdettä: {cv.summary.flags.flatMap((f) => f.reasons).join(", ")}</b>}</p>}
            {!edit && (cv.summary.style?.length || 0) > 0 && (
              <p className="stale" style={{ margin: "6px 0 0" }}>Tyylihuomio: {cv.summary.style!.map((x) => `”${x}”`).join(", ")}</p>
            )}
          </section>
          <section>
            <h2>Muutokset alkuperäiseen</h2>
            <ul className="points" style={{ gap: 4 }}>{cv.changes.map((c, i) => (
              <li key={i} style={{ paddingLeft: 0 }}>
                {c.kind === "rewrite" ? (
                  <span className="cv-rewrite">Muotoiltu <span className="mono muted">{c.id}</span>: <del>{c.before}</del> → <ins>{c.after}</ins>
                    {c.reason && <span className="muted"> ({c.reason})</span>}</span>
                ) : c.text}
              </li>
            ))}</ul>
            <div className="actions">
              <button className="btn small" aria-pressed={edit} onClick={() => setEdit((v) => !v)}>{edit ? "Lopeta muokkaus" : "Muokkaa"}</button>
              <button className="btn small" onClick={save} disabled={!dirty || busy}>Tallenna</button>
              <button className="btn small ghost" onClick={() => confirm("Räätälöidäänkö uudelleen? Nykyiset valinnat korvataan.") && generate()} disabled={busy}>Räätälöi uudelleen</button>
              <button className="btn small ghost" onClick={() => confirm("Ajetaanko paneeli? Valmis tulos korvaa nykyisen CV:n.") && startPanel()}
                      disabled={busy || !!runId || panelBlocked}>Arvioi paneelilla</button>
            </div>
            <p className="muted mono" style={{ fontSize: 11.5, marginBottom: 0 }}>{cv.model} · lähde {cv.source}</p>
          </section>
          {edit && <section><h2>Muokkaus</h2><p style={{ fontSize: 13.5, margin: 0 }}>Kirjoita suoraan CV:hen. Valintaruudulla kohdan saa pois,
            ja <b>+ rivi</b> lisää rivin. <code>**teksti**</code> lihavoi. Tallenna lopuksi: muokattu versio arkistoidaan myös PDF:nä.
            Uusi räätälöinti tai paneeliajo korvaa muokkaukset.</p></section>}
          {q.data && mine(q.data).length > 0 && (
            <section>
              <h2>Omat muokkaukset ({mine(q.data).length})</h2>
              <ul className="claims">{mine(q.data).map((u) => (
                <li key={u.id} className={u.flags?.length ? "st-flag" : ""}>
                  {u.orig ? <><del>{u.orig}</del> → </> : <span className="muted">Uusi rivi: </span>}{u.text}
                  {u.flags?.length ? <div className="why">{u.flags.join(" · ")}</div> : null}
                </li>))}</ul>
              <p className="muted" style={{ fontSize: 12.5, margin: "6px 0 0" }}>Teksti on omaasi, joten sitä ei estetä. Punaisella merkityt kohdat sisältävät
                tekniikan, luvun tai väitteen, jota CV:ssäsi ei ole: varmista, että pystyt perustelemaan ne haastattelussa.</p>
            </section>
          )}
          {cv.panel && <PanelResult panel={cv.panel} />}
          <RunHistory url={url} refreshKey={runsKey} />
          <section>
            <h2>Ilmoitus</h2>
            <p style={{ fontSize: 13.5, margin: 0 }}><Link to={`/ilmoitus?url=${encodeURIComponent(url)}`}>{cv.job_title}</Link> · {cv.company}<br />
              <Link to={`/hakemus?url=${encodeURIComponent(url)}`}>Saatekirje (valinnainen) →</Link></p>
            <p className="muted" style={{ fontSize: 12.5, margin: "6px 0 0" }}>Ilmoitus on seurannassa ja selainlaajennuksen listassa:
              lomakkeen kysymyksiin laajennus kirjoittaa vastaukset sivulla.</p>
          </section>
        </aside>
      )}
    </div>
  );
}
