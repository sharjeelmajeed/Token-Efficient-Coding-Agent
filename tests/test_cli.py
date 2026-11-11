import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import token_agent.cli as cli_module
from token_agent.api import create_app
from token_agent.cli import budget_line, git_summary, notice
from token_agent.providers import ProviderResult

from .conftest import live_client
from .test_git_contract import PUSH as CONTRACT_PUSH
from .test_git_contract import FakeGit, run_app
from .test_streaming import FINISH, SUMMARY, StreamingProvider, streaming_settings


def cli_env(environment, http, tmp_path) -> dict:
    return {
        **os.environ,
        "AGENT_API_TOKEN": environment["first"]["Authorization"].split()[1],
        "AGENT_CORE_URL": str(http.base_url),
        "AGENT_CLIENT_STATE": str(tmp_path / "cli-state.json"),
        # Rich prints box-drawing characters; keep the child and the reader on UTF-8
        # so the test does not depend on the Windows console code page.
        "PYTHONIOENCODING": "utf-8",
    }


def cli(env: dict, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "token_agent.cli", *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )


def test_cli_complete_interactive_flow(environment, tmp_path):
    app = create_app(environment["settings"])
    with live_client(app, environment["first"]) as http:
        env = cli_env(environment, http, tmp_path)
        initial = cli(env, "init", "--repo", str(environment["repo"]))
        assert initial.returncode == 0, initial.stderr
        run = cli(env, "run", "demo: fix calculator addition", stdin="a\n")
        assert run.returncode == 0, run.stderr
        assert "OFFLINE FIXTURE" in run.stdout
        assert "Fixture completed" in run.stdout  # streaming off: whole message as before
        assert "reply incomplete" not in run.stdout
        assert "completed_unverified" in run.stdout
        assert "return a + b" in (environment["repo"] / "calculator.py").read_text()
        assert env["AGENT_API_TOKEN"] not in run.stdout + run.stderr


class FinishingProvider:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens, self.output_tokens = input_tokens, output_tokens

    async def complete(self, messages, model, user, confidential, request_context=None):
        return ProviderResult(
            json.dumps({"action": {"tool": "finish", "summary": "Done"}}),
            model,
            self.input_tokens,
            self.output_tokens,
            "test_transport",
        )


def priced_app(environment, input_tokens: int):
    """A core whose user has a 6000 micro-USD budget and 1 micro-USD per token."""
    users = [u.model_copy(update={"budget_micro_usd": 6000}) for u in environment["settings"].users]
    settings = environment["settings"].model_copy(
        update={
            "mode": "bifrost",
            "standard_model": "cloud/primary",
            "standard_allowlist": ["cloud/primary"],
            "input_micro_usd_per_million": 1000000,
            "output_micro_usd_per_million": 1000000,
            "max_output_tokens": 128,
            "users": users,
        }
    )
    return create_app(settings, FinishingProvider(input_tokens=input_tokens, output_tokens=0))


def test_cli_budget_warning_comes_from_core_status(environment, tmp_path):
    # 5000 of 6000 micro-USD used: past the core's 80% warning threshold.
    with live_client(priced_app(environment, 5000), environment["first"]) as http:
        env = cli_env(environment, http, tmp_path)
        assert cli(env, "init", "--repo", str(environment["repo"])).returncode == 0
        run = cli(env, "run", "any task")
        assert run.returncode == 0, run.stderr
        assert "Budget warning:" in run.stdout
        assert "options: continue, cancel" in run.stdout
        assert "BUDGET WARNING" in run.stdout
        assert "remaining of $0.0060" in run.stdout

        # The warning is never stored by the client.
        assert "budget_warning" not in (tmp_path / "cli-state.json").read_text()

        status = cli(env, "status")
        assert status.returncode == 0, status.stderr
        assert "BUDGET WARNING" in status.stdout

        # The budget belongs to the user: a brand-new session still shows the warning.
        assert cli(env, "init", "--repo", str(environment["repo"])).returncode == 0
        fresh = cli(env, "status")
        assert fresh.returncode == 0, fresh.stderr
        assert "BUDGET WARNING" in fresh.stdout
        for output in (run, status, fresh):
            assert env["AGENT_API_TOKEN"] not in output.stdout + output.stderr


