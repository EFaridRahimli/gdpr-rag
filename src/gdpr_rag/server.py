"""FastAPI app: streaming, citation-checked answers, chat history, settings, eval results."""

import json
import logging
import threading
from contextlib import asynccontextmanager, closing
from pathlib import Path, PurePath
from typing import Literal
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import answer, chatgpt, compliance, docs, ingest, llm, retrieve, store

DB = Path("data/gdpr.db")
WEB = Path(__file__).resolve().parents[2] / "web" / "dist"
EVAL_RESULTS = Path("eval/results")
LOCAL_HOSTS = ["localhost", "127.0.0.1"]
# no remote images: a poisoned document can't make the browser leak chat text in an image URL
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
log = logging.getLogger(__name__)


def _db():
    return store.connect(DB)


# GDPR index built in the background once a key exists, so first-run setup happens in the UI
_index = {"building": False, "error": None}
_index_lock = threading.Lock()


def _embed_model() -> str:
    return llm.setting("EMBED_MODEL", llm.EMBED_MODEL)


CHATGPT_EMBED_MODEL = "text-embedding-3-large"  # used when the ChatGPT plan allows embeddings


def _chatgpt_token() -> str:
    with closing(_db()) as db:
        return chatgpt.access_token(db)


def _signed_in() -> bool:
    with closing(_db()) as db:
        return chatgpt.session(db) is not None


def _can_embed() -> bool:
    """Indexing needs embeddings: from the ChatGPT plan or from an OpenRouter key."""
    if llm.is_chatgpt(_embed_model()):
        return _signed_in()
    return bool(llm.setting("OPENROUTER_API_KEY", ""))


def _index_status(db) -> dict:
    if _index["building"]:
        return {"status": "building", "error": None}
    if ingest.is_ready(db, _embed_model()):
        return {"status": "ready", "error": None}
    return {"status": "error" if _index["error"] else "missing", "error": _index["error"]}


def _start_indexing() -> None:
    """Build the GDPR index in a thread unless it's ready or already building."""
    with _index_lock:
        db = _db()
        try:
            if _index["building"] or ingest.is_ready(db, _embed_model()):
                return
        finally:
            db.close()
        _index.update(building=True, error=None)

    def run():
        db = _db()
        try:
            ingest.ensure(db, _embed_model())
        except Exception as e:  # shown on the Settings page; the user can retry
            log.exception("building the GDPR index failed")
            _index["error"] = str(e) or repr(e)
        finally:
            _index["building"] = False
            db.close()

    threading.Thread(target=run, daemon=True).start()


def apply_saved_settings() -> None:
    """Use what was saved on the Settings page (key, models, ChatGPT sign-in). Also used by the
    CLI, so evals run with the same setup as the app."""
    with closing(_db()) as db:
        llm.use_api_key(store.get_setting(db, "api_key"))  # a key saved in the UI wins over .env
        llm.use_model(store.get_setting(db, "model"))
        llm.use_fast_model(store.get_setting(db, "fast_model"))
        llm.use_embed_model(store.get_setting(db, "embed_model"))
    llm.CHATGPT_TOKEN = _chatgpt_token


@asynccontextmanager
async def lifespan(_app):
    apply_saved_settings()
    if _can_embed():
        _start_indexing()
    yield


app = FastAPI(title="gdpr-rag", lifespan=lifespan)
# a Host other than localhost means DNS rebinding: refuse it
app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Refuse writes from other sites (they'd spend the user's credit or swap the key), add CSP."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        cross_site = request.headers.get("sec-fetch-site") == "cross-site"
        if cross_site or (origin and urlsplit(origin).hostname not in LOCAL_HOSTS):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if response.headers.get("content-type", "").startswith("text/html"):
        # re-check the page on every load so an updated install isn't hidden behind a cached
        # index.html; the hashed JS/CSS it points to can stay cached
        response.headers["Cache-Control"] = "no-cache"
    return response


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    chat_id: int | None = None
    scope: Literal["all", "gdpr", "docs"] = "all"


