"""Hakemusseurannan tilat, sallitut siirtymät ja muistutukset. Puhdasta logiikkaa ilman tiedostoja (testattava)."""
from __future__ import annotations

import datetime as dt

STATUSES = ["interested", "drafting", "applied", "interview", "offer", "rejected", "no_response", "withdrawn"]
LABELS = {"interested": "Kiinnostava", "drafting": "Hakemus työn alla", "applied": "Haettu",
          "interview": "Haastattelu", "offer": "Tarjous", "rejected": "Hylätty",
          "no_response": "Ei vastausta", "withdrawn": "Peruttu"}
FINAL = {"offer", "rejected", "withdrawn"}
ACTIVE_SENT = {"applied", "interview", "no_response"}   # hakemus on lähtenyt, odotetaan työnantajaa
LEGACY = {"planned": "interested"}                       # vanhan seurannan tilat

# Eteenpäin ja sivulle sallitut siirtymät. Peruminen ja hylkäys onnistuvat aina ennen lopputilaa.
# Edelliseen tilaan palaaminen (kumoa) on aina sallittu, ks. can_transition.
TRANSITIONS: dict[str, set[str]] = {
    "interested": {"drafting", "applied", "rejected", "withdrawn"},
    "drafting": {"interested", "applied", "rejected", "withdrawn"},
    "applied": {"interview", "offer", "rejected", "no_response", "withdrawn"},
    "interview": {"offer", "rejected", "no_response", "withdrawn"},
    "no_response": {"interview", "offer", "rejected", "withdrawn"},  # vastaus voi tulla myöhässä
    "offer": {"withdrawn", "rejected"},   # tarjous perutaan / jää toteutumatta
    "rejected": set(),
    "withdrawn": set(),
}
FOLLOW_UP_DAYS = 14


def normalize(status: str | None) -> str:
    s = LEGACY.get(status or "", status or "interested")
    return s if s in STATUSES else "interested"


def previous(app: dict) -> str | None:
    hist = [h.get("status") for h in app.get("history", [])]
    return normalize(hist[-2]) if len(hist) >= 2 else None


def can_transition(app: dict, new: str) -> bool:
    cur = normalize(app.get("status"))
    if new not in STATUSES or new == cur:
        return False
    return new in TRANSITIONS[cur] or new == previous(app)


def transition(app: dict, new: str, now: str | None = None) -> dict:
    """Vaihda tila ja kirjaa historiaan. ValueError, jos siirtymä ei ole sallittu."""
    if not can_transition(app, new):
        cur = normalize(app.get("status"))
        raise ValueError(f"Tilasta \"{LABELS[cur]}\" ei voi siirtyä tilaan \"{LABELS.get(new, new)}\"")
    now = now or dt.datetime.now().isoformat(timespec="seconds")
    app["status"] = new
    app.setdefault("history", []).append({"status": new, "at": now})
    if new == "applied" and not app.get("applied_date"):
        app["applied_date"] = now[:10]
    app["updated"] = now
    return app


def last_change(app: dict) -> str:
    hist = app.get("history") or []
    return (hist[-1].get("at") if hist else None) or app.get("applied_date") or app.get("updated") or ""


def reminders(apps: list[dict], today: dt.date | None = None) -> list[dict]:
    """Muistutukset: haettu ilman kuulumisia FOLLOW_UP_DAYS päivään, erääntyvät seuraavat askeleet ja hakuajat."""
    today = today or dt.date.today()
    out = []
    for a in apps:
        st = normalize(a.get("status"))
        base = {"id": a.get("id"), "title": a.get("title", ""), "company": a.get("company", ""), "url": a.get("url", "")}
        if st == "applied":
            try:
                since = dt.date.fromisoformat(last_change(a)[:10])
            except ValueError:
                since = None
            if since and (today - since).days >= FOLLOW_UP_DAYS:
                out.append({**base, "kind": "follow_up", "date": since.isoformat(), "days": (today - since).days,
                            "text": f"Ei kuulunut {(today - since).days} päivään: kysy perään tai merkitse \"Ei vastausta\""})
        nd = a.get("next_date")
        if nd and st not in FINAL and nd <= (today + dt.timedelta(days=2)).isoformat():
            out.append({**base, "kind": "next_step", "date": nd, "text": a.get("next_step") or "Seuraava askel"})
        dl = a.get("deadline")
        if dl and st in ("interested", "drafting") and today.isoformat() <= dl <= (today + dt.timedelta(days=5)).isoformat():
            out.append({**base, "kind": "deadline", "date": dl, "text": "Hakuaika päättyy"})
    return sorted(out, key=lambda r: r["date"])


# ------------------------------------------------------------ mikä toimii

CV_KIND_LABEL = {"panel": "Paneelin hyväksymä CV", "panel_review": "Paneeli, jäi tarkistettavaksi",
                 "tailored": "Räätälöity ilman paneelia", "none": "Ei räätälöityä CV:tä"}


def cv_kind(versions: dict | None) -> str:
    """Hakemuksen ilmoitukselle tallennettu CV (cvs.json: {lang: cv}): paras löytyvä versio."""
    if not versions:
        return "none"
    st = {(c.get("panel") or {}).get("status") for c in versions.values() if isinstance(c, dict)}
    return "panel" if "approved" in st else "panel_review" if "needs_review" in st else "tailored"


def outcomes(apps: list[dict], cvs: dict) -> list[dict]:
    """Lähetetyt hakemukset CV-tavan mukaan: vastaukset ja haastattelut. Seulontakattavuus paneeliajoista."""
    groups: dict[str, dict] = {}
    for a in apps:
        hist = {h.get("status") for h in a.get("history", [])} | {normalize(a.get("status"))}
        if not (hist & {"applied", "interview", "offer", "rejected", "no_response"} and
                (a.get("applied_date") or hist & {"applied", "interview", "offer"})):
            continue
        versions = cvs.get(a.get("url") or "") or {}
        kind = cv_kind(versions)
        g = groups.setdefault(kind, {"kind": kind, "label": CV_KIND_LABEL[kind], "applied": 0, "responded": 0,
                                     "interview": 0, "coverage": []})
        g["applied"] += 1
        g["responded"] += bool(hist & {"interview", "offer", "rejected"})
        g["interview"] += bool(hist & {"interview", "offer"})
        for c in versions.values():
            cov = (((c.get("panel") or {}).get("screening") or {}).get("coverage") or {}).get("score") if isinstance(c, dict) else None
            if isinstance(cov, (int, float)):
                g["coverage"].append(cov)
    out = []
    for k in CV_KIND_LABEL:
        if k in groups:
            g = groups[k]
            cov = g.pop("coverage")
            g["coverage"] = round(sum(cov) / len(cov), 2) if cov else None
            g["interview_rate"] = round(100 * g["interview"] / g["applied"])
            out.append(g)
    return out
