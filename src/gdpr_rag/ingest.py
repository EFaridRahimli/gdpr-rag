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


def is_ready(db, model: str) -> bool:
    """Every GDPR chunk has a vector for `model`."""
    total = db.execute("select count(*) from chunks where source = 'gdpr'").fetchone()[0]
    if not total or store.vec_table(model) not in store._vec_tables(db):
        return False
    embedded = db.execute(
        f"select count(*) from chunks c join {store.vec_table(model)} v on v.rowid = c.id "
        "where c.source = 'gdpr'"
    ).fetchone()[0]
    return embedded == total
