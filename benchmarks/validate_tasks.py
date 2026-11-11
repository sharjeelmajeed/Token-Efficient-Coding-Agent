"""Prove every frozen task is valid before it is used (FSD 10.3).

For each task, in a fresh copy of its repository:

1. the start state with no agent work must FAIL its check;
2. the start state with the reference change applied must PASS its check.

A task that passes without work, or fails with the real fix, measures nothing, so the
validator exits non-zero and the suite must not be frozen.

    python benchmarks/validate_tasks.py --python <python with pytest> \
        --node-modules node-express5=<path> --node-modules node-express4=<path> \
        [--tasks TASK-01 ...] [--out benchmarks/tasks/validation.json]
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.checks import Environment, apply_reference, evaluate, prepare  # noqa: E402

TASKS_DIR = Path(__file__).resolve().parent / "tasks"


def load(task_ids: list[str] | None) -> list[dict]:
    tasks = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(TASKS_DIR.glob("TASK-*.json"))
    ]
    return [t for t in tasks if not task_ids or t["id"] in task_ids]


def environments(python: str | None, node_modules: list[str]) -> dict[str, Environment]:
    envs = {"python": Environment(python=python)}
    for item in node_modules:
        name, _, path = item.partition("=")
        envs[name] = Environment(node_modules=path)
    return envs


def clone(cache: dict[str, Path], url: str, base: Path) -> Path:
    if url not in cache:
        target = base / f"cache_{len(cache)}"
        subprocess.run(["git", "clone", "--quiet", "--no-checkout", url, str(target)], check=True)
        cache[url] = target
    return cache[url]


def validate(task: dict, envs: dict[str, Environment], cache: dict[str, Path], base: Path) -> dict:
    env = envs.get(task["environment"])
    if env is None:
        return {"id": task["id"], "valid": False, "detail": f"no environment {task['environment']}"}
    source = clone(cache, task["repository"], base)
    work = base / task["id"]
    shutil.rmtree(work, ignore_errors=True)
    subprocess.run(["git", "clone", "--quiet", "--no-checkout", str(source), str(work)], check=True)
    try:
        prepare(work, task)
        without = evaluate(work, task, env)
        prepare(work, task)
        apply_reference(work, task)
        reference = evaluate(work, task, env)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "id": task["id"],
        "category": task["category"],
        "valid": (not without.passed) and reference.passed,
        "without_agent_work": {
            "passed": without.passed,
            "detail": without.detail,
            **without.counts,
        },
        "with_reference_change": {
            "passed": reference.passed,
            "detail": reference.detail,
            **reference.counts,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the frozen benchmark tasks")
    parser.add_argument("--python", help="Python interpreter with pytest for Python tasks")
    parser.add_argument("--node-modules", action="append", default=[], metavar="ENV=PATH")
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    envs = environments(args.python, args.node_modules)
    results, cache = [], {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        for task in load(args.tasks):
            started = time.monotonic()
            result = validate(task, envs, cache, Path(temp))
            result["seconds"] = round(time.monotonic() - started, 1)
            results.append(result)
            print(
                f"{result['id']} {'VALID  ' if result['valid'] else 'INVALID'} "
                f"no work: {result.get('without_agent_work', {}).get('detail', '')[:60]} | "
                f"reference: {result.get('with_reference_change', {}).get('detail', '')[:60]}",
                flush=True,
            )
    report = {
        "validated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine": {"platform": platform.platform(), "python": platform.python_version()},
        "all_valid": bool(results) and all(r["valid"] for r in results),
        "tasks": results,
    }
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{sum(r['valid'] for r in results)}/{len(results)} tasks valid")
    return 0 if report["all_valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
