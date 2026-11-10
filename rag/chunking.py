"""Pilkkominen: kappaleet ja luettelorivit ensin, liian pitkät rivit lauseittain, palat ahnaasti kokoon.

Pala on aina yhtenäinen pätkä siivottua tekstiä, joten (start, end) viittaa suoraan alkuperäiseen kohtaan.
Päällekkäisyys tehdään kokonaisina yksikköinä (rivi/lause), ettei vaatimuslista katkea kesken sanan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÅÄÖ0-9•\-])")


@dataclass(frozen=True)
class Chunk:
    text: str
    start: int
    end: int


def _units(text: str, size: int) -> list[tuple[int, int]]:
    """Jaa teksti yksiköiksi (start, end). Yksikkö on rivi tai, jos rivi on liian pitkä, lause tai sanaryhmä."""
    units: list[tuple[int, int]] = []
    pos = 0
    for line in text.split("\n"):
        start, end = pos, pos + len(line)
        pos = end + 1
        if not line.strip():
            continue
        if end - start <= size:
            units.append((start, end))
            continue
        s = start
        for m in _SENT.finditer(line):
            units.extend(_hard_split(text, s, start + m.start(), size))
            s = start + m.end()
        units.extend(_hard_split(text, s, end, size))
    return units


def _hard_split(text: str, start: int, end: int, size: int) -> list[tuple[int, int]]:
    """Yli palakoon menevä lause katkaistaan sanarajoilta."""
    out = []
    while end - start > size:
        cut = text.rfind(" ", start, start + size)
        if cut <= start:
            cut = start + size
        out.append((start, cut))
        start = cut + 1 if text[cut:cut + 1] == " " else cut
    if end > start:
        out.append((start, end))
    return out


def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> list[Chunk]:
    if overlap >= size:
        raise ValueError("overlap pitää olla pienempi kuin size")
    units = _units(text, size)
    chunks: list[Chunk] = []
    i = 0
    while i < len(units):
        j = i
        while j + 1 < len(units) and units[j + 1][1] - units[i][0] <= size:
            j += 1
        start, end = units[i][0], units[j][1]
        chunks.append(Chunk(text[start:end], start, end))
        if j + 1 >= len(units):
            break
        # seuraava pala alkaa niin monta yksikköä taaempaa, että päällekkäisyys pysyy rajan sisällä
        k = j + 1
        while k - 1 > i and end - units[k - 1][0] <= overlap:
            k -= 1
        i = k
    return chunks


def header(title: str, company: str, city: str) -> str:
    return " | ".join(x for x in (title, company, city) if x)
