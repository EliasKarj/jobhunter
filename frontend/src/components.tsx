import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, get } from "./api";
import { daysUntil, shortDate, useAiStatus } from "./lib";
import type { Row } from "./types";

// ------------------------------------------------------------ toast

const ToastCtx = createContext<(msg: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((m: string) => {
    setMsg(m);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(null), 2600);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div aria-live="polite" className="sr">{msg}</div>
      {msg && <div className="toast" role="status">{msg}</div>}
    </ToastCtx.Provider>
  );
}

// ------------------------------------------------------------ virheet ja tilat

const HINTS: Record<string, ReactNode> = {
  index_missing: <>Aja terminaalissa <code>python -m rag ingest</code>. Ensimmäinen indeksointi kestää noin 2–3 tuntia.</>,
  llm: <>Tarkista kielimalli <Link to="/asetukset">asetuksista</Link>. Paikallinen malli vaatii, että Ollama on käynnissä.</>,
  network: <>Käynnistä jobhunter (<code>python webapp.py</code>) tai ajastettu tehtävä uudelleen.</>,
};

export function ErrorNotice({ error, title = "Jokin meni pieleen", retry }: { error: unknown; title?: string; retry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  return (
    <div className="notice error" role="alert">
      <h3>{title}</h3>
      <p>{e?.message || String(error)}</p>
      {e && HINTS[e.code] && <p>{HINTS[e.code]}</p>}
      {retry && <p><button className="btn small" onClick={retry}>Yritä uudelleen</button></p>}
    </div>
  );
}

/** Koko sovelluksen tila: puuttuva indeksi tai kielimalli näkyy heti, ei vasta virheenä. */
export function SystemBanner({ need }: { need: ("llm" | "index")[] }) {
  const { data, error } = useAiStatus();
  if (error) return <ErrorNotice error={error} title="Palvelimeen ei saada yhteyttä" />;
  if (!data) return null;
  const out: ReactNode[] = [];
  if (need.includes("index") && (!data.index.exists || data.index.active_ok === false))
    out.push(<div key="i" className="notice error" role="alert"><h3>Semanttinen indeksi puuttuu</h3><p>{HINTS.index_missing}</p></div>);
  if (need.includes("llm")) {
    const l = data.llm;
    if (l.provider === "ollama" && l.reachable === false)
      out.push(<div key="o" className="notice error" role="alert"><h3>Ollama ei vastaa</h3>
        <p>Käynnistä Ollama-sovellus tai vaihda Claude-providerin <Link to="/asetukset">asetuksista</Link>.</p></div>);
    else if (l.provider === "ollama" && l.model_available === false)
      out.push(<div key="m" className="notice" role="alert"><h3>Mallia {l.model} ei ole ladattu</h3>
        <p>Aja <code>ollama pull {l.model}</code> tai valitse toinen malli <Link to="/asetukset">asetuksista</Link>.</p></div>);
    else if (l.provider === "claude" && !l.key_set)
      out.push(<div key="k" className="notice error" role="alert"><h3>Claude API -avain puuttuu</h3>
        <p>Lisää avain <Link to="/asetukset">asetuksissa</Link> tai vaihda paikalliseen malliin.</p></div>);
  }
  return <>{out}</>;
}

export function Loading({ lines = 6, label = "Ladataan" }: { lines?: number; label?: string }) {
  return (
    <div aria-busy="true" aria-label={label} style={{ display: "grid", gap: 10 }}>
      {Array.from({ length: lines }, (_, i) => <div key={i} className="skeleton" style={{ height: i % 3 ? 16 : 22, width: `${92 - (i * 13) % 40}%` }} />)}
    </div>
  );
}

// ------------------------------------------------------------ pisteet

function Score({ v, k, main, title }: { v: number | null | undefined; k: string; main?: boolean; title: string }) {
  const na = v === null || v === undefined;
  return (
    <div className={"score" + (main ? " main" : "") + (na ? " na" : "")} title={title}>
      <span className="k">{k}</span>
      <span className="v">{na ? "–" : Math.round(v!)}</span>
      <span className="bar" aria-hidden="true"><i style={{ width: `${na ? 0 : Math.max(2, Math.min(100, v!))}%` }} /></span>
    </div>
  );
}

export function Scores({ row, mode }: { row: Row; mode?: string }) {
  const kw = row.score_keyword ?? row.score;
  const label = `Pisteet: avainsana ${Math.round(kw)}, semanttinen ${row.score_semantic == null ? "ei indeksissä" : Math.round(row.score_semantic)}, hybridi ${row.score_hybrid == null ? "–" : Math.round(row.score_hybrid)}`;
  return (
    <div className="scores" role="img" aria-label={label}>
      <Score k="Avain" v={kw} main={mode === "keyword"} title="Avainsanapisteet (alue, osaaminen, taso, ala)" />
      <Score k="Sem." v={row.score_semantic} main={mode === "semantic"} title="Semanttinen vastaavuus profiiliin (bge-m3)" />
      <Score k="Hybr." v={row.score_hybrid} main={mode === "hybrid" || !mode} title="Yhdistetty piste" />
    </div>
  );
}

export function DeadlineTag({ iso }: { iso?: string | null }) {
  const d = daysUntil(iso);
  if (d === null) return null;
  if (d < 0) return <span className="tag">Hakuaika päättyi</span>;
  return <span className={"tag" + (d <= 3 ? " stamp" : "")}>{d === 0 ? "Päättyy tänään" : `Haku ${shortDate(iso)} (${d} pv)`}</span>;
}

// ------------------------------------------------------------ sivupaneeli (chatin viitteet)

export function Drawer({ url, onClose }: { url: string | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const { data, error, isLoading } = useQuery({
    queryKey: ["job", url], enabled: !!url,
    queryFn: () => get<{ row: Row }>("/api/job", { url: url! }),
  });
  useEffect(() => {
    if (!url) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const on = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", on);
    return () => { window.removeEventListener("keydown", on); prev?.focus(); };
  }, [url, onClose]);
  if (!url) return null;
  const r = data?.row;
  return (
    <>
      <div className="drawer-back" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Ilmoitus" tabIndex={-1} ref={ref}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <span className="kicker">Lähde</span>
          <button className="btn small ghost" onClick={onClose}>Sulje <kbd>Esc</kbd></button>
        </div>
        {isLoading && <Loading />}
        {error && <ErrorNotice error={error} />}
        {r && (
          <>
            <h2>{r.job.title}</h2>
            <div className="byline"><span className="co">{r.job.company}</span><span>{r.job.location}</span>
              {r.job.detail?.posted && <span className="mono">julkaistu {shortDate(r.job.detail.posted)}</span>}</div>
            <div className="actions">
              <Link className="btn primary" to={`/ilmoitus?url=${encodeURIComponent(r.job.url)}`}>Avaa ilmoitus</Link>
              <a className="btn" href={r.job.url} target="_blank" rel="noreferrer">Alkuperäinen ↗</a>
            </div>
            <div className="ad-text" style={{ marginTop: 16 }}>{(r.job.text || "").slice(0, 4000)}</div>
          </>
        )}
      </aside>
    </>
  );
}

// ------------------------------------------------------------ ohje

export function HelpDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  const keys: [string, string][] = [
    ["j / k", "seuraava / edellinen ilmoitus"], ["Enter", "avaa valittu"], ["/", "haku"],
    ["s", "tallenna (kiinnostava)"], ["x", "piilota (ei kiinnosta) / palauta"], ["1–8", "vaihda seurannan tila"],
    ["o", "avaa alkuperäinen ilmoitus"], ["e", "sopivuusselitys (ilmoitussivulla)"], ["h", "räätälöi CV"],
    ["g e / g k / g s", "etusivu / kysy / seuranta"], ["?", "tämä ohje"], ["Esc", "sulje"],
  ];
  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="help-title">
      <h2 id="help-title">Näppäinoikotiet</h2>
      <div className="help-grid">{keys.flatMap(([k, v]) => [<span key={k}><kbd>{k}</kbd></span>, <span key={k + "-v"}>{v}</span>])}</div>
      <p className="muted" style={{ fontSize: 14 }}>Tilat numeroilla: 1 Kiinnostava, 2 Työn alla, 3 Haettu, 4 Haastattelu, 5 Tarjous, 6 Hylätty, 7 Ei vastausta, 8 Peruttu.</p>
      <form method="dialog"><button className="btn">Sulje</button></form>
    </dialog>
  );
}
