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


def test_rrf_rewards_agreement():
    assert rrf([[1, 2], [2, 3]]) == [2, 1, 3]
