"""Interactive offline demo using a disposable, authored repository and an ephemeral token."""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def main():
    with tempfile.TemporaryDirectory(prefix="token-agent-demo-") as directory:
        root = Path(directory)
        repo = root / "repo"
        repo.mkdir()
        (repo / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
        (repo / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import add\n\n"
            "class CalculatorTests(unittest.TestCase):\n"
            "    def test_positive(self): self.assertEqual(add(2, 3), 5)\n"
            "    def test_negative(self): self.assertEqual(add(-2, -3), -5)\n"
            "    def test_zero(self): self.assertEqual(add(0, 7), 7)\n"
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {
            **os.environ,
            "AGENT_API_TOKEN": secrets.token_urlsafe(40),
            "AGENT_CORE_URL": f"http://127.0.0.1:{port}",
            "AGENT_CLIENT_STATE": str(root / "client.json"),
        }
        config = root / "demo.toml"
        config.write_text(
            f'mode = "fixture"\ndatabase = {json.dumps(str(root / "state.sqlite3"))}\n'
            '[[users]]\nid = "demo"\ntoken_env = "AGENT_API_TOKEN"\n'
            f"repo_roots = [{json.dumps(str(repo))}]\n"
        )
        command = [sys.executable, "-m", "token_agent.cli"]
        with (root / "server.log").open("w") as log:
            server = subprocess.Popen(
                [*command, "serve", "--config", str(config), "--port", str(port)],
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                with httpx.Client(trust_env=False, timeout=1) as http:
                    deadline = time.monotonic() + 10
                    while True:
                        if server.poll() is not None:
                            raise RuntimeError("Demo server exited before becoming ready")
                        try:
                            if http.get(env["AGENT_CORE_URL"] + "/health").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline:
                            raise RuntimeError("Demo server did not become ready")
                        time.sleep(0.05)
                print(
                    "Offline integration fixture. No API keys, cloud calls or benchmark claims.",
                    flush=True,
                )
                subprocess.run([*command, "init", "--repo", str(repo)], env=env, check=True)
                subprocess.run(
                    [*command, "run", "demo: fix calculator addition"], env=env, check=True
                )
                if "return a + b" not in (repo / "calculator.py").read_text():
                    print("No patch applied. The original fixture remains unchanged.")
                    return
                print(
                    "Running independent behavior checks on the authored demo fixture:", flush=True
                )
                subprocess.run(
                    [sys.executable, "-m", "unittest", "discover", "-s", str(repo)],
                    env=env,
                    check=True,
                )
                print("Demo behavior verified. These three checks are not the hackathon benchmark.")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
