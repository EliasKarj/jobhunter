import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { stream } from "../api";
import { Drawer, SystemBanner } from "../components";
import { safeStorage, shortDate, useTitle } from "../lib";
import type { Source } from "../types";
import { ProgressBar } from "../progress";

interface Turn {
  q: string;
  text: string;
  filters: string[];
  sources: Source[];
  done: boolean;
  found?: boolean;
  cited?: number[];
  uncited?: string[];
  issues?: { kind: string; from?: number; to?: number; quote?: string }[];
  error?: string;
  code?: string;
}

const [loadLog, saveLog] = safeStorage<Turn[]>("chat-log", []);
const SUGGEST = [
  "Mitkä Vaasan paikat vaativat Azurea mutta eivät ruotsia?",
  "Onko viime viikolla tullut junior Python -paikkoja Tampereella?",
  "Missä harjoittelupaikoissa käytetään Reactia tai Next.js:ää?",
  "Which positions in Espoo mention Docker and Kubernetes?",
];

/** Kevyt muotoilu: **lihavointi**, luettelot ja [n]-viitteet painikkeiksi. Lauseet ilman lähdettä aaltoviivalla. */
function Answer({ t, onCite }: { t: Turn; onCite: (n: number) => void }) {
  const valid = new Set(t.sources.map((s) => s.n));
  const uncited = new Set((t.uncited || []).map((u) => u.trim()));
  const inline = (line: string, key: string): ReactNode[] =>
    line.split(/(\*\*[^*]+\*\*|\[\d{1,2}\])/g).filter(Boolean).map((part, i) => {
      const m = part.match(/^\[(\d{1,2})\]$/);
      if (m) {
        const n = Number(m[1]);
        const s = t.sources.find((x) => x.n === n);
        return valid.has(n)
          ? <button key={key + i} className="cite" onClick={() => onCite(n)} aria-label={`Lähde ${n}: ${s?.title}`}>{n}</button>
          : null;
      }
      if (part.startsWith("**")) return <strong key={key + i}>{inline(part.slice(2, -2), `${key}${i}b`)}</strong>;   // viite lihavoinnin sisällä
      return <span key={key + i}>{part}</span>;
    });
  const blocks: ReactNode[] = [];
  let items: ReactNode[] = [];
  const flush = () => { if (items.length) { blocks.push(<ul key={"u" + blocks.length}>{items}</ul>); items = []; } };
  t.text.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (!line) { flush(); return; }
    const bullet = line.match(/^(?:[*\-•]|\d+\.)\s+(.*)$/);
    const body = bullet ? bullet[1] : line;
    const flagged = [...uncited].some((u) => body.includes(u.slice(0, 40)));
    const node = flagged ? <span className="uncited" title="Ei lähdeviitettä">{inline(body, `l${i}`)}</span> : inline(body, `l${i}`);
    if (bullet) items.push(<li key={i}>{node}</li>);
    else { flush(); blocks.push(<p key={i}>{node}</p>); }
  });
  flush();
  return <div className="answer">{blocks}{!t.done && <span className="caret" aria-hidden="true" />}</div>;
}

