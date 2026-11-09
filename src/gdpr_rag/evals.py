"""Synthetic golden set + retrieval bake-off (recall@5, recall@10, MRR)."""

import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from . import llm, retrieve, store

EVAL_DIR = Path("eval")
GOLDEN = EVAL_DIR / "golden.jsonl"  # tuning set: every pipeline choice was made on these
HELDOUT = EVAL_DIR / "heldout.jsonl"  # generated after tuning, never used to choose anything
UNANSWERABLE = EVAL_DIR / "unanswerable.jsonl"  # out-of-scope questions the app should decline
BASELINE = EVAL_DIR / "baseline.json"
WORKERS = 2  # stays under OpenRouter's new-account rate limit
llm.PACE_RPM = int(llm.setting("OPENROUTER_RPM", "18"))  # batch calls: pace per model
EMBED_MODELS = [
    "qwen/qwen3-embedding-8b",
    "mongodb/voyage-4-large",
    "google/gemini-embedding-2",
    "openai/text-embedding-3-large",
]
# Local (on-device) embeddings were measured and dropped: the best, Qwen3-Embedding-0.6B, reached
# 0.82 recall@5 even with reranking (vs 1.00 for Gemini); see the README's design decisions.

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


def _model_suffix() -> str:
    """Results for a non-default answer model (e.g. a ChatGPT plan model) get their own files."""
    model = llm.answer_model()
    return "" if model == llm.ANSWER_MODEL else "-" + re.sub(r"\W+", "-", model)


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def generate(db, out: Path = GOLDEN, n: int = 50, sample: int = 80, seed: int = 7) -> None:
    """Synthetic questions from sampled article paragraphs. A new set skips paragraphs already used."""
    model = llm.setting("EVAL_MODEL", llm.EVAL_MODEL)
    used = {target_of(g["source_label"]) for p in (GOLDEN, HELDOUT) if p.exists() and p != out
            for g in load(p)}
    rows = db.execute(
        "select * from chunks where source = 'gdpr' and label like 'Art.%' "
        "and heading not like 'Chapter X %' and heading not like 'Chapter XI %'"
    ).fetchall()
    rows = [r for r in rows if len(r["text"].split()) >= 30 and target_of(r["label"]) not in used]
    rows = random.Random(seed).sample(rows, min(sample, len(rows)))

    def ask(r):
        q = llm.chat([{"role": "user", "content": ASK.format(**dict(r))}], model).strip()
        return r, q

    with ThreadPoolExecutor(WORKERS) as pool:
        drafts = [(r, q) for r, q in pool.map(ask, rows)
                  if longest_copied_run(q, f"{r['heading']} {r['text']}") <= 3]
    items = "\n\n".join(
        f"[{i}] Question: {q}\nPassage ({r['label']}): {r['text']}" for i, (r, q) in enumerate(drafts)
    )
    verdict = llm.chat([{"role": "user", "content": FILTER.format(items=items)}], model, temperature=0)
    keep = sorted({int(x) for x in re.findall(r"\d+", verdict) if int(x) < len(drafts)})
    golden = [drafts[i] for i in keep][:n]
    EVAL_DIR.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for i, (r, q) in enumerate(golden, 1):
            rec = {"id": i, "question": q, "target": target_of(r["label"]), "source_label": r["label"]}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"{len(rows)} sampled, {len(drafts)} passed the copy check, {len(keep)} kept by the filter,"
          f" wrote {len(golden)} to {out}")


def _score(db_path: Path, golden: list[dict], mode: str, embed_model: str) -> dict:
    if mode != "bm25":
        retrieve.query_vectors([g["question"] for g in golden], embed_model)

    def rank(g):
        with closing(store.connect(db_path)) as db:  # one connection per worker thread
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


def default_config() -> tuple[str, str]:
    return retrieve.DEFAULT_MODE, llm.setting("EMBED_MODEL", llm.EMBED_MODEL)


def baseline_config() -> tuple[str, str]:
    """The configuration CI guards. It needs only embeddings (CI can't sign in to ChatGPT)."""
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    return base["mode"], base["embed_model"]


