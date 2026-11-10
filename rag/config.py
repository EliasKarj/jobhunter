"""Asetukset .env-tiedostosta (ja ympäristömuuttujista). Koodissa ei ole avaimia eikä polkuja kovakoodattuna."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict, replace
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
from datadir import DATA  # noqa: E402  (käyttäjän data repon ulkopuolella/ignoroitu)
MODES = ("keyword", "semantic", "hybrid")


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(BASE / ".env", override=False)


def _path(v: str) -> Path:
    p = Path(v)
    return p if p.is_absolute() else BASE / p


@dataclass(frozen=True)
class Settings:
    scoring_mode: str = "keyword"
    embed_backend: str = "local"          # local | openai | fake
    embed_model: str = "BAAI/bge-m3"
    openai_api_key: str = ""
    chunk_size: int = 1000
    chunk_overlap: int = 150
    aggregation: str = "topk_mean"        # max | topk_mean | mean
    agg_k: int = 3
    w_keyword: float = 0.6
    w_semantic: float = 0.4
    hybrid_method: str = "weighted"       # weighted | rrf
    rrf_k: int = 60
    db_path: Path = DATA / "rag.sqlite"
    cv_path: Path = DATA / "cv.md"
    cv_skip_sections: tuple[str, ...] = ()  # CV:n osiot, joita ei käytetä pisteytyksessä (otsikon osa, kirjainkoko ei ratkaise)
    prune_days: int = 14
    batch_size: int = 16
    auto_ingest: bool = True
    llm_provider: str = "ollama"          # ollama | claude | fake
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:12b"
    anthropic_api_key: str = ""
    claude_model: str = "claude-haiku-5-5"
    claude_model_writing: str = ""        # tyhjä = sama kuin claude_model
    score_provider: str = "off"           # kärjen sopivuusarvio: off | ollama | claude (oma valinta: CV ei lähde pilveen)
    score_top: int = 500
    score_workers: int = 6
    panel_model: str = ""                 # CV-paneelin arvioijat; tyhjä = sama kuin claude_model
    panel_price_in: float = 0.0           # $ / miljoona input-tokenia (0 = hintaa ei lasketa)
    panel_price_out: float = 0.0          # $ / miljoona output-tokenia
    remote_users: tuple[str, ...] = ()    # Tailscale-käyttäjät, jotka saavat käyttää etänä (tyhjä = etäkäyttö estetty)
    remote_hosts: tuple[str, ...] = ()    # sallitut etäosoitteet, esim. kone.tailnet.ts.net

    @property
    def config_id(self) -> str:
        """Indeksin tunniste: malli + pilkkominen. Eri asetuksilla tehdyt indeksit elävät rinnakkain."""
        model = re.sub(r"[^a-z0-9]+", "_", self.embed_model.lower().rsplit("/", 1)[-1]).strip("_")
        return f"{self.embed_backend}_{model}_c{self.chunk_size}_o{self.chunk_overlap}"

    def describe(self) -> dict:
        """Evaluointiajon konfiguraatio tallennettavaksi (ei avaimia)."""
        d = asdict(self)
        d.pop("openai_api_key")
        d.pop("anthropic_api_key")
        d["db_path"], d["cv_path"] = str(self.db_path), str(self.cv_path)
        d["config_id"] = self.config_id
        return d

    def with_(self, **kw) -> "Settings":
        s = replace(self, **{k: v for k, v in kw.items() if v is not None})
        s.validate()
        return s

    def validate(self) -> None:
        if self.scoring_mode not in MODES:
            raise ValueError(f"SCORING_MODE pitää olla jokin näistä: {', '.join(MODES)}")
        if self.chunk_size < 100:
            raise ValueError("CHUNK_SIZE on liian pieni (vähintään 100 merkkiä)")
        if not 0 <= self.chunk_overlap < self.chunk_size // 2:
            raise ValueError("CHUNK_OVERLAP pitää olla 0 ... CHUNK_SIZE/2")
        if self.aggregation not in ("max", "topk_mean", "mean"):
            raise ValueError("AGGREGATION pitää olla max, topk_mean tai mean")
        if self.hybrid_method not in ("weighted", "rrf"):
            raise ValueError("HYBRID_METHOD pitää olla weighted tai rrf")
        if self.llm_provider not in ("ollama", "claude", "fake"):
            raise ValueError("LLM_PROVIDER pitää olla ollama tai claude")
        if self.score_provider not in ("off", "ollama", "claude"):
            raise ValueError("SCORE_PROVIDER pitää olla off, ollama tai claude")
        if self.w_keyword < 0 or self.w_semantic < 0 or self.w_keyword + self.w_semantic == 0:
            raise ValueError("Painojen W_KEYWORD ja W_SEMANTIC pitää olla >= 0 eikä molemmat nollia")


def load() -> Settings:
    _load_env()
    e = os.environ.get
    s = Settings(
        scoring_mode=e("SCORING_MODE", "keyword").strip().lower(),
        embed_backend=e("EMBED_BACKEND", "local").strip().lower(),
        embed_model=e("EMBED_MODEL", "BAAI/bge-m3").strip(),
        openai_api_key=e("OPENAI_API_KEY", "").strip(),
        chunk_size=int(e("CHUNK_SIZE", "1000")),
        chunk_overlap=int(e("CHUNK_OVERLAP", "150")),
        aggregation=e("AGGREGATION", "topk_mean").strip().lower(),
        agg_k=int(e("AGG_K", "3")),
        w_keyword=float(e("W_KEYWORD", "0.6")),
        w_semantic=float(e("W_SEMANTIC", "0.4")),
        hybrid_method=e("HYBRID_METHOD", "weighted").strip().lower(),
        rrf_k=int(e("RRF_K", "60")),
        db_path=_path(e("RAG_DB", "data/rag.sqlite")),
        cv_path=_path(e("CV_PATH", "data/cv.md")),
        cv_skip_sections=tuple(x.strip().lower() for x in e("CV_SKIP_SECTIONS", "").split(";") if x.strip()),
        prune_days=int(e("PRUNE_DAYS", "14")),
        batch_size=int(e("EMBED_BATCH", "16")),
        auto_ingest=e("RAG_AUTO_INGEST", "1").strip() not in ("0", "false", "no", ""),
        llm_provider=e("LLM_PROVIDER", "ollama").strip().lower(),
        ollama_url=e("OLLAMA_URL", "http://localhost:11434").strip(),
        ollama_model=e("OLLAMA_MODEL", "gemma3:12b").strip(),
        anthropic_api_key=e("ANTHROPIC_API_KEY", "").strip(),
        claude_model=e("CLAUDE_MODEL", "claude-haiku-5-5").strip(),
        claude_model_writing=e("CLAUDE_MODEL_WRITING", "").strip(),
        score_provider=e("SCORE_PROVIDER", "off").strip().lower(),
        score_top=int(e("SCORE_TOP", "500")),
        score_workers=int(e("SCORE_WORKERS", "6")),
        panel_model=e("PANEL_MODEL", "").strip(),
        panel_price_in=float(e("PANEL_PRICE_IN", "0") or 0),
        panel_price_out=float(e("PANEL_PRICE_OUT", "0") or 0),
        remote_users=tuple(x.strip().lower() for x in e("REMOTE_USERS", "").split(",") if x.strip()),
        remote_hosts=tuple(x.strip().lower().rstrip(".") for x in e("REMOTE_HOSTS", "").split(",") if x.strip()),
    )
    s.validate()
    return s
