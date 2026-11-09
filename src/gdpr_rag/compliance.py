"""Compliance check: a privacy policy against the GDPR Art. 13/14 transparency checklist."""

import json
import re

from . import llm

# (id, requirement, GDPR point shown to the user, paragraph chunk label used as the citation)
CHECKLIST = [
    ("controller", "Identity and contact details of the controller", "Art. 13(1)(a)", "Art. 13(1)"),
    ("dpo", "Contact details of the data protection officer, where there is one", "Art. 13(1)(b)", "Art. 13(1)"),
    ("purposes", "Purposes of the processing", "Art. 13(1)(c)", "Art. 13(1)"),
    ("legal_basis", "Legal basis for each purpose", "Art. 13(1)(c)", "Art. 13(1)"),
    ("legitimate_interests", "The legitimate interests pursued, where that is a basis", "Art. 13(1)(d)", "Art. 13(1)"),
    ("recipients", "Recipients or categories of recipients", "Art. 13(1)(e)", "Art. 13(1)"),
    ("transfers", "Transfers outside the EU/EEA and the safeguards used", "Art. 13(1)(f)", "Art. 13(1)"),
    ("retention", "How long data is kept, or the criteria for deciding", "Art. 13(2)(a)", "Art. 13(2)"),
    ("rights", "Rights of access, rectification, erasure, restriction, objection and portability", "Art. 13(2)(b)", "Art. 13(2)"),
    ("withdraw_consent", "Right to withdraw consent at any time, where consent is a basis", "Art. 13(2)(c)", "Art. 13(2)"),
    ("complaint", "Right to lodge a complaint with a supervisory authority", "Art. 13(2)(d)", "Art. 13(2)"),
    ("requirement", "Whether providing the data is required, and what happens if it isn't provided", "Art. 13(2)(e)", "Art. 13(2)"),
    ("automated", "Automated decision-making, including profiling: logic and consequences", "Art. 13(2)(f)", "Art. 13(2)"),
    ("source", "Source of the data, when it isn't collected from the person", "Art. 14(2)(f)", "Art. 14(2)"),
]
STATUSES = ("Covered", "Partial", "Missing", "Not applicable")
MAX_POLICY_WORDS = 40_000  # shortcut: longer documents are rejected; audit per section if ever needed

PROMPT = """You audit a privacy policy against the GDPR transparency requirements (Articles 13 and 14).

For each requirement decide a status:
- Covered: the policy clearly gives this information.
- Partial: the policy touches the topic but leaves out part of what the requirement asks for.
- Missing: the policy does not give this information.
- Not applicable: the policy itself makes clear the situation does not arise (e.g. it states no
  automated decisions are made). Never assume this: if the policy is silent, use Missing.

Evidence: copy up to 3 sentences from the policy word for word (each under 40 words) that support
your status. Leave evidence empty for Missing. Fix: one short sentence on what to add or change;
leave it empty for Covered and Not applicable. Judge only from the policy text.

Requirements:
{requirements}

Return only JSON: {{"items": [{{"id": "<requirement id>", "status": "<status>",
"evidence": ["<exact quote>"], "fix": "<sentence>"}}]}}

Policy:
\"\"\"
{policy}
\"\"\""""

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _norm(text: str) -> str:
    return re.sub(r"[*_`#>]", "", re.sub(r"\s+", " ", text.lower().translate(_QUOTES))).strip()


def quote_in(quote: str, policy_norm: str) -> bool:
    """A quote counts if every part between ellipses appears verbatim (case/space-insensitive)."""
    parts = [p.strip(" .\"'") for p in re.split(r"\.\.\.|…", _norm(quote))]
    parts = [p for p in parts if len(p) >= 8]
    return bool(parts) and all(p in policy_norm for p in parts)


def ground(raw_items: list, policy: str) -> list[dict]:
    """Keep only verifiable quotes; Covered/Partial with no verifiable quote becomes Missing."""
    policy_norm = _norm(policy)
    by_id = {i.get("id"): i for i in raw_items if isinstance(i, dict)}
    out = []
    for cid, title, point, _label in CHECKLIST:
        item = by_id.get(cid, {})
        status = item.get("status") if item.get("status") in STATUSES else "Missing"
        quotes = [q for q in item.get("evidence") or [] if isinstance(q, str) and quote_in(q, policy_norm)]
        fix = str(item.get("fix") or "")
        note = None if cid in by_id else "Not assessed by the model."
        if status in ("Covered", "Partial") and not quotes:
            status, note = "Missing", "Coverage was claimed but no quote could be found in the policy."
            fix = fix or f"State: {title[0].lower()}{title[1:]}."
        out.append({
            "id": cid, "title": title, "article": point, "status": status, "evidence": quotes[:3],
            "fix": "" if status in ("Covered", "Not applicable") else fix, "note": note,
        })
    return out


def policy_text(db, doc_id: int) -> str:
    rows = db.execute("select heading, text from chunks where source = ? order by id", (f"user:{doc_id}",))
    return "\n\n".join(f"{r['heading']}\n{r['text']}" for r in rows)


def check(db, doc_id: int, model: str | None = None) -> dict:
    policy = policy_text(db, doc_id)
    if not policy:
        raise ValueError("This document has no text to check.")
    if len(policy.split()) > MAX_POLICY_WORDS:
        raise ValueError(f"Documents over {MAX_POLICY_WORDS:,} words aren't supported yet.")
    requirements = "\n".join(f"- {cid}: {title} ({point})" for cid, title, point, _ in CHECKLIST)
    out = llm.chat(
        [{"role": "user", "content": PROMPT.format(requirements=requirements, policy=policy)}],
        model or llm.answer_model(),
        temperature=0,
    )
    m = re.search(r"\{.*\}", out, re.DOTALL)
    try:
        raw = json.loads(m[0])["items"] if m else None
    except (json.JSONDecodeError, KeyError, TypeError):
        raw = None
    if not isinstance(raw, list):
        raise RuntimeError("the model returned an unreadable report; try again")  # noqa: TRY004 - bad model output, not a caller type error
    items = ground(raw, policy)
    gdpr = {r["label"]: dict(r) for r in db.execute(
        "select id, source, label, ref, heading, text from chunks where source = 'gdpr' "
        "and label in ('Art. 13(1)', 'Art. 13(2)', 'Art. 14(2)')")}
    for item, (*_, label) in zip(items, CHECKLIST, strict=True):
        if label in gdpr:  # the GDPR passage the UI opens from the article link
            item["source"] = {**gdpr[label], "cid": f"g{gdpr[label]['id']}"}
    counts = {s: sum(i["status"] == s for i in items) for s in STATUSES}
    return {"doc_id": doc_id, "items": items, "counts": counts}


def save(db, report: dict) -> None:
    with db:
        db.execute(
            "insert or replace into compliance(doc_id, report) values (?, ?)",
            (report["doc_id"], json.dumps(report, ensure_ascii=False)),
        )


def load(db, doc_id: int) -> dict | None:
    row = db.execute("select report, created_at from compliance where doc_id = ?", (doc_id,)).fetchone()
    return {**json.loads(row["report"]), "created_at": row["created_at"]} if row else None
