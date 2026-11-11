"""Measure the core's own work per model request, excluding network and model time (NFR-01).

The FSD targets policy overhead under 50 ms per request, excluding network. A provider that
answers instantly records when each model request arrives and when its reply is handed back.
The gap between handing back one reply and receiving the next request is the core's work for
that step:
- validating the reply and its policy checks;
- running the read-only tool on local files;
- persisting state and events;
- rechecking the repository's confidentiality;
- building and secret-scanning the next context.

That total is an upper bound on policy overhead. The script also times the policy steps on
their own.

Runs use a real loopback core with a scripted provider, on three repository sizes. Results
depend on the machine and disk; record them with the hardware. This measures overhead only,
not model behaviour, and it is not benchmark evidence.

    python scripts/measure_overhead.py [--runs 10] [--out overhead.json]
"""

import argparse
import json
import os
import platform
import secrets
import shutil
import socket
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

import httpx
import uvicorn

from token_agent.api import create_app
from token_agent.config import Settings, UserConfig
from token_agent.context import enabled_tools, system_prompt
from token_agent.models import AgentReply
from token_agent.policy import classify, resolve_repository
from token_agent.providers import ProviderResult
from token_agent.repo import Repository

ROOT = Path(__file__).resolve().parents[1]
TARGET_MS = 50.0


class InstantProvider:
    """Answers each request at once, recording when the core asked and when it was answered."""

    def __init__(self):
        self.script: list[dict] = []
        self.marks: list[tuple[float, float, str]] = []

    async def complete(self, messages, model, user, confidential, request_context=None):
        arrived = time.perf_counter()
        action = self.script.pop(0)
        result = ProviderResult(json.dumps({"action": action}), model, 1000, 50, "measure")
        self.marks.append((arrived, time.perf_counter(), action["tool"]))
        return result


def make_repositories(base: Path) -> dict[str, tuple[Path, str]]:
    """Three sizes; each entry is (repository, a file the script reads)."""
    demo = base / "demo"
    demo.mkdir(parents=True)
    (demo / "calculator.py").write_text("def add(a, b):\n    return a - b\n")

    project = base / "project"
    ignore = shutil.ignore_patterns(
        ".git", ".venv", "node_modules", "out", "__pycache__", "*.vsix", "*.pdf", ".agent"
    )
    for part in ("src", "tests", "docs", "scripts", "infra", "clients"):
        if (ROOT / part).exists():
            shutil.copytree(ROOT / part, project / part, ignore=ignore)

    large = base / "large"
    for d in range(400):
        folder = large / f"pkg_{d:03d}"
        folder.mkdir(parents=True)
        for f in range(10):
            body = "".join(f"def func_{f}_{i}(x):\n    return x + {i}\n\n" for i in range(15))
            (folder / f"module_{f}.py").write_text(body)
    return {
        "demo (1 file)": (demo, "calculator.py"),
        "project copy": (project, "src/token_agent/engine.py"),
        "large (4,000 files, 400 folders)": (large, "pkg_000/module_0.py"),
    }


def file_count(path: Path) -> int:
    return sum(len(files) for _, _, files in os.walk(path))


@contextmanager
def core(app, token):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    try:
        with httpx.Client(
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
            trust_env=False,
            timeout=120,
        ) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        listener.close()


def script_for(target: str) -> list[dict]:
    return [
        {"tool": "list_tree", "depth": 2},
        {"tool": "read_file", "path": target, "start_line": 1, "end_line": 80},
        {"tool": "grep", "pattern": "def add"},
        {"tool": "read_file", "path": target, "start_line": 1, "end_line": 40},
        {"tool": "finish", "summary": "Measurement run finished"},
    ]


