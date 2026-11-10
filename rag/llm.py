"""Kielimallirajapinta: yksi abstraktio, kaksi toteutusta (Ollama paikallisesti, Claude API) + testeille FakeLLM.

Kaikki toteutukset tarjoavat:
  stream(system, messages)        -> iteraattori tekstipaloja
  complete(system, messages)      -> koko vastaus
  complete_json(system, messages, schema) -> dict (JSON-skeeman mukainen)

Virheet nostetaan LLMError-poikkeuksena, jonka viesti on näytettävissä käyttäjälle sellaisenaan.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator, Protocol

from .config import Settings

Messages = list[dict]  # [{"role": "user"|"assistant", "content": str}]
# Järjestelmäkehote: merkkijono tai lista osia. Listan kaikki osat viimeistä lukuun ottamatta ovat yhteistä
# alkuosaa, jonka Claude-toteutus merkitsee välimuistiin (prompt caching); muut toteutukset liittävät osat yhteen.


def system_text(system) -> str:
    return system if isinstance(system, str) else "\n\n".join(p for p in system if p)


class LLMError(RuntimeError):
    """Käyttäjälle näytettävä virhe (malli ei vastaa, avain puuttuu, ...)."""


class LLM(Protocol):
    name: str

    def stream(self, system: str, messages: Messages, max_tokens: int = 2000) -> Iterator[str]: ...

    def complete(self, system: str, messages: Messages, max_tokens: int = 2000) -> str: ...

    def complete_json(self, system: str, messages: Messages, schema: dict, max_tokens: int = 3000,
                      on_start=None) -> dict: ...


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").split("\n", 1)[-1]
    a, b = t.find("{"), t.rfind("}")
    try:
        return json.loads(t[a:b + 1] if a >= 0 and b > a else t)
    except json.JSONDecodeError as e:
        raise LLMError("Malli ei palauttanut kelvollista JSONia. Yritä uudelleen tai vaihda mallia.") from e


# ------------------------------------------------------------ Ollama

def _usage() -> dict:
    """Kumulatiivinen tokenikirjanpito (input, output, välimuistista luetut ja välimuistiin kirjoitetut)."""
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


class OllamaLLM:
    def __init__(self, model: str, url: str = "http://localhost:11434", timeout: float = 600):
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout
        self.name = f"ollama:{model}"
        self.usage = _usage()

    def _post(self, body: dict):
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code == 404 or "not found" in detail:
                raise LLMError(f"Ollamassa ei ole mallia {self.model}. Lataa se: ollama pull {self.model}") from e
            raise LLMError(f"Ollama palautti virheen {e.code}: {detail[:200]}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise LLMError(f"Ollama ei vastaa osoitteessa {self.url}. Käynnistä Ollama tai tarkista OLLAMA_URL.") from e

    def _body(self, system: str, messages: Messages, max_tokens: int, stream: bool, fmt=None) -> dict:
        body = {"model": self.model, "stream": stream,
                "messages": [{"role": "system", "content": system_text(system)}, *messages],
                "options": {"num_predict": max_tokens, "temperature": 0.3, "num_ctx": 16384}}
        if fmt is not None:
            body["format"] = fmt
        return body

    def stream(self, system: str, messages: Messages, max_tokens: int = 2000) -> Iterator[str]:
        with self._post(self._body(system, messages, max_tokens, True)) as resp:
            for line in resp:
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("error"):
                    raise LLMError(f"Ollama: {ev['error']}")
                piece = ev.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if ev.get("done"):
                    break

    def complete(self, system: str, messages: Messages, max_tokens: int = 2000) -> str:
        return "".join(self.stream(system, messages, max_tokens))

    def complete_json(self, system: str, messages: Messages, schema: dict, max_tokens: int = 3000,
                      on_start=None) -> dict:
        for attempt in range(2):   # pienet mallit rikkovat JSONin satunnaisesti: yksi uusi yritys
            with self._post(self._body(system, messages, max_tokens, False, schema)) as resp:
                ev = json.loads(resp.read())
            self.usage["input"] += int(ev.get("prompt_eval_count") or 0)
            self.usage["output"] += int(ev.get("eval_count") or 0)
            try:
                return _parse_json(ev.get("message", {}).get("content", ""))
            except LLMError:
                if attempt:
                    raise
        raise AssertionError("unreachable")


# ------------------------------------------------------------ Claude

class ClaudeLLM:
    def __init__(self, model: str, api_key: str, effort: str = "low"):
        if not api_key:
            raise LLMError("LLM_PROVIDER=claude vaatii ANTHROPIC_API_KEY-avaimen .env-tiedostoon.")
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("Claude-provider vaatii paketin: pip install anthropic") from e
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model, self.effort = model, effort
        self.name = f"claude:{model}"
        self.usage = _usage()

    def _count(self, msg) -> None:
        u = getattr(msg, "usage", None)
        if u is None:
            return
        self.usage["input"] += int(getattr(u, "input_tokens", 0) or 0)
        self.usage["output"] += int(getattr(u, "output_tokens", 0) or 0)
        self.usage["cache_read"] += int(getattr(u, "cache_read_input_tokens", 0) or 0)
        self.usage["cache_write"] += int(getattr(u, "cache_creation_input_tokens", 0) or 0)

    def _errors(self, e: Exception) -> LLMError:
        a = self._anthropic
        if isinstance(e, a.AuthenticationError):
            return LLMError("Claude API hylkäsi avaimen. Tarkista ANTHROPIC_API_KEY.")
        if isinstance(e, a.NotFoundError):
            return LLMError(f"Claude-mallia {self.model} ei löytynyt. Tarkista CLAUDE_MODEL.")
        if isinstance(e, a.RateLimitError):
            return LLMError("Claude API:n pyyntöraja täyttyi. Odota hetki ja yritä uudelleen.")
        if isinstance(e, a.APIStatusError):
            return LLMError(f"Claude API palautti virheen {e.status_code}: {e.message}")
        if isinstance(e, a.APIConnectionError):
            return LLMError("Claude API:in ei saatu yhteyttä. Tarkista verkkoyhteys.")
        return LLMError(str(e))

    def _args(self, system, messages: Messages, max_tokens: int) -> dict:
        if not isinstance(system, str):
            parts = [p for p in system if p]
            # yhteiset osat välimuistiin (enintään 3 merkintää; API sallii 4)
            system = [{"type": "text", "text": p,
                       **({"cache_control": {"type": "ephemeral"}} if i < len(parts) - 1 and i < 3 else {})}
                      for i, p in enumerate(parts)]
        return {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": messages,
                "output_config": {"effort": self.effort}}

    @staticmethod
    def _check(msg) -> None:
        if msg.stop_reason == "refusal":
            raise LLMError("Malli kieltäytyi vastaamasta tähän pyyntöön.")

    def stream(self, system: str, messages: Messages, max_tokens: int = 2000) -> Iterator[str]:
        try:
            with self.client.messages.stream(**self._args(system, messages, max_tokens)) as s:
                yield from s.text_stream
                final = s.get_final_message()
                self._count(final)
                self._check(final)
        except self._anthropic.APIError as e:
            raise self._errors(e) from e

    def complete(self, system: str, messages: Messages, max_tokens: int = 2000) -> str:
        return "".join(self.stream(system, messages, max_tokens))

    def complete_json(self, system: str, messages: Messages, schema: dict, max_tokens: int = 3000,
                      on_start=None) -> dict:
        """on_start kutsutaan, kun malli on käsitellyt syötteen (ensimmäinen sisältölohko alkaa): silloin
        välimuistiin merkitty alkuosa on kirjoitettu ja rinnakkaiset kutsut voivat lukea sen."""
        args = self._args(system, messages, max_tokens)
        args["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        try:
            with self.client.messages.stream(**args) as s:
                for ev in s:
                    if on_start is not None and ev.type == "content_block_start":
                        on_start()
                        on_start = None
                msg = s.get_final_message()
        except self._anthropic.APIError as e:
            raise self._errors(e) from e
        self._count(msg)
        self._check(msg)
        if msg.stop_reason == "max_tokens":
            raise LLMError("Mallin vastaus katkesi token-rajaan. Yritä uudelleen.")
        return _parse_json(next((b.text for b in msg.content if b.type == "text"), ""))


# ------------------------------------------------------------ testit

class FakeLLM:
    """Palauttaa ennalta annetut vastaukset ja tallentaa kutsut. replies: str tai dict (complete_json)."""

    def __init__(self, replies: list | None = None, name: str = "fake"):
        self.replies = list(replies or [])
        self.calls: list[dict] = []
        self.name = name
        self.usage = _usage()

    def _next(self, kind: str, system: str, messages: Messages):
        self.calls.append({"kind": kind, "system": system_text(system), "messages": messages})
        if not self.replies:
            return "" if kind != "json" else {}
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def stream(self, system: str, messages: Messages, max_tokens: int = 2000) -> Iterator[str]:
        text = self._next("stream", system, messages)
        for i in range(0, len(text), 7):
            yield text[i:i + 7]

    def complete(self, system: str, messages: Messages, max_tokens: int = 2000) -> str:
        return "".join(self.stream(system, messages, max_tokens))

    def complete_json(self, system: str, messages: Messages, schema: dict, max_tokens: int = 3000,
                      on_start=None) -> dict:
        if on_start is not None:
            on_start()
        return self._next("json", system, messages)


def get_llm(s: Settings, task: str = "chat") -> LLM:
    """task: chat | explain | writing. writing käyttää CLAUDE_MODEL_WRITING:iä, jos se on asetettu."""
    if s.llm_provider == "ollama":
        return OllamaLLM(s.ollama_model, s.ollama_url)
    if s.llm_provider == "claude":
        model = (s.claude_model_writing if task == "writing" else "") or s.claude_model
        return ClaudeLLM(model, s.anthropic_api_key, effort="medium" if task == "writing" else "low")
    if s.llm_provider == "fake":
        return FakeLLM()
    raise LLMError(f"Tuntematon LLM_PROVIDER: {s.llm_provider} (ollama | claude)")