def test_cli_no_budget_warning_below_threshold_or_from_stale_state(environment, tmp_path):
    # 1000 of 6000 micro-USD used: well below 80%.
    with live_client(priced_app(environment, 1000), environment["first"]) as http:
        env = cli_env(environment, http, tmp_path)
        assert cli(env, "init", "--repo", str(environment["repo"])).returncode == 0
        run = cli(env, "run", "any task")
        assert run.returncode == 0, run.stderr
        assert "BUDGET WARNING" not in run.stdout
        assert "Budget warning:" not in run.stdout

        # A client file written by the old CLI must not bring the banner back.
        state_path = tmp_path / "cli-state.json"
        state = json.loads(state_path.read_text())
        state_path.write_text(json.dumps({**state, "budget_warning": True}))
        status = cli(env, "status")
        assert status.returncode == 0, status.stderr
        assert "BUDGET WARNING" not in status.stdout


def test_cli_streamed_reply_prints_as_one_continuous_text(environment, tmp_path):
    app = create_app(streaming_settings(environment), StreamingProvider([FINISH]))
    with live_client(app, environment["first"]) as http:
        env = cli_env(environment, http, tmp_path)
        assert cli(env, "init", "--repo", str(environment["repo"])).returncode == 0
        run = cli(env, "run", "any task")
    assert run.returncode == 0, run.stderr
    # The core sends the summary in 7-character pieces; the CLI must join them.
    assert SUMMARY.strip() in run.stdout
    assert "reply incomplete" not in run.stdout


def test_cli_model_switch_between_streamed_pieces_keeps_reply_whole(environment, tmp_path):
    # The gateway answers with another allowed model, so the core sends model_switched
    # after the partial pieces and before the final piece.
    provider = StreamingProvider([FINISH], model="cloud/fallback")
    app = create_app(streaming_settings(environment), provider)
    with live_client(app, environment["first"]) as http:
        env = cli_env(environment, http, tmp_path)
        assert cli(env, "init", "--repo", str(environment["repo"])).returncode == 0
        run = cli(env, "run", "any task")
    assert run.returncode == 0, run.stderr
    assert "reply incomplete" not in run.stdout
    assert SUMMARY.strip() in run.stdout
    # The notice is shown after the reply text, not in the middle of it.
    assert run.stdout.index(SUMMARY.strip()) < run.stdout.index("Model switched:")
    assert "gateway_returned_different_allowed_model" in run.stdout


def test_cli_marks_streamed_reply_incomplete_when_paused(environment, tmp_path):
    # Valid text streams first, then the full reply fails validation and the run pauses.
    broken = FINISH[:-2] + ',"approved":true}}'
    app = create_app(streaming_settings(environment), StreamingProvider([broken]))
    with live_client(app, environment["first"]) as http:
        env = cli_env(environment, http, tmp_path)
        assert cli(env, "init", "--repo", str(environment["repo"])).returncode == 0
        run = cli(env, "run", "any task")
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    marker = lines.index("(reply incomplete)")
    assert lines[marker - 1].startswith("Fixed the calculator.")
    after = "\n".join(lines[marker + 1 :])
    assert "Blocked by policy: Model action did not match the permitted schema" in after
    assert "Paused: Model action did not match the permitted schema" in after
    assert "Next step: Resolve the cause, then submit a new instruction" in after
    assert run.stdout.count("(reply incomplete)") == 1
    # Readable lines only: no raw event names or JSON payloads.
    assert "session_paused" not in run.stdout
    assert '"reason"' not in run.stdout


