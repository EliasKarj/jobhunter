"""Suomenkielisen IT-tekstin termit: mitkä pidetään englanniksi, mitkä suomennetaan.

Perusta (mitattu 11/2026): 326 suomenkielistä IT-painotteista työpaikkailmoitusta (miten työnantajat kirjoittavat)
ja Helsingin yliopiston Full Stack Open -kurssin suomenkielinen materiaali (miten kehittäjät kirjoittavat).
Esimerkkejä: backend 53 % / taustajärjestelmä 41 % / taustapalvelu 4 %; frontend 87 %; full stack 100 %;
stack 97 % / teknologiapino 2 %; kontti 96 % / container 3 %; pilvi 88 %; rajapinta 76 % / API 23 %;
testiautomaatio 97 %; käyttöönotto 74 % / deploy 2 %; CI/CD-putki 84 % / pipeline 15 %; ketterä 81 % / agile 2 %;
framework 62 % / sovelluskehys 4 %; kurssilla branch 81 %, linter 94 %, middleware 100 %, debug 100 %.
"""
from __future__ import annotations

import re

PROMPT = """TERMIT (suomalaisten IT-ilmoitusten ja suomenkielisen kehittäjätekstin käytännön mukaan):
- Pidä englanniksi, koska alalla Suomessa kirjoitetaan näin: backend (myös taustajärjestelmä käy), frontend,
  full stack, stack, DevOps, CI/CD, pull request, merge request, commit, branch ja feature branch, linter,
  middleware, framework, dashboard, open source, hackathon, repo, debuggaus. Taivuta suomeksi: "backendin",
  "pull requesteja", "React-frontend", "CI/CD-putki".
- Käytä suomea, kun vakiintunut vastine on yleisempi: pilvipalvelut (ei cloud), rajapinta (API lyhenteenä käy:
  REST-rajapinta), kontti ja kontitus (ei container), testiautomaatio, yksikkötesti, käyttöönotto ja julkaisu
  (ei deployaus), ketterä kehitys, integraatio, koneoppiminen, tekoäly, ominaisuus, versionhallinta,
  käyttöliittymä, sulautettu, mikropalvelu, skaalautuvuus, katselmointi, vianetsintä, valvonta, tiketti,
  työnkulku, refaktorointi.
- Älä keksi suomennoksia, joita alalla ei käytetä: ei "taustapalvelu", "täyden pinon", "vetopyyntö",
  "ominaisuushaara", "sovelluskehys", "väliohjelmisto", "teknologiapino", "säiliö", "koontinäyttö"."""

# Pakotetut suomennokset, joita alan teksteissä ei juuri käytetä: (lauseke, luontevampi muoto)
AVOID: list[tuple[str, str]] = [
    (r"taustapalvelu\w*", "backend"),
    (r"täyden pinon", "full stack"),
    (r"vetopyyn\w*", "pull request"),
    (r"yhdistämispyyn\w*", "pull request / merge request"),
    (r"ominaisuushaar\w*", "feature branch"),
    (r"sovelluskehy\w*", "framework"),
    (r"väliohjelmist\w*", "middleware"),
    (r"teknologiapino\w*", "stack tai tekniikat"),
    (r"(?<![a-zåäö])säiliö\w*", "kontti"),
    (r"koontinäyt\w*|kojelau\w*", "dashboard"),
    (r"(?<![a-zåäö])deployaa\w*|deployau\w*|deployat\w*", "käyttöönotto tai julkaisu"),
]


def unnatural(text: str) -> list[str]:
    """Pakotetut suomennokset tekstissä, muodossa 'taustapalvelun → backend' (toistot kerran)."""
    out: list[str] = []
    for pat, better in AVOID:
        for m in re.finditer(pat, text, re.I):
            item = f"{m.group(0)} → {better}"
            if item.lower() not in (x.lower() for x in out):
                out.append(item)
    return out
