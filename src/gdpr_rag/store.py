"""One SQLite file: chunk rows, FTS5 keyword index, one sqlite-vec table per embedding model."""

import re
import sqlite3
from pathlib import Path

import sqlite_vec

SCHEMA = """
create table if not exists chunks(
  id integer primary key, source text not null, label text not null,
  ref text, heading text, text text not null);
create virtual table if not exists chunks_fts using fts5(label, heading, text);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.executescript(SCHEMA)
    return db


def vec_table(model: str) -> str:
    return "vec_" + re.sub(r"\W", "_", model)


def _vec_tables(db: sqlite3.Connection) -> list[str]:
    sql = "select name from sqlite_master where sql like 'create virtual table%using vec0%'"
    return [r[0] for r in db.execute(sql)]


def replace_source(db: sqlite3.Connection, source: str, chunks) -> list[int]:
    """Drop every chunk of `source` (rows, keywords, vectors), insert `chunks`, return their ids."""
    with db:
        old = [(r[0],) for r in db.execute("select id from chunks where source = ?", (source,))]
        db.executemany("delete from chunks where id = ?", old)
        for table in ["chunks_fts", *_vec_tables(db)]:
            db.executemany(f"delete from {table} where rowid = ?", old)
        ids = []
        for c in chunks:
            cur = db.execute(
                "insert into chunks(source, label, ref, heading, text) values (?, ?, ?, ?, ?)",
                (source, c.label, c.ref, c.heading, c.text),
            )
            ids.append(cur.lastrowid)
            db.execute(
                "insert into chunks_fts(rowid, label, heading, text) values (?, ?, ?, ?)",
                (cur.lastrowid, c.label, c.heading, c.text),
            )
    return ids


def set_vectors(db: sqlite3.Connection, model: str, ids: list[int], vectors) -> None:
    t = vec_table(model)
    with db:
        db.execute(
            f"create virtual table if not exists {t} using vec0(embedding float[{len(vectors[0])}])"
        )
        db.executemany(f"delete from {t} where rowid = ?", [(i,) for i in ids])
        db.executemany(
            f"insert into {t}(rowid, embedding) values (?, ?)",
            [(i, sqlite_vec.serialize_float32(v)) for i, v in zip(ids, vectors, strict=True)],
        )


def bm25(db: sqlite3.Connection, query: str, k: int) -> list[int]:
    # quote every word so user punctuation can't break FTS5 query syntax
    words = re.findall(r"\w+", query.lower())
    if not words:
        return []
    match = " OR ".join(f'"{w}"' for w in words)
    sql = "select rowid from chunks_fts where chunks_fts match ? order by bm25(chunks_fts) limit ?"
    return [r[0] for r in db.execute(sql, (match, k))]


def knn(db: sqlite3.Connection, model: str, vector, k: int) -> list[int]:
    t = vec_table(model)
    if t not in _vec_tables(db):
        raise SystemExit(f"no vectors for {model}: run `gdpr-rag ingest --embed-model {model}`")
    sql = f"select rowid from {t} where embedding match ? and k = ? order by distance"
    return [r[0] for r in db.execute(sql, (sqlite_vec.serialize_float32(vector), k))]


def get(db: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    q = f"select * from chunks where id in ({','.join('?' * len(ids))})"
    return {r["id"]: r for r in db.execute(q, ids)}
