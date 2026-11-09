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


def setting(name: str, default: str) -> str:
    return os.environ.get(name) or default


@cache
def _client() -> OpenAI:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set: add it to .env")
    # new OpenRouter accounts get ~20 req/min per model; the SDK backs off on 429s
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, max_retries=8)


_lock = threading.Lock()
_next_slot: dict[str, float] = {}


def _pace(model: str) -> None:
    """Space calls per model to OPENROUTER_RPM (new accounts are capped at 20 req/min)."""
    gap = 60 / int(setting("OPENROUTER_RPM", "18"))
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


def chat(messages: list[dict], model: str, **kw) -> str:
    _pace(model)
    r =_client().chat.completions.create(model=model, messages=messages, **kw)
    return r.choices[0].message.content or ""
