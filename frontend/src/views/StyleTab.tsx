// Profiili → Kirjoitustyyli: harjoitusilmoitusten luonnokset, jotka käyttäjä muokkaa omiksi versioikseen (rag/stylesamples.py).
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api";
import { ErrorNotice, Loading, useToast } from "../components";

interface Sample {
  id: string; lang: "fi" | "en"; company: string; city: string; title: string; ad: string; question: string;
  generated: string; reasons: string[]; mine: string; updated: string;
}

function Card({ s }: { s: Sample }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [text, setText] = useState(s.mine || s.generated);
  const [busy, setBusy] = useState(false);
  const dirty = text !== (s.mine || s.generated);
  const save = async () => {
    setBusy(true);
    try {
      await post("/api/style/save", { id: s.id, mine: text });
      qc.invalidateQueries({ queryKey: ["style"] });
      toast("Oma versio tallennettu");
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  return (
    <article className="pkg-section" style={{ display: "grid", gap: 8 }}>
      <div className="section-head" style={{ marginBottom: 0 }}>
        <div>
          <span className="kicker">{s.title} · {s.company} · {s.city}{s.lang === "en" ? " · englanti" : ""}</span>
          <h3 style={{ margin: "2px 0 0", fontSize: 15 }}>{s.question}</h3>
        </div>
        {s.updated ? <span className="tag good">Muokattu</span> : <span className="tag">Muokkaamatta</span>}
      </div>
      <details><summary className="muted" style={{ fontSize: 13, cursor: "pointer" }}>Keksitty ilmoitus</summary>
        <p style={{ fontSize: 13.5, margin: "6px 0 0" }}>{s.ad}</p></details>
      <details><summary className="muted" style={{ fontSize: 13, cursor: "pointer" }}>Mallin alkuperäinen luonnos</summary>
        <p style={{ fontSize: 13.5, margin: "6px 0 0", whiteSpace: "pre-wrap" }}>{s.generated}</p>
        {s.reasons.length > 0 && <p className="stale" style={{ margin: "4px 0 0" }}>Tarkistus: {s.reasons.join(" · ")}</p>}</details>
      <label className="field">Sinun versiosi: muokkaa sellaiseksi, jonka lähettäisit itse
        <textarea rows={Math.min(16, Math.max(5, Math.ceil(text.length / 90) + 2))} value={text}
                  onChange={(e) => setText(e.target.value)} /></label>
      <div className="actions" style={{ marginTop: 0 }}>
        <button className="btn primary small" onClick={save} disabled={busy || (!dirty && !!s.updated)}>Tallenna oma versio</button>
        {dirty && <button className="btn ghost small" onClick={() => setText(s.mine || s.generated)}>Peru muutokset</button>}
        <span className="muted" style={{ fontSize: 12.5 }}>{words} sanaa</span>
      </div>
    </article>
  );
}

interface Profile { rules: string[]; samples: number; created: string; model: string }

function ProfileBox({ profile, edited }: { profile: Profile | null; edited: number }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const rebuild = async () => {
    setBusy(true);
    try { await post("/api/style/profile", {}); qc.invalidateQueries({ queryKey: ["style"] }); toast("Tyyliprofiili päivitetty"); }
    catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <section className="pkg-section">
      <div className="section-head" style={{ marginBottom: 6 }}>
        <h2 style={{ margin: 0 }}>Tyyliprofiili</h2>
        <button className="btn small" onClick={rebuild} disabled={busy || !edited}>{busy ? "Päivitetään…" : profile ? "Päivitä muokkauksista" : "Tee tyyliprofiili"}</button>
      </div>
      {profile ? (
        <>
          <p className="muted" style={{ fontSize: 13, margin: "0 0 6px" }}>Tehty {profile.samples} muokatusta näytteestä {profile.created.slice(0, 16).replace("T", " ")}.
            Malli saa nämä ohjeet ja 1–2 omaa tekstiäsi jokaiseen vastaukseen, vapaamuotoiseen hakemukseen ja saatekirjeeseen.</p>
          <ul className="points" style={{ gap: 3 }}>{profile.rules.map((r, i) => <li key={i} style={{ fontSize: 13.5 }}>{r}</li>)}</ul>
          <p className="muted" style={{ fontSize: 12.5, margin: "6px 0 0" }}>Päivitys tekee profiilin uudelleen kaikista muokkauksistasi (noin 0,03 $).
            Tarkista ohjeet päivityksen jälkeen: ne eivät saa sallia osaamista, jota CV:ssä ei ole.</p>
        </>
      ) : <p className="muted" style={{ fontSize: 13.5, margin: 0 }}>Tyyliprofiilia ei ole vielä tehty.</p>}
    </section>
  );
}

export default function StyleTab() {
  const q = useQuery({ queryKey: ["style"], queryFn: () => get<{ samples: Sample[]; profile: Profile | null }>("/api/style") });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNotice error={q.error} retry={() => q.refetch()} />;
  const samples = q.data!.samples;
  const done = samples.filter((s) => s.updated).length;
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <section className="pkg-section">
        <h2 style={{ marginTop: 0 }}>Kirjoitustyyli</h2>
        <p style={{ fontSize: 14, margin: 0 }}>Alla on mallin luonnoksia <b>keksittyihin</b> harjoitusilmoituksiin. Muokkaa jokainen sellaiseksi,
          jonka lähettäisit itse: sanavalinnat, virkkeiden pituus, aloitus ja lopetus, ja poista mitä et sanoisi. Luonnoksen ja
          sinun versiosi erot kertovat, miten kirjoitat. Niistä tehdään tyyliprofiili, jota malli käyttää jatkossa oikeissa hakemuksissa.
          Omia tekstejä käytetään vain tyylin mallina: faktat tulevat edelleen vain CV:stä.</p>
        <p className="muted" style={{ fontSize: 13, margin: "6px 0 0" }}>Muokattu {done}/{samples.length}.</p>
      </section>
      <ProfileBox profile={q.data!.profile} edited={done} />
      {samples.length === 0 && <div className="empty">Harjoitusluonnoksia ei ole vielä tehty.</div>}
      {samples.map((s) => <Card key={s.id} s={s} />)}
    </div>
  );
}
