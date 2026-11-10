// Laajennuksen tausta: käynnistää palkin kuvakkeesta ja välittää kysymykset omalle koneelle (127.0.0.1).
// Portaalin sivu ei voi itse kutsua paikallista palvelinta, joten kutsut kulkevat tätä kautta.
const API = "http://127.0.0.1:8765/api/assist/";

function badge(tabId, text, title) {
  chrome.action.setBadgeBackgroundColor({ tabId, color: text === "ON" ? "#059669" : "#b42318" });
  chrome.action.setBadgeText({ tabId, text });
  chrome.action.setTitle({ tabId, title });
}

// activeTab: laajennus pääsee vain sivulle, jolla kuvaketta painettiin
async function start(tab) {
  const inject = (target) => chrome.scripting.executeScript({ target, files: ["overlay.js"] });
  try {
    await inject({ tabId: tab.id, allFrames: true }).catch(() => inject({ tabId: tab.id }));   // vieraat kehykset voivat estää
    badge(tab.id, "ON", "Työhaku-avustin on käynnissä tällä sivulla");
  } catch (e) {
    console.error("Työhaku-avustin:", e);
    badge(tab.id, "!", `Avustin ei käynnisty tällä sivulla: ${e.message}`);
  }
}
chrome.action.onClicked.addListener(start);

async function call(path, body) {
  const { key = "" } = await chrome.storage.local.get("key");
  let r;
  try {
    r = await fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "jobhunter", "X-Jobhunter-Key": key },
      body: JSON.stringify(body),
    });
  } catch {
    return { error: "Jobhunter ei vastaa. Onko se käynnissä?", code: "network" };
  }
  const data = await r.json().catch(() => ({}));
  return r.ok ? data : { error: data.error || `Virhe ${r.status}`, code: data.code };
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "setKey") {
    chrome.storage.local.set({ key: msg.key }).then(() => reply({ ok: true }));
  } else if (msg.type === "getJob") {
    chrome.storage.local.get("job").then((v) => reply({ job: v.job || "" }));
  } else if (msg.type === "setJob") {
    chrome.storage.local.set({ job: msg.job }).then(() => reply({ ok: true }));
  } else {
    call(msg.type, msg.body).then(reply);
  }
  return true;   // vastaus tulee asynkronisesti
});
