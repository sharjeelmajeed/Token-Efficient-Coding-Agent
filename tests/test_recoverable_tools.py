"""Correctable model mistakes, plain read_file text, and reads remembered across runs.

Found in live runs: a small local model copied "N: " line labels into patches, and every
mismatch paused the run. Providers here are scripted, so model quality is not measured.
"""

import hashlib
import json

import pytest

from token_agent.api import create_app
from token_agent.engine import MAX_TOOL_ERRORS
from token_agent.models import ReadFile
from token_agent.providers import ProviderResult
from token_agent.repo import Repository

from .conftest import live_client, session, start, wait_status

READ = {"tool": "read_file", "path": "calculator.py"}
FINISH = {"tool": "finish", "summary": "Done"}


@pytest.fixture
def original(environment):
    # Read back from disk: on Windows the fixture file has CRLF line endings.
    return (environment["repo"] / "calculator.py").read_bytes().decode()


@pytest.fixture
def patch(original):
    return {
        "tool": "apply_patch",
        "path": "calculator.py",
        "expected_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "old": "return a - b",
        "new": "return a + b",
    }


class Scripted:
    def __init__(self, actions):
        self.actions, self.prompts = list(actions), []

    async def complete(self, messages, model, user, confidential, request_context=None):
        self.prompts.append(json.dumps(messages))
        action = self.actions.pop(0) if self.actions else FINISH
        return ProviderResult(json.dumps({"action": action}), model, 20, 10, "test_transport")


def app_with(environment, actions):
    provider = Scripted(actions)
    return create_app(environment["settings"], provider), provider


def results(app, sid):
    return [
        e["payload"]["result"] for e in app.state.store.events(sid) if e["type"] == "tool_result"
    ]


def current(environment):
    return (environment["repo"] / "calculator.py").read_bytes().decode()


# --- read_file output -------------------------------------------------------------------


def test_read_file_returns_the_exact_text_without_line_labels(environment, patch):
    repo = Repository(str(environment["repo"]), ".agent-confidential", [], 8000)
    result = repo.execute_read(ReadFile(tool="read_file", path="calculator.py"))
    assert result["text"] == "def add(a, b):\n    return a - b"
    assert (result["start_line"], result["end_line"], result["total_lines"]) == (1, 2, 2)
    assert result["sha256"] == patch["expected_sha256"]


def test_read_file_range_reports_where_the_text_starts(environment):
    (environment["repo"] / "long.py").write_text("".join(f"x{i} = {i}\n" for i in range(1, 11)))
    repo = Repository(str(environment["repo"]), ".agent-confidential", [], 8000)
    middle = repo.execute_read(ReadFile(tool="read_file", path="long.py", start_line=4, end_line=6))
    assert middle["text"] == "x4 = 4\nx5 = 5\nx6 = 6"
    assert (middle["start_line"], middle["end_line"]) == (4, 6)
    tail = repo.execute_read(ReadFile(tool="read_file", path="long.py", start_line=9, end_line=50))
    assert tail["text"] == "x9 = 9\nx10 = 10" and tail["end_line"] == 10


# --- Correctable mistakes ---------------------------------------------------------------


def test_mismatched_patch_is_reported_and_the_model_can_fix_it(environment, original, patch):
    labelled = {**patch, "old": "2:     return a - b", "new": "2:     return a + b"}
    app, provider = app_with(environment, [READ, labelled, patch])
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        waiting = wait_status(client, sid, {"waiting_approval"})
        # The bad patch changed nothing and produced no approval.
        assert len(waiting["pending_approvals"]) == 1
        assert waiting["pending_approvals"][0]["action"]["old"] == "return a - b"
        assert current(environment) == original
        error = next(r for r in results(app, sid) if "error" in r)
        assert error == {
            "error": "Patch must match exactly one occurrence",
            "recoverable": True,
            "changed": False,
        }
        aid = waiting["pending_approvals"][0]["id"]
        client.post(f"/sessions/{sid}/approvals/{aid}", json={"decision": "approve_once"})
        wait_status(client, sid, {"completed"})
    assert "return a + b" in current(environment)
    # The model saw the error before its next attempt.
    assert "Patch must match exactly one occurrence" in provider.prompts[2]


def test_editing_before_reading_is_reported_not_fatal(environment, original, patch):
    app, _ = app_with(environment, [patch, READ, patch])
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        waiting = wait_status(client, sid, {"waiting_approval"})
        assert results(app, sid)[0]["error"] == "Read the file with read_file before editing it"
        assert len(waiting["pending_approvals"]) == 1
        client.post(f"/sessions/{sid}/cancel")
    assert current(environment) == original


