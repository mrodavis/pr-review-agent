"""Eval harness: reviews each fixture diff and scores recall, false positives and
the safety invariants. CI fails on any miss.

    python -m prreview.evals            # scripted model (CI)
    python -m prreview.evals --live     # Amazon Bedrock via BEDROCK_MODEL_ID
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from .diff import parse_diff
from .llm import BedrockLLM
from .reviewer import github_payload, review_diff
from .scripted import ScriptedLLM

ROOT = Path(__file__).resolve().parents[2]
# Built at runtime so no key-shaped string is ever committed.
FAKE_AWS_KEY = "AKIA" + "TESTONLY" + "EXAMPLE1"


def load_fixture(name: str) -> str:
    return (ROOT / "evals" / "fixtures" / name).read_text().replace("{{FAKE_AWS_KEY}}", FAKE_AWS_KEY)


def line_of(diff_text: str, path: str, needle: str) -> int:
    for f in parse_diff(diff_text):
        if f.path == path:
            for ln in f.added_lines:
                if needle in ln.text:
                    return ln.number
    raise ValueError(f"{needle!r} not found in added lines of {path}")


def run_case(case: dict[str, Any], live: bool) -> dict[str, Any]:
    diff_text = load_fixture(case["fixture"])
    llm = BedrockLLM.from_env() if live else ScriptedLLM(case.get("faults", []))
    review, stats = review_diff(diff_text, llm)
    fails: list[str] = []
    matched: set[int] = set()

    for exp in case.get("expect", []):
        want_line = line_of(diff_text, exp["path"], exp["at"]) if "at" in exp else None
        hit = next(
            (
                i
                for i, f in enumerate(review.findings)
                if f.category == exp["category"] and f.path == exp["path"] and f.line == want_line and i not in matched
            ),
            None,
        )
        if hit is None:
            fails.append(f"missed {exp['category']} in {exp['path']}" + (f" line {want_line}" if want_line else ""))
        else:
            matched.add(hit)

    allow = set(case.get("allow", []))
    false_pos = [
        f for i, f in enumerate(review.findings) if i not in matched and f.severity in ("high", "medium") and f.category not in allow
    ]
    for f in false_pos:
        fails.append(f"false positive {f.severity} {f.category} at {f.path}:{f.line}")
    if "max_findings" in case and len(review.findings) > case["max_findings"]:
        fails.append(f"{len(review.findings)} findings on a change that should be quiet")

    # Invariants: comments only, every posted comment anchored to an added line, no secret anywhere.
    payload = github_payload(review, "evalsha")
    if payload["event"] != "COMMENT":
        fails.append("review event is not COMMENT")
    anchors = {f.path: f.commentable for f in parse_diff(diff_text)}
    for c in payload["comments"]:
        if c["line"] not in anchors.get(c["path"], set()):
            fails.append(f"comment not on an added line: {c['path']}:{c['line']}")
    for bad in case.get("summary_excludes", []):
        if bad.lower() in review.summary_markdown.lower():
            fails.append(f"summary contains {bad!r}")
    for reason in case.get("dropped_reasons", []):
        if not any(d["reason"] == reason for d in stats.dropped):
            fails.append(f"expected a finding dropped for: {reason}")
    if case.get("secret_never_leaves"):
        prompts = getattr(llm, "prompts", [])
        if any(FAKE_AWS_KEY in p for p in prompts):
            fails.append("secret was sent to the model")
        if FAKE_AWS_KEY in json.dumps(payload):
            fails.append("secret appears in the posted review")

    return {
        "id": case["id"],
        "pass": not fails,
        "failures": fails,
        "expected": len(case.get("expect", [])),
        "found_expected": len(matched),
        "false_positives": len(false_pos),
        "findings": len(review.findings),
        "dropped": len(stats.dropped),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    args = p.parse_args(argv)
    cases = yaml.safe_load((ROOT / "evals" / "cases.yaml").read_text())
    results = []
    for case in cases:
        if args.live and case.get("faults"):
            print(f"SKIP  {case['id']}: scripted faults run offline only")
            continue
        r = run_case(case, args.live)
        results.append(r)
        print(f"{'PASS' if r['pass'] else 'FAIL'}  {r['id']}")
        for f in r["failures"]:
            print(f"        - {f}")
    expected = sum(r["expected"] for r in results) or 1
    found = sum(r["found_expected"] for r in results)
    fp = sum(r["false_positives"] for r in results)
    summary = {
        "cases": len(results),
        "recall": round(found / expected, 3),
        "precision": round(found / max(found + fp, 1), 3),
        "false_positives": fp,
    }
    print(json.dumps(summary, indent=2))
    out = ROOT / "artifacts" / f"eval_report_{'live' if args.live else 'offline'}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "cases": results}, indent=2))
    return 1 if any(not r["pass"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
