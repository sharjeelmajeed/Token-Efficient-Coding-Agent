import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from .config import SandboxSettings
from .models import RunShell
from .repo import PolicyError, Repository, bounded


class DockerSandbox:
    def __init__(self, settings: SandboxSettings):
        self.settings = settings

    def available(self) -> bool:
        return self.settings.enabled and shutil.which("docker") is not None

    def create_argv(self, name: str, source: Path, action: RunShell) -> list[str]:
        if not self.settings.enabled:
            raise PolicyError("Sandbox commands are disabled")
        if any("\x00" in arg for arg in action.argv) or "," in str(source):
            raise PolicyError("Invalid command or snapshot path")
        return [
            "docker",
            "create",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            "64",
            "--memory",
            f"{self.settings.memory_mb}m",
            "--cpus",
            "1",
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,source={source},target=/workspace,readonly",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--entrypoint",
            action.argv[0],
            self.settings.image,
            *action.argv[1:],
        ]

    async def invoke(self, argv: list[str], env: dict, timeout: int) -> dict:
        process = await asyncio.create_subprocess_exec(
            *argv, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        data = bytearray()
        truncated = False
        timed_out = False
        try:
            async with asyncio.timeout(timeout):
                while chunk := await process.stdout.read(8192):
                    available = max(0, 64000 - len(data))
                    data.extend(chunk[:available])
                    truncated |= len(chunk) > available
                await process.wait()
        except TimeoutError:
            timed_out = True
            if process.returncode is None:
                process.kill()
            await process.wait()
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        return {
            "exit_code": process.returncode,
            "text": data.decode("utf-8", errors="replace"),
            "truncated": truncated,
            "timed_out": timed_out,
        }

    async def run(self, repo: Repository, action: RunShell, expected_snapshot: str) -> dict:
        if not self.available():
            raise PolicyError("Docker sandbox is unavailable; host execution is forbidden")
        files, digest = repo.snapshot(self.settings.max_snapshot_bytes)
        if digest != expected_snapshot:
            raise PolicyError("Repository changed after command approval")
        name = "token-agent-" + uuid4().hex
        with tempfile.TemporaryDirectory(prefix="token-agent-sandbox-") as directory:
            root = Path(directory)
            source = root / "repository"
            source.mkdir(mode=0o755)
            for relative, content in files.items():
                destination = source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8")
                destination.chmod(0o444)
            # Docker receives no model keys, proxies, registry config or caller environment.
            env = {"PATH": os.environ.get("PATH", ""), "HOME": str(root), "LANG": "C.UTF-8"}
            if os.name == "nt":
                env["SystemRoot"] = os.environ.get("SystemRoot", "C:\\Windows")
            created_ok = False
            try:
                created = await self.invoke(self.create_argv(name, source, action), env, 15)
                if created["exit_code"] != 0 or created["timed_out"]:
                    raise PolicyError(
                        "Container could not be created; check Docker and pinned image"
                    )
                created_ok = True
                result = await self.invoke(
                    ["docker", "start", "--attach", name], env, self.settings.timeout_seconds
                )
                repo.check_secrets(result["text"])
                output = bounded(result["text"], repo.output_chars)
                _, current = repo.snapshot(self.settings.max_snapshot_bytes)
                verified = (
                    action.purpose == "verification"
                    and action.argv in self.settings.verification_commands
                    and result["exit_code"] == 0
                    and not result["timed_out"]
                    and current == digest
                )
                return {
                    **result,
                    **output,
                    "truncated": result["truncated"] or output["truncated"],
                    "snapshot_sha256": digest,
                    "verification_passed": verified,
                    "network": "none",
                    "repository_writable": False,
                }
            finally:
                # Cleanup also runs on timeout or cancellation. No host command fallback exists.
                cleanup = asyncio.create_task(
                    self.invoke(["docker", "rm", "--force", name], env, 10)
                )
                try:
                    cleanup_result = await asyncio.shield(cleanup)
                    if created_ok and (
                        cleanup_result["exit_code"] != 0 or cleanup_result["timed_out"]
                    ):
                        raise PolicyError(f"Container cleanup failed; operator must inspect {name}")
                except asyncio.CancelledError:
                    await cleanup
                    raise
