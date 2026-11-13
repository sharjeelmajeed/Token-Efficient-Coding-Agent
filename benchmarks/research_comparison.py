"""Research on versus off on the frozen tasks (handoff G08, FSD 7 and 10.4).

Runs the same tasks with the same configuration twice: once with research disabled (the
web_research tool is not offered and no trigger can fire), and once with research enabled and
explicit user permission for public research (FSD 7.2). It then compares pass
rate, tokens, the tokens that research text added, research calls and credits, and
gateway-reconciled cost.

The report states numbers and spread only. It never claims a saving: with few tasks and
repeats, "no clear difference" is a valid result.

    python benchmarks/research_comparison.py --config research.toml \
        --gateway-log <bifrost logs.db> --python <python> \
        --node-modules node-express5=<path> --node-modules node-express4=<path> \
        [--tasks TASK-01 ...] [--repeats 3] [--out benchmarks/results]

The config must enable [research] with catalog entries for the task libraries; see
benchmarks/README.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.checks import Environment  # noqa: E402
from benchmarks.harness import (  # noqa: E402
    SYSTEM_OPTIMIZED,
    BenchmarkRunner,
    RunRecord,
    aggregate_system_results,
    load_tasks,
)
from token_agent.config import Settings, load_settings  # noqa: E402

# Two tasks per repository, mixing bug fixes and small features.
DEFAULT_TASKS = ["TASK-01", "TASK-03", "TASK-09", "TASK-12", "TASK-14", "TASK-15"]
MODES = {"research_off": False, "research_on": True}


def median(values: list[float]) -> float:
    return round(float(statistics.median(values)), 2) if values else 0.0


def compare(runs: dict[str, list[RunRecord]], tasks: list[str]) -> dict[str, Any]:
    per_task = {}
    for task in tasks:
        row = {}
        for mode, records in runs.items():
            mine = [r for r in records if r.task_id == task]
            row[mode] = {
                "runs": len(mine),
                "passed": sum(r.passed for r in mine),
                "median_total_tokens": median([r.input_tokens + r.output_tokens for r in mine]),
                "median_research_tokens": median([r.research_input_tokens for r in mine]),
                "research_calls": sum(r.research_calls for r in mine),
            }
        off, on = row["research_off"], row["research_on"]
        row["token_difference_on_minus_off"] = round(
            on["median_total_tokens"] - off["median_total_tokens"], 2
        )
        per_task[task] = row
    return per_task


def run(
    settings: Settings,
    tasks: list[dict],
    repeats: int,
    gateway_log: str | None,
    environments: dict[str, Environment],
    provider=None,
    workspace: str | Path = ".benchmark_workspaces",
) -> dict[str, Any]:
    if not settings.research.enabled:
        raise SystemExit("The config must enable [research] for the research-on runs")
    runs: dict[str, list[RunRecord]] = {}
    for mode, research in MODES.items():
        # Off means research is disabled outright: the web_research tool is not offered and
        # no automatic trigger can fire. On means enabled, with explicit user permission.
        mode_settings = settings.model_copy(
            update={"research": settings.research.model_copy(update={"enabled": research})}
        )
        runner = BenchmarkRunner(
            mode_settings,
            tasks,
            systems=[SYSTEM_OPTIMIZED],
            repeats=repeats,
            workspace_dir=Path(workspace) / mode,
            # Both modes share one clone of each repository.
            cache_dir=Path(workspace) / "_cache",
            gateway_log=gateway_log,
            provider=provider,
            environments=environments,
            research=research,
        )
        print(f"running {mode}: {len(tasks)} tasks x {repeats} repeats", flush=True)
        runs[mode] = [
            runner.run_single(task, SYSTEM_OPTIMIZED, repeat)
            for task in tasks
            for repeat in range(1, repeats + 1)
        ]
    all_runs = [r for records in runs.values() for r in records]
    reconciled = bool(all_runs) and all(r.cost_reconciled for r in all_runs)
    return {
        "measure": "research on versus off, same tasks, model and configuration (G08)",
        "timestamp": time.time(),
        "mode": settings.mode,
        "models": {"standard": settings.standard_model, "fallbacks": settings.standard_fallbacks},
        "research_catalog": sorted(settings.research.packages),
        # Evidence only when every run went through the live gateway and was reconciled.
        "benchmark_evidence": settings.mode == "bifrost" and reconciled,
        "tasks": [t["id"] for t in tasks],
        "repeats": repeats,
        "summaries": {mode: aggregate_system_results(records) for mode, records in runs.items()},
        "per_task": compare(runs, [t["id"] for t in tasks]),
        "runs": {mode: [r.to_dict() for r in records] for mode, records in runs.items()},
        "note": (
            "Numbers and spread only; no saving is claimed. Wall time depends on the store "
            "connection change in PR #35 and is not compared here."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Research on versus off on the frozen tasks")
    parser.add_argument("--config", required=True)
    parser.add_argument("--tasks", nargs="*", default=DEFAULT_TASKS)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--gateway-log")
    parser.add_argument("--python")
    parser.add_argument("--node-modules", action="append", default=[], metavar="ENV=PATH")
    parser.add_argument("--out", default="benchmarks/results")
    args = parser.parse_args()
    environments = {"python": Environment(python=args.python)}
    for item in args.node_modules:
        name, _, path = item.partition("=")
        environments[name] = Environment(node_modules=path)
    report = run(
        load_settings(args.config),
        load_tasks(task_ids=args.tasks),
        args.repeats,
        args.gateway_log,
        environments,
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"research_comparison_{int(report['timestamp'])}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for mode, summary in report["summaries"].items():
        print(
            f"{mode}: pass {summary['passed_runs']}/{summary['total_runs']}, "
            f"median tokens {summary['tokens']['total'].get('median')}, "
            f"research tokens {summary['research_input_tokens'].get('median')}, "
            f"cost per success {summary['cost_per_successful_task_micro_usd']} micro-USD"
        )
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
