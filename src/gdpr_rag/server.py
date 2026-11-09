"""FastAPI app: streaming, citation-checked answers, chat history, settings, eval results."""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import answer, llm, store

DB = Path("data/gdpr.db")
WEB = Path(__file__).resolve().parents[2] / "web" / "dist"
EVAL_RESULTS = Path("eval/results")


def _db():
    return store.connect(DB)


@asynccontextmanager
async def lifespan(_app):
    db = _db()
    llm.use_api_key(store.get_setting(db, "api_key"))  # a key saved in the UI wins over .env
    llm.use_model(store.get_setting(db, "model"))
    db.close()
    yield


app = FastAPI(title="gdpr-rag", lifespan=lifespan)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    chat_id: int | None = None


@app.post("/api/chat")
def chat(req: Ask) -> StreamingResponse:
    """Events: `chat` (id), `sources`, `token`*, then `done` (cleaned answer) or `error`."""

    def events():
        db = _db()
        try:
            history = store.chat_messages(db, req.chat_id) if req.chat_id else []
            question = answer.standalone(history, req.question)
            chat_id = store.add_message(db, req.chat_id, "user", req.question)
            yield _sse("chat", {"id": chat_id, "question": question})
            sources = answer.sources_for(db, question)
            yield _sse("sources", sources)
            model = llm.setting("OPENROUTER_MODEL", llm.ANSWER_MODEL)
            text = ""
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
        if req.model is not None:
            store.set_setting(db, "model", req.model.strip() or None)
            llm.use_model(req.model.strip() or None)
        return _settings_view(db)
    finally:
        db.close()


@app.get("/api/evals")
def evals() -> dict:
    def load(name):
        p = EVAL_RESULTS / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    answers = load("answers.json")
    return {"retrieval": load("retrieval.json"), "answers": answers and answers["summary"]}


if WEB.exists():  # built frontend; in development Vite serves it and proxies /api here
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