@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        (
            "model_switched",
            {"from": "cloud/a", "to": "cloud/b", "reason": "fallback"},
            ["Model switched: cloud/a -> cloud/b (fallback)"],
        ),
        (
            "model_switched",
            {"from": "cloud/a", "to": "cloud/b"},
            ["Model switched: cloud/a -> cloud/b"],
        ),
        (
            "session_paused",
            {"reason": "All providers failed", "resume": "Submit a new instruction"},
            ["Paused: All providers failed", "Next step: Submit a new instruction"],
        ),
        ("session_paused", {"reason": "Budget"}, ["Paused: Budget"]),
        (
            "run_complete",
            {"outcome": "completed_unverified", "note": "No checks configured"},
            ["Run finished: completed_unverified", "Note: No checks configured"],
        ),
        ("run_complete", {"outcome": "completed_verified"}, ["Run finished: completed_verified"]),
        (
            "policy_block",
            {"reason": "Path outside repository"},
            ["Blocked by policy: Path outside repository"],
        ),
        (
            "approval_expired",
            {"approval_id": "abc"},
            ["Approval abc expired - the action was not run"],
        ),
        (
            "research_used",
            {"query": "httpx timeout", "cache_hit": True},
            ["Public research: httpx timeout (cached)"],
        ),
        (
            "research_used",
            {"query": "httpx timeout", "cache_hit": False},
            ["Public research: httpx timeout (new)"],
        ),
        # Each trigger the core sends (docs/API.md), shown so the user knows why research ran.
        (
            "research_used",
            {"query": "httpx timeout", "cache_hit": False, "trigger": "unknown_dependency"},
            ["Public research (unknown_dependency, new): httpx timeout"],
        ),
        (
            "research_used",
            {"query": "httpx timeout", "cache_hit": True, "trigger": "repeated_error"},
            ["Public research (repeated_error, cached): httpx timeout"],
        ),
        (
            "research_used",
            {"query": "httpx timeout", "cache_hit": False, "trigger": "user_permission"},
            ["Public research (user_permission, new): httpx timeout"],
        ),
        (
            "research_used",
            {"query": "httpx timeout", "cache_hit": False, "trigger": "newer_version"},
            ["Public research (newer_version, new): httpx timeout"],
        ),
        # Missing or empty trigger: same output as before.
        (
            "research_used",
            {"query": "httpx timeout", "cache_hit": False, "trigger": ""},
            ["Public research: httpx timeout (new)"],
        ),
        (
            "research_used",
            {"query": "httpx timeout", "cache_hit": True, "trigger": None},
            ["Public research: httpx timeout (cached)"],
        ),
        # Shape from the recoverable tool error change (PR #6).
        (
            "tool_result",
            {
                "tool": "read_file",
                "result": {"error": "No such file", "recoverable": True, "changed": False},
            },
            ["Tool error (the agent can correct this): No such file"],
        ),
        # A reply that is not a valid action (PR #18) uses the same recoverable shape.
        (
            "tool_result",
            {
                "tool": "invalid_reply",
                "result": {
                    "error": "Reply did not match the action schema.",
                    "recoverable": True,
                    "changed": False,
                },
            },
            ["Tool error (the agent can correct this): Reply did not match the action schema."],
        ),
        ("tool_result", {"tool": "read_file", "result": {"text": "contents"}}, []),
        ("tool_result", {"tool": "read_file", "result": "plain text"}, []),
        ("unknown_future_event", {"anything": 1}, []),
    ],
)
def test_cli_notice_text(kind, payload, expected):
    assert notice(kind, payload) == expected


@pytest.mark.parametrize(
    "kind",
    [
        "model_switched",
        "session_paused",
        "run_complete",
        "policy_block",
        "approval_expired",
        "research_used",
        "tool_result",
    ],
)
def test_cli_notice_never_crashes_on_missing_or_odd_fields(kind):
    assert isinstance(notice(kind, {}), list)
    odd = {
        "from": None,
        "to": 5,
        "reason": ["x"],
        "resume": None,
        "outcome": {},
        "result": None,
        "query": "\x1b[31mred\x1b[0m",
        "trigger": "\x1b[31mwhy\x1b[0m",
    }
    lines = notice(kind, odd)
    assert all("\x1b" not in line for line in lines)


HEAD = "a" * 40
PUSH = {
    "tool": "git_push",
    "remote": "origin",
    "branch": "feature/x",
    "expected_head": HEAD,
    "git": {"head": HEAD, "remote_url": "https://github.com/team/repo.git"},
}
COMMIT = {
    "tool": "git_commit",
    "message": "Fix calculator",
    "paths": ["calculator.py", "tests/test_calc.py"],
    "expected_head": HEAD,
}


def test_cli_git_summary_shows_only_core_fields():
    assert git_summary(PUSH) == [
        "Exact git action requires your approval: git_push",
        "  branch: feature/x",
        "  remote: origin",
        f"  expected_head: {HEAD}",
        "  checked by core before running:",
        f"    head: {HEAD}",
        "    remote_url: https://github.com/team/repo.git",
    ]
    assert git_summary(COMMIT)[1:] == [
        f"  expected_head: {HEAD}",
        "  message: Fix calculator",
        "  paths: calculator.py, tests/test_calc.py",
    ]
    checkout = {
        "tool": "git_checkout",
        "branch": "fix\x1b[31m",
        "create": True,
        "expected_head": HEAD,
    }
    assert git_summary(checkout)[1:3] == ["  branch: fix[31m", "  create: True"]


