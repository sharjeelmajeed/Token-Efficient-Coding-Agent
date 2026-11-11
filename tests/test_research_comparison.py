"""Research on versus off (G08): off hides research entirely, on permits it explicitly."""

import json
import re
import subprocess
import sys

import httpx
import pytest

import benchmarks.harness as harness
from benchmarks.checks import Environment
from benchmarks.research_comparison import run
from token_agent.config import ResearchPackage, ResearchSettings, Settings, UserConfig
from token_agent.providers import ProviderResult
from token_agent.research import Research


class Model:
    """Researches first when the tool is offered, then reads, fixes and finishes."""

    async def complete(self, messages, model, user, confidential, request_context=None):
        offered = '"web_research"' in messages[0]["content"]
        last = messages[-1]["content"]
        previous = re.findall(r'"tool": "([a-z_]+)"', last)
        step = previous[0] if '"tool_result"' in last and previous else None
        if step is None:
            action = (
                {"tool": "web_research", "package": "pkg", "topic": "usage"}
                if offered
                else {"tool": "read_file", "path": "src/pkg/calculator.py"}
            )
        elif step == "web_research":
            action = {"tool": "read_file", "path": "src/pkg/calculator.py"}
        elif step == "read_file":
            digest = re.findall(r'"sha256": "([0-9a-f]{64})"', last)[-1]
            action = {
                "tool": "apply_patch",
                "path": "src/pkg/calculator.py",
                "expected_sha256": digest,
                "old": "return a - b",
                "new": "return a + b",
            }
        else:
            action = {"tool": "finish", "summary": "Fixed add"}
        return ProviderResult(json.dumps({"action": action}), model, 100, 10, "test_transport")


@pytest.fixture
def task(tmp_path):
    origin = tmp_path / "origin"
    (origin / "src" / "pkg").mkdir(parents=True)
    (origin / "tests").mkdir()
    git = ["git", "-c", "user.name=bench", "-c", "user.email=bench@example.com"]

    def commit(code, tests):
        (origin / "src" / "pkg" / "__init__.py").write_text("")
        (origin / "src" / "pkg" / "calculator.py").write_text(code)
        (origin / "tests" / "test_calculator.py").write_text(tests)
        subprocess.run([*git, "add", "."], cwd=origin, check=True)
        subprocess.run([*git, "commit", "--quiet", "-m", "x"], cwd=origin, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=origin, check=True, capture_output=True, text=True
        ).stdout.strip()

    subprocess.run([*git, "init", "--quiet"], cwd=origin, check=True)
    old = "def test_module():\n    import pkg.calculator\n"
    bug = commit("def add(a, b):\n    return a - b\n", old)
    fix = commit(
        "def add(a, b):\n    return a + b\n",
        old
        + "\n\ndef test_add():\n    from pkg.calculator import add\n\n    assert add(2, 3) == 5\n",
    )
    return {
        "id": "LOCAL-RESEARCH",
        "repository": str(origin),
        "commit": bug,
        "reference_commit": fix,
        "environment": "python",
        "prompt": "Fix add so it returns the sum",
        "check": {
            "kind": "fail_to_pass",
            "runner": "pytest",
            "test_files": ["tests/test_calculator.py"],
            "fail_to_pass": ["tests/test_calculator.py::test_add"],
            "pass_to_pass": ["tests/test_calculator.py::test_module"],
        },
    }


@pytest.fixture
def fake_tavily(monkeypatch):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://docs.example.org/add", "content": "add returns a sum"}
                ],
                "usage": {"credits": 1},
            },
        )

    real = harness.Engine

    def engine(settings, store, tokens, provider):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return real(settings, store, tokens, provider, Research(settings.research, store, client))

    monkeypatch.setenv("TAVILY_API_KEY", "unit-test-only-key")
    monkeypatch.setattr(harness, "Engine", engine)
    return calls


def test_research_off_hides_research_and_on_uses_it(tmp_path, monkeypatch, task, fake_tavily):
    monkeypatch.setenv("BENCH_TOKEN", "x" * 40)
    settings = Settings(
        database=str(tmp_path / "base.sqlite3"),
        users=[UserConfig(id="bench", token_env="BENCH_TOKEN", repo_roots=[str(tmp_path)])],
        research=ResearchSettings(
            enabled=True,
            packages={"pkg": ResearchPackage(name="pkg", domains=["docs.example.org"])},
        ),
    )
    report = run(
        settings,
        [task],
        repeats=1,
        gateway_log=None,
        environments={"python": Environment(python=sys.executable)},
        provider=Model(),
        workspace=tmp_path / "work",
    )
    off = report["runs"]["research_off"][0]
    on = report["runs"]["research_on"][0]
    assert off["error"] is None and on["error"] is None
    assert off["passed"] and on["passed"]
    assert off["research_enabled"] is False and off["research_calls"] == 0
    assert off["research_input_tokens"] == 0
    assert on["research_enabled"] is True and on["research_calls"] == 1
    assert on["research_input_tokens"] > 0 and on["research_credits"] == 1
    assert len(fake_tavily) == 1
    row = report["per_task"]["LOCAL-RESEARCH"]
    assert row["research_on"]["research_calls"] == 1
    assert report["benchmark_evidence"] is False


def test_research_must_be_enabled_in_the_config(tmp_path, monkeypatch, task):
    monkeypatch.setenv("BENCH_TOKEN", "x" * 40)
    settings = Settings(
        database=str(tmp_path / "base.sqlite3"),
        users=[UserConfig(id="bench", token_env="BENCH_TOKEN", repo_roots=[str(tmp_path)])],
    )
    with pytest.raises(SystemExit, match="enable \\[research\\]"):
        run(settings, [task], 1, None, {}, provider=Model(), workspace=tmp_path / "work")
