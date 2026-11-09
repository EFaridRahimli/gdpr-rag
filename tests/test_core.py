from gdpr_rag.gdpr import parse
from gdpr_rag.retrieve import rrf

DOC = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<div class="eli-subdivision" id="rct_1"><table><tr><td><p>(1)</p></td> <td><p>Protection of data is a right.</p></td></tr></table></div>
<div id="cpt_II"><p>CHAPTER II</p><div id="cpt_II.tit_1"><p>Principles</p></div>
<div class="eli-subdivision" id="art_6">
  <p class="oj-ti-art">Article 6</p>
  <div class="eli-title" id="art_6.tit_1"><p class="oj-sti-art">Lawfulness of processing</p></div>
  <div id="006.001"><p>1. Processing shall be lawful if:</p>
    <table><tr><td><p>(a)</p></td> <td><p>the data subject has given consent;</p></td></tr></table>
    <table><tr><td><p>(f)</p></td> <td><p>legitimate interests apply.</p></td></tr></table>
    <p>Point (f) shall not apply to public authorities.</p>
  </div>
</div>
<div class="eli-subdivision" id="art_10">
  <p class="oj-ti-art">Article 10</p>
  <div class="eli-title" id="art_10.tit_1"><p class="oj-sti-art">Criminal data</p></div>
  <p>Processing of criminal data needs official authority.</p>
