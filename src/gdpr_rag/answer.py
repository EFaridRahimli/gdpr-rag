"""Grounded answering: retrieve sources, ask the model, keep only citations to those sources."""

import re

from . import llm, retrieve, store

NOT_FOUND = "I couldn't find this in the selected sources."

SYSTEM = """You answer questions about the GDPR and the user's own documents using only the
numbered sources provided. Sources are GDPR articles and recitals, or passages from the user's
documents (their label names the document).
- Every sentence must be directly supported by the source it cites, e.g. [c2]; several are fine: [c1][c3].
- Never cite a tag that is not in the list, and never use outside knowledge.
- Do not add examples, lists, numbers, exceptions or conclusions that the sources do not state.
  Keep each source's exact scope and conditions (who it applies to, thresholds, "unless" clauses).
- If a source mentions another article that is not among the sources, do not describe what that
  article says.
- If the sources answer only part of the question, answer that part and say plainly that the
  sources do not cover the rest.
- If the sources do not answer the question at all, reply exactly: NOT_FOUND
- Plain English, concise, short paragraphs. No disclaimers (the app shows one)."""


def sources_for(db, question: str, scope: str = "all", k: int = 6) -> list[dict]:
    ids = retrieve.search(
        db, question, k=k, embed_model=llm.setting("EMBED_MODEL", llm.EMBED_MODEL), scope=scope
    )
    rows = store.get(db, ids)
    return [
        {"cid": f"c{n}", "id": i, "source": rows[i]["source"], "label": rows[i]["label"],
         "ref": rows[i]["ref"], "heading": rows[i]["heading"], "text": rows[i]["text"]}
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
    if not sources:
        return {"answer": NOT_FOUND, "citations": [], "found": False}
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
    model = llm.answer_model()
    return sources, finalize(llm.chat(messages_for(question, sources), model, temperature=0), sources)


REWRITE = """Rewrite the user's last question as one standalone question about the GDPR, filling in
anything it refers to from the conversation. If it is already standalone, return it unchanged.
Output only the question.

Conversation:
{history}

Last question: {question}"""


def standalone(history: list[dict], question: str) -> str:
    """Follow-ups like "and for employees?" need the earlier turns to retrieve the right articles."""
    if not history:
        return question
    turns = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in history[-6:])
    model = llm.fast_model()
    prompt = REWRITE.format(history=turns, question=question)
    return llm.chat([{"role": "user", "content": prompt}], model, temperature=0).strip() or question
