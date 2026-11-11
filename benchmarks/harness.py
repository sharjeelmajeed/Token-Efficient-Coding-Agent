"""Evaluation harness for the Token-Efficient Coding Agent (FSD section 10 & handoff G09).

Evaluates the agent across:
1. Naive baseline (all 9 optimization switches false)
2. Optimized agent (all optimization switches default/true)
3. 9 single-switch ablations (each with exactly one technique disabled)

Controls:
- Pinned models, temperatures, prices, budgets, output_chars, and context_chars.
- At least 3 repeats per task and system.
- Per-run metrics: pass/fail, input/output/cached tokens, token attribution,
  wall time, steps, tool calls, and research calls.
- Reconciled costs from Bifrost gateway logs via `token_agent.reconcile`.
- Statistics: medians, spread (min/max/IQR), and cost per successful task.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import shutil
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from token_agent.api import token_attribution
from token_agent.config import OptimizationSettings, Settings, load_settings
from token_agent.engine import Engine
from token_agent.policy import resolve_repository
from token_agent.reconcile import load_gateway_log, reconcile
from token_agent.store import Store

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.checks import Environment  # noqa: E402
from benchmarks.checks import evaluate as evaluate_task_check  # noqa: E402
from benchmarks.checks import prepare as prepare_task_state  # noqa: E402

logger = logging.getLogger("token_agent.benchmarks")

# Benchmark runs are unattended, so every exact pending action is approved automatically.
# Each approval is recorded as an `approval_decision` event by this identity; the report must
# disclose it. Approval still binds the exact action, and policy checks are unchanged.
BENCHMARK_APPROVER = "benchmark-auto-approver"

SYSTEM_BASELINE = "naive_baseline"
SYSTEM_OPTIMIZED = "optimized"
SYSTEM_ABLATIONS = [
    "ablation_no_repo_map",
    "ablation_no_targeted_reads",
    "ablation_no_output_truncation",
    "ablation_no_diff_edits",
    "ablation_no_history_compaction",
    "ablation_no_tool_pruning",
    "ablation_no_gateway_direct_cache",
    "ablation_no_tiered_routing",
    "ablation_no_early_termination",
]
ALL_SYSTEMS = [SYSTEM_BASELINE, SYSTEM_OPTIMIZED, *SYSTEM_ABLATIONS]

ABLATION_SWITCH_MAP = {
    "ablation_no_repo_map": "repo_map",
    "ablation_no_targeted_reads": "targeted_reads",
    "ablation_no_output_truncation": "output_truncation",
    "ablation_no_diff_edits": "diff_edits",
    "ablation_no_history_compaction": "history_compaction",
    "ablation_no_tool_pruning": "tool_pruning",
    "ablation_no_gateway_direct_cache": "gateway_direct_cache",
    "ablation_no_tiered_routing": "tiered_routing",
    "ablation_no_early_termination": "early_termination",
}


def full_optimized_settings() -> OptimizationSettings:
    """Return an OptimizationSettings with all nine optimization switches enabled."""
    return OptimizationSettings(
        repo_map=True,
        targeted_reads=True,
        output_truncation=True,
        diff_edits=True,
        history_compaction=True,
        tool_pruning=True,
        gateway_direct_cache=True,
        tiered_routing=True,
        early_termination=True,
    )


def system_optimization(
    system: str, base_opt: OptimizationSettings | None = None
) -> OptimizationSettings:
    """Return the OptimizationSettings corresponding to a benchmark system."""
    if system == SYSTEM_BASELINE:
        return OptimizationSettings(
            repo_map=False,
            targeted_reads=False,
            output_truncation=False,
            diff_edits=False,
            history_compaction=False,
            tool_pruning=False,
            gateway_direct_cache=False,
            tiered_routing=False,
            early_termination=False,
        )
    base = base_opt or full_optimized_settings()
    if system == SYSTEM_OPTIMIZED:
        return base

    if system in ABLATION_SWITCH_MAP:
        disabled_switch = ABLATION_SWITCH_MAP[system]
        params = base.model_dump()
        params[disabled_switch] = False
        return OptimizationSettings(**params)

    raise ValueError(f"Unknown benchmark system: {system}. Must be one of {ALL_SYSTEMS}")


@dataclass
class RunRecord:
    run_id: str
    task_id: str
    system: str
    repeat: int
    passed: bool
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_micro_usd: int
    cost_reconciled: bool
    wall_time_seconds: float
    steps: int
    tool_calls: int
    research_calls: int
    token_attribution: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    check_output: str = ""
    run_status: str = ""
    outcome: str | None = None
    auto_approvals: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_spread(values: list[float | int]) -> dict[str, float]:
    """Calculate median, min, max, and spread for a numerical sequence."""
    if not values:
        return {"median": 0.0, "min": 0.0, "max": 0.0, "spread": 0.0}
    vals = [float(v) for v in values]
    med = float(statistics.median(vals))
    lo, hi = min(vals), max(vals)
    return {
        "median": round(med, 4),
        "min": round(lo, 4),
        "max": round(hi, 4),
        "spread": round(hi - lo, 4),
    }


def aggregate_system_results(runs: list[RunRecord]) -> dict[str, Any]:
    """Aggregate per-run records for a system into statistical summaries."""
    if not runs:
        return {
            "total_runs": 0,
            "passed_runs": 0,
            "pass_rate": 0.0,
            "tokens": {"input": {}, "output": {}, "total": {}},
            "cost_micro_usd": {},
            "cost_per_successful_task_micro_usd": None,
            "wall_time_seconds": {},
            "steps": {},
            "tool_calls": {},
            "research_calls": {},
        }

    total_runs = len(runs)
    passed_runs = sum(1 for r in runs if r.passed)
    pass_rate = round(passed_runs / total_runs, 4)

    total_tokens_list = [r.input_tokens + r.output_tokens for r in runs]
    total_cost_micro_usd = sum(r.cost_micro_usd for r in runs)

    # Cost per successful task includes costs of failed attempts (FSD 10.5)
    cost_per_success: float | None = (
        round(total_cost_micro_usd / passed_runs, 2) if passed_runs > 0 else None
    )

    return {
        "total_runs": total_runs,
        "passed_runs": passed_runs,
        "pass_rate": pass_rate,
        "tokens": {
            "input": calculate_spread([r.input_tokens for r in runs]),
            "output": calculate_spread([r.output_tokens for r in runs]),
            "total": calculate_spread(total_tokens_list),
        },
        "cost_micro_usd": calculate_spread([r.cost_micro_usd for r in runs]),
        "total_cost_micro_usd": total_cost_micro_usd,
        # Costs are gateway-reconciled only when every run's usage was reconciled.
        "cost_reconciled_runs": sum(1 for r in runs if r.cost_reconciled),
        "cost_per_successful_task_micro_usd": cost_per_success,
        "wall_time_seconds": calculate_spread([r.wall_time_seconds for r in runs]),
        "steps": calculate_spread([r.steps for r in runs]),
        "tool_calls": calculate_spread([r.tool_calls for r in runs]),
        "research_calls": calculate_spread([r.research_calls for r in runs]),
    }


def load_tasks(
    tasks_dir: str | Path | None = None, task_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    """Load task specifications from benchmarks/tasks/ directory."""
    target_dir = Path(tasks_dir) if tasks_dir else Path(__file__).parent / "tasks"
    manifest_file = target_dir / "manifest.json"

    if manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        tasks = manifest.get("tasks", [])
    else:
        tasks = []
        for file_path in sorted(target_dir.glob("TASK-*.json")):
            tasks.append(json.loads(file_path.read_text(encoding="utf-8")))

    if task_ids:
        wanted = set(task_ids)
        tasks = [t for t in tasks if t.get("id") in wanted]

    return tasks


def approve_pending(store: Store, session_id: str, run_id: str) -> int:
    """Approve every exact action this run is waiting on, recording who approved it."""
    with store.connection() as db:
        rows = db.execute(
            "SELECT id FROM approvals WHERE run_id=? AND status='pending'", (run_id,)
        ).fetchall()
    approved = 0
    for row in rows:
        store.decide(row["id"], "approve_once", "")
        store.event(
            session_id,
            run_id,
            "approval_decision",
            {
                "approval_id": row["id"],
                "decision": "approve_once",
                "user": BENCHMARK_APPROVER,
                "feedback": "",
            },
        )
        approved += 1
    return approved


class BenchmarkRunner:
    """Orchestrates benchmark task runs across systems and configurations."""

    def __init__(
        self,
        base_settings: Settings,
        tasks: list[dict[str, Any]],
        systems: list[str] | None = None,
        repeats: int = 3,
        workspace_dir: str | Path = ".benchmark_workspaces",
        gateway_log: str | Path | None = None,
        provider=None,
        environments: dict[str, Environment] | None = None,
        run_timeout_seconds: float = 900.0,
        reconcile_delay_seconds: float = 2.0,
    ):
        self.base_settings = base_settings
        self.tasks = tasks
        self.systems = systems or ALL_SYSTEMS
        self.repeats = max(1, repeats)
        self.workspace_dir = Path(workspace_dir).resolve()
        self.gateway_log = Path(gateway_log) if gateway_log else None
        # None uses the configured mode's provider (Bifrost or fixture); tests inject one.
        self.provider = provider
        # Test environments by name (see benchmarks/setup_environments.py).
        self.environments = environments or {}
        self.run_timeout_seconds = run_timeout_seconds
        # Bifrost writes its log after answering; wait briefly before reconciling a run.
        self.reconcile_delay_seconds = reconcile_delay_seconds

    def repository_cache(self, repo_url: str) -> Path:
        """Clone each repository once; every run then copies it locally."""
        name = hashlib.sha256(repo_url.encode()).hexdigest()[:16]
        cache = self.workspace_dir / "_cache" / name
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--no-checkout", "--quiet", repo_url, str(cache)],
                check=True,
                capture_output=True,
                text=True,
                timeout=600,
            )
        return cache

    def prepare_task_workspace(self, task: dict[str, Any], run_identifier: str) -> Path:
        """Create a dedicated working copy of the task repository at its pinned commit."""
        repo_dir = self.workspace_dir / run_identifier
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        cache = self.repository_cache(task["repository"])
        subprocess.run(
            ["git", "clone", "--no-checkout", "--quiet", str(cache), str(repo_dir)],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if "check" in task:
            # Frozen tasks: the start commit, plus removing the fix's tests for test additions.
            prepare_task_state(repo_dir, task)
        else:
            subprocess.run(
                ["git", "-c", "advice.detachedHead=false", "checkout", "--quiet", task["commit"]],
                cwd=str(repo_dir),
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        return repo_dir

    def check_task(self, repo_dir: Path, task: dict[str, Any]) -> tuple[bool, str]:
        """Run a frozen task's structured check, or a legacy shell check command."""
        if "check" not in task:
            return self.evaluate_check_command(
                repo_dir, task["check_command"], task.get("timeout_seconds", 60)
            )
        env = self.environments.get(task["environment"])
        if env is None:
            raise ValueError(f"No test environment configured for {task['environment']}")
        result = evaluate_task_check(repo_dir, task, env)
        return result.passed, result.detail

    def evaluate_check_command(
        self, repo_dir: Path, check_command: str, timeout_seconds: int = 60
    ) -> tuple[bool, str]:
        """Execute the task's frozen check command in the repository workspace.

        The command comes only from the frozen, reviewed task file and runs through a shell.
        """
        try:
            res = subprocess.run(
                check_command,
                shell=True,
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            passed = res.returncode == 0
            output = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
            return passed, output
        except subprocess.TimeoutExpired:
            return False, "Verification timed out"
        except Exception as exc:
            return False, f"Verification failed with exception: {exc}"

    async def execute_session(
        self, engine: Engine, store: Store, session_id: str, prompt: str
    ) -> tuple[str, int]:
        """Run one task inside a single event loop, approving exact pending actions."""
        run_id = engine.start(session_id, prompt)
        task = engine.tasks.get(session_id)
        approvals = 0
        deadline = time.monotonic() + self.run_timeout_seconds
        try:
            while task is not None and not task.done():
                if time.monotonic() > deadline:
                    logger.warning(
                        "Run %s exceeded %ss; cancelling", run_id, self.run_timeout_seconds
                    )
                    break
                approvals += approve_pending(store, session_id, run_id)
                await asyncio.sleep(0.05)
        finally:
            # Cancels anything still running and marks unfinished work as paused.
            await engine.close()
        return run_id, approvals

    def run_single(
        self,
        task: dict[str, Any],
        system: str,
        repeat: int,
    ) -> RunRecord:
        """Execute a single run of a task under a specific optimization system."""
        opt = system_optimization(system)
        run_identifier = f"{task['id']}_{system}_rep{repeat}_{int(time.time())}"
        repo_dir: Path | None = None
        user = self.base_settings.users[0]
        elapsed = 0.0

        try:
            repo_dir = self.prepare_task_workspace(task, run_identifier)
            task_settings = self.base_settings.model_copy(
                update={
                    "optimization": opt,
                    "database": str(self.workspace_dir / f"{run_identifier}_state.sqlite3"),
                    "users": [
                        user.model_copy(
                            update={"repo_roots": [*user.repo_roots, str(repo_dir.resolve())]}
                        )
                    ],
                }
            )
            run_user = task_settings.users[0]

            # Same wiring as the API server: accounts, confidentiality and a fresh session.
            store = Store(task_settings.database)
            store.register_accounts(task_settings.users)
            engine = Engine(
                task_settings, store, {run_user.id: secrets.token_urlsafe(40)}, self.provider
            )
            repo, sources = resolve_repository(str(repo_dir.resolve()), run_user, task_settings)
            for source in sources:
                store.mark_confidential(source)
            session = store.create_session(run_user.id, repo, bool(sources))
            store.event(
                session["id"],
                None,
                "session_created",
                {"mode": task_settings.mode, "confidential": bool(session["confidential"])},
            )

            # Wall time covers the agent's work only, not cloning or checking.
            start_time = time.monotonic()
            run_id, approvals = asyncio.run(
                self.execute_session(engine, store, session["id"], task["prompt"])
            )
            elapsed = round(time.monotonic() - start_time, 4)

            passed, check_out = self.check_task(repo_dir, task)

            # Reconcile this run's own database before reading any cost.
            if self.gateway_log and self.gateway_log.exists():
                time.sleep(self.reconcile_delay_seconds)
                report = reconcile(
                    store,
                    load_gateway_log(self.gateway_log),
                    task_settings.reconciliation_token_tolerance,
                )
                logger.info("Reconciliation for %s: %s", run_id, report.summary())

            usage_rows = store.usage(run_id)
            run = store.run(run_id) or {}
            events = store.events(session["id"])
            return RunRecord(
                run_id=run_id,
                task_id=task["id"],
                system=system,
                repeat=repeat,
                passed=passed,
                input_tokens=sum(u.get("input_tokens", 0) for u in usage_rows),
                output_tokens=sum(u.get("output_tokens", 0) for u in usage_rows),
                cached_tokens=sum(
                    u.get("details", {}).get("cached_tokens", 0)
                    for u in usage_rows
                    if isinstance(u.get("details"), dict)
                ),
                cost_micro_usd=sum(u.get("amount", 0) for u in usage_rows),
                cost_reconciled=bool(usage_rows)
                and all(u.get("state") == "reconciled" for u in usage_rows),
                wall_time_seconds=elapsed,
                steps=run.get("step", 0),
                tool_calls=sum(1 for e in events if e.get("type") == "tool_call"),
                research_calls=sum(1 for e in events if e.get("type") == "research_used"),
                token_attribution=token_attribution(usage_rows),
                check_output=check_out[:2000],
                run_status=run.get("status", ""),
                outcome=run.get("outcome"),
                auto_approvals=approvals,
            )
        except Exception as exc:
            logger.exception("Run %s failed", run_identifier)
            return RunRecord(
                run_id=f"err_{run_identifier}",
                task_id=task["id"],
                system=system,
                repeat=repeat,
                passed=False,
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                cost_micro_usd=0,
                cost_reconciled=False,
                wall_time_seconds=elapsed,
                steps=0,
                tool_calls=0,
                research_calls=0,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if repo_dir and repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)

    def run_all(self, gateway_log: str | Path | None = None) -> dict[str, Any]:
        """Execute the full benchmark suite across all systems and tasks."""
        if gateway_log:
            self.gateway_log = Path(gateway_log)
        runs: list[RunRecord] = []
        for system in self.systems:
            for task in self.tasks:
                for rep in range(1, self.repeats + 1):
                    runs.append(self.run_single(task, system, rep))

        by_system: dict[str, list[RunRecord]] = {}
        for r in runs:
            by_system.setdefault(r.system, []).append(r)

        summaries = {sys: aggregate_system_results(by_system.get(sys, [])) for sys in self.systems}

        # Calculate token savings relative to baseline median
        baseline_summary = summaries.get(SYSTEM_BASELINE, {})
        baseline_median_tokens = (
            baseline_summary.get("tokens", {}).get("total", {}).get("median", 0.0)
        )

        for summary in summaries.values():
            sys_median = summary.get("tokens", {}).get("total", {}).get("median", 0.0)
            if baseline_median_tokens > 0:
                saving_pct = round(
                    100.0 * (baseline_median_tokens - sys_median) / baseline_median_tokens, 2
                )
            else:
                saving_pct = 0.0
            summary["token_saving_vs_baseline_pct"] = saving_pct

        all_reconciled = bool(runs) and all(r.cost_reconciled for r in runs)
        return {
            "timestamp": time.time(),
            "mode": self.base_settings.mode,
            # Only live gateway runs with every cost reconciled can be reported as evidence.
            "benchmark_evidence": self.base_settings.mode == "bifrost" and all_reconciled,
            "approval_policy": f"every exact pending action auto-approved by {BENCHMARK_APPROVER}",
            "total_runs": len(runs),
            "systems": self.systems,
            "tasks_evaluated": [t["id"] for t in self.tasks],
            "repeats": self.repeats,
            "summaries": summaries,
            "runs": [r.to_dict() for r in runs],
        }


def run_bench(
    config_path: str = "agent.toml",
    tasks: list[str] | None = None,
    systems: list[str] | None = None,
    repeats: int = 3,
    output_dir: str | Path = "benchmarks/results",
    gateway_log: str | Path | None = None,
    python: str | None = None,
    node_modules: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Clean programmatic harness interface for CLI `agent bench` invocation.

    ``python`` and ``node_modules`` name the test environments that
    ``benchmarks/setup_environments.py`` creates.
    """
    settings = load_settings(config_path)
    loaded_tasks = load_tasks(task_ids=tasks)
    environments = {"python": Environment(python=python)}
    for name, path in (node_modules or {}).items():
        environments[name] = Environment(node_modules=path)

    runner = BenchmarkRunner(
        base_settings=settings,
        tasks=loaded_tasks,
        systems=systems or ALL_SYSTEMS,
        repeats=repeats,
        gateway_log=gateway_log,
        environments=environments,
    )

    results = runner.run_all()

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    report_file = out_path / f"benchmark_report_{int(time.time())}.json"
    report_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    results["report_path"] = str(report_file)

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run token-efficient coding agent benchmarks")
    parser.add_argument("--config", default="agent.toml", help="Path to agent configuration TOML")
    parser.add_argument("--tasks", nargs="*", help="Specific task IDs to run")
    parser.add_argument("--systems", nargs="*", help="Specific systems/ablations to run")
    parser.add_argument("--repeats", type=int, default=3, help="Number of repeats per task")
    parser.add_argument(
        "--output-dir", default="benchmarks/results", help="Directory for output report"
    )
    parser.add_argument(
        "--gateway-log", help="Path to Bifrost gateway logs.db for billing reconciliation"
    )
    parser.add_argument("--python", help="Python with pytest for the Python tasks")
    parser.add_argument(
        "--node-modules",
        action="append",
        default=[],
        metavar="ENV=PATH",
        help="node_modules for a JavaScript environment, e.g. node-express5=<path>",
    )
    args = parser.parse_args()

    res = run_bench(
        config_path=args.config,
        tasks=args.tasks,
        systems=args.systems,
        repeats=args.repeats,
        output_dir=args.output_dir,
        gateway_log=args.gateway_log,
        python=args.python,
        node_modules=dict(item.split("=", 1) for item in args.node_modules),
    )
    print(f"Benchmark run complete. Report saved to: {res.get('report_path')}")
