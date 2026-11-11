"""Tests for the benchmark evaluation harness (G09)."""

import json
from pathlib import Path

import pytest

from benchmarks.harness import (
    ALL_SYSTEMS,
    SYSTEM_ABLATIONS,
    SYSTEM_BASELINE,
    SYSTEM_OPTIMIZED,
    RunRecord,
    aggregate_system_results,
    calculate_spread,
    load_tasks,
    run_bench,
    system_optimization,
)


def test_all_systems_count_and_members():
    assert len(ALL_SYSTEMS) == 11
    assert SYSTEM_BASELINE in ALL_SYSTEMS
    assert SYSTEM_OPTIMIZED in ALL_SYSTEMS
    for abl in SYSTEM_ABLATIONS:
        assert abl in ALL_SYSTEMS


def test_system_optimization_baseline_turns_all_switches_off():
    opt = system_optimization(SYSTEM_BASELINE)
    data = opt.model_dump()
    assert all(val is False for val in data.values())


def test_system_optimization_optimized_has_defaults():
    opt = system_optimization(SYSTEM_OPTIMIZED)
    assert opt.repo_map is True
    assert opt.targeted_reads is True
    assert opt.output_truncation is True
    assert opt.diff_edits is True
    assert opt.history_compaction is True
    assert opt.tool_pruning is True
    assert opt.early_termination is True


@pytest.mark.parametrize("ablation_name", SYSTEM_ABLATIONS)
def test_each_ablation_disables_exactly_one_switch(ablation_name):
    opt = system_optimization(ablation_name)
    optimized = system_optimization(SYSTEM_OPTIMIZED)

    opt_dict = opt.model_dump()
    base_dict = optimized.model_dump()

    differing = [k for k in opt_dict if opt_dict[k] != base_dict[k]]
    assert len(differing) == 1
    diff_key = differing[0]
    assert opt_dict[diff_key] is False


def test_system_optimization_unknown_raises():
    with pytest.raises(ValueError, match="Unknown benchmark system"):
        system_optimization("non_existent_system")


def test_load_tasks_loads_all_eighteen_tasks():
    tasks = load_tasks()
    assert len(tasks) == 18

    task_ids = [t["id"] for t in tasks]
    expected_ids = [f"TASK-{i:02d}" for i in range(1, 19)]
    assert task_ids == expected_ids

    for t in tasks:
        assert "repository" in t and t["repository"].startswith("https://")
        assert "commit" in t and len(t["commit"]) == 40
        assert t["language"] in {"python", "javascript"}
        assert t["category"] in {"bug_fix", "small_feature", "refactor", "test_addition"}
        assert len(t["prompt"]) > 10
        assert len(t["check_command"]) > 5


def test_load_tasks_filtering():
    filtered = load_tasks(task_ids=["TASK-01", "TASK-07"])
    assert len(filtered) == 2
    assert [t["id"] for t in filtered] == ["TASK-01", "TASK-07"]


def test_calculate_spread():
    assert calculate_spread([]) == {"median": 0.0, "min": 0.0, "max": 0.0, "spread": 0.0}
    res = calculate_spread([10, 20, 30, 40, 50])
    assert res["median"] == 30.0
    assert res["min"] == 10.0
    assert res["max"] == 50.0
    assert res["spread"] == 40.0


def test_aggregate_system_results():
    runs = [
        RunRecord(
            run_id="run_1",
            task_id="TASK-01",
            system=SYSTEM_OPTIMIZED,
            repeat=1,
            passed=True,
            input_tokens=1000,
            output_tokens=200,
            cached_tokens=50,
            cost_micro_usd=2500,
            cost_reconciled=True,
            wall_time_seconds=12.5,
            steps=3,
            tool_calls=2,
            research_calls=0,
        ),
        RunRecord(
            run_id="run_2",
            task_id="TASK-01",
            system=SYSTEM_OPTIMIZED,
            repeat=2,
            passed=False,
            input_tokens=1200,
            output_tokens=300,
            cached_tokens=0,
            cost_micro_usd=3000,
            cost_reconciled=True,
            wall_time_seconds=15.0,
            steps=4,
            tool_calls=3,
            research_calls=1,
        ),
    ]

    summary = aggregate_system_results(runs)
    assert summary["total_runs"] == 2
    assert summary["passed_runs"] == 1
    assert summary["pass_rate"] == 0.5
    assert summary["total_cost_micro_usd"] == 5500
    # Cost per successful task includes the failed attempt: 5500 / 1 = 5500.0
    assert summary["cost_per_successful_task_micro_usd"] == 5500.0
    assert summary["tokens"]["total"]["median"] == 1350.0  # (1200 + 1500) / 2


def test_aggregate_system_results_zero_successes():
    runs = [
        RunRecord(
            run_id="run_fail",
            task_id="TASK-01",
            system=SYSTEM_BASELINE,
            repeat=1,
            passed=False,
            input_tokens=500,
            output_tokens=100,
            cached_tokens=0,
            cost_micro_usd=1000,
            cost_reconciled=False,
            wall_time_seconds=5.0,
            steps=1,
            tool_calls=0,
            research_calls=0,
        )
    ]
    summary = aggregate_system_results(runs)
    assert summary["passed_runs"] == 0
    assert summary["pass_rate"] == 0.0
    assert summary["cost_per_successful_task_micro_usd"] is None


