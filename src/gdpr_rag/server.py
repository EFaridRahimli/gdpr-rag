"""FastAPI app: streaming, citation-checked answers, chat history, settings, eval results."""

import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path, PurePath
from typing import Literal

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import answer, compliance, docs, ingest, llm, retrieve, store

DB = Path("data/gdpr.db")
WEB = Path(__file__).resolve().parents[2] / "web" / "dist"
EVAL_RESULTS = Path("eval/results")


def _db():
    return store.connect(DB)


# GDPR index built in the background once a key exists, so first-run setup happens in the UI
_index = {"building": False, "error": None}
_index_lock = threading.Lock()


def _embed_model() -> str:
    return llm.setting("EMBED_MODEL", llm.EMBED_MODEL)


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
            ingest.build(db, [_embed_model()], log=lambda _msg: None)
        except Exception as e:  # noqa: BLE001 - shown on the Settings page, user can retry
            _index["error"] = str(e)
        finally:
            _index["building"] = False
            db.close()

    threading.Thread(target=run, daemon=True).start()


@asynccontextmanager
async def lifespan(_app):
    db = _db()
    llm.use_api_key(store.get_setting(db, "api_key"))  # a key saved in the UI wins over .env
    llm.use_model(store.get_setting(db, "model"))
    db.close()
    if llm.setting("OPENROUTER_API_KEY", ""):
        _start_indexing()
    yield


app = FastAPI(title="gdpr-rag", lifespan=lifespan)


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
            if req.scope != "docs" and _index_status(db)["status"] != "ready":
                yield _sse("error", {"message": "The GDPR index isn't ready yet. Add your OpenRouter "
                                     "key in Settings; the index then builds in about a minute."})
                return
            history = store.chat_messages(db, req.chat_id) if req.chat_id else []
            question = answer.standalone(history, req.question)
            chat_id = store.add_message(db, req.chat_id, "user", req.question)
            yield _sse("chat", {"id": chat_id, "question": question})
            sources = answer.sources_for(db, question, req.scope)
            yield _sse("sources", sources)
            model = llm.setting("OPENROUTER_MODEL", llm.ANSWER_MODEL)
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
    if not llm.setting("OPENROUTER_API_KEY", ""):
        raise HTTPException(400, "Add an OpenRouter key first")
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
        "model": llm.setting("OPENROUTER_MODEL", llm.ANSWER_MODEL),
        "index": _index_status(db),
    }


class SettingsUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=200)  # "" removes the saved key
    model: str | None = Field(default=None, max_length=200)


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
            if key and not llm.key_is_valid(key):
                raise HTTPException(400, "OpenRouter rejected this key")
            store.set_setting(db, "api_key", key or None)
            llm.use_api_key(key or None)
            if key:
                _start_indexing()
        if req.model is not None:
            store.set_setting(db, "model", req.model.strip() or None)
            llm.use_model(req.model.strip() or None)
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
