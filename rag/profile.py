"""Profiilipalat: CV osioittain, skills.json kategorioittain ja tavoiteroolit profile.json:n hakusanoista.

source_ref kertoo, mistä pala on peräisin (CV:n osio, skills.json-kategoria tai OPS-moduulin URL),
jotta myöhemmät selitykset voivat viitata siihen.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import config
from .chunking import chunk_text
from .clean import clean_text, sha1


@dataclass(frozen=True)
class ProfileChunk:
    kind: str          # cv | skills | target
    source_ref: str
    text: str

    @property
    def hash(self) -> str:
        return sha1(self.kind, self.text)


def _read_cv(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise RuntimeError("PDF-CV vaatii pypdf-kirjaston (pip install pypdf) tai tallenna CV .md/.txt-muodossa") from e
        return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8")


def _is_heading(line: str) -> bool:
    s = line.strip()
    if s.startswith("#"):
        return True
    return 0 < len(s) <= 40 and (s.endswith(":") or (s.isupper() and any(c.isalpha() for c in s)))


def cv_chunks(path: Path, size: int = 1000, overlap: int = 150, skip: tuple[str, ...] = ()) -> list[ProfileChunk]:
    """CV osioittain. skip: otsikon osia (pienillä kirjaimilla), joiden osiot jätetään pois profiilista."""
    if not path.exists():
        return []
    text = clean_text(_read_cv(path))  # puhelin/sähköposti pois
    sections: list[tuple[str, list[str]]] = [("Alku", [])]
    for line in text.split("\n"):
        if _is_heading(line):
            sections.append((line.strip("#: ").strip(), []))
        else:
            sections[-1][1].append(line)
    out = []
    for title, lines in sections:
        body = "\n".join(lines).strip()
        if len(body) < 60:
            continue  # nimi/yhteystiedot yms. ovat semanttisesti kohinaa
        if any(x in title.lower() for x in skip):
            continue  # esim. alan ulkopuolinen työkokemus: muuten esim. myyjän ilmoitus osuisi myyntitaustaan
        for i, c in enumerate(chunk_text(body, size, overlap)):
            ref = f"{path.name}#{title}" + (f"[{i}]" if i else "")
            out.append(ProfileChunk("cv", ref, f"{title}\n{c.text}"))
    return out


def skills_chunks(path: Path) -> list[ProfileChunk]:
    if not path.exists():
        return []
    skills = json.loads(path.read_text(encoding="utf-8")).get("skills", [])
    groups: dict[str, list[str]] = {}
    for sk in skills:
        if not sk.get("name") or float(sk.get("weight", 1)) <= 0:
            continue
        lvl = f" ({sk['level']})" if sk.get("level") else ""
        groups.setdefault(sk.get("category") or "Muu", []).append(sk["name"] + lvl)
    return [ProfileChunk("skills", f"{path.name}#{cat}", f"Osaaminen: {cat}. " + ", ".join(names))
            for cat, names in groups.items()]


def target_chunk(path: Path) -> list[ProfileChunk]:
    """Tavoiteroolit: ainoa tapa tuoda tasotoive (harjoittelija/junior) semanttiseen vertailuun."""
    if not path.exists():
        return []
    prof = json.loads(path.read_text(encoding="utf-8"))
    queries = prof.get("search", {}).get("queries", [])
    if not queries:
        return []
    uniq = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
    return [ProfileChunk("target", f"{path.name}#search.queries",
                         "Haen tehtäviä: " + ", ".join(uniq) + ". Etsin harjoittelu-, junior- tai opiskelijatehtävää.")]


def build_profile(s: config.Settings) -> list[ProfileChunk]:
    base = config.BASE
    chunks = cv_chunks(s.cv_path, s.chunk_size, s.chunk_overlap, s.cv_skip_sections)
    chunks += skills_chunks(base / "skills.json")
    chunks += target_chunk(base / "profile.json")
    # sama teksti vain kerran
    return list({c.hash: c for c in chunks}.values())
