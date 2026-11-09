"""One SQLite file: chunk rows, FTS5 keyword index, one sqlite-vec table per embedding model."""

import json
import re
import sqlite3
from pathlib import Path

import sqlite_vec

SCHEMA = """
create table if not exists chunks(
  id integer primary key, source text not null, label text not null,
  ref text, heading text, text text not null);
create virtual table if not exists chunks_fts using fts5(label, heading, text);
create table if not exists settings(key text primary key, value text not null);
create table if not exists chats(
  id integer primary key, title text not null, created_at text default current_timestamp);
create table if not exists messages(
  id integer primary key, chat_id integer not null references chats(id) on delete cascade,
  role text not null, content text not null, sources text, citations text,
  created_at text default current_timestamp);
create table if not exists documents(
  id integer primary key, name text not null, size integer not null, chunks integer not null,
  created_at text default current_timestamp);
create table if not exists compliance(
  doc_id integer primary key references documents(id) on delete cascade, report text not null,
  created_at text default current_timestamp);
"""

# chat scope -> SQL filter on chunks c; user documents are stored as source "user:<document id>"
SCOPES = {"all": None, "gdpr": "c.source = 'gdpr'", "docs": "c.source like 'user:%'"}


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute("pragma foreign_keys = on")
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


def bm25(db: sqlite3.Connection, query: str, k: int, scope: str = "all") -> list[int]:
    # quote every word so user punctuation can't break FTS5 query syntax
    words = re.findall(r"\w+", query.lower())
    if not words:
        return []
    match = " OR ".join(f'"{w}"' for w in words)
    where = f"and {SCOPES[scope]}" if SCOPES[scope] else ""
    sql = (
        "select chunks_fts.rowid from chunks_fts join chunks c on c.id = chunks_fts.rowid "
        f"where chunks_fts match ? {where} order by bm25(chunks_fts) limit ?"
    )
    return [r[0] for r in db.execute(sql, (match, k))]


def knn(db: sqlite3.Connection, model: str, vector, k: int, scope: str = "all") -> list[int]:
    t = vec_table(model)
    if t not in _vec_tables(db):
        raise RuntimeError(f"no vectors for {model}: run `gdpr-rag ingest --embed-model {model}`")
    blob = sqlite_vec.serialize_float32(vector)
    if not SCOPES[scope]:
        sql = f"select rowid from {t} where embedding match ? and k = ? order by distance"
        return [r[0] for r in db.execute(sql, (blob, k))]
    # filtered: exact scan of the allowed rows, same L2 metric as the index
    sql = (
        f"select v.rowid from {t} v join chunks c on c.id = v.rowid where {SCOPES[scope]} "
        "order by vec_distance_l2(v.embedding, ?) limit ?"
    )
    return [r[0] for r in db.execute(sql, (blob, k))]


def get(db: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    q = f"select * from chunks where id in ({','.join('?' * len(ids))})"
    return {r["id"]: r for r in db.execute(q, ids)}


def get_setting(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("select value from settings where key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(db: sqlite3.Connection, key: str, value: str | None) -> None:
    with db:
        if value:
            db.execute("insert or replace into settings(key, value) values (?, ?)", (key, value))
        else:
            db.execute("delete from settings where key = ?", (key,))


def add_message(db, chat_id: int | None, role: str, content: str, sources=None, citations=None) -> int:
    """Append a message, creating the chat (titled by its first message) when chat_id is None."""
    with db:
        if chat_id is None:
            chat_id = db.execute("insert into chats(title) values (?)", (content[:80],)).lastrowid
        db.execute(
            "insert into messages(chat_id, role, content, sources, citations) values (?, ?, ?, ?, ?)",
            (chat_id, role, content, json.dumps(sources) if sources is not None else None,
             json.dumps(citations) if citations is not None else None),
        )
    return chat_id


def chat_messages(db, chat_id: int) -> list[dict]:
    rows = db.execute("select * from messages where chat_id = ? order by id", (chat_id,))
    return [
        {"role": r["role"], "content": r["content"],
         "sources": json.loads(r["sources"]) if r["sources"] else None,
         "citations": json.loads(r["citations"]) if r["citations"] else None}
        for r in rows
    ]
