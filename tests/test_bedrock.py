import boto3
import pytest
from botocore.stub import Stubber

from prreview.evals import load_fixture
from prreview.llm import BedrockLLM
from prreview.reviewer import review_diff


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    c = boto3.client("bedrock-runtime", region_name="us-east-1")
    c.sent = []
    c.meta.events.register("provide-client-params.bedrock-runtime.Converse", lambda params, **_: c.sent.append(params))
    return c


def test_bedrock_review_path_with_validation(client):
    stub = Stubber(client)
    stub.add_response(
        "converse",
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "t",
                                "name": "ModelReview",
                                "input": {
                                    "findings": [
                                        {
                                            "path": "src/inventory.py",
                                            "line": 34,
                                            "severity": "medium",
                                            "category": "bug_risk",
                                            "title": "Off by one",
                                            "explanation": "Index goes past the end.",
                                        },
                                        {
                                            "path": "src/inventory.py",
                                            "line": 3,
                                            "severity": "high",
                                            "category": "bug_risk",
                                            "title": "Unchanged line",
                                            "explanation": "Not in this PR.",
                                        },
                                    ],
                                    "summary": "One loop bound is wrong.",
                                },
                            }
                        }
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 900, "outputTokens": 120, "totalTokens": 1020},
            "metrics": {"latencyMs": 700},
        },
    )
    with stub:
        review, stats = review_diff(
            load_fixture("model-only-bugs.patch"), BedrockLLM("us.anthropic.claude-sonnet-5", "us-east-1", client=client)
        )
    stub.assert_no_pending_responses()
    assert client.sent[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "ModelReview"}}
    assert [f.line for f in review.findings if f.source == "model"] == [34]
    assert stats.dropped[0]["reason"] == "line is not one this PR added"