@pytest.mark.parametrize(
    ("action", "diff", "answers", "decision", "shown"),
    [
        (
            PUSH,
            "abc123 Fix calculator\nto https://github.com/team/repo.git",
            ["a"],
            "approve_once",
            ["Exact git action requires your approval: git_push", "abc123 Fix calculator"],
        ),
        (
            COMMIT,
            "--- a/calculator.py\n+++ b/calculator.py\n-    return a - b\n+    return a + b",
            ["f", "Split the test change"],
            "deny_with_feedback",
            ["Exact git action requires your approval: git_commit", "+    return a + b"],
        ),
        (
            {"tool": "future_tool", "x": "[bold]y[/bold]"},
            "",
            ["d"],
            "deny",
            ["Exact action requires your approval: future_tool", "[bold]y[/bold]"],
        ),
    ],
)
def test_cli_decide_shows_git_preview_and_posts_to_the_core_approval_id(
    monkeypatch, capsys, action, diff, answers, decision, shown
):
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={})

    replies = iter(answers)
    monkeypatch.setattr(cli_module.Prompt, "ask", lambda *a, **k: next(replies))
    approval = {"id": "approval-1", "action": action, "diff": diff, "deadline": 0}
    with httpx.Client(base_url="http://core.test", transport=httpx.MockTransport(handler)) as http:
        cli_module.decide(http, "session-1", approval)
    output = capsys.readouterr().out
    for text in shown:
        assert text in output
    assert "Exact file change" not in output
    feedback = answers[1] if len(answers) > 1 else ""
    assert sent == [
        ("/sessions/session-1/approvals/approval-1", {"decision": decision, "feedback": feedback})
    ]


@pytest.mark.parametrize(("answer", "applied"), [("a\n", True), ("d\n", False)])
def test_cli_git_push_approval_end_to_end_with_core(environment, tmp_path, answer, applied):
    # Real core and policy; only the git executor is the contract test double.
    git = FakeGit(diff="abc1234 Fix add\npush to https://github.com/example/repo.git\n")
    app, executor, _ = run_app(environment, [CONTRACT_PUSH], git=git)
    with live_client(app, environment["first"]) as http:
        env = cli_env(environment, http, tmp_path)
        assert cli(env, "init", "--repo", str(environment["repo"])).returncode == 0
        run = cli(env, "run", "push the fix", stdin=answer)
    assert run.returncode == 0, run.stderr
    assert "Exact git action requires your approval: git_push" in run.stdout
    assert "remote: origin" in run.stdout
    assert "branch: feature/fix" in run.stdout
    assert "checked by core before running:" in run.stdout
    assert "push to https://github.com/example/repo.git" in run.stdout
    assert "Exact file change" not in run.stdout
    assert bool(executor.applied) is applied


@pytest.mark.parametrize("trigger", [["unknown_dependency"], 5, {"why": 1}, True])
def test_cli_research_trigger_that_is_not_a_string_does_not_raise(trigger):
    lines = notice("research_used", {"query": "httpx timeout", "trigger": trigger})
    assert len(lines) == 1
    assert lines[0].startswith("Public research (")
    assert lines[0].endswith(", new): httpx timeout")


def test_budget_options_use_plain_words_and_keep_unknown_codes():
    payload = {"remaining_micro_usd": 0, "limit_micro_usd": 6000}
    gateway = budget_line({**payload, "options": ["raise_gateway_budget_then_resubmit"]})
    assert gateway.endswith("options: raise the gateway budget, then resend")
    user = budget_line({**payload, "options": ["raise_budget_then_resubmit", "new_option"]})
    assert user.endswith("options: raise your budget, then resend, new_option")
    assert budget_line({**payload, "options": ["continue", "cancel"]}).endswith(
        "options: continue, cancel"
    )
    assert "options" not in budget_line({**payload, "options": "continue"})


