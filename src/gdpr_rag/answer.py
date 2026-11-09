"""Grounded answering: retrieve sources, ask the model, keep only citations to those sources."""

import re

from . import llm, retrieve, store

NOT_FOUND = "I couldn't find this in the GDPR sources."

SYSTEM = """You answer questions about the GDPR using only the numbered sources provided.
- Cite every claim with the tag of the source that supports it, e.g. [c2]; several are fine: [c1][c3].
- Never cite a tag that is not in the list, and never use outside knowledge.
- If the sources do not answer the question, reply exactly: NOT_FOUND
- Plain English, concise, short paragraphs. No disclaimers (the app shows one)."""


def sources_for(db, question: str, k: int = 6) -> list[dict]:
    ids = retrieve.search(db, question, k=k, embed_model=llm.setting("EMBED_MODEL", llm.EMBED_MODEL))
    rows = store.get(db, ids)
    return [
        {"cid": f"c{n}", "id": i, "source": rows[i]["source"], "label": rows[i]["label"],
         "heading": rows[i]["heading"], "text": rows[i]["text"]}
        for n, i in enumerate(ids, 1)
    ]


def messages_for(question: str, sources: list[dict]) -> list[dict]:
    listing = "\n\n".join(f"[{s['cid']}] {s['label']} ({s['heading']})\n{s['text']}" for s in sources)
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Sources:\n{listing}\n\nQuestion: {question}"},
    ]


def finalize(text: str, sources: list[dict]) -> dict:
    """Drop citations to unknown tags; an answer with no valid citation counts as not found."""
    valid = {s["cid"] for s in sources}
    cleaned = re.sub(r"\[(c\d+)\]", lambda m: m[0] if m[1] in valid else "", text)
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned).strip()  # tidy " ." left by removed tags
    used = list(dict.fromkeys(re.findall(r"\[(c\d+)\]", cleaned)))
    if "NOT_FOUND" in text or not used:
        return {"answer": NOT_FOUND, "citations": [], "found": False}
    return {"answer": cleaned, "citations": used, "found": True}


def answer(db, question: str) -> tuple[list[dict], dict]:
    """Non-streaming path, used by the eval."""
    sources = sources_for(db, question)
    model = llm.setting("OPENROUTER_MODEL", llm.ANSWER_MODEL)
    return sources, finalize(llm.chat(messages_for(question, sources), model, temperature=0), sources)
