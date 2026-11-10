// Leijuva palkki hakulomakkeen kentän vieressä: näyttää ehdotetun vastauksen, josta sen voi lukea ja kopioida.
// Lukee vain kentän otsikon, ei kenttään kirjoitettua tekstiä. Ei koskaan lähetä lomaketta.
(() => {
  if (window.__tyohakuAvustin) return window.__tyohakuAvustin();   // kuvaketta painettiin uudelleen: näytä palkki

  const send = (type, body) => chrome.runtime.sendMessage({ type, body });
  const SKIP = new Set(["password", "hidden", "file", "checkbox", "radio", "submit", "button", "reset", "image", "range", "color"]);

  // ---------------------------------------------------------- kysymyksen tunnistus

  const clean = (t) => (t || "").replace(/\s+/g, " ").replace(/\s*[*:]\s*$/, "").trim();

  function questionFor(el) {
    const byIds = (attr) => (el.getAttribute(attr) || "").split(/\s+/).filter(Boolean)
      .map((id) => document.getElementById(id)?.innerText || "").join(" ");
    const labelled = clean(byIds("aria-labelledby")) || clean(el.getAttribute("aria-label"));
    if (labelled) return labelled;
    if (el.labels && el.labels[0]) return clean(el.labels[0].innerText);
    const wrap = el.closest("label");
    if (wrap) return clean(wrap.innerText);
    const legend = el.closest("fieldset")?.querySelector("legend");
    if (legend) return clean(legend.innerText);
    // lähin edeltävä teksti: monessa portaalissa kysymys on tavallinen div kentän yläpuolella
    let node = el;
    for (let depth = 0; node && depth < 4; depth++, node = node.parentElement) {
      for (let p = node.previousElementSibling; p; p = p.previousElementSibling) {
        const t = clean(p.innerText);
        if (t && t.length < 300) return t;
      }
    }
    return clean(el.getAttribute("placeholder")) || clean(el.getAttribute("name"));
  }

  // pudotusvalikot (Workdayn "Miten kuulit meistä?") eivät ota vapaata vastausta
  const isPicker = (el) => /^(combobox|listbox|spinbutton)$/.test(el.getAttribute("role") || "") || el.hasAttribute("aria-haspopup")
    || el.hasAttribute("aria-autocomplete") || el.hasAttribute("readonly");
  const isField = (el) => el instanceof HTMLElement && !el.closest("[data-tyohaku]") && !isPicker(el) && (
    el.tagName === "TEXTAREA" || el.isContentEditable || (el.tagName === "INPUT" && !SKIP.has((el.type || "text").toLowerCase())));

  // ---------------------------------------------------------- palkki (Shadow DOM: portaalin tyylit eivät vaikuta)

  const host = document.createElement("div");
  host.setAttribute("data-tyohaku", "");
  host.style.cssText = "position:fixed;z-index:2147483647;top:0;left:0;display:none";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>
    :host { all: initial; }
    .bar { width: 340px; max-height: 420px; display: flex; flex-direction: column; box-sizing: border-box; background: #13161b; color: #e6e8eb;
      border: 1px solid #2f3440; border-radius: 10px; box-shadow: 0 10px 32px rgb(0 0 0 / .45); font: 13px/1.45 system-ui, "Segoe UI", sans-serif; }
    .top { display: flex; align-items: center; gap: 6px; padding: 7px 10px; border-bottom: 1px solid #262a33; cursor: move; user-select: none; }
    .brand { font-weight: 600; flex: 1; }
    .brand i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; background: #34d399; margin-right: 6px; }
    button { font: inherit; color: #e6e8eb; background: #1f232b; border: 1px solid #2f3440; border-radius: 6px; padding: 5px 10px; cursor: pointer; }
    button:hover { background: #262b35; }
    button:focus-visible, select:focus-visible, textarea:focus-visible, input:focus-visible { outline: 2px solid #34d399; outline-offset: 1px; }
    button.primary { background: #34d399; border-color: #34d399; color: #062b1f; font-weight: 600; }
    button.x { background: none; border: 0; padding: 0 4px; font-size: 16px; line-height: 1; color: #9aa1ad; }
    select, input { width: 100%; box-sizing: border-box; font: inherit; color: #e6e8eb; background: #171a20; border: 1px solid #2f3440; border-radius: 6px; padding: 4px 6px; }
    .body { padding: 8px 10px; display: flex; flex-direction: column; gap: 7px; overflow: hidden; min-height: 0; }
    .q { color: #9aa1ad; font-size: 12px; }
    .src { font-size: 11.5px; color: #6ee7b7; }
    .text { white-space: pre-wrap; overflow-y: auto; max-height: 190px; background: #171a20; border-radius: 6px; padding: 7px 9px; }
    .warn { font-size: 12px; color: #fcd34d; }
    .bad { font-size: 12px; color: #fca5a5; }
    .row { display: flex; gap: 6px; flex-wrap: wrap; }
    .hint { color: #9aa1ad; font-size: 12.5px; }
    [hidden] { display: none !important; }
  </style>
  <div class="bar" role="dialog" aria-label="Työhaku-avustin">
    <div class="top"><span class="brand"><i></i>Työhaku</span><span class="q" id="prog"></span><button class="x" id="close" aria-label="Piilota palkki">×</button></div>
    <div class="body">
      <select id="job" aria-label="Hakemus"></select>
      <div class="q" id="q"></div>
      <div class="hint" id="hint" hidden></div>
      <div class="src" id="src" hidden></div>
      <div class="text" id="text" tabindex="0" hidden></div>
      <div class="bad" id="bad" hidden></div>
      <div class="warn" id="warn" hidden></div>
      <div class="row" id="keyrow" hidden><input id="key" placeholder="Liitä avain: Jobhunter → Asetukset" aria-label="Laajennusavain"><button id="savekey">Tallenna</button></div>
      <div class="row">
        <button class="primary" id="copy" hidden>Kopioi</button>
        <button id="insert" hidden>Lisää kenttään</button>
        <button id="gen" hidden>Kirjoita ehdotus</button>
      </div>
    </div>
  </div>`;
  document.documentElement.appendChild(host);
  const $ = (id) => root.getElementById(id);
  const show = (id, on) => { $(id).hidden = !on; };
  const say = (id, text) => { $(id).textContent = text || ""; show(id, !!text); };

  let field = null, question = "", answer = "", job = "", muted = false, moved = false, seq = 0;

  function place() {
    if (!field || moved) return;
    const r = field.getBoundingClientRect();
    const w = 340, gap = 10, vw = innerWidth, vh = innerHeight;
    let left = r.right + gap;
    if (left + w > vw - 8) left = r.left - w - gap;       // oikealla ei tilaa: vasemmalle
    let top = r.top;
    if (left < 8) { left = Math.max(8, Math.min(r.left, vw - w - 8)); top = r.bottom + gap; }   // ei sivuillakaan: alle
    const h = host.offsetHeight || 260;
    host.style.left = `${left}px`;
    host.style.top = `${Math.max(8, Math.min(top, vh - h - 8))}px`;
  }

  function render(res) {
    const s = res.suggestions?.[0];
    answer = s?.text || "";
    const paras = answer.split(/\n\s*\n/).filter((p) => p.trim()).length;
    const size = answer ? ` · ${answer.length} merkkiä${paras > 1 ? `, ${paras} kappaletta (vieritä)` : ""}` : "";
    say("src", s ? (s.source === "Kielimalli" ? "Kielimallin ehdotus" : `${s.source}${s.label ? ": " + s.label : ""}`) + size : "");
    say("text", answer);
    say("hint", res.hint || "");
    const why = [...new Set(s?.reasons || [])].join(" · ");
    say("bad", s?.flagged ? `${s.flagged} väitettä ilman lähdettä CV:ssä${why ? ": " + why : ""}` : "");
    say("warn", s?.check && !s.flagged ? "Lue läpi: osa väitteistä kannattaa tarkistaa." : (s?.style || []).length ? "Tyylihuomio: " + s.style.join(", ") : "");
    show("copy", !!answer); show("insert", !!answer);
    show("gen", !!res.can_generate);
    place();
  }

  function fail(res) {
    render({ suggestions: [], hint: res.error });
    show("keyrow", res.code === "key");
  }

  // ---------------------------------------------------------- vastaukset etukäteen
  // Lomake luetaan kerralla: valmiit vastaukset haetaan heti, ja avoimet kysymykset kirjoitetaan taustalla yksi
  // kerrallaan (paikallinen malli tekee yhtä asiaa kerrallaan). Kun kenttään tullaan, vastaus on yleensä jo valmis.

  const cache = new Map();   // kysymys -> { res, state: "ready" | "queued" | "writing" }
  const queue = [];
  let working = false;

  const longField = (el) => el.tagName === "TEXTAREA" || el.isContentEditable;
  const LONG_FORM = /vapaamuotoi|hakemuksesi|hakemusteksti|saatekirj|motivaatiokirj|cover letter|motivation letter|personal statement|application letter/i;

  // Saman lomakkeen muut kysymykset ja niihin jo kirjoitetut vastaukset: palvelin jakaa projektit kenttien
  // kesken, jotta rekrytoija ei lue samaa projektia ja samoja lukuja jokaisessa vastauksessa.
  function formContext(q) {
    const others = [...cache.keys()].filter((x) => x !== q && cache.get(x).auto);
    const written = [];
    for (const [x, c] of cache) {
      const s = c.res?.suggestions?.[0];
      if (x !== q && c.state === "ready" && s?.text && s.source === "Kielimalli") written.push({ question: x, answer: s.text });
    }
    return { others, written };
  }
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };

  function progress() {
    const open = [...cache.values()].filter((c) => c.auto);
    const ready = open.filter((c) => c.state === "ready").length;
    $("prog").textContent = open.length ? `Valmiina ${ready}/${open.length}` : "";
  }

  function showCurrent(q) {
    if (q !== question || muted) return;
    const c = cache.get(q);
    if (!c) return;
    if (c.state === "ready") return c.res.error ? fail(c.res) : render(c.res);
    const ahead = queue.indexOf(q) + 1;
    render({ suggestions: [], hint: c.state === "writing" ? "Kirjoitetaan vastausta… (10–30 s)" : `Jonossa, ${ahead}. vuorossa. Vastaus tulee tähän itsestään.` });
  }

  async function lookup(q, auto) {
    if (cache.has(q)) return cache.get(q);
    const entry = { res: null, state: "ready", auto: false };
    cache.set(q, entry);
    entry.res = await send("suggest", { question: q, url: job });
    const r = entry.res;
    if (auto && job && !r.error && r.can_generate && !r.suggestions?.length) {
      entry.state = "queued"; entry.auto = true;
      // vapaamuotoinen hakemus kirjoitetaan viimeisenä: se näkee, mitä muihin kenttiin on jo kirjoitettu
      const firstLong = queue.findIndex((x) => LONG_FORM.test(x));
      if (!LONG_FORM.test(q) && firstLong >= 0) queue.splice(firstLong, 0, q); else queue.push(q);
      work();
    }
    return entry;
  }

  async function work() {
    if (working) return;
    working = true;
    for (let q = queue.shift(); q; q = queue.shift()) {
      const entry = cache.get(q);
      if (!entry) continue;              // hakemus vaihtui
      const forJob = job;
      entry.state = "writing";
      showCurrent(q); progress();
      const res = await send("generate", { question: q, url: forJob, ...formContext(q) });
      if (forJob !== job) continue;
      entry.res = res.error ? res : { suggestions: [res], can_generate: false };
      entry.state = "ready";
      showCurrent(q); progress();
    }
    working = false;
  }

  function scan() {
    const fields = [...document.querySelectorAll('textarea, input, [contenteditable="true"]')].filter((el) => isField(el) && visible(el));
    for (const el of fields) {
      const q = questionFor(el);
      // lyhyet kentät (postinumero yms.) eivät mene kielimallille itsestään; niihin saa ehdotuksen painikkeesta
      if (q) lookup(q, longField(el)).then(progress);
    }
  }

  function restart() {
    cache.clear();
    queue.length = 0;
    scan();
  }

  async function ask(el) {
    const my = ++seq;
    field = el; question = questionFor(el);
    if (muted) return;
    host.style.display = "block";
    say("q", question || "Kysymystä ei tunnistettu");
    if (!cache.has(question)) render({ suggestions: [], hint: "Haetaan…" });
    await lookup(question, longField(el));
    if (my === seq) showCurrent(question);   // muuten käyttäjä siirtyi jo toiseen kenttään
  }

  async function loadJobs() {
    const res = await send("context", { title: document.title, host: location.hostname });
    if (res.error) return fail(res);
    show("keyrow", false);
    const saved = (await chrome.runtime.sendMessage({ type: "getJob" })).job;
    job = res.items.some((i) => i.url === saved) ? saved : res.guess || "";
    const sel = $("job");
    sel.textContent = "";
    sel.append(new Option("Valitse hakemus…", ""));
    for (const i of res.items) sel.append(new Option(`${i.company || ""}: ${i.title || ""}${i.package ? "" : " (ei pakettia)"}`.slice(0, 70), i.url));
    sel.value = job;
  }

  // ---------------------------------------------------------- toiminnot

  $("job").addEventListener("change", (e) => {
    job = e.target.value;
    chrome.runtime.sendMessage({ type: "setJob", job });
    restart();
    if (field) ask(field);
  });
  $("close").addEventListener("click", () => { muted = true; host.style.display = "none"; });
  $("savekey").addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "setKey", key: $("key").value.trim() });
    await loadJobs();
    restart();
    if (field) ask(field);
  });
  $("copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(answer); } catch {
      const t = document.createElement("textarea");
      t.value = answer; root.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
    }
    $("copy").textContent = "Kopioitu";
    setTimeout(() => { $("copy").textContent = "Kopioi"; }, 1400);
  });
  $("insert").addEventListener("click", () => {
    if (!field) return;
    field.focus();
    // Ensisijaisesti kuin käyttäjä liittäisi tekstin (insertText): kappalejaot säilyvät ja kehykset (React ym.)
    // näkevät muutoksen. Pelkkä value-setter katkaisi joissain portaaleissa tekstin ensimmäiseen tyhjään riviin.
    let ok = false;
    if (field.isContentEditable) {
      document.execCommand("selectAll", false);
      ok = document.execCommand("insertText", false, answer);
    } else {
      field.select();
      ok = document.execCommand("insertText", false, answer);
      if (!ok) {   // vanhat selaimet: natiivi setter, jotta React huomaa muutoksen
        const proto = field.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, "value").set.call(field, answer);
        field.dispatchEvent(new Event("input", { bubbles: true }));
        field.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
    // tarkistus: päätyikö koko teksti kenttään (portaali voi rajata pituutta tai rivinvaihtoja)
    setTimeout(() => {
      const got = (field.isContentEditable ? field.innerText : field.value || "").replace(/\s+/g, " ").trim();
      const want = answer.replace(/\s+/g, " ").trim();
      if (got.length + 5 < want.length) {
        say("warn", `Kenttään meni vain ${got.length}/${want.length} merkkiä. Kopioi teksti ja liitä se itse, tai tarkista kentän pituusraja.`);
      }
    }, 300);
  });
  $("gen").addEventListener("click", () => {   // jonon kärkeen: tätä kysymystä odotetaan nyt
    const entry = cache.get(question);
    if (!entry || entry.state !== "ready") return;
    if (!job) return say("hint", "Valitse ensin hakemus ylhäältä.");
    entry.state = "queued";
    queue.unshift(question);
    showCurrent(question);
    work();
  });

  // palkin raahaus yläreunasta
  root.querySelector(".top").addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    const r = host.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
    const move = (m) => { moved = true; host.style.left = `${m.clientX - dx}px`; host.style.top = `${m.clientY - dy}px`; };
    const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
  });

  document.addEventListener("focusin", (e) => {
    const el = e.composedPath()[0];
    if (isField(el) && el !== field) { muted = false; ask(el); }
  }, true);
  addEventListener("scroll", place, true);
  addEventListener("resize", place);

  // monisivuiset lomakkeet: uudet kentät poimitaan, kun portaali vaihtaa näkymää
  let timer = 0;
  new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(scan, 700); })
    .observe(document.documentElement, { childList: true, subtree: true });

  // palkki näkyy heti kuvakkeen painalluksesta: käyttäjä näkee, että avustin käynnistyi ja tarvitseeko se jotain
  function showIdle() {
    if (window !== window.top) return;     // upotetuissa kehyksissä palkki tulee vasta kenttää klikattaessa
    muted = false;
    host.style.display = "block";
    if (field) return ask(field);
    host.style.left = `${Math.max(8, innerWidth - 356)}px`;
    host.style.top = "16px";
    say("q", "");
  }
  window.__tyohakuAvustin = showIdle;
  showIdle();
  if (!field) render({ suggestions: [], hint: "Käynnistetään…" });

  loadJobs().then(() => {
    scan();
    const a = document.activeElement;
    if (isField(a)) return ask(a);
    if (!$("keyrow").hidden || field) return;
    const ok = !$("hint").textContent.includes("ei vastaa");
    if (ok) render({ suggestions: [], hint: job ? "Valmis. Klikkaa lomakkeen kenttää." : "Valmis. Valitse hakemus ylhäältä ja klikkaa kenttää." });
  });
})();