</div></div>
</body></html>"""


def test_parse_whole_paragraphs():
    by_label = {c.label: c for c in parse(DOC)}
    assert set(by_label) == {"Recital 1", "Art. 6(1)", "Art. 10"}
    assert by_label["Recital 1"].text == "Protection of data is a right."
    assert by_label["Art. 6(1)"].heading == "Chapter II Principles > Article 6 Lawfulness of processing"
    assert by_label["Art. 10"].text == "Processing of criminal data needs official authority."


def test_parse_splits_long_paragraph_into_points():
    chunks = [c for c in parse(DOC, max_words=5) if c.label.startswith("Art. 6")]
    assert [c.label for c in chunks] == ["Art. 6(1)(a)", "Art. 6(1)(f)", "Art. 6(1)"]
    assert chunks[1].text == "1. Processing shall be lawful if: (f) legitimate interests apply."


def test_store_replace_source_drops_old_rows_keywords_and_vectors(tmp_path):
    from gdpr_rag import store
    from gdpr_rag.gdpr import Chunk

    db = store.connect(tmp_path / "t.db")
    old = store.replace_source(db, "gdpr", [Chunk("Art. 1", "a", "h", "consent rules")])
    store.set_vectors(db, "m/x", old, [[1.0, 0.0]])
    new = store.replace_source(
        db, "gdpr", [Chunk("Art. 2", "b", "h", "retention rules"), Chunk("Art. 3", "c", "h", "x")]
    )
    store.set_vectors(db, "m/x", new, [[1.0, 0.0], [0.0, 1.0]])
    assert store.bm25(db, "consent?", 5) == []
    assert store.bm25(db, "retention", 5) == [new[0]]
    assert store.knn(db, "m/x", [0.0, 1.0], 5) == [new[1], new[0]]


def test_eval_helpers():
    from gdpr_rag.evals import is_hit, longest_copied_run, target_of

    assert longest_copied_run("Can I withdraw my consent later?", "withdraw his or her consent") == 1
    assert longest_copied_run("is the right to erasure absolute", "the right to erasure shall") == 4
    assert target_of("Art. 17(1)(b)") == "Art. 17(1)"
    assert target_of("Art. 10") == "Art. 10"
    assert is_hit("Art. 17(1)(b)", "Art. 17(1)") and not is_hit("Art. 17(10)", "Art. 17(1)")


def test_finalize_keeps_only_known_citations():
    from gdpr_rag.answer import NOT_FOUND, finalize

    sources = [{"cid": "c1"}, {"cid": "c2"}]
    res = finalize("You can withdraw consent [c2][c9]. It is free [c1] .", sources)
    assert res == {"answer": "You can withdraw consent [c2]. It is free [c1].",
                   "citations": ["c2", "c1"], "found": True}
    assert finalize("Probably yes [c7].", sources)["answer"] == NOT_FOUND  # no valid citation left
    assert finalize("NOT_FOUND", sources)["found"] is False


def test_rrf_rewards_agreement():
    assert rrf([[1, 2], [2, 3]]) == [2, 1, 3]


def test_server_settings_never_return_the_key_and_chats_cascade(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from gdpr_rag import llm, server, store

    monkeypatch.setattr(server, "DB", tmp_path / "app.db")
    monkeypatch.setattr(llm, "ENV_KEY", None)
    monkeypatch.setattr(llm, "key_is_valid", lambda key: key.startswith("sk-or-good"))
    key = "sk-or-good-" + "x" * 40 + "abcd"
    with TestClient(server.app) as client:
        assert client.put("/api/settings", json={"api_key": "sk-or-bad"}).status_code == 400
        saved = client.put("/api/settings", json={"api_key": key}).json()
        assert saved["key_hint"] == "sk-or-…abcd" and saved["key_source"] == "settings"
        assert key not in client.get("/api/settings").text
        cleared = client.put("/api/settings", json={"api_key": ""}).json()
        assert cleared["key_source"] is None

        db = store.connect(server.DB)
        chat_id = store.add_message(db, None, "user", "Can I delete my data?")
        store.add_message(db, chat_id, "assistant", "Yes [c1].", [{"cid": "c1"}], ["c1"])
        assert client.get(f"/api/chats/{chat_id}").json()["messages"][1]["citations"] == ["c1"]
        assert client.delete(f"/api/chats/{chat_id}").status_code == 204
        assert client.get(f"/api/chats/{chat_id}").status_code == 404
        assert db.execute("select count(*) from messages").fetchone()[0] == 0
    llm.use_api_key(None)


def test_chunk_markdown_follows_headings_and_caps_size():
    from gdpr_rag.docs import chunk_markdown

    md = "Intro text.\n\n# Data we collect\n\nName and email.\n\n## Retention\n\n" + (
        "We keep data. " * 30 + "\n\n"
    ) * 3
    chunks = chunk_markdown(md, "Acme policy", max_words=100)
    assert chunks[0].label == "Acme policy" and chunks[0].text == "Intro text."
    assert chunks[1].heading == "Acme policy > Data we collect"
    retention = [c for c in chunks if c.label == "Acme policy: Retention"]
    assert len(retention) == 3  # 3 paragraphs of 90 words, cap 100 -> one per chunk
    assert retention[0].heading == "Acme policy > Data we collect > Retention"


def test_to_markdown_rejects_bad_files_and_reads_docx_and_pdf():
    import io

    import docx
    import pymupdf
    import pytest

    from gdpr_rag.docs import UploadError, to_markdown

    with pytest.raises(UploadError, match="Unsupported"):
        to_markdown("policy.exe", b"x")
    blank = pymupdf.open()
    blank.new_page()
    with pytest.raises(UploadError, match="scanned"):
        to_markdown("scan.pdf", blank.tobytes())

    d = docx.Document()
    d.add_heading("Retention", level=1)
    d.add_paragraph("We keep data for two years.")
    buf = io.BytesIO()
    d.save(buf)
    assert to_markdown("p.docx", buf.getvalue()).startswith("# Retention\n\nWe keep data")

    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "We keep customer data for two years after closing. " * 3)
    assert "two years" in to_markdown("p.pdf", pdf.tobytes())


def test_scope_filters_keyword_and_vector_search(tmp_path):
    from gdpr_rag import store
    from gdpr_rag.gdpr import Chunk

    db = store.connect(tmp_path / "t.db")
    g = store.replace_source(db, "gdpr", [Chunk("Art. 5(1)", "a", "h", "storage limitation data")])
    u = store.replace_source(db, "user:1", [Chunk("Policy", "s1", "h", "we keep data two years")])
    store.set_vectors(db, "m/x", g + u, [[1.0, 0.0], [0.9, 0.1]])
    assert set(store.bm25(db, "data", 5)) == {g[0], u[0]}
    assert store.bm25(db, "data", 5, "docs") == u and store.bm25(db, "data", 5, "gdpr") == g
    assert store.knn(db, "m/x", [1.0, 0.0], 5, "docs") == u
    assert store.knn(db, "m/x", [0.0, 1.0], 5, "gdpr") == g


def test_document_upload_list_delete(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from gdpr_rag import llm, server, store

    monkeypatch.setattr(server, "DB", tmp_path / "app.db")
    monkeypatch.setattr(llm, "embed", lambda texts, model: [[1.0, 0.0] for _ in texts])
    with TestClient(server.app) as client:
        bad = client.post("/api/documents", files={"file": ("x.exe", b"MZ")})
        assert bad.status_code == 422 and "Unsupported" in bad.json()["detail"]
        md = b"# Retention\n\nWe keep customer data for two years.\n\n# Rights\n\nEmail us."
        doc = client.post("/api/documents", files={"file": ("Acme policy.md", md)}).json()
        assert doc["name"] == "Acme policy.md" and doc["chunks"] == 2
        assert [d["id"] for d in client.get("/api/documents").json()] == [doc["id"]]
        db = store.connect(server.DB)
        assert store.bm25(db, "customer", 5, "docs")
        assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
        assert store.bm25(db, "customer", 5, "docs") == []
        assert client.get("/api/documents").json() == []
        assert client.delete(f"/api/documents/{doc['id']}").status_code == 404


def test_compliance_grounding_drops_invented_quotes():
    from gdpr_rag.compliance import CHECKLIST, ground

    policy = "## Retention\n\nWe keep **order data** for six years.\n\nContact privacy@acme.example."
    items = ground([
        {"id": "retention", "status": "Covered", "evidence": ["We keep order data for six years."]},
        {"id": "rights", "status": "Covered", "evidence": ["You may access your data."], "fix": ""},
        {"id": "controller", "status": "Partial", "evidence": ["Contact … acme.example."], "fix": "Add the name."},
        {"id": "automated", "status": "Bogus"},
    ], policy)
    by_id = {i["id"]: i for i in items}
    assert [i["id"] for i in items] == [c[0] for c in CHECKLIST]
    assert by_id["retention"]["status"] == "Covered" and by_id["retention"]["fix"] == ""
    assert by_id["rights"]["status"] == "Missing" and by_id["rights"]["evidence"] == []  # invented
    assert by_id["controller"]["status"] == "Partial"  # ellipsis quote: both parts are in the text
    assert by_id["automated"]["status"] == "Missing"
    assert by_id["source"]["note"] == "Not assessed by the model."


def test_compliance_endpoint_runs_stores_and_cascades(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from gdpr_rag import llm, server

    monkeypatch.setattr(server, "DB", tmp_path / "app.db")
    monkeypatch.setattr(llm, "embed", lambda texts, model: [[1.0, 0.0] for _ in texts])
    reply = {"items": [{"id": "retention", "status": "Covered",
                        "evidence": ["We keep customer data for two years."]}]}
    monkeypatch.setattr(llm, "chat", lambda messages, model, **kw: json.dumps(reply))
    with TestClient(server.app) as client:
        md = b"# Retention\n\nWe keep customer data for two years."
        doc = client.post("/api/documents", files={"file": ("p.md", md)}).json()
        assert client.get(f"/api/documents/{doc['id']}/compliance").status_code == 404
        report = client.post(f"/api/documents/{doc['id']}/compliance").json()
        assert report["counts"]["Covered"] == 1 and report["counts"]["Missing"] == 13
        assert client.get(f"/api/documents/{doc['id']}/compliance").json()["items"] == report["items"]
        client.delete(f"/api/documents/{doc['id']}")
        assert client.get(f"/api/documents/{doc['id']}/compliance").status_code == 404


def test_first_run_index_builds_after_key_is_saved(tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from gdpr_rag import ingest, llm, server, store
    from gdpr_rag.gdpr import Chunk

    monkeypatch.setattr(server, "DB", tmp_path / "app.db")
    monkeypatch.setattr(llm, "ENV_KEY", None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(llm, "key_is_valid", lambda key: True)

    def fake_build(db, models, log=print):
        ids = store.replace_source(db, "gdpr", [Chunk("Art. 1", "a", "h", "text")])
        store.set_vectors(db, models[0], ids, [[1.0, 0.0]])
        return 1

    monkeypatch.setattr(ingest, "build", fake_build)
    with TestClient(server.app) as client:  # no key: nothing starts
        assert client.get("/api/settings").json()["index"]["status"] == "missing"
        chat = client.post("/api/chat", json={"question": "hi"}).text
        assert "event: error" in chat and "Settings" in chat
        client.put("/api/settings", json={"api_key": "sk-or-new"})
        for _ in range(50):  # background thread
            if client.get("/api/settings").json()["index"]["status"] == "ready":
                break
            time.sleep(0.05)
        assert client.get("/api/settings").json()["index"]["status"] == "ready"
    llm.use_api_key(None)
