import asyncio
import sys
from pathlib import Path

import pytest

from token_agent.config import SandboxSettings
from token_agent.models import RunShell, WriteFile
from token_agent.repo import PolicyError, Repository
from token_agent.sandbox import DockerSandbox

IMAGE = "python@sha256:" + "a" * 64
COMMAND = ["python", "-m", "unittest", "discover", "-v"]


@pytest.fixture
def repo(environment):
    return Repository(str(environment["repo"]), ".agent-confidential", [], 1000)


def sandbox_settings(**updates):
    return SandboxSettings(enabled=True, image=IMAGE, verification_commands=[COMMAND], **updates)


class RecordingDocker(DockerSandbox):
    """Process transport double: never starts a real container."""

    def __init__(self, settings, result=None, cancel=False):
        super().__init__(settings)
        self.calls = []
        self.result = result or {
            "exit_code": 0,
            "timed_out": False,
            "text": "controlled test output",
            "truncated": False,
        }
        self.cancel = cancel

    def available(self):
        return True

    async def invoke(self, argv, env, timeout):
        self.calls.append((argv, dict(env), timeout))
        if argv[1] == "create":
            source = Path(argv[argv.index("--mount") + 1].split("source=", 1)[1].split(",", 1)[0])
            assert not (source / ".env").exists()
            assert (source / "calculator.py").exists()
        if argv[1] == "start":
            if self.cancel:
                raise asyncio.CancelledError
            return self.result
        return {"exit_code": 0, "timed_out": False, "text": "", "truncated": False}


def test_container_command_limits_and_environment(repo, monkeypatch):
    monkeypatch.setenv("BIFROST_VIRTUAL_KEY", "sensitive-test-key")
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted:2375")
    (repo.root / ".env").write_text("hidden credentials")
    runner = RecordingDocker(sandbox_settings())
    action = RunShell(tool="run_shell", argv=COMMAND, purpose="verification")
    result = asyncio.run(runner.run(repo, action, repo.snapshot()[1]))
    assert result["verification_passed"]
    args, env, _ = runner.calls[0]
    for flag, value in [
        ("--network", "none"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges=true"),
        ("--user", "65534:65534"),
        ("--pull", "never"),
        ("--pids-limit", "64"),
    ]:
        assert args[args.index(flag) + 1] == value
    assert "--read-only" in args and IMAGE in args
    assert "BIFROST_VIRTUAL_KEY" not in env and "DOCKER_HOST" not in env
    assert "sensitive-test-key" not in str(runner.calls)
    assert runner.calls[-1][0][1:3] == ["rm", "--force"]
    assert not Path(env["HOME"]).exists()


@pytest.mark.parametrize(
    "result",
    [
        {"exit_code": 1, "timed_out": False, "text": "FAIL", "truncated": False},
        {"exit_code": -9, "timed_out": True, "text": "", "truncated": False},
    ],
)
def test_failed_or_timed_out_command_is_not_verified_and_is_cleaned(repo, result):
    runner = RecordingDocker(sandbox_settings(), result)
    output = asyncio.run(
        runner.run(
            repo,
            RunShell(tool="run_shell", argv=COMMAND, purpose="verification"),
            repo.snapshot()[1],
        )
    )
    assert not output["verification_passed"]
    assert runner.calls[-1][0][1] == "rm"


def test_cancel_cleans_container(repo):
    runner = RecordingDocker(sandbox_settings(), cancel=True)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.run(repo, RunShell(tool="run_shell", argv=COMMAND), repo.snapshot()[1]))
    assert runner.calls[-1][0][1] == "rm"


def test_unconfigured_command_cannot_establish_verification(repo):
    runner = RecordingDocker(sandbox_settings())
    result = asyncio.run(
        runner.run(
            repo,
            RunShell(tool="run_shell", argv=["echo", "passed"], purpose="verification"),
            repo.snapshot()[1],
        )
    )
    assert not result["verification_passed"]


