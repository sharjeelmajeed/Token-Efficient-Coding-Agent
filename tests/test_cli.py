import json
import os
import subprocess
import sys

from token_agent.api import create_app
from token_agent.providers import ProviderResult

from .conftest import live_client
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
    assert run.stdout.index(SUMMARY.strip()) < run.stdout.index("model_switched")
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
    assert "session_paused" in "\n".join(lines[marker + 1 :])
    assert run.stdout.count("(reply incomplete)") == 1
