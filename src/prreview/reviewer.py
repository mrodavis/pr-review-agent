"""The review pipeline: parse, run rules, ask the model per file, validate, merge.

    diff ─► parse ─► rules ─────────────────────────────┐
                 └─► redact secrets ─► model (per file) ─► validate ─► merge ─► Review (event=COMMENT)

Validation is what makes the model safe to post: a finding must sit on a line this
PR added, can't duplicate a rule, can't echo a secret, and can't signal approval.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .diff import FileDiff, parse_diff
from .llm import LLMError, StructuredLLM
from .models import SEVERITY_RANK, Finding, ModelReview, Review
from .prompts import SYSTEM, file_user
from .rules import CODE_EXT, SECRET_PATTERNS, mask, run_rules

MAX_PER_FILE = 8
MAX_TOTAL = 25
MAX_FILE_LINES = 600
APPROVAL = re.compile(r"(?i)\b(lgtm|approved?|ready to merge|ship it|good to merge)\b")
MARKER = "<!-- pr-review-agent head={sha} -->"


def redact_secrets(text: str) -> str:
    for _, rx in SECRET_PATTERNS:
        text = rx.sub("[REDACTED SECRET]", text)
    return text


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    for _, rx in SECRET_PATTERNS:
        text = rx.sub(lambda m: mask(m.group(0)), text)
    return text


@dataclass
class RunStats:
    files: int = 0
    model_calls: int = 0
    model_errors: int = 0
    rule_findings: int = 0
    model_findings_kept: int = 0
    dropped: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0


def review_diff(diff_text: str, llm: StructuredLLM | None) -> tuple[Review, RunStats]:
    t0 = time.perf_counter()
    files = parse_diff(diff_text)
    stats = RunStats(files=len(files))
    findings = run_rules(files)
    stats.rule_findings = len(findings)
    summaries: list[str] = []

    if llm is not None:
        for f in files:
            if not _reviewable(f):
                continue
            kept, summary = _review_file(f, findings, llm, stats)
            findings += kept
            if summary:
                summaries.append(f"`{f.path}`: {summary}")

    findings = _cap(findings, stats)
    risk = "high" if any(f.severity == "high" for f in findings) else "medium" if any(f.severity == "medium" for f in findings) else "low"
    stats.latency_ms = round((time.perf_counter() - t0) * 1000)
    body = render_summary(findings, summaries, stats, risk, llm.name if llm else None)
    return Review(risk=risk, summary_markdown=body, findings=findings, dropped=stats.dropped), stats


def _reviewable(f: FileDiff) -> bool:
    return f.status not in ("deleted", "binary") and f.path.endswith(CODE_EXT) and 0 < len(f.added_lines) <= MAX_FILE_LINES


def _review_file(f: FileDiff, existing: list[Finding], llm: StructuredLLM, stats: RunStats) -> tuple[list[Finding], str | None]:
    already = [{"line": x.line, "category": x.category} for x in existing if x.path == f.path]
    prompt = file_user(f.path, redact_secrets(f.numbered()), already)
    stats.model_calls += 1
    try:
        result, _meta = llm.structured(ModelReview, SYSTEM, prompt)
    except LLMError as e:
        stats.model_errors += 1
        stats.dropped.append({"path": f.path, "reason": f"model call failed: {type(e).__name__}"})
        return [], None

    allowed = f.commentable
    taken = {(x.path, x.line, x.category) for x in existing}
    kept: list[Finding] = []
    for mf in result.findings:
        reason = None
        if mf.path != f.path:
            reason = "path is not the file under review"
        elif mf.line not in allowed:
            reason = "line is not one this PR added"
        elif (mf.path, mf.line, mf.category) in taken:
            reason = "duplicates a rule finding"
        if reason:
            stats.dropped.append({"path": mf.path, "line": mf.line, "category": mf.category, "reason": reason})
            continue
        taken.add((mf.path, mf.line, mf.category))
        kept.append(
            Finding(**{**mf.model_dump(), "explanation": _clean(mf.explanation), "suggestion": _clean(mf.suggestion), "source": "model"})
        )
    stats.model_findings_kept += len(kept)

    summary = _clean(result.summary)
    if summary and APPROVAL.search(summary):
        stats.dropped.append({"path": f.path, "reason": "summary contained approval language"})
        summary = None
    return kept, summary


def _cap(findings: list[Finding], stats: RunStats) -> list[Finding]:
    ordered = sorted(findings, key=lambda x: (-SEVERITY_RANK[x.severity], x.path, x.line or 0))
    per_file: dict[str, int] = {}
    out = []
    for f in ordered:
        n = per_file.get(f.path, 0)
        if n >= MAX_PER_FILE or len(out) >= MAX_TOTAL:
            stats.dropped.append({"path": f.path, "line": f.line, "category": f.category, "reason": "over the comment cap"})
            continue
        per_file[f.path] = n + 1
        out.append(f)
    return out


def render_summary(findings: list[Finding], summaries: list[str], stats: RunStats, risk: str, model: str | None) -> str:
    counts = {s: sum(1 for f in findings if f.severity == s) for s in ("high", "medium", "low", "info")}
    lines = [
        "### Automated review (comments only)",
        "",
        f"**Risk: {risk}** · high {counts['high']} · medium {counts['medium']} · low {counts['low']} · info {counts['info']}",
        "",
        "This review is advisory. It cannot approve or block the PR; a person decides.",
    ]
    file_level = [f for f in findings if f.line is None]
    if file_level:
        lines += ["", "| Severity | File | Finding |", "| --- | --- | --- |"]
        lines += [f"| {f.severity} | `{f.path}` | {f.title}: {f.explanation} |" for f in file_level]
    if summaries:
        lines += ["", "**Per file**", ""] + [f"- {s}" for s in summaries]
    lines += [
        "",
        f"<sub>{stats.rule_findings} rule findings · {stats.model_findings_kept} model findings kept · {len(stats.dropped)} dropped by validation · model: {model or 'none (rules only)'}</sub>",
    ]
    return "\n".join(lines)


def comment_body(f: Finding) -> str:
    text = f"**{f.severity.upper()} · {f.category.replace('_', ' ')}** · {f.title}\n\n{f.explanation}"
    if f.suggestion:
        text += f"\n\n_Suggestion:_ {f.suggestion}"
    return text + f"\n\n<sub>source: {f.source}</sub>"


def github_payload(review: Review, head_sha: str) -> dict[str, Any]:
    """Body for POST /repos/{owner}/{repo}/pulls/{number}/reviews."""
    return {
        "commit_id": head_sha,
        "event": review.event,
        "body": review.summary_markdown + "\n" + MARKER.format(sha=head_sha),
        "comments": [
            {"path": f.path, "line": f.line, "side": "RIGHT", "body": comment_body(f)} for f in review.findings if f.line is not None
        ],
    }
