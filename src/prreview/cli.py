"""pr-review: review a unified diff, print the result, optionally post it to GitHub.

git diff origin/main...HEAD > pr.diff
pr-review pr.diff                       # rules only (no model)
LLM_MODE=bedrock pr-review pr.diff      # rules + Bedrock
pr-review pr.diff --post --repo owner/name --pr 12 --sha <head>   # needs GITHUB_TOKEN
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .github import post_review
from .llm import BedrockLLM, StructuredLLM
from .reviewer import github_payload, review_diff
from .scripted import ScriptedLLM


def llm_from_env() -> StructuredLLM | None:
    mode = os.environ.get("LLM_MODE", "rules")
    if mode == "bedrock":
        return BedrockLLM.from_env()
    if mode == "offline":
        return ScriptedLLM()
    if mode == "rules":
        return None
    raise SystemExit(f"unknown LLM_MODE {mode!r} (rules, offline or bedrock)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pr-review")
    p.add_argument("diff", help="path to a unified diff, or - for stdin")
    p.add_argument("--sha", default="local")
    p.add_argument("--json", action="store_true", help="print the GitHub review payload")
    p.add_argument("--post", action="store_true")
    p.add_argument("--repo")
    p.add_argument("--pr", type=int)
    args = p.parse_args(argv)

    text = sys.stdin.read() if args.diff == "-" else open(args.diff, encoding="utf-8").read()
    review, stats = review_diff(text, llm_from_env())
    print(json.dumps(github_payload(review, args.sha), indent=2) if args.json else review.summary_markdown)
    print(
        f"\nrisk={review.risk} findings={len(review.findings)} dropped={len(stats.dropped)} model_calls={stats.model_calls}",
        file=sys.stderr,
    )
    if args.post:
        token = os.environ.get("GITHUB_TOKEN")
        if not (token and args.repo and args.pr):
            raise SystemExit("--post needs GITHUB_TOKEN, --repo and --pr")
        print(json.dumps(post_review(review, args.repo, args.pr, args.sha, token)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