def test_repeated_mistakes_pause_after_the_limit(environment, original, patch):
    mistakes = [{**patch, "old": f"missing text {i}"} for i in range(MAX_TOOL_ERRORS)]
    app, provider = app_with(environment, [READ, *mistakes])
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"paused"})
    assert data["run"]["outcome"] == (
        "Patch must match exactly one occurrence (repeated tool input errors)"
    )
    assert len(provider.prompts) == 1 + MAX_TOOL_ERRORS
    assert current(environment) == original


def test_a_success_resets_the_mistake_count(environment, patch):
    first, second = {**patch, "old": "missing"}, {**patch, "old": "also missing"}
    actions = [READ, first, {"tool": "repo_map"}, second, {"tool": "list_tree"}, patch]
    app, _ = app_with(environment, actions)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"waiting_approval", "paused"})
        client.post(f"/sessions/{sid}/cancel")
    assert data["run"]["status"] == "waiting_approval"


@pytest.mark.parametrize(
    ("action", "reason"),
    [
        ({"tool": "read_file", "path": ".env"}, "Protected repository path"),
        ({"tool": "read_file", "path": "../outside.py"}, "Path traversal is forbidden"),
        ({"tool": "grep", "pattern": "x"}, None),
    ],
)
def test_security_refusals_still_pause_immediately(environment, action, reason):
    (environment["repo"] / ".env").write_text("X=1\n")
    app, provider = app_with(environment, [action, FINISH])
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"paused", "completed"})
    if reason is None:
        assert data["run"]["status"] == "completed"  # Control: a normal read is unaffected.
        return
    assert data["run"]["status"] == "paused"
    assert data["run"]["outcome"] == reason
    assert len(provider.prompts) == 1


def test_secret_in_file_still_pauses(environment):
    (environment["repo"] / "config.py").write_text('password = "correct-horse-battery-staple"\n')
    app, _ = app_with(environment, [{"tool": "read_file", "path": "config.py"}])
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"paused"})
    assert "secret" in data["run"]["outcome"]
    assert "correct-horse" not in json.dumps(app.state.store.events(sid))


def test_read_only_edit_is_still_a_policy_block(environment, patch):
    app, _ = app_with(environment, [READ, patch])
    with live_client(app, environment["first"]) as client:
        created = client.post(
            "/sessions", json={"repo_path": str(environment["repo"]), "mode": "read_only"}
        ).json()
        start(client, created["session_id"])
        data = wait_status(client, created["session_id"], {"paused"})
    assert "Read-only" in data["run"]["outcome"] or "disabled" in data["run"]["outcome"]
    assert not data["pending_approvals"]


def test_file_changed_after_approval_still_pauses(environment, patch):
    app, _ = app_with(environment, [READ, patch])
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        aid = wait_status(client, sid, {"waiting_approval"})["pending_approvals"][0]["id"]
        (environment["repo"] / "calculator.py").write_text("def add(a, b):\n    return 0\n")
        client.post(f"/sessions/{sid}/approvals/{aid}", json={"decision": "approve_once"})
        data = wait_status(client, sid, {"paused"})
    assert "File changed" in data["run"]["outcome"]
    assert "return 0" in current(environment)


# --- Reads remembered across runs -------------------------------------------------------


def test_new_run_can_edit_a_file_read_in_an_earlier_run(environment, patch):
    app, _ = app_with(environment, [READ, FINISH, patch])
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid, "Look at the calculator")
        wait_status(client, sid, {"completed"})
        start(client, sid, "Now fix add")
        waiting = wait_status(client, sid, {"waiting_approval"})
        assert waiting["pending_approvals"][0]["action"]["tool"] == "apply_patch"
        client.post(f"/sessions/{sid}/cancel")


def test_failed_read_does_not_count_as_inspection(environment, patch):
    app, _ = app_with(environment, [{"tool": "read_file", "path": "missing.py"}, FINISH, patch])
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        wait_status(client, sid, {"completed"})
        start(client, sid, "Now fix add")
        data = wait_status(client, sid, {"completed", "paused"})
        assert not data["pending_approvals"]
    errors = [r["error"] for r in results(app, sid) if "error" in r]
    assert errors[0] == "File does not exist or is not a regular file"
    assert "Read the file with read_file before editing it" in errors


def test_inspection_is_per_session_not_shared_with_other_sessions(environment, patch):
    app, _ = app_with(environment, [READ, FINISH, patch])
    with live_client(app, environment["first"]) as client:
        first = session(client, environment)
        start(client, first)
        wait_status(client, first, {"completed"})
        second = session(client, environment)
        start(client, second)
        data = wait_status(client, second, {"completed", "paused"})
        assert not data["pending_approvals"]
    assert results(app, second)[0]["error"].startswith("Read the file")
