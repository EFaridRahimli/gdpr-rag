"""OpenRouter access: chat and embeddings through the OpenAI-compatible API."""

import os
import threading
import time
from functools import cache
from pathlib import Path

from openai import OpenAI

ANSWER_MODEL = "anthropic/claude-haiku-5.5"
EVAL_MODEL = "anthropic/claude-sonnet-5.5"
EMBED_MODEL = "google/gemini-embedding-2"  # bake-off winner, see eval/results/retrieval.md


def _load_dotenv(path: Path = Path(".env")) -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()
ENV_KEY = os.environ.get("OPENROUTER_API_KEY")  # shell or .env; a key saved in the UI overrides it


def setting(name: str, default: str) -> str:
    return os.environ.get(name) or default


ENV_MODEL = os.environ.get("OPENROUTER_MODEL")


def _override(name: str, value: str | None, env_value: str | None) -> None:
    if value or env_value:
        os.environ[name] = value or env_value
    else:
        os.environ.pop(name, None)


def use_api_key(key: str | None) -> None:
    """Switch to a UI-saved key, or back to the .env key when None."""
    _override("OPENROUTER_API_KEY", key, ENV_KEY)


def use_model(model: str | None) -> None:
    """Switch to a UI-saved answer model, or back to the .env/default one when None."""
    _override("OPENROUTER_MODEL", model, ENV_MODEL)


def key_is_valid(key: str) -> bool:
    """Ask OpenRouter whether the key exists (free call, no credit used)."""
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, max_retries=0)
    try:
        client.get("/key", cast_to=object)  # 401 for unknown keys
        return True
    except Exception:  # noqa: BLE001 - any auth/network failure means "can't use this key"
        return False


def _client() -> OpenAI:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("No OpenRouter API key: add one in Settings or in .env")
    return _client_for(key)


@cache
def _client_for(key: str) -> OpenAI:
    # new OpenRouter accounts get ~20 req/min per model; the SDK backs off on 429s
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, max_retries=8)


_lock = threading.Lock()
_next_slot: dict[str, float] = {}
PACE_RPM: int | None = None  # set by batch jobs (evals); interactive chat relies on SDK 429 retries


def _pace(model: str) -> None:
    """Space calls per model to PACE_RPM (new OpenRouter accounts are capped at 20 req/min)."""
    if not PACE_RPM:
        return
    gap = 60 / PACE_RPM
    with _lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(model, 0.0))
        _next_slot[model] = slot + gap
    time.sleep(slot - now)


def embed(texts: list[str], model: str, batch: int = 64) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        _pace(model)
        r = _client().embeddings.create(model=model, input=texts[i : i + batch])
        out += [d.embedding for d in sorted(r.data, key=lambda d: d.index)]
    return out


def stream(messages: list[dict], model: str, **kw):
    """Yield text deltas as the model produces them."""
    _pace(model)
    for chunk in _client().chat.completions.create(
        model=model, messages=messages, stream=True, **kw
    ):
        if chunk.choices and (delta := chunk.choices[0].delta.content):
            yield delta


def chat(messages: list[dict], model: str, **kw) -> str:
    _pace(model)
    r =_client().chat.completions.create(model=model, messages=messages, **kw)
    return r.choices[0].message.content or ""
