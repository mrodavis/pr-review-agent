import json
import subprocess
import sys
from pathlib import Path

from prreview import evals

ROOT = Path(__file__).resolve().parents[1]


def test_eval_suite_passes(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["evals"])
    assert evals.main([]) == 0


def test_cli_prints_github_payload():
    out = subprocess.run(
        [sys.executable, "-m", "prreview.cli", str(ROOT / "evals/fixtures/unsafe-calls.patch"), "--json", "--sha", "deadbeef"],
        capture_output=True,
        text=True,
        check=True,
        env={"LLM_MODE": "rules", "PATH": "/usr/bin:/bin"},
    )
    payload = json.loads(out.stdout)
    assert payload["event"] == "COMMENT" and len(payload["comments"]) == 3
