import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, get, post } from "../api";
import { copy } from "../actions";
import { ErrorNotice, Loading, SystemBanner, useToast } from "../components";
import { shortDate, useTitle } from "../lib";
import type { Claim, Draft, Section } from "../types";
import { ProgressBar } from "../progress";

/** Tekstikenttä, jonka taakse piirretään väitteiden korostukset (textarea ei osaa muotoilla tekstiä itse). */
function HighlightArea({ value, onChange, claims, active, label, onSave }: {
  value: string; onChange: (v: string) => void; claims: Claim[] | null; active: number | null; label: string; onSave: () => void;
}) {
  const ta = useRef<HTMLTextAreaElement>(null);
  const back = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const fit = () => {
      const el = ta.current;
      if (!el) return;
      el.style.height = "auto";
      el.style.height = el.scrollHeight + 2 + "px";
    };
    fit();
    document.fonts?.ready.then(fit);   // serif-fontin latautuminen muuttaa rivitystä
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [value]);
  const marks: ReactNode[] = [];
  let pos = 0;
  (claims || []).forEach((c, i) => {
    if (c.status === "ok" && active !== i) return;
    if (c.start < pos || c.end > value.length) return;
    marks.push(value.slice(pos, c.start));
    marks.push(<mark key={i} className={(c.status !== "ok" ? "st-" + c.status : "") + (active === i ? " on" : "")}>{value.slice(c.start, c.end)}</mark>);
    pos = c.end;
  });
  marks.push(value.slice(pos) + "\n");
  return (
    <div className="hl-wrap">
      <div className="hl-back" ref={back} aria-hidden="true">{marks}</div>
      <textarea ref={ta} value={value} aria-label={label} spellCheck lang="fi"
                onChange={(e) => onChange(e.target.value)}
                onScroll={(e) => { if (back.current) back.current.scrollTop = (e.target as HTMLTextAreaElement).scrollTop; }}
                onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); onSave(); } }} />
    </div>
  );
}

function SectionEditor({ s, text, onText, onSave, toast }: {
  s: Section; text: string; onText: (v: string) => void; onSave: () => void; toast: (m: string) => void;
}) {
  const [active, setActive] = useState<number | null>(null);
  const [all, setAll] = useState(false);
  const stale = text !== s.text;   // tarkistus koskee tallennettua tekstiä
  const claims = stale ? null : s.verify.claims;
  const shown = (s.verify.claims || []).map((c, i) => [c, i] as const).filter(([c]) => all || c.status !== "ok");
  const okCount = s.verify.claims.filter((c) => c.status === "ok").length;
  return (
    <section className="pkg-section" aria-labelledby={`h-${s.key}`}>
      <header>
        <h2 id={`h-${s.key}`}>{s.title}</h2>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          {s.key !== "salary" && (
            <span className="tally" aria-label="Tarkistuksen tulos">
              <span className="f">{s.verify.flagged} liputettu</span><span className="c">{s.verify.check} tarkista</span><span className="o">{okCount} ok</span>
            </span>
          )}
          <span className="mono muted" style={{ fontSize: 12 }}>{text.trim().split(/\s+/).filter(Boolean).length} sanaa</span>
          <button className="btn small" onClick={() => copy(text, toast)}>Kopioi</button>
        </div>
      </header>
      <HighlightArea value={text} onChange={onText} claims={claims} active={active} label={s.title} onSave={onSave} />
      {stale && s.key !== "salary" && <p className="stale">Tekstiä on muokattu: korostukset näkyvät taas, kun tallennat ja tarkistat.</p>}
      {!stale && s.style && s.style.length > 0 && (
        <p className="stale" style={{ marginTop: 8 }}>Tyylihuomio (kuulostaa tekoälyltä): {s.style.map((x) => `”${x}”`).join(", ")}. Muotoile omin sanoin.</p>
      )}
      {s.key !== "salary" && s.verify.claims.length > 0 && (
        <>
          <ul className="claims" style={{ marginTop: 10 }} aria-label={`${s.title}: väitteet`}>
            {shown.map(([c, i]) => (
              <li key={i} className={"st-" + c.status + (active === i ? " on" : "")} tabIndex={0}
                  onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)} onFocus={() => setActive(i)} onBlur={() => setActive(null)}>
                <div><strong>{c.status === "flag" ? "Liputettu: " : c.status === "check" ? "Tarkista: " : ""}</strong>{c.text}</div>
                {c.reasons.length > 0 && <div className="why">{c.reasons.join(" · ")}</div>}
                {c.source && (
                  <div className="src" title={c.source.text}>
                    Lähin lähde {c.source.id}{c.source.source_ref ? ` (${c.source.source_ref})` : c.source.type === "ad" ? " (ilmoitus)" : ""} ·
                    samankaltaisuus {c.source.sim.toFixed(2)}{c.source.text ? ` · ”${c.source.text.slice(0, 90).replace(/\s+/g, " ")}…”` : ""}
                  </div>
                )}
                {c.kind === "motivation" && <div className="why">Kiinnostuksen ilmaus, ei tosiasiaväite.</div>}
              </li>
            ))}
          </ul>
          <button className="btn small ghost" onClick={() => setAll((v) => !v)}>
            {all ? "Näytä vain liputetut ja tarkistettavat" : `Näytä kaikki väitteet (${s.verify.claims.length})`}</button>
        </>
      )}
    </section>
  );
}

