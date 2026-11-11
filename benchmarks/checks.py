"""Automated pass/fail checks for the frozen benchmark tasks (FSD 10.3).

Each task's check runs the repository's own tests, never a shell command:

- ``fail_to_pass`` (bug fixes, small features, refactors): after the agent's run, the test
  files are replaced by the reference commit's versions, and every test listed in
  ``fail_to_pass`` and ``pass_to_pass`` must pass. The ``fail_to_pass`` tests fail at the
  start commit and pass with the reference change; ``pass_to_pass`` tests guard against
  breaking existing behaviour.
- ``test_addition``: the source fix is already in place and the agent writes tests. The
  task's test files must pass on the fixed code, and at least one test must fail when the
  source files are reverted to the commit before the fix, so the new tests really test it.

The environments (a Python interpreter with pytest, or an installed ``node_modules`` with
mocha) are created by ``benchmarks/setup_environments.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

TEST_TIMEOUT_SECONDS = 600


@dataclass
class Environment:
    """Where a runner finds its tools. ``python`` for pytest, ``node_modules`` for mocha."""

    python: str | None = None
    node_modules: str | None = None


@dataclass
class CheckResult:
    passed: bool
    detail: str
    counts: dict[str, int] = field(default_factory=dict)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "advice.detachedHead=false", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def pytest_node_id(classname: str, name: str) -> str:
    """Rebuild a pytest node id from JUnit XML's dotted classname."""
    parts = classname.split(".")
    modules = [i for i, part in enumerate(parts) if part.startswith("test")]
    split = modules[-1] if modules else len(parts) - 1
    return "::".join(["/".join(parts[: split + 1]) + ".py", *parts[split + 1 :], name])


def run_pytest(repo: Path, files: list[str], env: Environment) -> dict[str, str]:
    if not env.python:
        raise ValueError("A Python interpreter with pytest is required for this task")
    with tempfile.TemporaryDirectory() as temp:
        report = Path(temp) / "report.xml"
        subprocess.run(
            [
                env.python,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
                "--tb=no",
                "-o",
                "addopts=",
                f"--junitxml={report}",
                *files,
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT_SECONDS,
            env={**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if not report.exists():
            return {}
        results = {}
        for case in ET.parse(report).getroot().iter("testcase"):
            node = pytest_node_id(case.get("classname", ""), case.get("name", ""))
            state = "passed"
            for child in case:
                if child.tag in ("failure", "error"):
                    state = "failed"
                elif child.tag == "skipped":
                    state = "skipped"
            results[node] = state
        return results


def run_mocha(repo: Path, files: list[str], env: Environment) -> dict[str, str]:
    if not env.node_modules:
        raise ValueError("An installed node_modules with mocha is required for this task")
    mocha = Path(env.node_modules) / "mocha" / "bin" / "_mocha"
    proc = subprocess.run(
        ["node", str(mocha), "--require", "test/support/env", "--reporter", "json", *files],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TEST_TIMEOUT_SECONDS,
        env={**os.environ, "NODE_PATH": str(env.node_modules), "NODE_ENV": "test"},
    )
    start = proc.stdout.find("{")
    try:
        data = json.loads(proc.stdout[start:]) if start >= 0 else {}
    except json.JSONDecodeError:
        return {}
    results = {}
    for state, key in (("passed", "passes"), ("failed", "failures"), ("skipped", "pending")):
        for test in data.get(key, []):
            results[test.get("fullTitle", "")] = state
    return results


RUNNERS = {"pytest": run_pytest, "mocha": run_mocha}


def run_tests(runner: str, repo: Path, files: list[str], env: Environment) -> dict[str, str]:
    present = [f for f in files if (repo / f).is_file()]
    if not present:
        return {}
    return RUNNERS[runner](repo, present, env)


def check_fail_to_pass(repo: Path, task: dict, env: Environment) -> CheckResult:
    check = task["check"]
    git(repo, "checkout", task["reference_commit"], "--", *check["test_files"])
    results = run_tests(check["runner"], repo, check["test_files"], env)
    required = [*check["fail_to_pass"], *check["pass_to_pass"]]
    missing = [t for t in required if results.get(t) != "passed"]
    counts = {
        "required": len(required),
        "failed_or_missing": len(missing),
        "fail_to_pass_passing": sum(results.get(t) == "passed" for t in check["fail_to_pass"]),
    }
    if missing:
        return CheckResult(
            False, f"{len(missing)} required tests did not pass: {missing[:5]}", counts
        )
    return CheckResult(True, f"all {len(required)} required tests passed", counts)


def check_test_addition(repo: Path, task: dict, env: Environment) -> CheckResult:
    check = task["check"]
    fixed = run_tests(check["runner"], repo, check["test_files"], env)
    passing = {t for t, state in fixed.items() if state == "passed"}
    failing = [t for t, state in fixed.items() if state == "failed"]
    counts = {"tests": len(fixed), "failing_on_fixed_code": len(failing)}
    if not fixed or failing:
        return CheckResult(False, "tests must exist and pass on the fixed code", counts)
    git(repo, "checkout", check["revert_to"], "--", *check["source_files"])
    try:
        reverted = run_tests(check["runner"], repo, check["test_files"], env)
    finally:
        git(repo, "checkout", "HEAD", "--", *check["source_files"])
    caught = sorted(t for t in passing if reverted.get(t) != "passed")
    counts["fail_when_fix_reverted"] = len(caught)
    if not caught:
        return CheckResult(False, "no test fails when the fix is reverted", counts)
    return CheckResult(True, f"{len(caught)} tests fail when the fix is reverted", counts)


CHECKS = {"fail_to_pass": check_fail_to_pass, "test_addition": check_test_addition}


def prepare(repo: Path, task: dict) -> None:
    """Put a fresh checkout of the task's start commit into the state the agent starts from."""
    git(repo, "checkout", "--quiet", "--force", task["commit"])
    check = task["check"]
    if check["kind"] == "test_addition":
        # The source fix is in place; the tests that came with it are not.
        git(repo, "checkout", check["revert_to"], "--", *check["test_files"])


def evaluate(repo: Path, task: dict, env: Environment) -> CheckResult:
    return CHECKS[task["check"]["kind"]](repo, task, env)


def apply_reference(repo: Path, task: dict) -> None:
    """Apply the reference solution, used only to validate a task."""
    check = task["check"]
    if check["kind"] == "test_addition":
        git(repo, "checkout", task["reference_commit"], "--", *check["test_files"])
    else:
        git(repo, "checkout", "--quiet", "--force", task["reference_commit"])