def measure_runs(repo: Path, root: Path, target: str, runs: int, work: Path) -> dict:
    token = secrets.token_urlsafe(40)
    os.environ["MEASURE_TOKEN"] = token
    settings = Settings(
        database=str(work / f"{repo.name}.sqlite3"),
        users=[UserConfig(id="measure", token_env="MEASURE_TOKEN", repo_roots=[str(root)])],
    )
    provider = InstantProvider()
    gaps: list[tuple[float, str]] = []
    creations: list[float] = []
    with core(create_app(settings, provider), token) as client:
        for index in range(runs + 1):  # the first run warms caches and is discarded
            provider.script, provider.marks = script_for(target), []
            started = time.perf_counter()
            created = client.post("/sessions", json={"repo_path": str(repo)})
            if index > 0:
                creations.append((time.perf_counter() - started) * 1000)
            sid = created.json()["session_id"]
            client.post(f"/sessions/{sid}/messages", json={"text": "Measure overhead"})
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                run = client.get(f"/sessions/{sid}/status").json()["run"]
                if run and run["status"] not in ("running", "queued"):
                    break
                time.sleep(0.02)
            if run["status"] != "completed":
                raise SystemExit(f"Run did not complete on {repo.name}: {run}")
            marks = provider.marks
            run_gaps = [
                ((marks[i + 1][0] - marks[i][1]) * 1000, marks[i][2]) for i in range(len(marks) - 1)
            ]
            if index > 0:
                gaps.extend(run_gaps)
    values = [g for g, _ in gaps]
    by_tool: dict[str, list[float]] = {}
    for gap, tool in gaps:
        by_tool.setdefault(tool, []).append(gap)
    return {
        "session_creation_ms": summary(creations),
        "requests_measured": len(values),
        **summary(values),
        "by_preceding_tool_median_ms": {
            tool: round(statistics.median(v), 2) for tool, v in sorted(by_tool.items())
        },
    }


def summary(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "median_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
        "max_ms": round(ordered[-1], 2),
    }


def timed(fn, repeat: int = 50) -> dict:
    fn()
    values = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        values.append((time.perf_counter() - start) * 1000)
    return summary(values)


def measure_policy(repo: Path, root: Path, work: Path) -> dict:
    os.environ["MEASURE_TOKEN"] = secrets.token_urlsafe(40)
    settings = Settings(
        database=str(work / "unused.sqlite3"),
        users=[UserConfig(id="measure", token_env="MEASURE_TOKEN", repo_roots=[str(root)])],
    )
    user = settings.users[0]
    repository = Repository(str(repo), settings.confidential_marker, [], settings.output_chars)
    context = (system_prompt(enabled_tools(settings, True)) + "\n" + "x = 1\n" * 6000)[
        : settings.context_chars
    ]
    reply = json.dumps({"action": {"tool": "read_file", "path": "a.py", "end_line": 50}})

    def validate():
        classify(AgentReply.model_validate_json(reply).action)

    return {
        "confidentiality_rescan_ms": timed(lambda: resolve_repository(str(repo), user, settings)),
        "secret_scan_full_context_ms": timed(lambda: repository.check_secrets(context)),
        "reply_validation_and_classify_ms": timed(validate),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10, help="measured runs per repository")
    parser.add_argument("--out", type=Path, help="write the JSON result here")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        base = Path(temp)
        root = base / "repos"
        results = {}
        for name, (repo, target) in make_repositories(root).items():
            print(f"measuring {name} ...", file=sys.stderr, flush=True)
            results[name] = {
                "files": file_count(repo),
                "core_work_per_request": measure_runs(repo, root, target, args.runs, base),
                "policy_steps": measure_policy(repo, root, base),
            }
    worst = max(r["core_work_per_request"]["p95_ms"] for r in results.values())
    report = {
        "measure": "core work between model requests, excluding network and model time",
        "target_ms": TARGET_MS,
        "worst_p95_ms": worst,
        "within_target": worst < TARGET_MS,
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "cpus": os.cpu_count(),
        },
        "runs_per_repository": args.runs,
        "results": results,
        "note": "Scripted instant provider; not model behaviour and not benchmark evidence.",
    }
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
