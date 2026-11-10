"""Asetusten muokkaus käyttöliittymästä: päivittää .env-tiedoston rivit (kommentit säilyvät) ja prosessin ympäristön."""
from __future__ import annotations

import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ENV = BASE / ".env"

# Käyttöliittymästä muokattavat avaimet ja niiden tarkistus
EDITABLE = {
    "SCORING_MODE": lambda v: v in ("keyword", "semantic", "hybrid"),
    "W_KEYWORD": lambda v: _float(v, 0, 10),
    "W_SEMANTIC": lambda v: _float(v, 0, 10),
    "HYBRID_METHOD": lambda v: v in ("weighted", "rrf"),
    "LLM_PROVIDER": lambda v: v in ("ollama", "claude"),
    "OLLAMA_URL": lambda v: bool(re.fullmatch(r"https?://[\w.\-]+(:\d+)?/?", v)),
    "OLLAMA_MODEL": lambda v: bool(re.fullmatch(r"[\w.\-:/]{1,80}", v)),
    "CLAUDE_MODEL": lambda v: bool(re.fullmatch(r"claude-[\w.\-]{1,60}", v)),
    "CLAUDE_MODEL_WRITING": lambda v: v == "" or bool(re.fullmatch(r"claude-[\w.\-]{1,60}", v)),
    "ANTHROPIC_API_KEY": lambda v: v == "" or bool(re.fullmatch(r"[\w\-]{20,200}", v)),
    "EMBED_MODEL": lambda v: bool(re.fullmatch(r"[\w.\-/]{1,100}", v)),
    "CHUNK_SIZE": lambda v: v.isdigit() and 100 <= int(v) <= 8000,
    "CHUNK_OVERLAP": lambda v: v.isdigit() and int(v) <= 4000,
}
SECRET = {"ANTHROPIC_API_KEY"}


def _float(v: str, lo: float, hi: float) -> bool:
    try:
        return lo <= float(v) <= hi
    except ValueError:
        return False


def update(values: dict[str, str], path: Path = ENV) -> list[str]:
    """Päivitä avaimet. Palauttaa muutetut avaimet. ValueError, jos arvo ei kelpaa."""
    clean = {}
    for k, v in values.items():
        if k not in EDITABLE:
            raise ValueError(f"Asetusta {k} ei voi muuttaa käyttöliittymästä")
        v = str(v).strip()
        if "\n" in v or not EDITABLE[k](v):
            raise ValueError(f"Virheellinen arvo asetukselle {k}")
        clean[k] = v
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in clean:
            lines[i] = f"{m.group(1)}={clean[m.group(1)]}"
            seen.add(m.group(1))
    lines += [f"{k}={v}" for k, v in clean.items() if k not in seen]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in clean.items():
        os.environ[k] = v   # config.load() lukee ympäristön ennen .env:iä
    return list(clean)