export default function PackageEditor() {
  const [params] = useSearchParams();
  const url = params.get("url") || "";
  const toast = useToast();
  const qc = useQueryClient();
  const [lang, setLang] = useState<"" | "fi" | "en">("");
  const [busy, setBusy] = useState<null | "generate" | "save" | "verify">(null);
  const [err, setErr] = useState<unknown>(null);
  const [texts, setTexts] = useState<Record<string, string>>({});
  const draft = useQuery({
    queryKey: ["draft", url], enabled: !!url, retry: false,
    queryFn: () => get<Draft>("/api/drafts", { url }).catch((e) => {
      if (e instanceof ApiError && e.code === "not_found") return null;
      throw e;
    }),
  });
  const job = useQuery({ queryKey: ["job", url], enabled: !!url, queryFn: () => get<{ row: { job: { title: string; company: string } } }>("/api/job", { url }) });
  const answers = useQuery({ queryKey: ["answers"], queryFn: () => get<{ fields: { key: string; label: string; value: string }[] }>("/api/answers") });
  const filled = (answers.data?.fields || []).filter((f) => f.value.trim());
  const d = draft.data;
  useTitle(d ? `Hakemus: ${d.title}` : "Hakemuspaketti");

  useEffect(() => {
    if (d) setTexts(Object.fromEntries(d.sections.map((s) => [s.key, s.text])));
  }, [d]);
  const dirty = !!d && d.sections.some((s) => (texts[s.key] ?? s.text) !== s.text);
  useEffect(() => {
    const on = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", on);
    return () => window.removeEventListener("beforeunload", on);
  }, [dirty]);

  const generate = async () => {
    if (d && !confirm("Luodaanko luonnokset uudelleen? Nykyinen versio jää historiaan.")) return;
    setBusy("generate"); setErr(null);
    try {
      const r = await post<Draft>("/api/package", { url, lang: lang || undefined });
      qc.setQueryData(["draft", url], r);
      qc.invalidateQueries({ queryKey: ["apps"] });
      toast("Hakemuspaketti luotu");
    } catch (e) { setErr(e); } finally { setBusy(null); }
  };
  const save = async (verify: boolean) => {
    if (!d) return;
    setBusy(verify ? "verify" : "save"); setErr(null);
    try {
      const sections = d.sections.map((s) => ({ ...s, text: texts[s.key] ?? s.text }));
      const r = await post<Draft>("/api/drafts", { url, sections, verify });
      qc.setQueryData(["draft", url], r);
      toast(verify ? "Tallennettu ja tarkistettu" : "Tallennettu");
    } catch (e) { setErr(e); } finally { setBusy(null); }
  };
  const restore = () => {
    const prev = d?.history[0];
    if (prev) { setTexts(Object.fromEntries(prev.sections.map((s) => [s.key, s.text]))); toast(`Palautettu versio ${prev.at?.slice(0, 16).replace("T", " ")} – tallenna, jos haluat pitää sen`); }
  };

  if (!url) return <ErrorNotice error="Ilmoituksen osoite puuttuu" />;
  const title = d?.title || job.data?.row.job.title || "";
  const flagged = d?.sections.reduce((n, s) => n + s.verify.flagged, 0) || 0;

  return (
    <div className="pkg">
      <section aria-labelledby="pkg-title">
        <div style={{ padding: "16px 0 14px", borderBottom: "1px solid var(--line)", marginBottom: 16 }}>
          <span className="kicker">Hakemuspaketti</span>
          <h1 id="pkg-title" style={{ fontSize: 20, margin: "4px 0 0" }}>{title || "…"}</h1>
        </div>
        <SystemBanner need={["llm", "index"]} />
        {err ? <ErrorNotice error={err} title="Toiminto epäonnistui" /> : null}
        {draft.isLoading && <Loading lines={8} />}
        {draft.error && <ErrorNotice error={draft.error} retry={() => draft.refetch()} />}
        {busy === "generate" && (
          <div className="notice" role="status">
            <ProgressBar id="package" llm={[35, 90]} label="Kirjoitetaan luonnoksia ja tarkistetaan väitteitä"
                         detail="Saatekirje ja portaalivastaukset kirjoitetaan, minkä jälkeen jokainen väite tarkistetaan CV:tä vasten." /></div>
        )}
        {draft.isSuccess && !d && busy !== "generate" && (
          <div className="notice ok">
            <h3>Luonnoksia ei ole vielä tehty</h3>
            <p>Saatekirje ja vastaukset portaalikysymyksiin kirjoitetaan vain CV:n ja osaamisprofiilin tiedoista. Jokainen väite
              tarkistetaan, ja ne väitteet, joille ei löydy lähdettä, liputetaan. Palkkatoive jätetään sinulle.</p>
            <div className="actions">
              <label className="field" style={{ width: 200 }}>Kieli
                <select value={lang} onChange={(e) => setLang(e.target.value as typeof lang)}>
                  <option value="">Ilmoituksen kielen mukaan</option><option value="fi">Suomi</option><option value="en">Englanti</option>
                </select></label>
              <button className="btn primary" onClick={generate} disabled={!!busy} style={{ alignSelf: "end" }}>Luo hakemuspaketti</button>
            </div>
          </div>
        )}
        {d && d.sections.map((s) => (
          <SectionEditor key={s.key} s={s} text={texts[s.key] ?? s.text} toast={toast} onSave={() => save(true)}
                         onText={(v) => setTexts((t) => ({ ...t, [s.key]: v }))} />
        ))}
      </section>

      {d && (
        <aside className="margin" aria-label="Paketin toiminnot">
          <section>
            <h2>Tarkistus</h2>
            <p style={{ fontSize: 15 }}>{flagged === 0
              ? "Yhtään väitettä ei liputettu. Lue silti läpi: tarkistus tunnistaa tekniikat, luvut ja käsitteet, mutta ei kaikkea."
              : <><b style={{ color: "var(--stamp)" }}>{flagged} väitettä</b> mainitsee jotain, mitä CV:stä tai profiilista ei löydy. Poista tai korjaa ne ennen lähettämistä.</>}</p>
            <div className="actions" style={{ marginTop: 0 }}>
              <button className="btn primary" onClick={() => save(true)} disabled={!!busy}>
                {busy === "verify" ? "Tarkistetaan…" : "Tallenna ja tarkista"}</button>
              <button className="btn" onClick={() => save(false)} disabled={!!busy || !dirty}>Tallenna <kbd>Ctrl S</kbd></button>
            </div>
            {busy === "verify" && <ProgressBar compact id="package-verify" estimate={6} label="Tarkistetaan väitteet CV:tä vasten" />}
            {dirty && <p className="stale">Tallentamattomia muutoksia.</p>}
          </section>
          <section>
            <h2>Luonnos</h2>
            <table className="meta-table"><tbody>
              <tr><th>Kieli</th><td>{d.lang === "en" ? "englanti" : "suomi"}</td></tr>
              <tr><th>Malli</th><td className="mono" style={{ fontSize: 13 }}>{d.model}</td></tr>
              <tr><th>Luotu</th><td className="mono">{shortDate(d.created)}</td></tr>
              <tr><th>Muokattu</th><td className="mono">{d.updated?.slice(0, 16).replace("T", " ")}</td></tr>
              <tr><th>Versioita</th><td>{d.history.length + 1}</td></tr>
            </tbody></table>
            <div className="actions">
              <button className="btn small" onClick={() => copy(d.sections.map((s) => `${s.title}\n\n${texts[s.key] ?? s.text}`).join("\n\n\n"), toast)}>Kopioi kaikki</button>
              {d.history.length > 0 && <button className="btn small" onClick={restore}>Palauta edellinen versio</button>}
              <Link className="btn small" to={`/cv?url=${encodeURIComponent(url)}&lang=${d.lang}`}>Räätälöi CV</Link>
            </div>
            <div className="actions">
              <select value={lang} onChange={(e) => setLang(e.target.value as typeof lang)} aria-label="Kieli uudelle luonnokselle" style={{ width: "auto" }}>
                <option value="">Ilmoituksen kieli</option><option value="fi">Suomi</option><option value="en">Englanti</option>
              </select>
              <button className="btn small" onClick={generate} disabled={!!busy}>Luo uudelleen</button>
            </div>
          </section>
          <section>
            <h2>Vakiovastaukset</h2>
            {filled.length === 0 ? (
              <p className="muted" style={{ fontSize: 13, margin: 0 }}>Täytä aloituspäivä, palkkatoive, ajokortti ja linkit kerran kohdassa <Link to="/profiili?tab=vastaukset">Profiili → Vakiovastaukset</Link>.</p>
            ) : (
              <>
                <ul className="points" style={{ gap: 6 }}>
                  {filled.map((f) => (
                    <li key={f.key} style={{ paddingLeft: 0, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                      <span style={{ minWidth: 0 }}><b style={{ fontWeight: 600 }}>{f.label}</b><br /><span className="muted">{f.value.slice(0, 60)}</span></span>
                      <button className="btn small" onClick={() => copy(f.value, toast)} aria-label={`Kopioi: ${f.label}`}>Kopioi</button>
                    </li>
                  ))}
                </ul>
                <p style={{ fontSize: 12.5, marginBottom: 0 }}><Link to="/profiili?tab=vastaukset">Muokkaa</Link></p>
              </>
            )}
          </section>
          <section>
            <h2>Ilmoitus</h2>
            <p style={{ fontSize: 15 }}><Link to={`/ilmoitus?url=${encodeURIComponent(url)}`}>{d.title}</Link><br />
              <span className="muted">{d.company}</span></p>
            <p className="muted" style={{ fontSize: 13.5 }}>Luonnoksia ei lähetetä mihinkään. Kopioi osat portaaliin itse.</p>
          </section>
        </aside>
      )}
    </div>
  );
}
