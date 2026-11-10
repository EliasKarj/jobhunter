import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { ErrorNotice, Loading, useToast } from "../components";
import { useTitle } from "../lib";
import ProjectsTab from "./ProjectsTab";
import StyleTab from "./StyleTab";
import { ProgressBar } from "../progress";

// ------------------------------------------------------------ osaaminen (skills.json)

interface Skill { name: string; aliases: string[]; weight: number; level: string; category: string; source: string }
interface Suggestion { name: string; aliases: string[]; category: string; weight: number; module?: string; url?: string }
interface SkillsPayload {
  skills: Skill[]; categories: string[]; levels: string[]; suggestions: Suggestion[];
  ops_url: string; ops_note: string; gaps: { name: string; count: number; share: number }[];
}

const splitList = (s: string) => s.split(/[,\n]/).map((x) => x.trim()).filter(Boolean);

function SkillsTab() {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["skills"], queryFn: () => get<SkillsPayload>("/api/skills") });
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");
  useEffect(() => { if (q.data && skills === null) setSkills(q.data.skills); }, [q.data, skills]);
  const dirty = !!q.data && !!skills && JSON.stringify(skills) !== JSON.stringify(q.data.skills);

  if (q.isLoading || !skills) return q.error ? <ErrorNotice error={q.error} /> : <Loading />;
  const d = q.data!;
  const have = new Set(skills.map((s) => s.name.toLowerCase()));
  const set = (i: number, patch: Partial<Skill>) => setSkills(skills.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const add = (s: Partial<Skill>) => {
    if (!s.name || have.has(s.name.toLowerCase())) return;
    setSkills([...skills, { name: s.name, aliases: s.aliases || [], weight: s.weight ?? 1, level: s.level || "", category: s.category || "Muu", source: s.source || "oma" }]);
  };
  const save = async () => {
    setBusy(true);
    try {
      await post("/api/skills", { skills });
      toast("Osaamisprofiili tallennettu, pisteet laskettu uudelleen");
      qc.invalidateQueries({ queryKey: ["skills"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setSkills(null);
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  const byCat = d.categories.map((c) => ({ c, items: skills.map((s, i) => [s, i] as const).filter(([s]) => s.category === c) }))
    .filter((g) => g.items.length);

  return (
    <div className="pkg">
      <section aria-label="Taidot">
        <div className="actions" style={{ marginTop: 0, marginBottom: 14 }}>
          <form style={{ display: "flex", gap: 8 }} onSubmit={(e) => { e.preventDefault(); add({ name: newName.trim() }); setNewName(""); }}>
            <label className="sr" htmlFor="new-skill">Uusi taito</label>
            <input id="new-skill" type="text" placeholder="Lisää taito, esim. Kotlin" value={newName} onChange={(e) => setNewName(e.target.value)} style={{ width: 260 }} />
            <button className="btn" disabled={!newName.trim()}>Lisää</button>
          </form>
          <span style={{ flex: 1 }} />
          {dirty && <span className="stale">Tallentamattomia muutoksia</span>}
          <button className="btn primary" onClick={save} disabled={!dirty || busy}>{busy ? "Tallennetaan…" : "Tallenna"}</button>
        </div>
        {busy && <ProgressBar compact id="skills-save" estimate={8} label="Tallennetaan ja pisteytetään ilmoitukset uudelleen" />}
        {byCat.map(({ c, items }) => (
          <div key={c} className="pkg-section">
            <header><h2>{c}</h2><span className="mono muted" style={{ fontSize: 12 }}>{items.length}</span></header>
            <div style={{ overflowX: "auto" }}>
              <table className="data">
                <thead><tr><th>Taito</th><th>Taso</th><th className="n" title="Paino avainsanapisteytyksessä (0–5)">Paino</th><th>Synonyymit</th><th>Ryhmä</th><th /></tr></thead>
                <tbody>{items.map(([s, i]) => (
                  <tr key={s.name}>
                    <td style={{ fontWeight: 600 }}>{s.name}{s.source && <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}> · {s.source}</span>}</td>
                    <td><select value={s.level} onChange={(e) => set(i, { level: e.target.value })} aria-label={`${s.name}: taso`} style={{ minWidth: 120 }}>
                      {d.levels.map((l) => <option key={l} value={l}>{l || "–"}</option>)}</select></td>
                    <td className="n"><input type="number" min={0} max={5} step={0.5} value={s.weight} aria-label={`${s.name}: paino`} style={{ width: 72 }}
                      onChange={(e) => set(i, { weight: Number(e.target.value) })} /></td>
                    <td><input type="text" value={s.aliases.join(", ")} aria-label={`${s.name}: synonyymit`} placeholder="–"
                      onChange={(e) => set(i, { aliases: splitList(e.target.value) })} /></td>
                    <td><select value={s.category} onChange={(e) => set(i, { category: e.target.value })} aria-label={`${s.name}: ryhmä`}>
                      {d.categories.map((x) => <option key={x}>{x}</option>)}</select></td>
                    <td><button className="btn small ghost" onClick={() => setSkills(skills.filter((_, j) => j !== i))} aria-label={`Poista ${s.name}`}>Poista</button></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        ))}
      </section>
      <aside className="margin" aria-label="Ehdotukset">
        <section>
          <h2>Ilmoituksissa usein, profiilista puuttuu</h2>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Osuus vahvoista osumista, joissa tekniikka mainitaan.</p>
          <ul className="points" style={{ gap: 6 }}>
            {d.gaps.filter((g) => !have.has(g.name.toLowerCase())).map((g) => (
              <li key={g.name} style={{ paddingLeft: 0, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                <span>{g.name} <span className="mono muted" style={{ fontSize: 12 }}>{g.count} ilm.</span></span>
                <button className="btn small" onClick={() => add({ name: g.name, level: "Opiskelen", source: "puute" })}>Lisää</button>
              </li>
            ))}
          </ul>
        </section>
        <section>
          <h2>Ehdotukset opetussuunnitelmasta</h2>
          {d.ops_note && <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>{d.ops_note}</p>}
          <ul className="points" style={{ gap: 6 }}>
            {d.suggestions.filter((s) => !have.has(s.name.toLowerCase())).slice(0, 14).map((s) => (
              <li key={s.name} style={{ paddingLeft: 0, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                <span>{s.name}{s.module && <span className="muted" style={{ fontSize: 12 }}> · {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.module}</a> : s.module}</span>}</span>
                <button className="btn small" onClick={() => add({ ...s, level: "Perusteet", source: "ops" })}>Lisää</button>
              </li>
            ))}
          </ul>
        </section>
      </aside>
    </div>
  );
}

// ------------------------------------------------------------ haku ja pisteytys (profile.json)

interface Loc { name: string; weight: number; aliases: string[] }
interface SearchSettings {
  queries: string[]; areas: string[]; broad_enabled: boolean; max_pages: number; max_jobs: number; workers: number;
  locations: Loc[]; remote_bonus: number; skills_target: number; weekly_goal: number; [k: string]: unknown;
}
interface Feedback { hidden: number; liked: number; exclude: { term: string; count: number }[]; block: { company: string; count: number }[];
  watch: { company: string; count: number }[] }

const TERMS: [string, string, string][] = [
  ["level_positive", "Taso: plussaa", "esim. harjoittelija, junior, trainee"],
  ["level_negative", "Taso: miinusta", "esim. senior, 5+ years"],
  ["title_senior_terms", "Senior-sanat otsikossa", "rajaavat avainsanapisteet 40:een"],
  ["field_title_terms", "Ala: ohjelmistokehitys otsikossa", "esim. developer, kehittäjä"],
  ["it_title_terms", "Ala: yleinen IT otsikossa", "IT-tuki yms."],
  ["it_body_terms", "Ala: IT-sanat kuvauksessa", ""],
  ["exclude_title_terms", "Poissulje otsikosta", "ilmoitus piilotetaan pisteytyksessä"],
  ["remote_terms", "Etätyösanat", "antavat etätyöbonuksen"],
  ["block_companies", "Estetyt työnantajat", ""],
];

function SearchTab() {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["search-settings"], queryFn: () => get<SearchSettings>("/api/settings") });
  const fb = useQuery({ queryKey: ["feedback"], queryFn: () => get<Feedback>("/api/feedback") });
  const [s, setS] = useState<SearchSettings | null>(null);
  const [text, setText] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const base = useRef<string | null>(null);
  useEffect(() => {
    if (q.data && s === null) {
      base.current = null;
      setS(q.data);
      setText(Object.fromEntries([["queries", q.data.queries.join("\n")], ["areas", q.data.areas.join(", ")],
        ...TERMS.map(([k]) => [k, ((q.data[k] as string[]) || []).join(", ")])]));
    }
  }, [q.data, s]);
  const out = useMemo(() => s && ({
    ...s, queries: text.queries.split("\n").map((x) => x.trim()).filter(Boolean), areas: splitList(text.areas || ""),
    ...Object.fromEntries(TERMS.map(([k]) => [k, splitList(text[k] || "")])),
  }), [s, text]);
  // vertailukohta on lomakkeen oma alkutila (palvelimen data ei ole täsmälleen samassa muodossa)
  if (out && base.current === null) base.current = JSON.stringify(out);
  if (q.isLoading || !s || !out) return q.error ? <ErrorNotice error={q.error} /> : <Loading />;
  const dirty = JSON.stringify(out) !== base.current;
  const save = async () => {
    setBusy(true);
    try {
      await post("/api/settings", out);
      toast("Hakuasetukset tallennettu, pisteet laskettu uudelleen");
      qc.invalidateQueries({ queryKey: ["search-settings"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["feedback"] });
      setS(null);
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  const addTerm = (k: string, term: string) => setText((t) => ({ ...t, [k]: [t[k], term].filter(Boolean).join(", ") }));
  const setLoc = (i: number, p: Partial<Loc>) => setS({ ...s, locations: s.locations.map((l, j) => (j === i ? { ...l, ...p } : l)) });
  const num = (k: keyof SearchSettings, label: string, min: number, max: number, step = 1, hint = "") => (
    <label className="field">{label}
      <input type="number" min={min} max={max} step={step} value={s[k] as number} onChange={(e) => setS({ ...s, [k]: Number(e.target.value) })} />
      {hint && <span style={{ fontSize: 12 }}>{hint}</span>}</label>
  );
  const f = fb.data;
  const tips = f ? [
    ...f.exclude.map((x) => ({ text: `Piilotit ${x.count} ilmoitusta, joiden otsikossa on ”${x.term}”, etkä tallentanut yhtään.`, act: "Poissulje", fn: () => addTerm("exclude_title_terms", x.term) })),
    ...f.block.map((x) => ({ text: `Piilotit ${x.count} ilmoitusta yritykseltä ${x.company}.`, act: "Estä yritys", fn: () => addTerm("block_companies", x.company) })),
  ] : [];

  return (
    <div className="pkg">
      <section className="settings" aria-label="Hakuasetukset" style={{ maxWidth: "none" }}>
        <section>
          <h2>Haku</h2>
          <label className="field">Hakusanat (yksi riville)
            <textarea rows={8} value={text.queries} onChange={(e) => setText({ ...text, queries: e.target.value })} /></label>
          <p className="muted" style={{ fontSize: 13, margin: "-4px 0 10px" }}>Duunitorista luetaan koko Suomen ilmoitukset sivukartasta, ja otsikkosuodatin
            päästää läpi IT-, data-, harjoittelu- ja muut sopivat tehtävät (noin neljännes). Pisteytys valitsee niistä osuvat, ja
            alueiden painot ovat kohdassa Hakualueet ja pisteytys. Hakusanoja käyttää vain Jobly; alla olevat hakualueet ja laaja haku
            koskevat vain vanhaa alueittaista selausta.</p>
          <label className="field">Hakualueet (pilkulla eroteltuna)
            <input type="text" value={text.areas} onChange={(e) => setText({ ...text, areas: e.target.value })} /></label>
          <label className="check" style={{ padding: 0 }}><input type="checkbox" checked={s.broad_enabled} onChange={(e) => setS({ ...s, broad_enabled: e.target.checked })} />
            Laaja haku: käy läpi kaikki alueen ilmoitukset hakusanojen lisäksi</label>
          <div className="form-grid">
            {num("max_pages", "Laaja haku: sivuja enintään", 1, 300)}
            {num("max_jobs", "Laaja haku: ilmoituksia enintään", 100, 20000, 100)}
            {num("workers", "Rinnakkaiset haut", 1, 8)}
            {num("weekly_goal", "Viikkotavoite (hakemuksia)", 0, 100)}
          </div>
        </section>
        <section>
          <h2>Paikkakunnat ja painot</h2>
          <table className="data">
            <thead><tr><th>Paikkakunta</th><th className="n">Pisteet</th><th>Tunnistussanat</th><th /></tr></thead>
            <tbody>{s.locations.map((l, i) => (
              <tr key={i}>
                <td><input type="text" value={l.name} aria-label="Paikkakunta" onChange={(e) => setLoc(i, { name: e.target.value })} /></td>
                <td className="n"><input type="number" min={0} max={20} value={l.weight} aria-label={`${l.name}: pisteet`} style={{ width: 72 }} onChange={(e) => setLoc(i, { weight: Number(e.target.value) })} /></td>
                <td><input type="text" value={l.aliases.join(", ")} aria-label={`${l.name}: tunnistussanat`} onChange={(e) => setLoc(i, { aliases: splitList(e.target.value.toLowerCase()) })} /></td>
                <td><button className="btn small ghost" onClick={() => setS({ ...s, locations: s.locations.filter((_, j) => j !== i) })}>Poista</button></td>
              </tr>
            ))}</tbody>
          </table>
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="btn small" onClick={() => setS({ ...s, locations: [...s.locations, { name: "", weight: 10, aliases: [] }] })}>Lisää paikkakunta</button>
          </div>
          <div className="form-grid">
            {num("remote_bonus", "Etätyöbonus (pisteitä)", 0, 10)}
            {num("skills_target", "Osaamispisteiden tavoite", 1, 50, 1, "Montako osaamisosumaa antaa täydet osaamispisteet")}
          </div>
        </section>
        <section>
          <h2>Pisteytyksen sanastot</h2>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>Pilkulla eroteltuna. Sanat haetaan pienillä kirjaimilla ja alkuosana (”harjoittel” osuu myös ”harjoittelija”).</p>
          {TERMS.map(([k, label, hint]) => (
            <label key={k} className="field">{label}{hint && <span style={{ fontWeight: 400 }}> · {hint}</span>}
              <textarea rows={2} value={text[k]} onChange={(e) => setText({ ...text, [k]: e.target.value })} /></label>
          ))}
        </section>
      </section>
      <aside className="margin" aria-label="Tallennus ja ehdotukset">
        <section>
          <h2>Tallennus</h2>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Pisteet lasketaan heti uudelleen nykyisille ilmoituksille. Hakusanat ja alueet vaikuttavat seuraavaan hakuun.</p>
          {dirty && <p className="stale">Tallentamattomia muutoksia</p>}
          <button className="btn primary" onClick={save} disabled={!dirty || busy}>{busy ? "Tallennetaan…" : "Tallenna"}</button>
          {busy && <ProgressBar compact id="search-save" estimate={8} label="Tallennetaan ja pisteytetään ilmoitukset uudelleen" />}
        </section>
        <section>
          <h2>Ehdotukset valinnoistasi</h2>
          {!f ? <Loading lines={2} /> : f.hidden + f.liked < 8 ? (
            <p className="muted" style={{ fontSize: 13, margin: 0 }}>Ehdotukset tulevat, kun olet tallentanut tai piilottanut vähintään 8 ilmoitusta ({f.hidden + f.liked} nyt).</p>
          ) : tips.length === 0 ? <p className="muted" style={{ fontSize: 13, margin: 0 }}>Ei ehdotuksia juuri nyt.</p> : (
            <ul className="claims">{tips.map((t, i) => (
              <li key={i}><div>{t.text}</div><button className="btn small" style={{ marginTop: 4 }} onClick={t.fn}>{t.act}</button></li>
            ))}</ul>
          )}
        </section>
      </aside>
    </div>
  );
}

// ------------------------------------------------------------ vakiovastaukset (answers.json)

interface Answers { fields: { key: string; label: string; value: string }[] }

function AnswersTab() {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["answers"], queryFn: () => get<Answers>("/api/answers") });
  const [v, setV] = useState<Record<string, string> | null>(null);
  useEffect(() => { if (q.data && v === null) setV(Object.fromEntries(q.data.fields.map((f) => [f.key, f.value]))); }, [q.data, v]);
  if (q.isLoading || !v) return q.error ? <ErrorNotice error={q.error} /> : <Loading />;
  const save = async () => {
    try {
      qc.setQueryData(["answers"], await post<Answers>("/api/answers", { values: v }));
      toast("Vakiovastaukset tallennettu");
    } catch (e) { toast((e as Error).message); }
  };
  return (
    <section className="settings" aria-label="Vakiovastaukset">
      <section>
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>Täytä nämä kerran. Ne näkyvät jokaisessa hakemuspaketissa kopiointipainikkeen kanssa.
          Kielimalli ei kirjoita eikä muuta näitä, koska vain sinä tiedät nämä asiat. Palkkatoive tulee hakemuspakettiin suoraan tästä.</p>
        {q.data!.fields.map((f) => (
          <label key={f.key} className="field">{f.label}
            <textarea rows={2} value={v[f.key] || ""} onChange={(e) => setV({ ...v, [f.key]: e.target.value })} /></label>
        ))}
        <div><button className="btn primary" onClick={save}>Tallenna</button></div>
      </section>
    </section>
  );
}

export default function Profile() {
  useTitle("Profiili");
  const [params, setParams] = useSearchParams();
  const t = params.get("tab");
  const tab = t === "haku" || t === "vastaukset" || t === "projektit" || t === "tyyli" ? t : "osaaminen";
  return (
    <section aria-labelledby="prof-title">
      <div className="section-head">
        <h1 id="prof-title">Profiili</h1>
        <div role="tablist" aria-label="Profiilin osat" style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          <button role="tab" className="btn small" aria-selected={tab === "osaaminen"} aria-pressed={tab === "osaaminen"} onClick={() => setParams({})}>Osaaminen</button>
          <button role="tab" className="btn small" aria-selected={tab === "haku"} aria-pressed={tab === "haku"} onClick={() => setParams({ tab: "haku" })}>Haku ja pisteytys</button>
          <button role="tab" className="btn small" aria-selected={tab === "vastaukset"} aria-pressed={tab === "vastaukset"} onClick={() => setParams({ tab: "vastaukset" })}>Vakiovastaukset</button>
          <button role="tab" className="btn small" aria-selected={tab === "projektit"} aria-pressed={tab === "projektit"} onClick={() => setParams({ tab: "projektit" })}>Projektit</button>
          <button role="tab" className="btn small" aria-selected={tab === "tyyli"} aria-pressed={tab === "tyyli"} onClick={() => setParams({ tab: "tyyli" })}>Kirjoitustyyli</button>
        </div>
      </div>
      {tab === "osaaminen" ? <SkillsTab /> : tab === "haku" ? <SearchTab /> : tab === "projektit" ? <ProjectsTab /> : tab === "tyyli" ? <StyleTab /> : <AnswersTab />}
    </section>
  );
}
