"""Synthetic golden set + retrieval bake-off (recall@5, recall@10, MRR)."""

import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import llm, retrieve, store

EVAL_DIR = Path("eval")
GOLDEN = EVAL_DIR / "golden.jsonl"
BASELINE = EVAL_DIR / "baseline.json"
WORKERS = 2  # stays under OpenRouter's new-account rate limit
llm.PACE_RPM = int(llm.setting("OPENROUTER_RPM", "18"))  # batch calls: pace per model
EMBED_MODELS = [
    "qwen/qwen3-embedding-8b",
    "mongodb/voyage-4-large",
    "google/gemini-embedding-2",
    "openai/text-embedding-3-large",
]

ASK = """You write test questions for a GDPR search engine.
Read the passage and write ONE question that a non-lawyer (a customer, an employee, a small-business
owner) might ask and that this passage answers.
Rules: everyday words; never copy more than 3 consecutive words from the passage; no article or
recital numbers; one sentence. Output only the question.

Passage ({label}, {heading}):
{text}"""

FILTER = """Below are candidate test questions for a GDPR search engine, each with the passage it was
written from. Keep a question only if it is unambiguous, the passage clearly answers it, and it is
not a near-duplicate of an earlier kept question.
Return a JSON array of the numbers to keep, nothing else.

{items}"""


def longest_copied_run(question: str, passage: str) -> int:
    """Length of the longest run of consecutive words the question shares with the passage."""
    q, p = re.findall(r"\w+", question.lower()), re.findall(r"\w+", passage.lower())
    best, prev = 0, [0] * (len(p) + 1)
    for qw in q:
        cur = [0] * (len(p) + 1)
        for j, pw in enumerate(p, 1):
            if qw == pw:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def target_of(label: str) -> str:
    """Paragraph-level target, so a hit doesn't depend on point-level chunking: Art. 17(1)(b) -> Art. 17(1)."""
    m = re.match(r"Art\. \d+(\(\d+\))?", label)
    return m[0] if m else label


def is_hit(label: str, target: str) -> bool:
    return label == target or label.startswith(target + "(")


def generate(db, n: int = 50, sample: int = 80, seed: int = 7) -> None:
    model = llm.setting("EVAL_MODEL", llm.EVAL_MODEL)
    rows = db.execute(
        "select * from chunks where source = 'gdpr' and label like 'Art.%' "
        "and heading not like 'Chapter X %' and heading not like 'Chapter XI %'"
    ).fetchall()
    rows = random.Random(seed).sample([r for r in rows if len(r["text"].split()) >= 30], sample)

    def ask(r):
        q = llm.chat([{"role": "user", "content": ASK.format(**dict(r))}], model).strip()
        return r, q

    with ThreadPoolExecutor(WORKERS) as pool:
        drafts = [(r, q) for r, q in pool.map(ask, rows) if longest_copied_run(q, r["text"]) <= 3]
    items = "\n\n".join(
        f"[{i}] Question: {q}\nPassage ({r['label']}): {r['text']}" for i, (r, q) in enumerate(drafts)
    )
    out = llm.chat([{"role": "user", "content": FILTER.format(items=items)}], model, temperature=0)
    keep = sorted({int(x) for x in re.findall(r"\d+", out) if int(x) < len(drafts)})
    golden = [drafts[i] for i in keep][:n]
    EVAL_DIR.mkdir(exist_ok=True)
    with GOLDEN.open("w", encoding="utf-8") as f:
        for i, (r, q) in enumerate(golden, 1):
            rec = {"id": i, "question": q, "target": target_of(r["label"]), "source_label": r["label"]}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"{len(rows)} sampled, {len(drafts)} passed the copy check, {len(keep)} kept by the filter,"
          f" wrote {len(golden)} to {GOLDEN}")


def _score(db_path: Path, golden: list[dict], mode: str, embed_model: str) -> dict:
    if mode != "bm25":
        retrieve.query_vectors([g["question"] for g in golden], embed_model)

    def rank(g):
        db = store.connect(db_path)  # one connection per worker thread
        ids = retrieve.search(db, g["question"], mode, 10, embed_model)
        rows = store.get(db, ids)
        labels = [rows[i]["label"] for i in ids]
        return next((n for n, lab in enumerate(labels, 1) if is_hit(lab, g["target"])), None)

    with ThreadPoolExecutor(WORKERS) as pool:
        ranks = list(pool.map(rank, golden))
    total = len(golden)
    return {
        "mode": mode,
        "embed_model": embed_model if mode != "bm25" else "-",
        "recall@5": sum(1 for r in ranks if r and r <= 5) / total,
        "recall@10": sum(1 for r in ranks if r) / total,
        "mrr": sum(1 / r for r in ranks if r) / total,
    }


