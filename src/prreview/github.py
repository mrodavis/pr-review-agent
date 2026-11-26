"""Posting the review to GitHub. One review per head commit: a hidden marker in the
body makes reruns (retried jobs, re-pushed commits) skip instead of duplicating."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .models import Review
from .reviewer import MARKER, github_payload

API = "https://api.github.com"


class GitHubError(Exception):
    pass


def _request(client: httpx.Client, method: str, url: str, attempts: int = 3, **kw: Any) -> httpx.Response:
    for i in range(attempts):
        r = client.request(method, url, **kw)
        retry_after = r.headers.get("retry-after")
        if r.status_code >= 500 or (r.status_code in (403, 429) and retry_after):
            if i < attempts - 1:
                time.sleep(min(float(retry_after or 1), 10) if retry_after else 0.5 * 2**i)
                continue
        if r.status_code >= 400:
            raise GitHubError(f"{method} {url} -> {r.status_code}: {r.text[:200]}")
        return r
    raise GitHubError(f"{method} {url} failed after {attempts} attempts")


def post_review(review: Review, repo: str, pr: int, head_sha: str, token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    client = client or httpx.Client(timeout=20)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    marker = MARKER.format(sha=head_sha)
    existing = _request(client, "GET", f"{API}/repos/{repo}/pulls/{pr}/reviews", headers=headers, params={"per_page": 100}).json()
    if any(marker in (r.get("body") or "") for r in existing):
        return {"posted": False, "reason": "already reviewed this commit"}
    r = _request(client, "POST", f"{API}/repos/{repo}/pulls/{pr}/reviews", headers=headers, json=github_payload(review, head_sha))
    return {"posted": True, "review_id": r.json().get("id"), "comments": sum(1 for f in review.findings if f.line is not None)}