@app.post("/api/chat")
def chat(req: Ask) -> StreamingResponse:
    """Events: `chat` (id), `sources`, `token`*, then `done` (cleaned answer) or `error`."""

    def events():
        db = _db()
        try:
            if llm.is_chatgpt(llm.answer_model()) and chatgpt.session(db) is None:
                yield _sse("error", {"message": "Sign in with ChatGPT (Plus or Pro) in Settings to ask questions."})
                return
            index = _index_status(db)
            if req.scope != "docs" and index["status"] != "ready":
                yield _sse("error", {"message": {
                    "building": "The GDPR index is still building (about a minute). Try again shortly.",
                    "error": f"Building the GDPR index failed: {index['error']} Retry in Settings.",
                    "missing": "The GDPR index isn't built yet. Add your OpenRouter key in Settings.",
                }[index["status"]]})
                return
            history = store.chat_messages(db, req.chat_id) if req.chat_id else []
            question = answer.standalone(history, req.question)
            chat_id = store.add_message(db, req.chat_id, "user", req.question)
            yield _sse("chat", {"id": chat_id, "question": question})
            sources = answer.sources_for(db, question, req.scope)
            yield _sse("sources", sources)
            model = llm.answer_model()
            text = ""
            if sources:  # e.g. "My documents" with nothing uploaded: no point asking the model
                for delta in llm.stream(answer.messages_for(question, sources), model, temperature=0):
                    text += delta
                    yield _sse("token", delta)
            result = answer.finalize(text, sources)
            store.add_message(db, chat_id, "assistant", result["answer"], sources, result["citations"])
            yield _sse("done", result)
        except Exception as e:  # noqa: BLE001 - surface any failure to the client instead of a dropped stream
            yield _sse("error", {"message": str(e)})
        finally:
            db.close()

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/api/index")
def start_index() -> dict:
    """Retry building the GDPR index (e.g. after a network or key error)."""
    if not _can_embed():
        raise HTTPException(400, "Add an OpenRouter key or sign in with ChatGPT first")
    _start_indexing()
    db = _db()
    try:
        return _index_status(db)
    finally:
        db.close()


