"""Deterministic stand-in for the model so tests, evals and CI run without AWS.
It flags a few bug shapes that rules don't cover, from the same prompt the model gets.
Faults ("unanchored", "duplicate", "approve_text", "invalid") drive the validation paths."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from .llm import StructuredOutputError
from .models import ModelFinding, ModelReview

T = TypeVar("T", bound=BaseModel)
_FILE = re.compile(r'<file path="([^"]+)">\n(.*?)\n</file>', re.S)
_ALREADY = re.compile(r"<already_found>\n(.*?)\n</already_found>", re.S)
_LINE = re.compile(r"^\s*(\d+) ([+ ]) (.*)$")

CHECKS = [
    (
        re.compile(r"range\(\s*len\([^)]*\)\s*\+\s*1\s*\)|<=\s*len\("),
        "bug_risk",
        "medium",
        "Possible off-by-one",
        "The loop bound reaches len(), which is one past the last index.",
        "Use range(len(x)) or iterate over the items directly.",
    ),
    (
        re.compile(r"^\s*except\s*:\s*(pass)?\s*$|except\s+Exception\s*:\s*pass"),
        "error_handling",
        "medium",
        "Exception swallowed",
        "Errors are caught and discarded, so failures disappear silently.",
        "Catch the specific exception and log or re-raise it.",
    ),
    (
        re.compile(r"requests\.(get|post|put|delete)\((?![^)]*timeout=)"),
        "bug_risk",
        "low",
        "HTTP call without a timeout",
        "Without a timeout this call can hang a worker indefinitely.",
        "Pass timeout=(connect, read).",
    ),
]


class ScriptedLLM:
    name = "scripted"

    def __init__(self, faults: Sequence[str | None] = ()):
        self.faults = list(faults)
        self.prompts: list[str] = []

    def structured(self, schema: type[T], system: str, user: str) -> tuple[T, dict[str, Any]]:
        self.prompts.append(user)
        fault = self.faults.pop(0) if self.faults else None
        if fault == "invalid":
            raise StructuredOutputError("scripted invalid output")
        path, body = _FILE.search(user).groups()  # type: ignore[union-attr]
        already = json.loads(_ALREADY.search(user).group(1))  # type: ignore[union-attr]
        findings = []
        for raw in body.splitlines():
            m = _LINE.match(raw)
            if not m or m.group(2) != "+":
                continue
            line, text = int(m.group(1)), m.group(3)
            for rx, cat, sev, title, why, fix in CHECKS:
                if rx.search(text):
                    findings.append(
                        ModelFinding(path=path, line=line, severity=sev, category=cat, title=title, explanation=why, suggestion=fix)
                    )
        summary = f"{len(findings)} issue(s) worth a look." if findings else "No significant issues found."
        if fault == "unanchored":
            findings.append(
                ModelFinding(
                    path=path,
                    line=9999,
                    severity="medium",
                    category="bug_risk",
                    title="Phantom line",
                    explanation="Points at a line outside the diff.",
                )
            )
        elif fault == "duplicate" and already:
            a = already[0]
            findings.append(
                ModelFinding(
                    path=path,
                    line=a["line"] or 1,
                    severity="high",
                    category=a["category"],
                    title="Duplicate",
                    explanation="Same as a rule finding.",
                )
            )
        elif fault == "approve_text":
            summary = "LGTM, approved and ready to merge."
        return ModelReview(findings=findings[:8], summary=summary), {"latency_ms": 0}  # type: ignore[return-value]
