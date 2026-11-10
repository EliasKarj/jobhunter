"""CV:n PDF arkistoon: koneelle asennettu Chrome (tai Edge) tulostaa sovelluksen CV-sivun ilman ikkunaa.

Sama sivu ja samat tulostustyylit kuin käyttäjän itse tulostamassa PDF:ssä (A4, ei ylä- ja alatunnisteita), joten
arkiston PDF on sama tiedosto, joka lähtisi työnantajalle. Mitään ei ladata verkosta: käytetään asennettua selainta
omalla tyhjällä profiililla (cache/pdf-profile), joka ei koske käyttäjän omaan selainprofiiliin.
"""
from __future__ import annotations
from datadir import DATA

import os
import shutil
import subprocess
import threading
import urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PROFILE = DATA / "cache" / "pdf-profile"
ENABLED = True            # testit kytkevät pois
_lock = threading.Lock()  # yksi selain kerrallaan

# Edge ei kelpaa ensisijaiseksi: taustalla käynnissä oleva Edge ottaa --headless-ajon itselleen eikä tulosta mitään.
CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def browser() -> str | None:
    env = os.environ.get("PDF_BROWSER", "").strip()
    if env:
        return env if Path(env).exists() else None
    for c in CANDIDATES:
        if Path(c).exists():
            return c
    return shutil.which("chrome") or shutil.which("chromium") or shutil.which("msedge")


def render(url: str, lang: str, out: Path, port: int = 8765, timeout: float = 90) -> str | None:
    """Tulosta /cv?url=..&lang=.. PDF:ksi. Palauttaa virheilmoituksen tai None, jos onnistui."""
    exe = browser()
    if not exe:
        return "Chromea tai Edgeä ei löytynyt (aseta PDF_BROWSER)"
    page = f"http://127.0.0.1:{port}/cv?url={urllib.parse.quote(url, safe='')}&lang={lang}"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.pdf")
    with _lock:
        try:
            subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                            "--disable-extensions", "--no-pdf-header-footer", f"--user-data-dir={PROFILE}",
                            "--virtual-time-budget=15000", f"--print-to-pdf={tmp}", page],
                           capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"PDF:n tulostus epäonnistui: {e}"
    if not tmp.exists() or tmp.stat().st_size < 2000:
        tmp.unlink(missing_ok=True)
        return "PDF jäi tyhjäksi (onko jobhunter käynnissä?)"
    tmp.replace(out)
    return None


def _port() -> int:
    """Käynnissä olevan palvelimen portti (webapp.CFG), ilman että moduulia tuodaan tässä."""
    import sys
    w = sys.modules.get("webapp")
    return int(getattr(w, "CFG", {}).get("port") or 8765) if w else 8765


def render_async(url: str, lang: str, out: Path, port: int | None = None) -> None:
    """Taustalla: tallennus ei odota selainta."""
    if not ENABLED:
        return
    port = port or _port()

    def run():
        err = render(url, lang, out, port)
        if err:
            print(f"! CV:n PDF ({out.name}): {err}")
    threading.Thread(target=run, daemon=True, name="cv-pdf").start()
