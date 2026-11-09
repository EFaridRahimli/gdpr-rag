"""Build the GDPR index: download, chunk, embed, store. Used by the CLI and by the server."""

from pathlib import Path

from . import gdpr, llm, retrieve, store

RAW = Path("data/raw/gdpr.xhtml")


def build(db, models: list[str], log=print) -> int:
    chunks = gdpr.parse(gdpr.fetch(RAW))
    texts = [retrieve.passage(c.label, c.heading, c.text) for c in chunks]
    # embed before writing anything, so a failed call leaves the previous index intact
    vectors = {m: llm.embed(texts, m) for m in models}
    ids = store.replace_source(db, "gdpr", chunks)
    for model, vecs in vectors.items():
        store.set_vectors(db, model, ids, vecs)
        log(f"{model}: embedded {len(ids)} chunks")
    return len(ids)


def _missing(db, model: str) -> list:
    """Chunks (any source) without a vector for `model`, e.g. uploads after the model changed."""
    t = store.vec_table(model)
    if t not in store._vec_tables(db):
        return db.execute("select * from chunks").fetchall()
    return db.execute(f"select * from chunks where id not in (select rowid from {t})").fetchall()


def embed_missing(db, model: str) -> int:
    rows = _missing(db, model)
    if rows:
        texts = [retrieve.passage(r["label"], r["heading"], r["text"]) for r in rows]
        store.set_vectors(db, model, [r["id"] for r in rows], llm.embed(texts, model))
    return len(rows)


def _gdpr_count(db) -> int:
    return db.execute("select count(*) from chunks where source = 'gdpr'").fetchone()[0]


def is_ready(db, model: str) -> bool:
    """The GDPR is indexed and every chunk, uploads included, has a vector for `model`."""
    return _gdpr_count(db) > 0 and not _missing(db, model)


def ensure(db, model: str) -> None:
    """Build what's missing: the GDPR if it was never indexed, then vectors for any other chunk."""
    if _gdpr_count(db) == 0:
        build(db, [model], log=lambda _msg: None)
    embed_missing(db, model)
