"""FastAPI app: streaming, citation-checked answers over server-sent events."""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import answer, llm, store

DB = Path("data/gdpr.db")
app = FastAPI(title="gdpr-rag")


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
def chat(req: Ask) -> StreamingResponse:
    """Events: `sources` (retrieved chunks), `token`* (answer text), then `done` or `error`."""

    def events():
        db = store.connect(DB)
        try:
            sources = answer.sources_for(db, req.question)
            yield _sse("sources", sources)
            model = llm.setting("OPENROUTER_MODEL", llm.ANSWER_MODEL)
            text = ""
            for delta in llm.stream(answer.messages_for(req.question, sources), model, temperature=0):
                text += delta
                yield _sse("token", delta)
            yield _sse("done", answer.finalize(text, sources))
        except Exception as e:  # noqa: BLE001 - surface any failure to the client instead of a dropped stream
            yield _sse("error", {"message": str(e)})
        finally:
            db.close()

    return StreamingResponse(events(), media_type="text/event-stream")