def retrieval(db_path: Path, bakeoff: bool) -> list[dict]:
    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()]
    default = (retrieve.DEFAULT_MODE, llm.setting("EMBED_MODEL", llm.EMBED_MODEL))
    configs = [default]
    if bakeoff:
        modes = [m for m in retrieve.MODES if m != "bm25"]
        try:
            import fastembed  # noqa: F401
        except ImportError:
            modes.remove("local-rerank")
            print("skipping local-rerank: install with `uv sync --extra local-rerank`")
        configs = [("bm25", EMBED_MODELS[0])] + [(m, e) for e in EMBED_MODELS for m in modes]
    results = [_score(db_path, golden, m, e) for m, e in configs]
    table = "| mode | embedding model | recall@5 | recall@10 | MRR |\n|---|---|---|---|---|\n" + "\n".join(
        f"| {r['mode']} | {r['embed_model']} | {r['recall@5']:.2f} | {r['recall@10']:.2f} | {r['mrr']:.2f} |"
        for r in results
    )
    print(f"{len(golden)} questions\n\n{table}")
    if bakeoff:
        out = EVAL_DIR / "results"
        out.mkdir(parents=True, exist_ok=True)
        (out / "retrieval.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        (out / "retrieval.md").write_text(table + "\n", encoding="utf-8")
    return results


def check_baseline(result: dict, tolerance: float = 0.02) -> None:
    """CI gate: fail if the default config's recall@10 drops below the committed baseline."""
    base = json.loads(BASELINE.read_text(encoding="utf-8"))["recall@10"]
    if result["recall@10"] < base - tolerance:
        raise SystemExit(f"recall@10 {result['recall@10']:.2f} fell below baseline {base:.2f}")
    print(f"recall@10 {result['recall@10']:.2f} vs baseline {base:.2f}: ok")


JUDGE = """You grade an answer from a GDPR assistant against the sources it was given.

Question: {question}

Sources:
{sources}

Answer:
{answer}

faithful: every claim in the answer is supported by the sources.
citations_ok: every [cN] tag is attached to a claim that source cN supports.
Return only JSON: {{"faithful": true or false, "citations_ok": true or false, "reason": "<one sentence>"}}"""


def answers(db_path: Path) -> None:
    """Answer every golden question, check the target article is cited, LLM-judge the rest."""
    from . import answer

    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()]
    judge_model = llm.setting("EVAL_MODEL", llm.EVAL_MODEL)

    def run(g):
        db = store.connect(db_path)
        sources, res = answer.answer(db, g["question"])
        rec = {"id": g["id"], "question": g["question"], "target": g["target"], **res}
        cited = [s for s in sources if s["cid"] in res["citations"]]
        rec["target_cited"] = any(is_hit(s["label"], g["target"]) for s in cited)
        if res["found"]:
            listing = "\n\n".join(f"[{s['cid']}] {s['label']}\n{s['text']}" for s in sources)
            prompt = JUDGE.format(question=g["question"], sources=listing, answer=res["answer"])
            out = llm.chat([{"role": "user", "content": prompt}], judge_model, temperature=0)
            m = re.search(r"\{.*\}", out, re.DOTALL)
            rec.update(json.loads(m[0]) if m else {"faithful": False, "citations_ok": False,
                                                   "reason": f"unparseable judge output: {out[:80]}"})
        return rec

    with ThreadPoolExecutor(WORKERS) as pool:
        recs = list(pool.map(run, golden))
    found = [r for r in recs if r["found"]]
    n, f = len(recs), max(len(found), 1)
    row = {
        "answered": len(found) / n,
        "target_cited": sum(r["target_cited"] for r in recs) / n,
        "faithful": sum(bool(r.get("faithful")) for r in found) / f,
        "citations_ok": sum(bool(r.get("citations_ok")) for r in found) / f,
    }
    table = ("| answered | target article cited | faithful | citations correct |\n|---|---|---|---|\n"
             f"| {row['answered']:.2f} | {row['target_cited']:.2f} | {row['faithful']:.2f}"
             f" | {row['citations_ok']:.2f} |")
    out = EVAL_DIR / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "answers.json").write_text(json.dumps({"summary": row, "answers": recs}, indent=2,
                                                 ensure_ascii=False), encoding="utf-8")
    (out / "answers.md").write_text(table + "\n", encoding="utf-8")
    print(f"{n} questions, answers by {llm.setting('OPENROUTER_MODEL', llm.ANSWER_MODEL)},"
          f" judged by {judge_model}\n\n{table}")


def compliance_eval() -> dict:
    """Audit the synthetic policies and score planted gaps found vs covered items wrongly flagged."""
    import tempfile

    from . import compliance, docs

    policies = EVAL_DIR / "policies"
    truth = json.loads((policies / "truth.json").read_text(encoding="utf-8"))
    rows, found, gaps, alarms, covered = [], 0, 0, 0, 0
    with tempfile.TemporaryDirectory() as tmp:
        db = store.connect(Path(tmp) / "policies.db")
        for n, (name, expected) in enumerate(truth.items(), 1):
            md = (policies / name).read_text(encoding="utf-8")
            store.replace_source(db, f"user:{n}", docs.chunk_markdown(md, Path(name).stem))
            report = compliance.check(db, n)
            status = {i["id"]: i["status"] for i in report["items"]}
            for item_id, want in expected.items():
                flagged = status[item_id] in ("Missing", "Partial")
                ok = flagged if want == "gap" else not flagged
                gaps += want == "gap"
                found += want == "gap" and flagged
                covered += want == "covered"
                alarms += want == "covered" and flagged
                rows.append({"policy": name, "item": item_id, "expected": want,
                             "status": status[item_id], "ok": ok})
        db.close()
    summary = {"gaps_found": found / gaps, "false_alarms": alarms / covered,
               "planted_gaps": gaps, "covered_items": covered}
    table = ("| planted gaps found | covered items wrongly flagged |\n|---|---|\n"
             f"| {found}/{gaps} ({summary['gaps_found']:.2f}) | {alarms}/{covered}"
             f" ({summary['false_alarms']:.2f}) |")
    out = EVAL_DIR / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "compliance.json").write_text(json.dumps({"summary": summary, "items": rows}, indent=2),
                                         encoding="utf-8")
    (out / "compliance.md").write_text(table + "\n", encoding="utf-8")
    misses = [r for r in rows if not r["ok"]]
    print(f"{len(truth)} policies, model {llm.setting('OPENROUTER_MODEL', llm.ANSWER_MODEL)}\n\n{table}")
    for r in misses:
        print(f"  wrong: {r['policy']} {r['item']}: expected {r['expected']}, got {r['status']}")
    return summary
