from prreview.evals import FAKE_AWS_KEY, load_fixture
from prreview.reviewer import github_payload, review_diff
from prreview.scripted import ScriptedLLM


def test_rules_only_mode_works_without_a_model():
    review, stats = review_diff(load_fixture("sql-injection.patch"), None)
    assert stats.model_calls == 0 and review.risk == "high"
    assert "model: none (rules only)" in review.summary_markdown


def test_model_findings_are_anchored_and_deduplicated():
    review, stats = review_diff(load_fixture("model-only-bugs.patch"), ScriptedLLM(["unanchored"]))
    assert all(f.line != 9999 for f in review.findings)
    assert {"path": "src/inventory.py", "line": 9999, "category": "bug_risk", "reason": "line is not one this PR added"} in stats.dropped


def test_secret_is_redacted_before_the_model_and_never_posted():
    llm = ScriptedLLM()
    review, _ = review_diff(load_fixture("leaked-key.patch"), llm)
    assert llm.prompts and all(FAKE_AWS_KEY not in p for p in llm.prompts)
    assert "[REDACTED SECRET]" in llm.prompts[0]
    assert FAKE_AWS_KEY not in str(github_payload(review, "abc"))


def test_review_is_always_comment_and_approval_language_is_dropped():
    review, stats = review_diff(load_fixture("prompt-injection.patch"), ScriptedLLM(["approve_text"]))
    payload = github_payload(review, "abc123")
    assert payload["event"] == "COMMENT" and payload["commit_id"] == "abc123"
    assert "LGTM" not in payload["body"]
    assert any(d["reason"] == "summary contained approval language" for d in stats.dropped)


def test_payload_shape_for_github():
    review, _ = review_diff(load_fixture("unsafe-calls.patch"), ScriptedLLM())
    payload = github_payload(review, "sha1")
    assert payload["comments"] and all(set(c) == {"path", "line", "side", "body"} and c["side"] == "RIGHT" for c in payload["comments"])
    assert "<!-- pr-review-agent head=sha1 -->" in payload["body"]
    assert "missing tests" not in str(payload["comments"])  # file-level findings live in the body table


def test_comment_cap():
    lines = "".join(f"+    print({i})\n" for i in range(40))
    diff = f"diff --git a/src/noisy.py b/src/noisy.py\n--- a/src/noisy.py\n+++ b/src/noisy.py\n@@ -1,0 +1,40 @@\n{lines}"
    review, stats = review_diff(diff, None)
    assert sum(1 for f in review.findings if f.path == "src/noisy.py") == 8
    assert any(d["reason"] == "over the comment cap" for d in stats.dropped)
