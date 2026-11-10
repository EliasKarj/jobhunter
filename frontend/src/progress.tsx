// Edistymisen näyttäminen kaikissa odotuksissa.
// 1) TopProgress: ohut palkki sivun yläreunassa, kun mikä tahansa palvelinkutsu kestää yli 0,4 s (api.ts → track).
// 2) ProgressBar: todellinen edistyminen (value 0–1), kun backend kertoo sen, muuten aika-arvio.
//    Arvio opitaan: kunkin toiminnon (id) toteutunut kesto tallennetaan selaimeen, ja seuraava palkki käyttää sitä.
import { useEffect, useRef, useState } from "react";
import { subscribe } from "./inflight";
import { useAiStatus } from "./lib";

export function TopProgress() {
  const [n, setN] = useState(0);
  const [show, setShow] = useState(false);
  useEffect(() => subscribe(setN), []);
  useEffect(() => {
    if (!n) { setShow(false); return; }
    const t = window.setTimeout(() => setShow(true), 400);   // nopeat kutsut eivät välky
    return () => window.clearTimeout(t);
  }, [n]);
  return <div className={"top-progress" + (show ? " on" : "")} role="progressbar" aria-hidden={!show} aria-label="Ladataan" />;
}

// ------------------------------------------------------------ opittu arvio

const KEY = "progress-estimates";

function learned(id: string): number | null {
  try { const v = JSON.parse(localStorage.getItem(KEY) || "{}")[id]; return typeof v === "number" ? v : null; } catch { return null; }
}

function learn(id: string, secs: number) {
  try {
    const all = JSON.parse(localStorage.getItem(KEY) || "{}");
    all[id] = all[id] ? Math.round((all[id] * 0.6 + secs * 0.4) * 10) / 10 : secs;   // liukuva keskiarvo
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch { /* yksityinen tila: käytetään oletusarviota */ }
}

const fmt = (s: number) => (s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, "0")} s`);

/**
 * value annettu: todellinen edistyminen. Muuten arvio: palkki etenee kohti 95 %:a arvioidun keston mukaan eikä
 * koskaan väitä valmista ennen kuin työ on valmis. id: toiminnon tunniste, jonka kesto opitaan.
 */
export function ProgressBar({ label, id, estimate = 30, llm, value, detail, compact }: {
  label: string; id?: string; estimate?: number; llm?: [claude: number, local: number]; value?: number | null;
  detail?: string; compact?: boolean;
}) {
  const start = useRef(Date.now());
  const [now, setNow] = useState(Date.now());
  const ai = useAiStatus();
  const provider = ai.data?.llm.provider || "";
  // kielimallitoiminnoissa kesto opitaan mallikohtaisesti (Claude on paljon nopeampi kuin paikallinen malli)
  if (llm) {
    estimate = provider === "claude" ? llm[0] : llm[1];
    if (id) id = `${id}:${provider}`;
  }
  const est = (id && learned(id)) || estimate;
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 250);
    return () => {
      window.clearInterval(t);
      const secs = (Date.now() - start.current) / 1000;
      if (id && secs > 2) learn(id, secs);
    };
  }, [id]);
  const elapsed = (now - start.current) / 1000;
  const real = value !== undefined && value !== null;
  const p = real ? Math.max(0, Math.min(1, value!)) : Math.min(0.95, 1 - Math.exp((-2.3 * elapsed) / est));
  const left = Math.max(0, est - elapsed);
  const info = real
    ? `${Math.round(p * 100)} %`
    : elapsed < est ? `arvio noin ${fmt(left)} jäljellä` : "kestää arvioitua kauemmin";
  return (
    <div className={"progress" + (compact ? " compact" : "")} role="progressbar" aria-label={label}
         aria-valuemin={0} aria-valuemax={100} aria-valuenow={real ? Math.round(p * 100) : undefined}
         aria-valuetext={real ? `${Math.round(p * 100)} %` : `${fmt(elapsed)} kulunut, ${info}`}>
      <div className="progress-head">
        <span className="progress-label">{label}</span>
        <span className="progress-time mono">{fmt(elapsed)} · {info}</span>
      </div>
      <div className={"progress-track" + (real ? "" : " estimated")}><i style={{ width: `${p * 100}%` }} /></div>
      {detail && <div className="progress-detail">{detail}</div>}
    </div>
  );
}

/** "Arvioidaan sopivuutta 120/500" -> 0.24 (backendin tilaviesteistä). */
export function ratioFrom(message?: string | null): number | null {
  const m = message?.match(/(\d+)\s*\/\s*(\d+)/);
  return m && +m[2] > 0 ? +m[1] / +m[2] : null;
}
