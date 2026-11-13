"""Run the live Docker sandbox acceptance and record what actually happened.

This drives the shipped `DockerSandbox` against a real daemon and a disposable
repository. Nothing is mocked, and no model, gateway or network access is used.
The container image must already be installed locally and pinned by digest,
because the runner uses `--pull never` and never installs packages.

    python scripts/accept_docker_sandbox.py --image python@sha256:<digest> \\
        --out docs/evidence/live/docker-sandbox.json

The script checks the acceptance list in docs/M2_USAGE.md: a passing and a
failing verification command, blocked network, read-only repository snapshot,
non-executable tmpfs, changed-snapshot refusal, timeout and cancellation
cleanup, secret withholding, a non-root user, no host environment leakage,
bounded output, a missing image that fails without pulling, the hardening flags
reported by Docker, and that no container outlives the run.

Exit code 0 means every check passed. The evidence file records the Docker
version, the pinned image and each check with its observed values; it is not a
benchmark result.
"""

import argparse
import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from token_agent.config import SandboxSettings
from token_agent.models import RunShell
from token_agent.repo import PolicyError, Repository
from token_agent.sandbox import DockerSandbox

PASS_ARGV = ["python", "-m", "unittest", "discover", "-v"]
FAIL_ARGV = ["python", "-c", "import sys; print('failing on purpose'); sys.exit(3)"]
NETWORK_ARGV = [
    "python",
    "-c",
    "import socket; socket.create_connection(('1.1.1.1', 443), 4); print('NETWORK-REACHED')",
]
WRITE_ARGV = ["python", "-c", "open('/workspace/escape.txt', 'w').write('x'); print('WROTE')"]
TMP_ARGV = [
    "python",
    "-c",
    "open('/tmp/payload.sh', 'w').write('#!/bin/sh\\necho TMP-EXECUTED\\n');"
    " import os, subprocess;"
    " os.chmod('/tmp/payload.sh', 0o755);"
    " subprocess.run(['/tmp/payload.sh'], check=True);"
    " print('TMP-EXECUTED')",
]
MOUNT_ARGV = ["python", "-c", "print(open('/proc/mounts').read())"]
SLEEP_ARGV = ["python", "-c", "import time; time.sleep(60)"]
ID_ARGV = ["id", "-u"]
ENV_ARGV = ["python", "-c", "import os, json; print(json.dumps(sorted(os.environ)))"]
FLOOD_ARGV = ["python", "-c", "import sys; sys.stdout.write('x' * 200000)"]


