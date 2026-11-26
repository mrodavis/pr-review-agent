from prreview.diff import parse_diff
from prreview.evals import FAKE_AWS_KEY, load_fixture
from prreview.rules import is_test_path, run_rules


def test_parser_tracks_new_side_line_numbers():
    files = parse_diff(load_fixture("sql-injection.patch"))
    users = next(f for f in files if f.path == "app/users.py")
    added = {ln.number: ln.text for ln in users.added_lines}
    assert added[15].strip().startswith('cur.execute(f"SELECT')
    assert users.commentable == {13, 14, 15, 16, 17}


def test_parser_statuses():
    files = {f.path: f for f in parse_diff(load_fixture("clean-docs.patch"))}
    assert files["docs/setup.md"].status == "added"
    assert files["docs/setup.md"].commentable == {1, 2, 3, 4, 5}


def test_test_path_detection():
    assert is_test_path("tests/test_users.py") and is_test_path("src/app.test.ts") and is_test_path("pkg/thing_test.go")
    assert not is_test_path("src/testing_utils.py") and not is_test_path("app/users.py")


def test_secret_rule_masks_the_value():
    findings = run_rules(parse_diff(load_fixture("leaked-key.patch")))
    secret = next(f for f in findings if f.category == "secret")
    assert secret.line == 4 and FAKE_AWS_KEY not in secret.explanation and "AKIA****" in secret.explanation


def test_missing_tests_only_when_no_test_changed():
    assert any(f.category == "missing_tests" for f in run_rules(parse_diff(load_fixture("missing-tests.patch"))))
    assert not any(f.category == "missing_tests" for f in run_rules(parse_diff(load_fixture("sql-injection.patch"))))


def test_docs_change_has_no_findings():
    assert run_rules(parse_diff(load_fixture("clean-docs.patch"))) == []
