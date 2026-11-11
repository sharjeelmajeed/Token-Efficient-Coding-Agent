"""The frozen-task checks: a check must fail without the change and pass with it (FSD 10.3)."""

import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.checks import Environment, apply_reference, evaluate, prepare, pytest_node_id

ENV = Environment(python=sys.executable)
BUGGY = "def add(a, b):\n    return a - b\n\n\ndef double(a):\n    return a * 2\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef double(a):\n    return a * 2\n"
OLD_TESTS = "from pkg.calc import double\n\n\ndef test_double():\n    assert double(3) == 6\n"
NEW_TESTS = (
    OLD_TESTS + "\n\ndef test_add():\n    from pkg.calc import add\n\n    assert add(2, 3) == 5\n"
)


def git(repo: Path, *args: str) -> str:
    base = ["git", "-c", "user.name=bench", "-c", "user.email=bench@example.com"]
    return subprocess.run(
        [*base, *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def write(repo: Path, calc: str, tests: str) -> None:
    (repo / "src" / "pkg").mkdir(parents=True, exist_ok=True)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("")
    (repo / "src" / "pkg" / "calc.py").write_text(calc)
    (repo / "tests" / "test_calc.py").write_text(tests)


@pytest.fixture
def history(tmp_path):
    """A repository with a bug commit followed by its fix commit (source and test)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    write(repo, BUGGY, OLD_TESTS)
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "bug")
    bug = git(repo, "rev-parse", "HEAD")
    write(repo, FIXED, NEW_TESTS)
    git(repo, "commit", "--quiet", "-am", "fix")
    return repo, bug, git(repo, "rev-parse", "HEAD")


def fail_to_pass_task(bug, fix):
    return {
        "commit": bug,
        "reference_commit": fix,
        "check": {
            "kind": "fail_to_pass",
            "runner": "pytest",
            "test_files": ["tests/test_calc.py"],
            "fail_to_pass": ["tests/test_calc.py::test_add"],
            "pass_to_pass": ["tests/test_calc.py::test_double"],
        },
    }


def addition_task(bug, fix):
    return {
        "commit": fix,
        "reference_commit": fix,
        "check": {
            "kind": "test_addition",
            "runner": "pytest",
            "test_files": ["tests/test_calc.py"],
            "source_files": ["src/pkg/calc.py"],
            "revert_to": bug,
        },
    }


def test_node_ids_match_pytest():
    assert pytest_node_id("tests.test_calc", "test_add") == "tests/test_calc.py::test_add"
    assert (
        pytest_node_id("tests.test_filters.TestFilter", "test_x[a]")
        == "tests/test_filters.py::TestFilter::test_x[a]"
    )


def test_fail_to_pass_fails_without_work_and_passes_with_the_reference(history):
    repo, bug, fix = history
    task = fail_to_pass_task(bug, fix)
    prepare(repo, task)
    assert evaluate(repo, task, ENV).passed is False
    prepare(repo, task)
    apply_reference(repo, task)
    assert evaluate(repo, task, ENV).passed is True


def test_an_agent_fix_passes_but_breaking_existing_behaviour_fails(history):
    repo, bug, fix = history
    task = fail_to_pass_task(bug, fix)
    prepare(repo, task)
    (repo / "src" / "pkg" / "calc.py").write_text(FIXED)
    assert evaluate(repo, task, ENV).passed is True

    prepare(repo, task)
    broken = FIXED.replace("return a * 2", "return a * 3")
    (repo / "src" / "pkg" / "calc.py").write_text(broken)
    result = evaluate(repo, task, ENV)
    assert result.passed is False
    assert "test_double" in result.detail


def test_the_agent_cannot_pass_by_editing_the_check_tests(history):
    repo, bug, fix = history
    task = fail_to_pass_task(bug, fix)
    prepare(repo, task)
    (repo / "tests" / "test_calc.py").write_text(OLD_TESTS + "\n\ndef test_add():\n    pass\n")
    assert evaluate(repo, task, ENV).passed is False


def test_test_addition_requires_tests_that_catch_the_reverted_fix(history):
    repo, bug, fix = history
    task = addition_task(bug, fix)
    prepare(repo, task)
    assert (repo / "tests" / "test_calc.py").read_text() == OLD_TESTS
    assert evaluate(repo, task, ENV).passed is False
    apply_reference(repo, task)
    assert evaluate(repo, task, ENV).passed is True
    assert (repo / "src" / "pkg" / "calc.py").read_text() == FIXED


def test_test_addition_rejects_tests_that_do_not_test_the_fix(history):
    repo, bug, fix = history
    task = addition_task(bug, fix)
    prepare(repo, task)
    weak = OLD_TESTS + "\n\ndef test_add():\n    assert True\n"
    (repo / "tests" / "test_calc.py").write_text(weak)
    assert evaluate(repo, task, ENV).passed is False


def test_test_addition_rejects_tests_that_fail_on_the_fixed_code(history):
    repo, bug, fix = history
    task = addition_task(bug, fix)
    prepare(repo, task)
    wrong = (
        OLD_TESTS
        + "\n\ndef test_add():\n    from pkg.calc import add\n\n    assert add(2, 3) == -1\n"
    )
    (repo / "tests" / "test_calc.py").write_text(wrong)
    assert evaluate(repo, task, ENV).passed is False


def test_every_frozen_task_is_complete():
    import json

    tasks_dir = Path(__file__).resolve().parents[1] / "benchmarks" / "tasks"
    tasks = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(tasks_dir.glob("TASK-*.json"))
    ]
    assert 15 <= len(tasks) <= 20
    assert len({t["repository"] for t in tasks}) >= 3
    assert len({t["language"] for t in tasks}) >= 2
    assert {t["category"] for t in tasks} == {
        "bug_fix",
        "small_feature",
        "refactor",
        "test_addition",
    }
    for task in tasks:
        assert len(task["commit"]) == 40 and len(task["reference_commit"]) == 40
        check = task["check"]
        assert check["kind"] in {"fail_to_pass", "test_addition"}
        assert check["test_files"]
        if check["kind"] == "fail_to_pass":
            assert check["fail_to_pass"] and task["commit"] != task["reference_commit"]
        else:
            assert check["source_files"] and len(check["revert_to"]) == 40