@app.get("/api/chats")
def list_chats() -> list[dict]:
    db = _db()
    try:
        return [dict(r) for r in db.execute("select * from chats order by id desc")]
    finally:
        db.close()


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: int) -> dict:
    db = _db()
    try:
        row = db.execute("select * from chats where id = ?", (chat_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "chat not found")
        return {**dict(row), "messages": store.chat_messages(db, chat_id)}
    finally:
        db.close()


@app.delete("/api/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: int) -> None:
    db = _db()
    try:
        with db:
            db.execute("delete from chats where id = ?", (chat_id,))
    finally:
        db.close()


def _settings_view(db) -> dict:
    ui_key = store.get_setting(db, "api_key")
    key = ui_key or llm.ENV_KEY
    return {
        "key_hint": f"{key[:6]}…{key[-4:]}" if key else None,  # never return the key itself
        "key_source": "settings" if ui_key else ("env" if key else None),
        "model": llm.answer_model(),
        "fast_model": llm.fast_model(),
        "embed_model": _embed_model(),
        "index": _index_status(db),
        "chatgpt": {
            "connected": (s := chatgpt.session(db)) is not None,
            "email": s and s.get("email"),
            "embeddings": store.get_setting(db, "chatgpt_embeddings"),  # "yes" / "no" / None
        },
    }


class SettingsUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=200)  # "" removes the saved key
    model: str | None = Field(default=None, max_length=200)
    fast_model: str | None = Field(default=None, max_length=200)


@app.get("/api/settings")
def get_settings() -> dict:
    db = _db()
    try:
        return _settings_view(db)
    finally:
        db.close()


@app.put("/api/settings")
def put_settings(req: SettingsUpdate) -> dict:
    db = _db()
    try:
        if req.api_key is not None:
            key = req.api_key.strip()
            try:
                valid = not key or llm.key_is_valid(key)
            except Exception as e:  # noqa: BLE001 - offline / OpenRouter down: not the key's fault
                raise HTTPException(502, f"Couldn't reach OpenRouter to check the key: {e}") from None
            if not valid:
                raise HTTPException(400, "OpenRouter rejected this key")
            store.set_setting(db, "api_key", key or None)
            llm.use_api_key(key or None)
            if key:
                _start_indexing()
        if req.model is not None:
            store.set_setting(db, "model", req.model.strip() or None)
            llm.use_model(req.model.strip() or None)
        if req.fast_model is not None:
            store.set_setting(db, "fast_model", req.fast_model.strip() or None)
            llm.use_fast_model(req.fast_model.strip() or None)
        return _settings_view(db)
    finally:
        db.close()


@app.get("/api/documents")
def list_documents() -> list[dict]:
    db = _db()
    try:
        return [dict(r) for r in db.execute("select * from documents order by id desc")]
    finally:
        db.close()


@app.post("/api/documents", status_code=201)
def upload_document(file: UploadFile) -> dict:
    """Parse, chunk and embed an upload; the text is sent to OpenRouter for embedding."""
    name = PurePath(file.filename or "document").name[:120]
    data = file.file.read(docs.MAX_BYTES + 1)  # one byte over the limit is enough to reject
    try:
        chunks = docs.chunk_markdown(docs.to_markdown(name, data), PurePath(name).stem)
    except docs.UploadError as e:
        raise HTTPException(422, str(e)) from None
    if not chunks:
        raise HTTPException(422, "No text found in this file.")
    model = llm.setting("EMBED_MODEL", llm.EMBED_MODEL)
    try:  # embed before writing anything, so a failed call leaves no half-stored document
        vectors = llm.embed([retrieve.passage(c.label, c.heading, c.text) for c in chunks], model)
    except Exception as e:  # noqa: BLE001 - report key/network/provider errors to the user
        raise HTTPException(502, f"Embedding failed: {e}") from None
    db = _db()
    try:
        with db:
            doc_id = db.execute(
                "insert into documents(name, size, chunks) values (?, ?, ?)",
                (name, len(data), len(chunks)),
            ).lastrowid
        try:
            store.set_vectors(db, model, store.replace_source(db, f"user:{doc_id}", chunks), vectors)
        except Exception:
            _delete_document(db, doc_id)
            raise
        return dict(db.execute("select * from documents where id = ?", (doc_id,)).fetchone())
    finally:
        db.close()


def _delete_document(db, doc_id: int) -> None:
    store.replace_source(db, f"user:{doc_id}", [])
    with db:
        db.execute("delete from documents where id = ?", (doc_id,))


@app.delete("/api/documents/{doc_id}", status_code=204)
def delete_document(doc_id: int) -> None:
    db = _db()
    try:
        if db.execute("select 1 from documents where id = ?", (doc_id,)).fetchone() is None:
            raise HTTPException(404, "document not found")
        _delete_document(db, doc_id)
    finally:
        db.close()


@app.post("/api/documents/{doc_id}/compliance")
def run_compliance(doc_id: int) -> dict:
    """Audit a document against the Art. 13/14 checklist (one model call) and keep the report."""
    db = _db()
    try:
        if db.execute("select 1 from documents where id = ?", (doc_id,)).fetchone() is None:
            raise HTTPException(404, "document not found")
        if llm.is_chatgpt(llm.answer_model()) and chatgpt.session(db) is None:
            raise HTTPException(400, "Sign in with ChatGPT (Plus or Pro) in Settings to run a check.")
        try:
            report = compliance.check(db, doc_id)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except Exception as e:  # noqa: BLE001 - key/network/provider errors, shown to the user
            raise HTTPException(502, f"Compliance check failed: {e}") from None
        compliance.save(db, report)
        return compliance.load(db, doc_id)
    finally:
        db.close()


@app.get("/api/documents/{doc_id}/compliance")
def get_compliance(doc_id: int) -> dict:
    db = _db()
    try:
        report = compliance.load(db, doc_id)
        if report is None:
            raise HTTPException(404, "no report yet")
        return report
    finally:
        db.close()


def _set_models(db, model: str | None, fast: str | None, embed: str | None) -> None:
    for key, value, use in (("model", model, llm.use_model), ("fast_model", fast, llm.use_fast_model),
                            ("embed_model", embed, llm.use_embed_model)):
        store.set_setting(db, key, value)
        use(value)


def _after_chatgpt_sign_in(db) -> None:
    """Pick plan models and find out whether the plan also covers embeddings."""
    slugs = [m["slug"] for m in chatgpt.models(chatgpt.access_token(db))]
    if not slugs:
        raise chatgpt.SignInError("Your ChatGPT plan didn't list any models this app can use.")
    model = llm.ANSWER_MODEL if llm.ANSWER_MODEL in slugs else slugs[0]
    fast = llm.FAST_MODEL if llm.FAST_MODEL in slugs else next(
        (s for s in slugs if "luna" in s or "mini" in s), model)
    try:
        llm.embed(["probe"], CHATGPT_EMBED_MODEL)
        embed, can_embed = CHATGPT_EMBED_MODEL, "yes"
    except Exception:  # noqa: BLE001 - any refusal means: keep OpenRouter for embeddings
        log.info("ChatGPT plan token can't create embeddings; keeping OpenRouter for search")
        embed, can_embed = None, "no"
    _set_models(db, model, fast, embed)
    store.set_setting(db, "chatgpt_embeddings", can_embed)


@app.post("/api/chatgpt/start")
def chatgpt_start(request: Request) -> dict:
    """Start Sign in with ChatGPT; the browser goes to the returned URL."""
    with closing(_db()) as db:
        try:
            return {"url": chatgpt.start(db, request.url.port or 8000)}
        except Exception as e:  # noqa: BLE001 - offline / OpenAI unreachable
            raise HTTPException(502, f"Couldn't reach ChatGPT sign-in: {e}") from None


@app.get(chatgpt.CALLBACK_PATH)
def chatgpt_callback(request: Request) -> RedirectResponse:
    """OpenAI sends the browser back here after sign-in; finish it and return to the app."""
    q = request.query_params
    with closing(_db()) as db:
        try:
            if q.get("error"):
                raise chatgpt.SignInError(q.get("error_description") or f"Sign-in cancelled ({q['error']}).")
            chatgpt.finish(db, q.get("state", ""), q.get("code", ""), q.get("client_id"))
            _after_chatgpt_sign_in(db)
        except Exception as e:  # every failure goes back to the Settings page
            log.exception("Sign in with ChatGPT failed")
            message = str(e) if isinstance(e, chatgpt.SignInError) else f"Sign-in failed: {e}"
            return RedirectResponse("/?" + urlencode({"view": "settings", "chatgpt_error": message}), 303)
    _start_indexing()  # the embedding model may have changed
    return RedirectResponse("/?" + urlencode({"view": "settings", "chatgpt": "connected"}), 303)


@app.get("/api/chatgpt/models")
def chatgpt_models() -> list[dict]:
    with closing(_db()) as db:
        try:
            return chatgpt.models(chatgpt.access_token(db))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Couldn't load ChatGPT models: {e}") from None


@app.post("/api/chatgpt/signout")
def chatgpt_signout() -> dict:
    with closing(_db()) as db:
        chatgpt.sign_out(db)
        _set_models(db, None, None, None)  # back to the OpenRouter defaults
        store.set_setting(db, "chatgpt_embeddings", None)
        view = _settings_view(db)
    _start_indexing()
    return view


@app.get("/api/evals")
def evals() -> dict:
    def load(name):
        p = EVAL_RESULTS / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    answers, checks = load("answers.json"), load("compliance.json")
    return {"retrieval": load("retrieval.json"), "answers": answers and answers["summary"],
            "compliance": checks and checks["summary"]}


if WEB.exists():  # built frontend; in development Vite serves it and proxies /api here
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
