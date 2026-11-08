import os
import subprocess
import sys

from token_agent.api import create_app

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