def test_changed_snapshot_rejected_before_docker(repo):
    runner = RecordingDocker(sandbox_settings())
    digest = repo.snapshot()[1]
    (repo.root / "calculator.py").write_text("changed after approval")
    with pytest.raises(PolicyError, match="changed"):
        asyncio.run(runner.run(repo, RunShell(tool="run_shell", argv=COMMAND), digest))
    assert not runner.calls


def test_missing_docker_never_falls_back_to_host(repo, monkeypatch):
    monkeypatch.setattr("token_agent.sandbox.shutil.which", lambda name: None)
    runner = DockerSandbox(sandbox_settings())
    with pytest.raises(PolicyError, match="host execution is forbidden"):
        asyncio.run(runner.run(repo, RunShell(tool="run_shell", argv=COMMAND), repo.snapshot()[1]))


def test_process_transport_bounds_output_and_kills_timeout():
    # Only authored Python test snippets run on the host; repository commands never do.
    runner = DockerSandbox(sandbox_settings())
    result = asyncio.run(runner.invoke([sys.executable, "-c", "print('x' * 70000)"], {}, 5))
    assert len(result["text"]) == 64000 and result["truncated"]
    result = asyncio.run(runner.invoke([sys.executable, "-c", "import time; time.sleep(5)"], {}, 1))
    assert result["timed_out"] and result["exit_code"] != 0


def test_snapshot_rejects_binary_secrets_and_incomplete_scan(repo, monkeypatch):
    (repo.root / "binary.bin").write_bytes(b"\x00")
    with pytest.raises(PolicyError, match="safely snapshot"):
        repo.snapshot()
    (repo.root / "binary.bin").unlink()
    (repo.root / "leak.py").write_text("password=not-a-real-test-password")
    with pytest.raises(PolicyError, match="safely snapshot"):
        repo.snapshot()
    (repo.root / "leak.py").unlink()
    monkeypatch.setattr("token_agent.repo.MAX_FILES", 1)
    with pytest.raises(PolicyError, match="limit"):
        repo.snapshot()


def test_file_creation_empty_replacement_and_stale_hash(repo):
    new = WriteFile(tool="write_file", path="new.py", expected_sha256="absent", content="")
    assert "Create empty file" in repo.prepare_write(new)[1]
    result = repo.write(new)
    assert result["applied"] and (repo.root / "new.py").exists()
    with pytest.raises(PolicyError, match="exists"):
        repo.write(new)
    change = new.model_copy(update={"expected_sha256": repo.digest(""), "content": "value = 1\n"})
    assert repo.write(change)["applied"]
    with pytest.raises(PolicyError, match="changed"):
        repo.write(change)


def test_cleanup_failure_prevents_verified_outcome(repo):
    class FailedCleanup(RecordingDocker):
        async def invoke(self, argv, env, timeout):
            result = await super().invoke(argv, env, timeout)
            return {**result, "exit_code": 1} if argv[1] == "rm" else result

    runner = FailedCleanup(sandbox_settings())
    with pytest.raises(PolicyError, match="cleanup failed"):
        asyncio.run(
            runner.run(
                repo,
                RunShell(tool="run_shell", argv=COMMAND, purpose="verification"),
                repo.snapshot()[1],
            )
        )


def test_repository_changed_during_verification_cannot_pass(repo):
    class ConcurrentEdit(RecordingDocker):
        async def invoke(self, argv, env, timeout):
            result = await super().invoke(argv, env, timeout)
            if argv[1] == "start":
                (repo.root / "calculator.py").write_text("new content during execution")
            return result

    runner = ConcurrentEdit(sandbox_settings())
    result = asyncio.run(
        runner.run(
            repo,
            RunShell(tool="run_shell", argv=COMMAND, purpose="verification"),
            repo.snapshot()[1],
        )
    )
    assert not result["verification_passed"]