class Acceptance:
    def __init__(self, image: str, timeout: int, memory: int):
        self.image = image
        self.settings = SandboxSettings(
            enabled=True,
            image=image,
            timeout_seconds=timeout,
            memory_mb=memory,
            verification_commands=[PASS_ARGV],
        )
        self.sandbox = DockerSandbox(self.settings)
        self.checks: list[dict] = []
        self.canary = "canary-" + uuid4().hex

    def docker(self, *args: str, timeout: int = 90) -> subprocess.CompletedProcess:
        return subprocess.run(
            [shutil.which("docker") or "docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )

    def containers(self) -> list[str]:
        listed = self.docker("ps", "-a", "--filter", "name=token-agent-", "--format", "{{.Names}}")
        return [line for line in listed.stdout.splitlines() if line.strip()]

    def record(self, name: str, passed: bool, detail) -> None:
        self.checks.append({"check": name, "passed": bool(passed), "detail": detail})
        print(("PASS " if passed else "FAIL ") + name + ": " + str(detail)[:300])

    def fixture(self, root: Path) -> Repository:
        root.mkdir(parents=True, exist_ok=True)
        (root / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (root / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import add\n\n\n"
            "class CalculatorTest(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n\n\n"
            "if __name__ == '__main__':\n    unittest.main()\n",
            encoding="utf-8",
        )
        (root / "README.md").write_text("# Acceptance fixture\n", encoding="utf-8")
        return Repository(str(root), ".agent-confidential", [self.canary], 2000)

    async def run(self, repo: Repository, digest: str, work: Path) -> dict:
        os.environ["TOKEN_AGENT_ACCEPTANCE_CANARY"] = self.canary

        present = self.docker(
            "image", "inspect", self.image, "--format", "{{index .RepoDigests 0}}"
        )
        self.record(
            "pinned image is already installed with that digest",
            present.returncode == 0 and present.stdout.strip() == self.image,
            present.stdout.strip() or present.stderr.strip()[:200],
        )
        self.record(
            "sandbox reports itself available",
            self.sandbox.available(),
            self.docker(
                "info", "--format", "{{.ServerVersion}} {{.OSType}}/{{.Architecture}}"
            ).stdout.strip(),
        )

        passed = await self.sandbox.run(repo, self.action(PASS_ARGV, "verification"), digest)
        self.record(
            "verification command passes",
            passed["verification_passed"] and passed["exit_code"] == 0,
            {
                "exit_code": passed["exit_code"],
                "verification_passed": passed["verification_passed"],
                "network": passed["network"],
                "repository_writable": passed["repository_writable"],
            },
        )
        self.record(
            "snapshot digest unchanged after the run",
            passed["snapshot_sha256"] == digest and repo.snapshot()[1] == digest,
            digest[:16] + "...",
        )

        failed = await self.sandbox.run(repo, self.action(FAIL_ARGV, "verification"), digest)
        self.record(
            "failing command cannot establish verification",
            failed["exit_code"] != 0 and not failed["verification_passed"],
            {
                "exit_code": failed["exit_code"],
                "verification_passed": failed["verification_passed"],
            },
        )

        network = await self.sandbox.run(repo, self.action(NETWORK_ARGV), digest)
        self.record(
            "network is unreachable inside the container",
            network["exit_code"] != 0 and "NETWORK-REACHED" not in network["text"],
            {
                "exit_code": network["exit_code"],
                "reached_marker": "NETWORK-REACHED" in network["text"],
            },
        )

        write = await self.sandbox.run(repo, self.action(WRITE_ARGV), digest)
        self.record(
            "repository mount is read-only",
            write["exit_code"] != 0
            and "WROTE" not in write["text"]
            and not (work / "repo" / "escape.txt").exists(),
            {
                "exit_code": write["exit_code"],
                "host_file_created": (work / "repo" / "escape.txt").exists(),
            },
        )

        tmp_run = await self.sandbox.run(repo, self.action(TMP_ARGV), digest)
        mounts = (await self.sandbox.run(repo, self.action(MOUNT_ARGV), digest))["text"]
        tmp_mount = next((line for line in mounts.splitlines() if "/tmp " in line), "")
        self.record(
            "tmpfs is writable but not executable",
            tmp_run["exit_code"] != 0
            and "TMP-EXECUTED" not in tmp_run["text"]
            and "noexec" in tmp_mount,
            {"exit_code": tmp_run["exit_code"], "tmp_mount": tmp_mount[:120]},
        )

        before = set(self.containers())
        try:
            await self.sandbox.run(repo, self.action(PASS_ARGV, "verification"), "0" * 64)
            changed_rejected = False
        except PolicyError as error:
            changed_rejected = "changed" in str(error).lower()
        self.record(
            "changed snapshot is rejected before docker runs",
            changed_rejected and not (set(self.containers()) - before),
            "no container created",
        )

        fast = DockerSandbox(self.settings.model_copy(update={"timeout_seconds": 2}))
        timed = await fast.run(repo, self.action(SLEEP_ARGV), digest)
        self.record(
            "timeout is reported and the container is removed",
            timed["timed_out"] and not self.containers(),
            {"timed_out": timed["timed_out"], "containers": self.containers()},
        )

        task = asyncio.create_task(self.sandbox.run(repo, self.action(SLEEP_ARGV), digest))
        await asyncio.sleep(6)
        task.cancel()
        cancelled = False
        try:
            await task
        except asyncio.CancelledError:
            cancelled = True
        self.record(
            "cancellation kills the container",
            cancelled and not self.containers(),
            {"cancelled": cancelled, "containers": self.containers()},
        )

        try:
            await self.sandbox.run(
                repo, self.action(["python", "-c", f"print('{self.canary}')"]), digest
            )
            secret_withheld = False
        except PolicyError as error:
            secret_withheld = "secret" in str(error).lower() and self.canary not in str(error)
        self.record(
            "secret in command output is withheld", secret_withheld, "refused, canary not echoed"
        )

        uid = await self.sandbox.run(repo, self.action(ID_ARGV), digest)
        self.record(
            "container runs as uid 65534", uid["text"].strip() == "65534", uid["text"].strip()
        )

        environ = await self.sandbox.run(repo, self.action(ENV_ARGV), digest)
        keys = json.loads(environ["text"].strip().splitlines()[-1])
        leaked = [
            key
            for key in keys
            if key in {"USERPROFILE", "APPDATA", "TOKEN_AGENT_ACCEPTANCE_CANARY"}
        ]
        self.record(
            "no host environment or canary reaches the container",
            not leaked and self.canary not in environ["text"],
            {"container_env_keys": keys, "leaked": leaked},
        )

        flood = await self.sandbox.run(repo, self.action(FLOOD_ARGV), digest)
        self.record(
            "output is bounded",
            flood["truncated"] and len(flood["text"]) <= 2100,
            {"chars": len(flood["text"]), "truncated": flood["truncated"]},
        )

        missing = DockerSandbox(
            self.settings.model_copy(update={"image": "python@sha256:" + "b" * 64})
        )
        try:
            await missing.run(repo, self.action(PASS_ARGV, "verification"), digest)
            refused = False
        except PolicyError as error:
            refused = "pinned image" in str(error).lower()
        self.record(
            "missing pinned image fails without pulling",
            refused and not self.containers(),
            {"containers": self.containers()},
        )

        probe = "token-agent-acceptance-probe"
        self.docker("rm", "--force", probe)
        created = self.docker(
            *self.sandbox.create_argv(probe, work / "repo", self.action(ID_ARGV))[1:]
        )
        inspected = self.docker(
            "inspect",
            probe,
            "--format",
            "{{json .HostConfig}}|{{.Config.User}}",
        )
        host = inspected.stdout.split("|")[0] if inspected.returncode == 0 else "{}"
        config = json.loads(host) if host.strip().startswith("{") else {}
        user = inspected.stdout.split("|")[1].strip() if "|" in inspected.stdout else ""
        self.record(
            "hardening flags are applied",
            created.returncode == 0
            and config.get("ReadonlyRootfs") is True
            and config.get("NetworkMode") == "none"
            and config.get("Memory") == self.settings.memory_mb * 1024 * 1024
            and config.get("PidsLimit") == 64
            and "ALL" in (config.get("CapDrop") or [])
            and config.get("NanoCpus") == 1_000_000_000
            and user == "65534:65534"
            and (config.get("SecurityOpt") or [""])[0].startswith("no-new-privileges"),
            {
                "readonly_rootfs": config.get("ReadonlyRootfs"),
                "network": config.get("NetworkMode"),
                "memory_bytes": config.get("Memory"),
                "pids_limit": config.get("PidsLimit"),
                "cap_drop": config.get("CapDrop"),
                "security_opt": config.get("SecurityOpt"),
                "nano_cpus": config.get("NanoCpus"),
                "user": user,
            },
        )
        self.docker("rm", "--force", probe)

        self.record("no container outlives the run", not self.containers(), self.containers())

        return {
            "captured_at": datetime.now(UTC).isoformat(),
            "host": platform.platform(),
            "python": platform.python_version(),
            "docker_client": self.docker(
                "version", "--format", "{{.Client.Version}}"
            ).stdout.strip(),
            "docker_server": self.docker(
                "version", "--format", "{{.Server.Version}}"
            ).stdout.strip(),
            "docker_platform": self.docker(
                "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"
            ).stdout.strip(),
            "image": self.image,
            "image_present_digest": self.docker(
                "image", "inspect", self.image, "--format", "{{index .RepoDigests 0}}"
            ).stdout.strip(),
            "image_acquisition": (
                "installed by the operator before this run; the runner never pulls"
            ),
            "image_selection": {
                "approved_by": None,
                "status": "pending owner agreement for the benchmark task image",
            },
            "runner": {
                "network": "none",
                "repository_writable": False,
                "user": "65534:65534",
                "memory_mb": self.settings.memory_mb,
                "timeout_seconds": self.settings.timeout_seconds,
                "output_chars": 2000,
                "verification_command": PASS_ARGV,
            },
            "checks": self.checks,
            "all_passed": all(check["passed"] for check in self.checks),
            "note": (
                "Live Docker acceptance only. It is not a benchmark result and says nothing "
                "about model quality or token savings."
            ),
        }

    def action(self, argv: list[str], purpose: str = "inspection") -> RunShell:
        return RunShell(tool="run_shell", argv=argv, purpose=purpose)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True, help="image pinned as name@sha256:<digest>")
    parser.add_argument("--out", type=Path, help="write the evidence JSON here")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--memory", type=int, default=256)
    args = parser.parse_args()

    acceptance = Acceptance(args.image, args.timeout, args.memory)
    work = Path(tempfile.mkdtemp(prefix="token-agent-acceptance-"))
    repo = acceptance.fixture(work / "repo")
    _, digest = repo.snapshot()
    evidence = await acceptance.run(repo, digest, work)

    text = json.dumps(evidence, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    print("\nALL_PASSED=" + str(evidence["all_passed"]))
    return 0 if evidence["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
