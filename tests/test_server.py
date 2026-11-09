import importlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from gdpr_rag import compliance, docs, ingest, llm, server, store
from gdpr_rag.gdpr import Chunk


def signed_in(db_path):
    """Answers need Sign in with ChatGPT; give the test app a session."""
    db = store.connect(db_path)
    store.set_setting(db, "chatgpt_session", json.dumps(
        {"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 3600, "scopes": []}))
    db.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB", tmp_path / "app.db")
    monkeypatch.setattr(llm, "embed", lambda texts, model: [[1.0, 0.0] for _ in texts])
    signed_in(server.DB)
    with TestClient(server.app, base_url="http://localhost") as c:
        yield c


def test_questions_and_checks_need_chatgpt_sign_in(client):
    db = store.connect(server.DB)
    store.set_setting(db, "chatgpt_session", None)
    db.close()
    chat = client.post("/api/chat", json={"question": "hi"}).text
    assert "event: error" in chat and "Sign in with ChatGPT" in chat
    doc = client.post("/api/documents", files={"file": ("p.md", b"# A\n\nPolicy text here.")}).json()
    r = client.post(f"/api/documents/{doc['id']}/compliance")
    assert r.status_code == 400 and "Sign in with ChatGPT" in r.json()["detail"]


def _rows(db_path, table):
    db = store.connect(db_path)
    try:
        return db.execute(f"select count(*) from {table}").fetchone()[0]
    finally:
        db.close()


# --- security -----------------------------------------------------------------------------------


def test_foreign_host_is_refused(client):
    assert client.get("/api/settings", headers={"host": "evil.example"}).status_code == 400


def test_cross_site_writes_are_refused_and_same_site_allowed(client):
    for headers in ({"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"},
                    {"origin": "null"}):
        r = client.put("/api/settings", json={"model": "x/y"}, headers=headers)
        assert r.status_code == 403, headers
    ok = client.put("/api/settings", json={"model": "x/y"}, headers={"origin": "http://localhost:8000"})
    assert ok.status_code == 200
    client.put("/api/settings", json={"model": ""})


def test_responses_carry_csp_without_remote_images(client):
    csp = client.get("/api/settings").headers["content-security-policy"]
    assert "img-src 'self' data:" in csp and "frame-ancestors 'none'" in csp


# --- uploads ------------------------------------------------------------------------------------


def test_upload_too_large_or_failed_embedding_leaves_nothing(client, monkeypatch):
    monkeypatch.setattr(docs, "MAX_BYTES", 10)
    r = client.post("/api/documents", files={"file": ("p.md", b"# A\n\nmore than ten bytes")})
    assert r.status_code == 422 and "20 MB" in r.json()["detail"]
    monkeypatch.setattr(docs, "MAX_BYTES", 20 * 1024 * 1024)

    def boom(texts, model):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm, "embed", boom)
    r = client.post("/api/documents", files={"file": ("p.md", b"# A\n\nSome policy text.")})
    assert r.status_code == 502 and "provider down" in r.json()["detail"]
    assert _rows(server.DB, "documents") == 0 and _rows(server.DB, "chunks") == 0


# --- compliance ---------------------------------------------------------------------------------


def _upload(client, text=b"# Retention\n\nWe keep customer data for two years."):
    return client.post("/api/documents", files={"file": ("p.md", text)}).json()["id"]


@pytest.mark.parametrize("reply, status", [
    ('Sure! ```json\n{"items": []}\n```', 200),  # fenced and prose-wrapped: still parsed
    ("I can't do that.", 502),
    ('{"items": "nope"}', 502),
])
def test_compliance_model_output_handling(client, monkeypatch, reply, status):
    doc = _upload(client)
    monkeypatch.setattr(llm, "chat", lambda messages, model, **kw: reply)
    assert client.post(f"/api/documents/{doc}/compliance").status_code == status


def test_compliance_rejects_overlong_policy(client, monkeypatch):
    doc = _upload(client)
    monkeypatch.setattr(compliance, "MAX_POLICY_WORDS", 3)
    r = client.post(f"/api/documents/{doc}/compliance")
    assert r.status_code == 422 and "words" in r.json()["detail"]


# --- background index ---------------------------------------------------------------------------


def _wait_until_not_building(client):
    for _ in range(100):
        index = client.get("/api/settings").json()["index"]
        if index["status"] != "building":
            return index
        time.sleep(0.02)
    raise AssertionError("index never finished building")


def test_failed_index_build_reports_error_and_retry_works(client, monkeypatch):
    monkeypatch.setattr(llm, "ENV_KEY", None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert client.post("/api/index").status_code == 400  # no key yet

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def broken(db, models, log=print):
        raise RuntimeError("")  # empty message: the repr must still say something

    monkeypatch.setattr(ingest, "build", broken)
    client.post("/api/index")
    index = _wait_until_not_building(client)
    assert index["status"] == "error" and "RuntimeError" in index["error"]
    chat = client.post("/api/chat", json={"question": "hi"}).text
    assert "event: error" in chat and "failed" in chat

    def works(db, models, log=print):
        ids = store.replace_source(db, "gdpr", [Chunk("Art. 1", "a", "h", "text")])
        store.set_vectors(db, models[0], ids, [[1.0, 0.0]])

    monkeypatch.setattr(ingest, "build", works)
    client.post("/api/index")
    assert _wait_until_not_building(client)["status"] == "ready"


# --- chat stream --------------------------------------------------------------------------------


def _events(body: str) -> list[tuple[str, object]]:
    out = []
    for block in body.split("\n\n"):
        if block.startswith("event: "):
            name, data = block.split("\n", 1)
            out.append((name[7:], json.loads(data[6:])))
    return out


def _ready_index():
    db = store.connect(server.DB)
    ids = store.replace_source(db, "gdpr", [Chunk("Art. 17(1)", "017.001", "h", "erasure")])
    store.set_vectors(db, llm.setting("EMBED_MODEL", llm.EMBED_MODEL), ids, [[1.0, 0.0]])
    db.close()


SOURCE = {"cid": "c1", "id": 1, "source": "gdpr", "label": "Art. 17(1)", "ref": "017.001",
          "heading": "h", "text": "erasure"}


def test_chat_stream_order_citation_filter_and_saved(client, monkeypatch):
    from gdpr_rag import answer

    _ready_index()
    monkeypatch.setattr(answer, "sources_for", lambda db, q, scope="all", k=6: [SOURCE])
    monkeypatch.setattr(llm, "stream", lambda m, model, **kw: iter(["You can ask [c1]", " or [c9]."]))
    events = _events(client.post("/api/chat", json={"question": "Can I delete my data?"}).text)
    assert [e for e, _ in events] == ["chat", "sources", "token", "token", "done"]
    done = events[-1][1]
    assert done["citations"] == ["c1"] and "[c9]" not in done["answer"]
    chat_id = events[0][1]["id"]
    saved = client.get(f"/api/chats/{chat_id}").json()["messages"]
    assert [m["role"] for m in saved] == ["user", "assistant"] and saved[1]["citations"] == ["c1"]


def test_chat_error_mid_stream_becomes_error_event(client, monkeypatch):
    from gdpr_rag import answer

    _ready_index()
    monkeypatch.setattr(answer, "sources_for", lambda db, q, scope="all", k=6: [SOURCE])

    def dies(m, model, **kw):
        yield "Partial"
        raise RuntimeError("stream broke")

    monkeypatch.setattr(llm, "stream", dies)
    events = _events(client.post("/api/chat", json={"question": "q"}).text)
    assert events[-1] == ("error", {"message": "stream broke"})


# --- key precedence -----------------------------------------------------------------------------


def test_settings_key_wins_over_env_and_clearing_falls_back(client, monkeypatch):
    monkeypatch.setattr(llm, "ENV_KEY", "sk-or-env-0000000000000000enva")
    monkeypatch.setattr(llm, "key_is_valid", lambda key: True)
    llm.use_api_key(None)
    assert client.get("/api/settings").json()["key_source"] == "env"
    saved = client.put("/api/settings", json={"api_key": "sk-or-ui-00000000000000000uiab"}).json()
    assert saved["key_source"] == "settings" and saved["key_hint"].endswith("uiab")
    assert llm.setting("OPENROUTER_API_KEY", "") == "sk-or-ui-00000000000000000uiab"
    cleared = client.put("/api/settings", json={"api_key": ""}).json()
    assert cleared["key_source"] == "env" and cleared["key_hint"].endswith("enva")
    assert llm.setting("OPENROUTER_API_KEY", "") == "sk-or-env-0000000000000000enva"


def test_unreachable_openrouter_is_not_reported_as_a_bad_key(client, monkeypatch):
    def offline(key):
        raise ConnectionError("no network")

    monkeypatch.setattr(llm, "key_is_valid", offline)
    r = client.put("/api/settings", json={"api_key": "sk-or-whatever"})
    assert r.status_code == 502 and "Couldn't reach OpenRouter" in r.json()["detail"]


# --- retrieval details --------------------------------------------------------------------------


def test_bm25_ignores_stopwords(tmp_path):
    db = store.connect(tmp_path / "t.db")
    ids = store.replace_source(db, "gdpr", [
        Chunk("Art. 1", "a", "h", "what is the scope of this regulation and how does it apply"),
        Chunk("Art. 2", "b", "h", "the controller must erase personal data"),
    ])
    assert store.bm25(db, "What is erasure? How do I erase it?", 5) == [ids[1]]


def test_model_switch_re_embeds_uploads(tmp_path, monkeypatch):
    real = importlib.reload(ingest)  # the autouse fixture stubs embed_missing; use the real one here
    monkeypatch.setattr(llm, "embed", lambda texts, model: [[0.0, 1.0] for _ in texts])
    db = store.connect(tmp_path / "t.db")
    g = store.replace_source(db, "gdpr", [Chunk("Art. 1", "a", "h", "x")])
    u = store.replace_source(db, "user:1", [Chunk("Policy", "s1", "h", "y")])
    store.set_vectors(db, "m/old", g + u, [[1.0, 0.0], [1.0, 0.0]])
    store.set_vectors(db, "m/new", g, [[1.0, 0.0]])  # GDPR re-indexed, the upload wasn't
    assert real.is_ready(db, "m/old") and not real.is_ready(db, "m/new")
    assert real.embed_missing(db, "m/new") == 1
    assert real.is_ready(db, "m/new")


def test_generate_writes_a_new_set_that_skips_used_paragraphs(tmp_path, monkeypatch):
    from gdpr_rag import evals

    db = store.connect(tmp_path / "t.db")
    words = " ".join(["word"] * 40)
    store.replace_source(db, "gdpr", [
        Chunk("Art. 5(1)", "a", "Chapter II > Article 5", f"principles {words}"),
        Chunk("Art. 6(1)", "b", "Chapter II > Article 6", f"lawfulness {words}"),
        Chunk("Art. 7(1)", "c", "Chapter II > Article 7", f"consent {words}"),
    ])
    golden, heldout = tmp_path / "golden.jsonl", tmp_path / "heldout.jsonl"
    golden.write_text('{"id": 1, "question": "q", "target": "Art. 5(1)", "source_label": "Art. 5(1)"}\n')
    monkeypatch.setattr(evals, "GOLDEN", golden)
    monkeypatch.setattr(evals, "HELDOUT", heldout)
    monkeypatch.setattr(evals, "EVAL_DIR", tmp_path)

    def fake_chat(messages, model, **kw):
        text = messages[0]["content"]
        return "[0, 1]" if "Return a JSON array" in text else "Is this allowed for a small shop?"

    monkeypatch.setattr(llm, "chat", fake_chat)
    evals.generate(db, heldout, n=5, sample=5, seed=1)
    targets = {r["target"] for r in evals.load(heldout)}
    assert targets == {"Art. 6(1)", "Art. 7(1)"}  # Art. 5(1) is already in the golden set
