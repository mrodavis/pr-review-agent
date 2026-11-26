import httpx
import pytest

from prreview.evals import load_fixture
from prreview.github import GitHubError, post_review
from prreview.reviewer import review_diff


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def review():
    return review_diff(load_fixture("sql-injection.patch"), None)[0]


def test_posts_once_per_head_commit(review):
    posted = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json=[{"body": b} for b in posted])
        posted.append(__import__("json").loads(req.content)["body"])
        return httpx.Response(200, json={"id": 77})

    c = client(handler)
    first = post_review(review, "o/r", 5, "abc", "tkn", client=c)
    second = post_review(review, "o/r", 5, "abc", "tkn", client=c)
    assert first["posted"] is True and first["review_id"] == 77
    assert second == {"posted": False, "reason": "already reviewed this commit"}


def test_retries_server_errors_then_succeeds(review, monkeypatch):
    monkeypatch.setattr("prreview.github.time.sleep", lambda s: None)
    calls = {"post": 0}

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json=[])
        calls["post"] += 1
        return httpx.Response(502) if calls["post"] == 1 else httpx.Response(200, json={"id": 1})

    assert post_review(review, "o/r", 5, "abc", "tkn", client=client(handler))["posted"] is True
    assert calls["post"] == 2


def test_client_errors_are_not_retried(review):
    def handler(req):
        return (
            httpx.Response(200, json=[]) if req.method == "GET" else httpx.Response(422, json={"message": "line must be part of the diff"})
        )

    with pytest.raises(GitHubError, match="422"):
        post_review(review, "o/r", 5, "abc", "tkn", client=client(handler))