def retrieval(db_path: Path, bakeoff: bool, questions: Path = GOLDEN,
              config: tuple[str, str] | None = None) -> list[dict]:
    golden = load(questions)
    default = config or default_config()
    configs = [default]
    if bakeoff:
        # local-rerank (a tiny CPU cross-encoder) is left out: it scored ~0.2 recall@5 and was the
        # slowest part of every bake-off
        modes = [m for m in retrieve.MODES if m not in ("bm25", "local-rerank")]
        configs = [("bm25", EMBED_MODELS[0])] + [(m, e) for e in EMBED_MODELS for m in modes]
    results = [_score(db_path, golden, m, e) for m, e in configs]
    table = "| mode | embedding model | recall@5 | recall@10 | MRR |\n|---|---|---|---|---|\n" + "\n".join(
        f"| {r['mode']} | {r['embed_model']} | {r['recall@5']:.2f} | {r['recall@10']:.2f} | {r['mrr']:.2f} |"
        for r in results
    )
    print(f"{len(golden)} questions\n\n{table}")
    out = EVAL_DIR / "results"
    out.mkdir(parents=True, exist_ok=True)
    if bakeoff:
        (out / "retrieval.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        (out / "retrieval.md").write_text(table + "\n", encoding="utf-8")
    elif questions == HELDOUT:
        (out / "retrieval-heldout.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


# allowed drop per metric: one question of 50 for recall; MRR moves a little run to run (LLM rerank)
TOLERANCE = {"recall@5": 0.02, "recall@10": 0.02, "mrr": 0.05}


def check_baseline(results: list[dict]) -> None:
    """CI gate: fail if the baseline config drops below the committed baseline on any metric."""
    mode, model = baseline_config()
    row = next((r for r in results if r["mode"] == mode and r["embed_model"] == model), None)
    if row is None:
        raise SystemExit(f"no result for the baseline config {mode} / {model}")
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    failed = [f"{m} {row[m]:.2f} < baseline {base[m]:.2f}" for m, tol in TOLERANCE.items()
              if m in base and row[m] < base[m] - tol]
    if failed:
        raise SystemExit("retrieval regressed: " + "; ".join(failed))
    print("baseline ok: " + ", ".join(f"{m} {row[m]:.2f}" for m in TOLERANCE if m in base))


JUDGE = """You grade an answer from a GDPR assistant against the sources it was given.

Question: {question}

Sources:
{sources}

Answer:
{answer}

faithful: every claim in the answer is supported by the sources.
citations_ok: every [cN] tag is attached to a claim that source cN supports.
Return only JSON: {{"faithful": true or false, "citations_ok": true or false, "reason": "<one sentence>"}}"""


def answers(db_path: Path, questions: Path = GOLDEN) -> None:
    """Answer every question, check the target article is cited, LLM-judge the rest, and check
    that out-of-scope questions are declined."""
    from . import answer

    golden = load(questions)
    judge_model = llm.setting("EVAL_MODEL", llm.EVAL_MODEL)

    def run(g):
        with closing(store.connect(db_path)) as db:
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

    def decline(q):
        with closing(store.connect(db_path)) as db:
            _, res = answer.answer(db, q["question"])
        return {**q, "found": res["found"], "answer": res["answer"]}

    with ThreadPoolExecutor(WORKERS) as pool:
        recs = list(pool.map(run, golden))
        off_topic = list(pool.map(decline, load(UNANSWERABLE))) if UNANSWERABLE.exists() else []
    found = [r for r in recs if r["found"]]
    n, f = len(recs), max(len(found), 1)
    row = {
        "answered": len(found) / n,
        "target_cited": sum(r["target_cited"] for r in recs) / n,
        "faithful": sum(bool(r.get("faithful")) for r in found) / f,
        "citations_ok": sum(bool(r.get("citations_ok")) for r in found) / f,
        "declined_off_topic": sum(not r["found"] for r in off_topic) / max(len(off_topic), 1),
        "off_topic_questions": len(off_topic),
    }
    table = ("| answered | target article cited | faithful | citations correct | off-topic declined |\n"
             "|---|---|---|---|---|\n"
             f"| {row['answered']:.2f} | {row['target_cited']:.2f} | {row['faithful']:.2f}"
             f" | {row['citations_ok']:.2f} | {row['declined_off_topic']:.2f} |")
    out = EVAL_DIR / "results"
    out.mkdir(parents=True, exist_ok=True)
    name = "answers" if questions == GOLDEN else f"answers-{questions.stem}"
    name += _model_suffix()
    (out / f"{name}.json").write_text(
        json.dumps({"summary": row, "answers": recs, "off_topic": off_topic}, indent=2,
                   ensure_ascii=False), encoding="utf-8")
    (out / f"{name}.md").write_text(table + "\n", encoding="utf-8")
    for r in off_topic:
        if r["found"]:
            print(f"  answered an off-topic question: {r['question']}")
    print(f"{n} questions, answers by {llm.answer_model()},"
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
    name = "compliance" + _model_suffix()
    (out / f"{name}.json").write_text(json.dumps({"summary": summary, "items": rows}, indent=2),
                                         encoding="utf-8")
    (out / f"{name}.md").write_text(table + "\n", encoding="utf-8")
    misses = [r for r in rows if not r["ok"]]
    print(f"{len(truth)} policies, model {llm.answer_model()}\n\n{table}")
    for r in misses:
        print(f"  wrong: {r['policy']} {r['item']}: expected {r['expected']}, got {r['status']}")
    return summary
