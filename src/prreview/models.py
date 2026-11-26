"""Finding and review schemas. The model's output goes through ModelReview; what
gets posted is a Review built by code."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low", "info"]
Category = Literal[
    "secret",
    "sql_injection",
    "dangerous_call",
    "missing_tests",
    "bug_risk",
    "error_handling",
    "prompt_injection",
    "maintainability",
    "dependency",
    "large_change",
]
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1, "info": 0}


class Finding(BaseModel):
    path: str
    line: int | None = Field(default=None, description="New-side line number of an added line, or null for a file-level note.")
    severity: Severity
    category: Category
    title: str = Field(max_length=100)
    explanation: str = Field(max_length=500)
    suggestion: str | None = Field(default=None, max_length=400)
    source: Literal["rule", "model"] = "model"


class ModelFinding(BaseModel):
    path: str
    line: int
    severity: Severity
    category: Category
    title: str = Field(max_length=100)
    explanation: str = Field(max_length=500)
    suggestion: str | None = Field(default=None, max_length=400)


class ModelReview(BaseModel):
    """What the model returns for one file."""

    findings: list[ModelFinding] = Field(default_factory=list, max_length=8)
    summary: str = Field(max_length=400, description="One or two sentences on this file's changes.")


class Review(BaseModel):
    """What gets posted. `event` is always COMMENT: the agent never approves or blocks a PR."""

    event: Literal["COMMENT"] = "COMMENT"
    risk: Literal["high", "medium", "low"]
    summary_markdown: str
    findings: list[Finding]
    dropped: list[dict] = Field(default_factory=list, description="Model findings removed by validation, and why.")
