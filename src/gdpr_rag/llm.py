"""Model access: OpenRouter with an API key, or a ChatGPT plan via Sign in with ChatGPT.

Models are routed by name: OpenRouter ids look like "vendor/model", ChatGPT plan models are bare
slugs ("gpt-..."), so the same calls work for both."""

import os
import threading
import time
from collections.abc import Callable
from functools import cache
from pathlib import Path

import openai
from openai import OpenAI

ANSWER_MODEL = "gpt-6.1-sol"  # ChatGPT plan model; answers need Sign in with ChatGPT
FAST_MODEL = "gpt-6-luna"  # small tier: follow-up rewriting and reranking
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
ENV_FAST_MODEL = os.environ.get("FAST_MODEL")
ENV_EMBED_MODEL = os.environ.get("EMBED_MODEL")


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


def use_fast_model(model: str | None) -> None:
    """Model for the small jobs (follow-up rewriting, reranking); defaults to the answer model."""
    _override("FAST_MODEL", model, ENV_FAST_MODEL)


def use_embed_model(model: str | None) -> None:
    _override("EMBED_MODEL", model, ENV_EMBED_MODEL)


def answer_model() -> str:
    return setting("OPENROUTER_MODEL", ANSWER_MODEL)


def fast_model() -> str:
    return setting("FAST_MODEL", FAST_MODEL)


def is_chatgpt(model: str) -> bool:
    return "/" not in model


# set by the server: returns a fresh ChatGPT access token, or raises when not signed in
CHATGPT_TOKEN: Callable[[], str] | None = None


def _chatgpt() -> OpenAI:
    if CHATGPT_TOKEN is None:
        raise RuntimeError("Not signed in with ChatGPT: sign in on the Settings page")
    # a ChatGPT plan token, not a Platform key: no organization or project
    return OpenAI(api_key=CHATGPT_TOKEN(), base_url="https://api.openai.com/v1",
                  organization=None, project=None, max_retries=2)


def key_is_valid(key: str) -> bool:
    """Ask OpenRouter whether the key exists (free call, no credit used).

    Network and server errors raise, so being offline isn't reported as a bad key."""
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, max_retries=1)
    try:
        client.get("/key", cast_to=object)
    except (openai.AuthenticationError, openai.PermissionDeniedError):
        return False
    return True


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
        client = _chatgpt() if is_chatgpt(model) else _client()
        r = client.embeddings.create(model=model, input=texts[i : i + batch])
        out += [d.embedding for d in sorted(r.data, key=lambda d: d.index)]
    return out


# OpenRouter reserves credit for max_tokens up front; without a cap it reserves the model's whole
# output limit (65k for Claude), so a nearly empty key fails even on tiny requests. Our longest
# output, a 14-item compliance report, is well under this.
MAX_TOKENS = 4096


def _responses_stream(messages: list[dict], model: str):
    """ChatGPT plan models go through the Responses API; system messages become instructions."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    request = {"model": model, "store": False, "stream": True,
               "input": [{"role": m["role"], "content": m["content"]}
                         for m in messages if m["role"] != "system"]}
    if system:
        request["instructions"] = system
    completed = False
    for event in _chatgpt().responses.create(**request):
        if event.type == "response.output_text.delta":
            yield event.delta
        elif event.type in ("response.failed", "error"):
            error = getattr(getattr(event, "response", None), "error", None) or event
            raise RuntimeError(f"ChatGPT failed: {getattr(error, 'message', None) or 'unknown error'}")
        elif event.type == "response.incomplete":
            raise RuntimeError("ChatGPT stopped before finishing the answer. Try again.")
        elif event.type == "response.completed":
            completed = True
            break
    if not completed:
        raise RuntimeError("ChatGPT's response ended early. Try again.")


def stream(messages: list[dict], model: str, **kw):
    """Yield text deltas as the model produces them."""
    _pace(model)
    if is_chatgpt(model):
        # sampling options (temperature, max_tokens) aren't sent: reasoning models reject them,
        # and plan usage doesn't reserve credit up front
        yield from _responses_stream(messages, model)
        return
    kw.setdefault("max_tokens", MAX_TOKENS)
    for chunk in _client().chat.completions.create(
        model=model, messages=messages, stream=True, **kw
    ):
        if chunk.choices and (delta := chunk.choices[0].delta.content):
            yield delta


def chat(messages: list[dict], model: str, **kw) -> str:
    if is_chatgpt(model):
        return "".join(stream(messages, model))
    _pace(model)
    kw.setdefault("max_tokens", MAX_TOKENS)
    r = _client().chat.completions.create(model=model, messages=messages, **kw)
    return r.choices[0].message.content or ""
