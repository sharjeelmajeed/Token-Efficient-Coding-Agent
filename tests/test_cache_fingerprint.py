"""Gateway cache partitioning (TO-08) works on real repositories with binary files.

Found in the first live research pilot: with gateway_direct_cache on, every request built the
strict sandbox snapshot, which refuses binary files (and repositories over 500 files), so a
run on a real repository paused at step 1.
"""

import json
import os

from token_agent.api import create_app
from token_agent.config import OptimizationSettings
from token_agent.providers import ProviderResult
from token_agent.repo import ABSOLUTE_OUTPUT_CHARS, Repository

from .conftest import live_client, session, start, wait_status
from .test_providers import live_settings


def repository(environment):
    return Repository(str(environment["repo"]), ".agent-confidential", [], ABSOLUTE_OUTPUT_CHARS)


def touch(path, content):
    path.write_bytes(content)
    stamp = path.stat().st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(stamp, stamp))


def test_fingerprint_is_stable_and_follows_every_change(environment):
    repo = repository(environment)
    first = repo.fingerprint()
    assert repo.fingerprint() == first

    touch(environment["repo"] / "calculator.py", b"def add(a, b):\n    return a + b\n")
    edited = repo.fingerprint()
    assert edited != first

    (environment["repo"] / "new.py").write_text("x = 1\n")
    added = repo.fingerprint()
    assert added != edited

    (environment["repo"] / "new.py").unlink()
    assert repo.fingerprint() != added


def test_fingerprint_includes_binary_files_and_large_repositories(environment):
    (environment["repo"] / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    for i in range(600):
        (environment["repo"] / f"module_{i:03d}.py").write_text("")
    repo = repository(environment)
    before = repo.fingerprint()
    touch(environment["repo"] / "icon.png", b"\x89PNG changed")
    assert repo.fingerprint() != before


def test_fingerprint_ignores_blocked_folders(environment):
    repo = repository(environment)
    before = repo.fingerprint()
    for folder in (".git", "node_modules"):
        (environment["repo"] / folder).mkdir()
        (environment["repo"] / folder / "data").write_text("ignored")
    assert repo.fingerprint() == before


class Recording:
    def __init__(self):
        self.contexts = []

    async def complete(self, messages, model, user, confidential, request_context=None):
        self.contexts.append(request_context or {})
        action = {"tool": "finish", "summary": "Done"}
        if len(self.contexts) == 1:
            action = {"tool": "read_file", "path": "calculator.py"}
        return ProviderResult(json.dumps({"action": action}), model, 10, 5, "test_transport")


def test_cache_partitioning_runs_on_a_repository_with_binary_files(environment):
    (environment["repo"] / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    provider = Recording()
    settings = live_settings(
        environment, optimization=OptimizationSettings(gateway_direct_cache=True)
    )
    app = create_app(settings, provider)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"completed", "paused"})
    assert data["run"]["status"] == "completed", data["run"]["outcome"]
    fingerprints = [c.get("snapshot_sha256") for c in provider.contexts]
    assert len(fingerprints) == 2 and all(fingerprints)
    assert fingerprints[0] == fingerprints[1]  # reading a file changes nothing
