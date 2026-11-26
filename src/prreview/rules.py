"""Deterministic checks that run before (and regardless of) the model.

Rules are fast, free and never hallucinate, so anything a pattern can catch is
caught here. The model is asked for what patterns can't see.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .diff import FileDiff
from .models import Finding

CODE_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rb", ".java")
TEST_HINT = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test|\.test|\.spec|_spec)\.|(^|/)test_[^/]+$")
DEP_FILES = ("requirements.txt", "pyproject.toml", "package.json", "package-lock.json", "go.mod", "Gemfile", "Gemfile.lock")

SECRET_PATTERNS = [
    ("AWS access key id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("hard-coded credential", re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*['\"][^'\"\s]{12,}['\"]")),
]
SQL_INTERP = re.compile(
    r"""(?ix)
    \.(execute|executemany|raw|query)\s*\(\s*
    (f["']|["'][^"']*["']\s*(\+|%)|["'][^"']*["']\s*\.format\()
    """
)
DANGEROUS = [
    (re.compile(r"\beval\s*\("), "eval() on dynamic input"),
    (re.compile(r"\bexec\s*\("), "exec() on dynamic input"),
    (re.compile(r"\bpickle\.loads?\s*\("), "pickle can execute code from untrusted data"),
    (re.compile(r"\byaml\.load\s*\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)"), "yaml.load without SafeLoader"),
    (re.compile(r"\bshell\s*=\s*True\b"), "subprocess with shell=True"),
    (re.compile(r"\bverify\s*=\s*False\b"), "TLS verification disabled"),
]
INJECTION = re.compile(
    r"(?i)(ignore (all |any |your |the )?(previous|prior|above) (instructions|rules)|"
    r"\b(ai|llm|bot|assistant|copilot|reviewer)\b[^\n]{0,40}\b(approve|lgtm|merge|ignore|skip)\b|"
    r"note to the (ai|assistant|model|reviewer))"
)
DEBUG = re.compile(r"^\s*(print\(|console\.log\(|debugger;?$|breakpoint\(\))")
TODO = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")


def mask(value: str) -> str:
    return value[:4] + "*" * max(len(value) - 4, 4)


def is_test_path(path: str) -> bool:
    return bool(TEST_HINT.search(path))


def run_rules(files: Iterable[FileDiff], large_change_lines: int = 400) -> list[Finding]:
    files = list(files)
    out: list[Finding] = []
    for f in files:
        if f.status in ("deleted", "binary"):
            continue
        for ln in f.added_lines:
            for label, rx in SECRET_PATTERNS:
                if m := rx.search(ln.text):
                    out.append(
                        Finding(
                            path=f.path,
                            line=ln.number,
                            severity="high",
                            category="secret",
                            title=f"Possible {label} committed",
                            explanation=f"Line adds what looks like a {label} ({mask(m.group(0))}). Anything pushed to the repo should be treated as leaked.",
                            suggestion="Remove it, rotate the credential, and load it from a secret manager or environment variable.",
                            source="rule",
                        )
                    )
                    break
            if SQL_INTERP.search(ln.text):
                out.append(
                    Finding(
                        path=f.path,
                        line=ln.number,
                        severity="high",
                        category="sql_injection",
                        title="SQL built from string interpolation",
                        explanation="The query text is built with an f-string, concatenation or format(), so input can change the query.",
                        suggestion="Use parameter placeholders and pass values separately.",
                        source="rule",
                    )
                )
            for rx, why in DANGEROUS:
                if rx.search(ln.text):
                    out.append(
                        Finding(
                            path=f.path,
                            line=ln.number,
                            severity="medium",
                            category="dangerous_call",
                            title=why[0].upper() + why[1:],
                            explanation=f"This line uses {why}.",
                            suggestion="Use a safe alternative or justify it in a comment.",
                            source="rule",
                        )
                    )
            if INJECTION.search(ln.text):
                out.append(
                    Finding(
                        path=f.path,
                        line=ln.number,
                        severity="high",
                        category="prompt_injection",
                        title="Text aimed at an AI reviewer",
                        explanation="This line contains instructions addressed to an AI reviewer. It was treated as code under review, not as an instruction.",
                        suggestion="Remove it. Reviews from this agent are comments only and cannot approve a PR.",
                        source="rule",
                    )
                )
            if f.path.endswith(CODE_EXT) and not is_test_path(f.path):
                if DEBUG.search(ln.text):
                    out.append(
                        Finding(
                            path=f.path,
                            line=ln.number,
                            severity="low",
                            category="maintainability",
                            title="Debug output left in",
                            explanation="A print, console.log or breakpoint was added.",
                            suggestion="Use the project logger or remove it.",
                            source="rule",
                        )
                    )
                if TODO.search(ln.text):
                    out.append(
                        Finding(
                            path=f.path,
                            line=ln.number,
                            severity="info",
                            category="maintainability",
                            title="New TODO",
                            explanation="A TODO or FIXME was added.",
                            suggestion="Link it to an issue so it isn't lost.",
                            source="rule",
                        )
                    )

    src_changed = [f for f in files if f.path.endswith(CODE_EXT) and not is_test_path(f.path) and f.status != "deleted" and f.added_lines]
    tests_changed = [f for f in files if is_test_path(f.path)]
    if src_changed and not tests_changed:
        for f in src_changed:
            out.append(
                Finding(
                    path=f.path,
                    line=None,
                    severity="medium",
                    category="missing_tests",
                    title="Code changed without tests",
                    explanation=f"{len(f.added_lines)} lines changed in this file and no test files changed in the PR.",
                    suggestion="Add or update a test that covers the new behavior.",
                    source="rule",
                )
            )
    for f in files:
        if f.path.rsplit("/", 1)[-1] in DEP_FILES and f.added_lines:
            out.append(
                Finding(
                    path=f.path,
                    line=None,
                    severity="info",
                    category="dependency",
                    title="Dependencies changed",
                    explanation="Dependency manifests changed.",
                    suggestion="Check versions, licenses and known vulnerabilities.",
                    source="rule",
                )
            )
    total = sum(len(f.added_lines) for f in files)
    if total > large_change_lines:
        out.append(
            Finding(
                path=files[0].path,
                line=None,
                severity="info",
                category="large_change",
                title="Large PR",
                explanation=f"{total} added lines. Large PRs are harder to review well.",
                suggestion="Split it if the parts can ship separately.",
                source="rule",
            )
        )
    return out
