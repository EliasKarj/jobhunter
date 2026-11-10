import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { copy } from "../actions";
import { ErrorNotice, Loading, useToast } from "../components";
import { useAiStatus, useTitle } from "../lib";

type Theme = "auto" | "light" | "dark";

function applyTheme(t: Theme) {
  if (t === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  try { if (t === "auto") localStorage.removeItem("theme"); else localStorage.setItem("theme", t); } catch { /* */ }
}

export default function Settings() {
  useTitle("Asetukset");
  const toast = useToast();
  const qc = useQueryClient();
  const st = useAiStatus();
  const extKey = useQuery({ queryKey: ["ext-key"], queryFn: () => get<{ key: string }>("/api/assist/key"),
    enabled: st.isSuccess && !st.data?.remote });
  const [v, setV] = useState<Record<string, string>>({});
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme) || "auto");

  useEffect(() => {
    if (!st.data || Object.keys(v).length) return;
    const d = st.data;
    setV({
      LLM_PROVIDER: d.llm.provider,
      OLLAMA_MODEL: d.llm.provider === "ollama" ? d.llm.model : "gemma3:12b",
      CLAUDE_MODEL: d.llm.provider === "claude" ? d.llm.model : "claude-haiku-5-5",
      SCORING_MODE: d.scoring.mode, W_SEMANTIC: String(d.scoring.w_semantic), HYBRID_METHOD: d.scoring.method,
    });
  }, [st.data, v]);

  if (st.isLoading) return <Loading />;
  if (st.error) return <ErrorNotice error={st.error} retry={() => st.refetch()} />;
  const d = st.data!;
  const wsem = Number(v.W_SEMANTIC ?? d.scoring.w_semantic);

  const save = async (extra: Record<string, unknown> = {}) => {
    setBusy(true); setErr(null);
    const values: Record<string, string> = { ...v, W_KEYWORD: String(Math.round((1 - wsem) * 100) / 100), W_SEMANTIC: String(wsem) };
    if (key) values.ANTHROPIC_API_KEY = key;
    try {
      await post("/api/ai/settings", { values, ...extra });
      setKey("");
      toast("Asetukset tallennettu .env-tiedostoon");
      qc.invalidateQueries({ queryKey: ["ai-status"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });   // pisteytys lasketaan taustalla uudelleen
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };

  return (
    <form className="settings" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <div className="section-head"><h1 style={{ fontSize: 28 }}>Asetukset</h1>
        <button className="btn primary" disabled={busy || d.remote}>{busy ? "Tallennetaan…" : "Tallenna"}</button></div>
      {d.remote && <div className="notice" role="status"><h3>Etäyhteys</h3>
        <p>Olet yhteydessä Tailscalen kautta. Kielimalli- ja hakuasetuksia, API-avainta ja selainlaajennuksen avainta voi muuttaa
          ja katsoa vain koneen ääressä. Muu sovellus toimii normaalisti.</p></div>}
      {err ? <ErrorNotice error={err} /> : null}

      <section aria-labelledby="s-llm">
        <h2 id="s-llm" style={{ fontSize: 21 }}>Kielimalli</h2>
        <div className="radio-row" role="radiogroup" aria-label="Provider">
          <label><input type="radio" name="p" checked={v.LLM_PROVIDER === "ollama"} onChange={() => setV({ ...v, LLM_PROVIDER: "ollama" })} />
            Paikallinen (Ollama) <span className={"dot " + (d.llm.provider === "ollama" ? (d.llm.reachable ? "ok" : "bad") : "")} /></label>
          <label><input type="radio" name="p" checked={v.LLM_PROVIDER === "claude"} onChange={() => setV({ ...v, LLM_PROVIDER: "claude" })} />
            Claude API</label>
        </div>
        {v.LLM_PROVIDER === "ollama" ? (
          <>
            <label className="field">Malli
              <select value={v.OLLAMA_MODEL} onChange={(e) => setV({ ...v, OLLAMA_MODEL: e.target.value })}>
                {[...new Set([v.OLLAMA_MODEL, ...(d.llm.models || [])])].filter(Boolean).map((m) => <option key={m}>{m}</option>)}
              </select></label>
            <p className="muted" style={{ fontSize: 14 }}>Suositus <span className="mono">gemma3:12b</span>: paras suomi vertailussa (README), mahtuu 12 Gt:n näytönohjaimeen.
              Kaikki pysyy omalla koneella.{d.llm.provider === "ollama" && d.llm.reachable === false && <b style={{ color: "var(--stamp)" }}> Ollama ei vastaa juuri nyt.</b>}</p>
          </>
        ) : (
          <>
            <label className="field">Malli
              <select value={v.CLAUDE_MODEL} onChange={(e) => setV({ ...v, CLAUDE_MODEL: e.target.value })}>
                <option value="claude-haiku-5-5">claude-haiku-5-5 (halvin, $0,10 / $0,50 per 1M tokenia)</option>
                <option value="claude-sonnet-5-5">claude-sonnet-5-5 ($2 / $10 per 1M tokenia)</option>
              </select></label>
            <label className="field">API-avain {d.llm.provider === "claude" && d.llm.key_set && <span className="tag good">asetettu</span>}
              <input type="password" autoComplete="off" value={key} placeholder={d.llm.key_set ? "•••••••• (jätä tyhjäksi, jos ei muutosta)" : "sk-ant-…"}
                     onChange={(e) => setKey(e.target.value)} /></label>
            <p className="notice" style={{ fontSize: 14 }}>Claude-providerilla CV:si ja ilmoitustekstit lähetetään Anthropicille. Avain tallennetaan vain omaan .env-tiedostoosi.</p>
          </>
        )}
      </section>

      <section aria-labelledby="s-score">
        <h2 id="s-score" style={{ fontSize: 21 }}>Pisteytys</h2>
        <div className="radio-row" role="radiogroup" aria-label="Pisteytystapa">
          {(["keyword", "semantic", "hybrid"] as const).map((m) => (
            <label key={m}><input type="radio" name="m" checked={v.SCORING_MODE === m} onChange={() => setV({ ...v, SCORING_MODE: m })} />
              {{ keyword: "Avainsanat", semantic: "Semanttinen", hybrid: "Hybridi" }[m]}</label>
          ))}
        </div>
        <label className="field">Semanttisen osuus hybridissä
          <div className="range-row">
            <span className="mono" style={{ fontSize: 13 }}>avain {(1 - wsem).toFixed(2)}</span>
            <input type="range" min={0} max={1} step={0.05} value={wsem} onChange={(e) => setV({ ...v, W_SEMANTIC: e.target.value })} aria-label="Semanttinen paino" />
            <span className="mono" style={{ fontSize: 13 }}>sem {wsem.toFixed(2)}</span>
          </div></label>
        <p className="muted" style={{ fontSize: 14 }}>Evaluoinnissa paras alue oli 0,4–0,6. Tallennus laskee pisteet uudelleen taustalla.</p>
      </section>

      <section aria-labelledby="s-index">
        <h2 id="s-index" style={{ fontSize: 21 }}>Aktiivinen indeksi</h2>
        {!d.index.exists ? <p className="notice error">Indeksiä ei ole. Aja <code>python -m rag ingest</code>.</p> : (
          <table className="data">
            <thead><tr><th>Indeksi</th><th>Malli</th><th className="n">Pala / limitys</th><th className="n">Ilmoituksia</th><th /></tr></thead>
            <tbody>{d.index.configs.map((c) => (
              <tr key={c.config_id} className={c.config_id === d.index.active ? "best" : ""}>
                <td className="mono" style={{ fontSize: 13 }}>{c.config_id}</td><td>{c.model}</td>
                <td className="n">{c.chunk_size} / {c.chunk_overlap}</td><td className="n">{c.jobs.toLocaleString("fi-FI")}</td>
                <td>{c.config_id === d.index.active ? "käytössä" :
                  <button type="button" className="btn small" onClick={() => save({ index: c.config_id })}>Ota käyttöön</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
        <p className="muted" style={{ fontSize: 14 }}>Uuden indeksin rakentaminen (eri malli tai palakoko) tehdään komentorivillä, esim.
          <code> python -m rag --chunk-size 500 --overlap 100 ingest</code>.</p>
      </section>

      <section aria-labelledby="s-look">
        <h2 id="s-look" style={{ fontSize: 21 }}>Ulkoasu</h2>
        <div className="radio-row" role="radiogroup" aria-label="Teema">
          {(["auto", "light", "dark"] as const).map((t) => (
            <label key={t}><input type="radio" name="t" checked={theme === t} onChange={() => { setTheme(t); applyTheme(t); }} />
              {{ auto: "Järjestelmän mukaan", light: "Vaalea", dark: "Tumma" }[t]}</label>
          ))}
        </div>
      </section>

      <section aria-labelledby="s-ext">
        <h2 id="s-ext" style={{ fontSize: 21 }}>Selainlaajennus</h2>
        <p style={{ fontSize: 14, margin: 0 }}>Työhaku-avustin näyttää hakulomakkeen kentän vieressä palkin, josta vastauksen voi lukea ja kopioida. Se ei koskaan lähetä lomaketta.</p>
        <ol style={{ fontSize: 14, margin: 0, paddingLeft: 20 }}>
          <li>Avaa Chromessa <code>chrome://extensions</code> ja ota kehittäjätila käyttöön.</li>
          <li>Valitse ”Lataa pakkaamaton” ja projektin kansio <code>extension</code>.</li>
          <li>Avaa hakulomake ja paina laajennuksen kuvaketta. Liitä avain palkkiin ensimmäisellä kerralla.</li>
        </ol>
        <div className="actions" style={{ marginTop: 0 }}>
          <code>{d.remote ? "näkyy vain koneen ääressä" : extKey.data?.key || "…"}</code>
          <button type="button" className="btn small" onClick={() => extKey.data && copy(extKey.data.key, toast)}>Kopioi avain</button>
        </div>
      </section>

      <section aria-labelledby="s-data">
        <h2 id="s-data" style={{ fontSize: 21 }}>Data ja profiili</h2>
        <div className="actions" style={{ marginTop: 0 }}>
          <a className="btn small" href="/api/export/ilmoitukset.csv">Ilmoitukset CSV</a>
          <a className="btn small" href="/api/export/hakemukset.csv">Hakemukset CSV</a>
          <button type="button" className="btn small" onClick={async () => { await post("/api/backup", {}); toast("Varmuuskopio tehty"); }}>Varmuuskopioi nyt</button>
        </div>
        <p style={{ fontSize: 14 }}>Osaaminen, hakusanat ja paikkakunnat: <Link to="/profiili">Profiili</Link>. Seurattavat yritykset: <Link to="/yritykset">Yritykset</Link>.</p>
      </section>
    </form>
  );
}
