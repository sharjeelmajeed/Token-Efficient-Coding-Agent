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
