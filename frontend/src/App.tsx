import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "./api";
import { HelpDialog, Loading, useToast } from "./components";
import { useAiStatus, useJobs, useKeys, useProjectSummary, useTracking } from "./lib";
import { ProgressBar, ratioFrom, TopProgress } from "./progress";
import Dashboard from "./views/Dashboard";

const JobView = lazy(() => import("./views/JobView"));
const Chat = lazy(() => import("./views/Chat"));
const PackageEditor = lazy(() => import("./views/PackageEditor"));
const Tracking = lazy(() => import("./views/Tracking"));
const Evaluation = lazy(() => import("./views/Evaluation"));
const Settings = lazy(() => import("./views/Settings"));
const Profile = lazy(() => import("./views/Profile"));
const Companies = lazy(() => import("./views/Companies"));
const CvView = lazy(() => import("./views/CvView"));

interface RefreshStatus { running: boolean; message: string; error: string | null; warning: string | null }

function Sidebar() {
  const jobs = useJobs();
  const tracking = useTracking();
  const ai = useAiStatus();
  const toast = useToast();
  const qc = useQueryClient();
  const refresh = useQuery({ queryKey: ["refresh"], queryFn: () => get<RefreshStatus>("/api/status"),
    refetchInterval: (q) => (q.state.data?.running ? 3000 : 60_000) });
  const wasRunning = useRef(false);
  useEffect(() => {
    const r = refresh.data;
    if (!r) return;
    if (wasRunning.current && !r.running) {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      toast(r.error ? `Haku epäonnistui: ${r.error}` : "Haku valmis");
    }
    wasRunning.current = r.running;
  }, [refresh.data, qc, toast]);
  const n = jobs.data?.rows.filter((r) => r.status === "ok").length;
  const upd = jobs.data?.updated ? new Date(jobs.data.updated) : null;
  const rem = tracking.data?.reminders.length || 0;
  const companies = useQuery({ queryKey: ["companies"], staleTime: 300_000,
    queryFn: () => get<{ companies: { days_since_check: number | null }[] }>("/api/companies") });
  const coDue = (companies.data?.companies || []).filter((c) => c.days_since_check === null || c.days_since_check > 7).length;
  const projNew = useProjectSummary().data?.new.length || 0;
  const llm = ai.data?.llm;
  const llmOk = llm && (llm.provider === "claude" ? llm.key_set : llm.reachable && llm.model_available !== false);
  const start = async () => { await post("/api/refresh", {}); refresh.refetch(); };
  return (
    <aside className="side" aria-label="Sivupalkki">
      <a className="brand" href="/"><i aria-hidden="true">T</i><span>Työhaku</span></a>
      <nav className="nav" aria-label="Osastot">
        <NavLink to="/" end>Ilmoitukset {n !== undefined && <span className="count">{n.toLocaleString("fi-FI")}</span>}</NavLink>
        <NavLink to="/kysy">Kysy</NavLink>
        <NavLink to="/seuranta">Seuranta {rem > 0 && <span className="count due" aria-label={`${rem} muistutusta`}>{rem}</span>}</NavLink>
        <NavLink to="/yritykset">Yritykset {coDue > 0 && <span className="count" title="Rekrysivu tarkistamatta yli 7 päivää">{coDue}</span>}</NavLink>
        <NavLink to={projNew ? "/profiili?tab=projektit" : "/profiili"}>Profiili {projNew > 0 &&
          <span className="count due" title="Uusia projektiehdotuksia odottaa hyväksyntääsi" aria-label={`${projNew} uutta projektiehdotusta`}>{projNew}</span>}</NavLink>
        <NavLink to="/evaluointi">Evaluointi</NavLink>
        <NavLink to="/asetukset">Asetukset</NavLink>
      </nav>
      <div className="side-foot">
        {llm && (
          <span className="row" title="Kielimalli">
            <span className={"dot " + (llmOk ? "ok" : "bad")} aria-hidden="true" />
            {llm.provider === "ollama" ? `${llm.model} paikallisesti` : llm.model}
          </span>
        )}
        {ai.data && <span>Pisteytys: {{ keyword: "avainsanat", semantic: "semanttinen", hybrid: `hybridi ${ai.data.scoring.w_keyword}/${ai.data.scoring.w_semantic}` }[ai.data.scoring.mode]}</span>}
        {upd && <span>Päivitetty {upd.toLocaleDateString("fi-FI")} klo {upd.toLocaleTimeString("fi-FI", { hour: "2-digit", minute: "2-digit" })}</span>}
        {refresh.data?.running
          ? <div role="status"><ProgressBar compact id="refresh" estimate={180} label={refresh.data.message || "Haetaan…"}
                                             value={ratioFrom(refresh.data.message)} /></div>
          : <div className="row" style={{ gap: 6 }}>
              <button className="btn small" onClick={start}>Päivitä haku</button>
              <button className="btn small ghost" onClick={() => window.dispatchEvent(new Event("help"))} aria-label="Näppäinoikotiet"><kbd>?</kbd></button>
            </div>}
      </div>
    </aside>
  );
}

export default function App() {
  const nav = useNavigate();
  const [help, setHelp] = useState(false);
  const g = useRef(0);
  useEffect(() => {
    const on = () => setHelp(true);
    window.addEventListener("help", on);
    return () => window.removeEventListener("help", on);
  }, []);
  // g + kirjain siirtyy osastoon (g e ilmoitukset, g k kysy, g s seuranta, g v evaluointi, g a asetukset)
  const go = (path: string) => () => { if (Date.now() - g.current < 1200) { g.current = 0; nav(path); } };
  useKeys({
    "?": () => setHelp(true),
    g: () => { g.current = Date.now(); },
    e: go("/"), k: go("/kysy"), s: go("/seuranta"), y: go("/yritykset"), p: go("/profiili"), v: go("/evaluointi"), a: go("/asetukset"),
  });
  return (
    <div className="shell">
      <TopProgress />
      <a href="#main" className="sr">Siirry sisältöön</a>
      <Sidebar />
      <div className="content">
        <main id="main">
          <Suspense fallback={<div style={{ paddingTop: 20 }}><Loading /></div>}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/ilmoitus" element={<JobView />} />
              <Route path="/kysy" element={<Chat />} />
              <Route path="/hakemus" element={<PackageEditor />} />
              <Route path="/seuranta" element={<Tracking />} />
              <Route path="/evaluointi" element={<Evaluation />} />
              <Route path="/asetukset" element={<Settings />} />
              <Route path="/profiili" element={<Profile />} />
              <Route path="/yritykset" element={<Companies />} />
              <Route path="/cv" element={<CvView />} />
              <Route path="*" element={<div className="empty">Sivua ei löydy.<p><a href="/">Ilmoituksiin</a></p></div>} />
            </Routes>
          </Suspense>
        </main>
      </div>
      <HelpDialog open={help} onClose={() => setHelp(false)} />
    </div>
  );
}