export default function Chat() {
  useTitle("Kysy");
  const [log, setLog] = useState<Turn[]>(loadLog);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [drawer, setDrawer] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { saveLog(log.filter((t) => t.done)); }, [log]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [log.length]);
  useEffect(() => { input.current?.focus(); }, []);

  const update = (fn: (t: Turn) => Turn) => setLog((l) => [...l.slice(0, -1), fn(l[l.length - 1])]);

  const ask = useCallback(async (question: string) => {
    const text = question.trim();
    if (!text || busy) return;
    const history = log.filter((t) => t.done && !t.error).slice(-3)
      .flatMap((t) => [{ role: "user", content: t.q }, { role: "assistant", content: t.text }]);
    setLog((l) => [...l, { q: text, text: "", filters: [], sources: [], done: false }]);
    setQ("");
    setBusy(true);
    const ctl = new AbortController();
    abort.current = ctl;
    try {
      await stream("/api/chat", { question: text, history }, (ev) => {
        if (ev.type === "filters") update((t) => ({ ...t, filters: ev.filters }));
        else if (ev.type === "sources") update((t) => ({ ...t, sources: ev.sources }));
        else if (ev.type === "delta") update((t) => ({ ...t, text: t.text + ev.text }));
        else if (ev.type === "done")   // backend korjaa viitteet: lopullinen teksti korvaa streamatun
          update((t) => ({ ...t, text: ev.answer && ev.found ? ev.answer : t.text, done: true, found: ev.found,
                           cited: ev.cited, uncited: ev.uncited, issues: ev.issues }));
        else if (ev.type === "error") update((t) => ({ ...t, done: true, error: ev.message, code: ev.code }));
      }, ctl.signal);
      update((t) => (t.done ? t : { ...t, done: true }));
    } catch (e) {
      const aborted = (e as Error).name === "AbortError";
      update((t) => ({ ...t, done: true, error: aborted ? undefined : (e as Error).message, text: t.text + (aborted ? " [keskeytetty]" : "") }));
    } finally {
      setBusy(false);
      abort.current = null;
      input.current?.focus();
    }
  }, [busy, log]);

  const openSource = (t: Turn, n: number) => setDrawer(t.sources.find((s) => s.n === n)?.url || null);

  return (
    <div className="chat">
      <section aria-labelledby="chat-title">
        <div className="section-head">
          <h1 id="chat-title" style={{ fontSize: 28 }}>Kysy ilmoituksista</h1>
          {log.length > 0 && <button className="btn small ghost" onClick={() => setLog([])} disabled={busy}>Tyhjennä keskustelu</button>}
        </div>
        <SystemBanner need={["llm", "index"]} />
        <div className="chat-log" aria-live="polite" aria-busy={busy}>
          {log.length === 0 && (
            <div className="suggest">
              <p className="muted">Vastaukset perustuvat vain haettuihin ilmoituksiin, ja jokainen väite viittaa lähteeseen.
                Kaupunki, julkaisupäivä ja kieltoehdot (”ei ruotsia”) tunnistetaan kysymyksestä.</p>
              {SUGGEST.map((s) => <button key={s} onClick={() => ask(s)}>{s}</button>)}
            </div>
          )}
          {log.map((t, i) => (
            <div key={i}>
              <div className="q"><span className="kicker">Kysymys</span>{t.q}</div>
              {t.filters.length > 0 && <div className="chips" aria-label="Suodattimet">{t.filters.map((f) => <span className="chip" key={f}>{f}</span>)}</div>}
              {!t.text && !t.done && (t.sources.length
                ? <ProgressBar compact id="chat-answer" llm={[5, 30]} label="Kirjoitetaan vastausta" />
                : <ProgressBar compact id="chat-search" estimate={8} label="Haetaan ilmoituksista" detail="Ensimmäinen haku käynnistyksen jälkeen lataa hakumallin (noin minuutti)." />)}
              {t.text && <Answer t={t} onCite={(n) => openSource(t, n)} />}
              {t.error && (
                <div className="notice error" role="alert">
                  <h3>{t.code === "index_missing" ? "Indeksi puuttuu" : t.code === "llm" ? "Kielimalli ei vastaa" : "Vastaus epäonnistui"}</h3>
                  <p>{t.error}</p>
                  <button className="btn small" onClick={() => ask(t.q)} disabled={busy}>Kysy uudelleen</button>
                </div>
              )}
              {t.done && t.found && (
                <>
                  {(t.uncited?.length || 0) > 0 && <p className="stale">{t.uncited!.length} lausetta ilman lähdeviitettä (aaltoviiva): tarkista ne itse.</p>}
                  {t.issues?.some((x) => x.kind === "fixed") && <p className="muted" style={{ fontSize: 13.5 }}>Viitenumero korjattu otsikon perusteella.</p>}
                  {t.issues?.filter((x) => x.kind === "quote_not_found").map((x, j) => (
                    <p key={j} className="stale">Lainausta ”{x.quote}” ei löydy viitatusta ilmoituksesta.</p>
                  ))}
                  <ol className="sources" aria-label="Lähteet">
                    {t.sources.map((s) => (
                      <li key={s.n} className={t.cited?.includes(s.n) ? "" : "uncited-src"}>
                        <span className="mono" style={{ color: "var(--stamp)" }}>[{s.n}]</span>
                        <span><button onClick={() => setDrawer(s.url)}>{s.title}</button>
                          <span className="muted"> · {s.company} · {s.city}{s.posted ? ` · ${shortDate(s.posted)}` : ""}</span>
                          {!t.cited?.includes(s.n) && <span className="muted"> · ei käytetty</span>}</span>
                      </li>
                    ))}
                  </ol>
                </>
              )}
            </div>
          ))}
          <div ref={end} />
        </div>
      </section>
      <aside className="hide-narrow" style={{ fontSize: 15 }}>
        <div className="section-head"><h2 style={{ fontSize: 18 }}>Miten haku toimii</h2></div>
        <p>Kysymyksestä päätellään suodattimet (kaupunki, ”viime viikolla”, lähde). Sen jälkeen ilmoitusten paloista haetaan sekä
          merkityksen (bge-m3) että tarkkojen sanojen (esim. <em>Azure</em>) mukaan, ja malli saa 8 parasta ilmoitusta.</p>
        <p>Viitenumerot avaavat ilmoituksen sivupaneeliin. Jos mikään ilmoitus ei vastaa kysymystä, malli sanoo sen eikä keksi.</p>
        <p><Link to="/asetukset">Kielimallin valinta →</Link></p>
      </aside>

      <div className="ask">
        <form onSubmit={(e) => { e.preventDefault(); ask(q); }}>
          <label htmlFor="ask" className="sr">Kysymys</label>
          <textarea id="ask" ref={input} rows={1} value={q} placeholder="Kysy esim. ”Mitkä Espoon paikat sopivat opiskelijalle?”"
                    onChange={(e) => setQ(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(q); } }} />
          {busy
            ? <button type="button" className="btn" onClick={() => abort.current?.abort()}>Keskeytä</button>
            : <button className="btn primary" disabled={!q.trim()}>Kysy</button>}
        </form>
      </div>
      <Drawer url={drawer} onClose={() => setDrawer(null)} />
    </div>
  );
}
