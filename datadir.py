"""Käyttäjän oma data yhdessä kansiossa, joka ei ole versionhallinnassa.

Kaikki henkilökohtainen (CV, profiili, osaaminen, hakemukset, CV-arkisto, välimuistit, varmuuskopiot, lokit,
evaluointimerkinnät) on kansiossa DATA. Oletus on <repo>/data (.gitignore: data/). Kansion voi siirtää kokonaan
repon ulkopuolelle ympäristömuuttujalla tai .env-rivillä JOBHUNTER_DATA=C:\\polku\\kansioon.

Repoon kuuluu vain koodi ja keksitty esimerkkihenkilö (examples/), jota testit ja demotila käyttävät.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
EXAMPLES = BASE / "examples"


def _from_env_file() -> str:
    """JOBHUNTER_DATA .env-tiedostosta (luetaan ennen muita asetuksia, koska datakansio tarvitaan heti)."""
    env = BASE / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "JOBHUNTER_DATA":
            return v.strip().strip('"').strip("'")
    return ""


import shutil  # noqa: E402
import sys  # noqa: E402

DEMO = "--demo" in sys.argv and not os.environ.get("JOBHUNTER_DATA")
# Demotila: oma kansio, johon kopioidaan keksitty esimerkkihenkilö (examples/). Oma data ei ole demossa mukana.
DATA = Path(os.environ.get("JOBHUNTER_DATA") or (BASE / "demo-data" if DEMO else None)
            or _from_env_file() or BASE / "data").expanduser().resolve()
DATA.mkdir(parents=True, exist_ok=True)
if DEMO:
    for name in ("cv.md", "profile.json", "skills.json"):
        if not (DATA / name).exists():
            shutil.copy(EXAMPLES / name, DATA / name)


def path(*parts: str) -> Path:
    """Polku datakansion sisällä, esim. path("results", "latest.json")."""
    return DATA.joinpath(*parts)
