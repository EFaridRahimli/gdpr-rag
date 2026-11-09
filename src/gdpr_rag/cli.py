import argparse
from pathlib import Path

from . import llm, retrieve, store

DATA = Path("data")


def main() -> None:
    p = argparse.ArgumentParser(prog="gdpr-rag")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest", help="fetch, chunk, embed and index the GDPR")
    i.add_argument("--embed-model", action="append", help="repeatable; default: EMBED_MODEL")
    s = sub.add_parser("search", help="retrieve chunks for a question")
    s.add_argument("query")
    s.add_argument("--mode", choices=retrieve.MODES, default=retrieve.DEFAULT_MODE)
    s.add_argument("--embed-model", default=llm.setting("EMBED_MODEL", llm.EMBED_MODEL))
    s.add_argument("-k", type=int, default=6)
    e = sub.add_parser("eval", help="golden set and retrieval metrics")
    e.add_argument("what", choices=["generate", "retrieval", "answers", "compliance"])
    e.add_argument("--bakeoff", action="store_true", help="all embedding models x modes")
    e.add_argument("--check", action="store_true", help="fail below eval/baseline.json")
    e.add_argument("--set", choices=["golden", "heldout"], default="golden",
                   help="golden: the tuning set; heldout: questions never used to choose anything")
    v = sub.add_parser("serve", help="run the web app on localhost")
    v.add_argument("--port", type=int, default=8000)
    a = p.parse_args()

    if a.cmd == "serve":
        import uvicorn

        # localhost only: the API spends the user's OpenRouter credit
        uvicorn.run("gdpr_rag.server:app", host="127.0.0.1", port=a.port)
        return
    db = store.connect(DATA / "gdpr.db")
    if a.cmd in ("eval", "search"):
        from . import server

        server.apply_saved_settings()  # same key, models and ChatGPT sign-in as the app
    if a.cmd == "eval":
        from . import evals

        questions = evals.HELDOUT if a.set == "heldout" else evals.GOLDEN
        if a.what == "generate":
            if a.set == "heldout":  # different seed; skips every paragraph the golden set used
                evals.generate(db, evals.HELDOUT, n=30, sample=50, seed=11)
            else:
                evals.generate(db)
            return
        if a.what == "compliance":
            evals.compliance_eval()
            return
        if a.what == "answers":
            evals.answers(DATA / "gdpr.db", questions)
            return
        config = evals.baseline_config() if a.check and not a.bakeoff else None
        results = evals.retrieval(DATA / "gdpr.db", a.bakeoff, questions, config)
        if a.check:
            evals.check_baseline(results)
    elif a.cmd == "ingest":
        from . import ingest

        models = a.embed_model or [llm.setting("EMBED_MODEL", llm.EMBED_MODEL)]
        ingest.build(db, models)
        for model in models:  # uploads too, e.g. after switching embedding model
            ingest.embed_missing(db, model)
    else:
        ids = retrieve.search(db, a.query, a.mode, a.k, a.embed_model)
        rows = store.get(db, ids)
        for i in ids:
            print(f"{rows[i]['label']:<14} {rows[i]['text'][:110]}")