def test_run_bench_mocked(tmp_path, monkeypatch):
    from benchmarks.harness import BenchmarkRunner

    fake_record = RunRecord(
        run_id="fake_run",
        task_id="TASK-01",
        system=SYSTEM_OPTIMIZED,
        repeat=1,
        passed=True,
        input_tokens=500,
        output_tokens=100,
        cached_tokens=0,
        cost_micro_usd=1500,
        cost_reconciled=True,
        wall_time_seconds=4.0,
        steps=2,
        tool_calls=1,
        research_calls=0,
    )

    monkeypatch.setattr(BenchmarkRunner, "run_single", lambda self, task, sys, rep: fake_record)

    out_dir = tmp_path / "results"
    report = run_bench(
        tasks=["TASK-01"],
        systems=[SYSTEM_OPTIMIZED],
        repeats=1,
        output_dir=out_dir,
    )

    assert report["total_runs"] == 1
    assert SYSTEM_OPTIMIZED in report["summaries"]
    assert report["summaries"][SYSTEM_OPTIMIZED]["passed_runs"] == 1
    assert Path(report["report_path"]).is_file()

    saved_data = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert saved_data["total_runs"] == 1


# --- End-to-end run path (real core, scripted model, local git repository) ----------------

import re  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

from token_agent.config import Settings, UserConfig  # noqa: E402
from token_agent.providers import ProviderResult  # noqa: E402

FIXED = "    return a + b"


class ScriptedModel:
    """Reads the file, patches it using the hash the core reported, then finishes."""

    def __init__(self, patch: bool = True):
        self.patch, self.calls = patch, 0

    async def complete(self, messages, model, user, confidential, request_context=None):
        self.calls += 1
        if self.calls == 1:
            action = {"tool": "read_file", "path": "calculator.py"}
        elif self.calls == 2 and self.patch:
            digest = re.findall(
                r'"sha256": "([0-9a-f]{64})"', " ".join(m["content"] for m in messages)
            )[-1]
            action = {
                "tool": "apply_patch",
                "path": "calculator.py",
                "expected_sha256": digest,
                "old": "    return a - b",
                "new": FIXED,
            }
        else:
            action = {"tool": "finish", "summary": "Done"}
        return ProviderResult(json.dumps({"action": action}), model, 100, 10, "test_transport")


@pytest.fixture
def bench_task(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    git = ["git", "-c", "user.name=bench", "-c", "user.email=bench@example.com"]
    subprocess.run([*git, "init", "--quiet"], cwd=origin, check=True)
    subprocess.run([*git, "add", "calculator.py"], cwd=origin, check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "bug"], cwd=origin, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin, check=True, capture_output=True, text=True
    ).stdout.strip()
    check = f'"{sys.executable}" -c "import calculator; assert calculator.add(2, 3) == 5"'
    return {
        "id": "LOCAL-01",
        "repository": str(origin),
        "commit": commit,
        "prompt": "Fix add so it returns the sum",
        "check_command": check,
        "timeout_seconds": 60,
    }


def bench_runner(tmp_path, monkeypatch, model, **options):
    from benchmarks.harness import BenchmarkRunner

    monkeypatch.setenv("BENCH_TOKEN", "x" * 40)
    settings = Settings(
        database=str(tmp_path / "base.sqlite3"),
        users=[UserConfig(id="bench", token_env="BENCH_TOKEN", repo_roots=[str(tmp_path)])],
    )
    return BenchmarkRunner(
        settings,
        [],
        systems=[SYSTEM_OPTIMIZED],
        repeats=1,
        workspace_dir=tmp_path / "work",
        provider=model,
        **options,
    )


def test_real_run_applies_the_approved_fix_and_passes_the_check(tmp_path, monkeypatch, bench_task):
    runner = bench_runner(tmp_path, monkeypatch, ScriptedModel())
    record = runner.run_single(bench_task, SYSTEM_OPTIMIZED, 1)
    assert record.error is None
    assert record.passed is True
    assert record.auto_approvals == 1
    assert record.run_status == "completed"
    assert record.steps == 3 and record.tool_calls >= 2
    assert record.input_tokens == 300 and record.output_tokens == 30
    assert record.wall_time_seconds > 0


def test_without_a_fix_the_same_check_fails(tmp_path, monkeypatch, bench_task):
    runner = bench_runner(tmp_path, monkeypatch, ScriptedModel(patch=False))
    record = runner.run_single(bench_task, SYSTEM_OPTIMIZED, 1)
    assert record.error is None
    assert record.passed is False
    assert record.auto_approvals == 0


def test_each_run_is_reconciled_against_its_own_database(tmp_path, monkeypatch, bench_task):
    import benchmarks.harness as harness

    seen = []
    monkeypatch.setattr(
        harness, "reconcile", lambda store, rows, tolerance: seen.append(store.path) or _Report()
    )
    log = tmp_path / "gateway.json"
    log.write_text("[]")
    runner = bench_runner(
        tmp_path, monkeypatch, ScriptedModel(), gateway_log=log, reconcile_delay_seconds=0
    )
    record = runner.run_single(bench_task, SYSTEM_OPTIMIZED, 1)
    assert record.error is None
    assert len(seen) == 1
    assert Path(seen[0]).name.startswith("LOCAL-01_optimized_rep1_")
    assert Path(seen[0]) != tmp_path / "base.sqlite3"


class _Report:
    def summary(self):
        return {}