FIXTURE_TOML = 'mode = "fixture"\n'
# CI runs on a narrow, coloured terminal, where error panels wrap and add colour codes.
PLAIN_TERMINAL = {"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}


class FakeHarness:
    """Stands in for benchmarks.harness: records the call, runs nothing."""

    def __init__(self, report: dict):
        self.report = report
        self.calls: list[dict] = []

    def run_bench(self, **kwargs):
        self.calls.append(kwargs)
        return self.report


def bench(monkeypatch, tmp_path, *args: str, report: dict | None = None):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent.toml").write_text(FIXTURE_TOML)
    (tmp_path / "team.toml").write_text(FIXTURE_TOML)
    fake = FakeHarness(
        report
        if report is not None
        else {"total_runs": 2, "mode": "fixture", "benchmark_evidence": False, "report_path": "r"}
    )
    monkeypatch.setattr(cli_module, "load_harness", lambda: fake)
    result = CliRunner().invoke(cli_module.app, ["bench", *args], env=PLAIN_TERMINAL)
    return result, fake


def test_cli_bench_passes_every_option_to_the_harness(monkeypatch, tmp_path):
    result, fake = bench(
        monkeypatch,
        tmp_path,
        *("--config", "team.toml", "--task", "TASK-01", "--task", "TASK-02"),
        *("--system", "baseline", "--system", "optimized", "--repeats", "5"),
        *("--output-dir", str(tmp_path), "--gateway-log", "logs.db", "--python", "py"),
        *("--node-modules", "node-express5=C:/e5=x", "--node-modules", "node-express4=e4"),
    )
    assert result.exit_code == 0, result.output
    assert fake.calls == [
        {
            "config_path": "team.toml",
            "tasks": ["TASK-01", "TASK-02"],
            "systems": ["baseline", "optimized"],
            "repeats": 5,
            "output_dir": tmp_path,
            "gateway_log": Path("logs.db"),
            "python": "py",
            "node_modules": {"node-express5": "C:/e5=x", "node-express4": "e4"},
        }
    ]


def test_cli_bench_defaults_leave_the_choice_to_the_harness(monkeypatch, tmp_path):
    result, fake = bench(monkeypatch, tmp_path)
    assert result.exit_code == 0, result.output
    call = fake.calls[0]
    assert call["tasks"] is None and call["systems"] is None
    assert call["repeats"] == 3 and call["config_path"] == "agent.toml"
    assert call["gateway_log"] is None and call["node_modules"] == {}


def test_cli_bench_labels_evidence_only_from_the_report(monkeypatch, tmp_path):
    result, _ = bench(monkeypatch, tmp_path)
    assert "Report: r" in result.output
    assert "Not benchmark evidence (mode fixture;" in result.output
    live = {"total_runs": 6, "mode": "bifrost", "benchmark_evidence": True, "report_path": "r"}
    result, _ = bench(monkeypatch, tmp_path, report=live)
    assert "Benchmark evidence: yes" in result.output
    # A live run whose costs were not all reconciled is still not evidence.
    result, _ = bench(monkeypatch, tmp_path, report={**live, "benchmark_evidence": False})
    assert "Not benchmark evidence (mode bifrost;" in result.output


def test_cli_bench_says_when_no_runs_were_made(monkeypatch, tmp_path):
    empty = {"total_runs": 0, "mode": "fixture", "benchmark_evidence": False, "report_path": "r"}
    result, _ = bench(monkeypatch, tmp_path, "--task", "TASK-99", report=empty)
    assert result.exit_code == 0
    assert "No runs were made: check the --task IDs and --system names." in result.output


@pytest.mark.parametrize("value", ["node-express5", "=path", "node-express5="])
def test_cli_bench_rejects_a_malformed_node_modules_value(monkeypatch, tmp_path, value):
    result, fake = bench(monkeypatch, tmp_path, "--node-modules", value)
    assert result.exit_code == 2
    assert "Use --node-modules ENV=PATH" in result.output
    assert fake.calls == []


def test_cli_bench_needs_an_existing_config_file(monkeypatch, tmp_path):
    result, fake = bench(monkeypatch, tmp_path, "--config", "missing.toml")
    assert result.exit_code == 2
    assert "missing.toml" in result.output and "does not exist" in result.output
    assert fake.calls == []


def test_cli_bench_needs_the_repository_checkout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent.toml").write_text(FIXTURE_TOML)
    monkeypatch.setattr(cli_module, "CHECKOUT", tmp_path)
    result = CliRunner().invoke(cli_module.app, ["bench"], env=PLAIN_TERMINAL)
    assert result.exit_code == 2
    assert "agent bench needs the repository checkout" in result.output


def test_cli_bench_loads_the_real_harness_from_the_checkout():
    harness = cli_module.load_harness()
    assert Path(harness.__file__).resolve() == cli_module.CHECKOUT / "benchmarks" / "harness.py"
    assert callable(harness.run_bench)
