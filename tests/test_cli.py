import json
import os
import subprocess
import sys

from token_agent.api import create_app
from token_agent.providers import ProviderResult

from .conftest import live_client


def test_cli_complete_interactive_flow(environment, tmp_path):
    app = create_app(environment["settings"])
    with live_client(app, environment["first"]) as http:
        env = {
            **os.environ,
            "AGENT_API_TOKEN": environment["first"]["Authorization"].split()[1],
            "AGENT_CORE_URL": str(http.base_url),
            "AGENT_CLIENT_STATE": str(tmp_path / "cli-state.json"),
        }
        command = [sys.executable, "-m", "token_agent.cli"]
        initial = subprocess.run(
            [*command, "init", "--repo", str(environment["repo"])],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert initial.returncode == 0, initial.stderr
        run = subprocess.run(
            [*command, "run", "demo: fix calculator addition"],
            env=env,
            input="a\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert run.returncode == 0, run.stderr
        assert "OFFLINE FIXTURE" in run.stdout
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


def test_cli_shows_and_persists_budget_warning(environment, tmp_path):
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
    app = create_app(settings, FinishingProvider(input_tokens=5000, output_tokens=0))
    with live_client(app, environment["first"]) as http:
        env = {
            **os.environ,
            "AGENT_API_TOKEN": environment["first"]["Authorization"].split()[1],
            "AGENT_CORE_URL": str(http.base_url),
            "AGENT_CLIENT_STATE": str(tmp_path / "cli-state.json"),
        }
        command = [sys.executable, "-m", "token_agent.cli"]
        initial = subprocess.run(
            [*command, "init", "--repo", str(environment["repo"])],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert initial.returncode == 0, initial.stderr
        run = subprocess.run(
            [*command, "run", "any task"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert run.returncode == 0, run.stderr
        assert "BUDGET WARNING" in run.stdout

        status = subprocess.run(
            [*command, "status"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert status.returncode == 0, status.stderr
        assert "BUDGET WARNING" in status.stdout
        assert env["AGENT_API_TOKEN"] not in run.stdout + run.stderr + status.stdout + status.stderr
