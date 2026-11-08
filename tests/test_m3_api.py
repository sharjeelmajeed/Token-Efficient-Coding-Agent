import json
import os
import subprocess
from pathlib import Path

import pytest

from token_agent.api import create_app
from token_agent.config import OptimizationSettings
from token_agent.store import Store

from .conftest import live_client, session, start, wait_status
from .test_providers import CountingProvider


@pytest.mark.parametrize(
    "action",
    [
        {
            "tool": "write_file",
            "path": "forbidden.py",
            "expected_sha256": "absent",
            "content": "x=1",
        },
        {"tool": "run_shell", "argv": ["echo", "not permitted"]},
        {
            "tool": "apply_patch",
            "path": "calculator.py",
            "expected_sha256": "a" * 64,
            "old": "return a - b",
            "new": "return a + b",
        },
    ],
)
def test_read_only_blocks_gated_tools_even_with_pruning_disabled(environment, action):
    settings = environment["settings"].model_copy(
        update={"optimization": OptimizationSettings(tool_pruning=False)}
    )
    provider = CountingProvider(action=action)
    app = create_app(settings, provider)
    with live_client(app, environment["first"]) as client:
        created = client.post(
            "/sessions", json={"repo_path": str(environment["repo"]), "mode": "read_only"}
        ).json()
        assert created["read_only"]
        sid = created["session_id"]
        start(client, sid)
        result = wait_status(client, sid, {"paused"})
        assert "Read-only" in result["run"]["outcome"]
        assert not result["pending_approvals"]
        assert not any(e["type"] == "tool_call" for e in app.state.store.events(sid))
    assert not (environment["repo"] / "forbidden.py").exists()


def test_read_only_identity_cannot_upgrade_or_approve(environment):
    settings = environment["settings"].model_copy(
        update={
            "users": [
                u.model_copy(update={"read_only": True}) for u in environment["settings"].users
            ]
        }
    )
    app = create_app(settings)
    with live_client(app, environment["first"]) as client:
        response = client.post(
            "/sessions", json={"repo_path": str(environment["repo"]), "mode": "interactive"}
        )
        assert response.json()["read_only"]
        sid = response.json()["session_id"]
        assert (
            client.post(
                f"/sessions/{sid}/approvals/nonexistent", json={"decision": "approve_once"}
            ).status_code
            == 403
        )


def test_preview_is_exact_read_only_owner_checked_and_stale_safe(connected):
    client, _, env = connected
    sid = session(client, env)
    start(client, sid)
    approval = wait_status(client, sid, {"waiting_approval"})["pending_approvals"][0]
    url = f"/sessions/{sid}/approvals/{approval['id']}/preview"
    assert client.get(url, headers=env["second"]).status_code == 404
    result = client.get(url)
    assert result.status_code == 200
    preview = result.json()
    assert "return a - b" in preview["before"] and "return a + b" in preview["after"]
    assert "return a - b" in (env["repo"] / "calculator.py").read_text()
    assert preview["expected_sha256"] == approval["action"]["expected_sha256"]
    (env["repo"] / "calculator.py").write_text("changed since approval")
    assert client.get(url).status_code == 409


def test_decided_approval_preview_is_not_replayed(connected):
    client, _, env = connected
    sid = session(client, env)
    start(client, sid)
    approval = wait_status(client, sid, {"waiting_approval"})["pending_approvals"][0]
    url = f"/sessions/{sid}/approvals/{approval['id']}"
    client.post(url, json={"decision": "deny"})
    assert client.get(url + "/preview").status_code == 409


def test_read_only_migration_preserves_existing_state(environment):
    store = Store(environment["settings"].database)
    store.register_accounts(environment["settings"].users)
    sid = store.create_session("first", str(environment["repo"]), False)["id"]
    Store(environment["settings"].database)
    assert store.session(sid)["read_only"] == 0
    store.restrict_session(sid)
    assert Store(environment["settings"].database).session(sid)["read_only"] == 1


def test_typescript_client_against_real_core(environment):
    root = Path(__file__).resolve().parents[1]
    executable = root / "clients/vscode/out/client.js"
    if not executable.is_file():
        pytest.fail("Compile the VS Code client before running M3 integration tests")
    app = create_app(environment["settings"])
    with live_client(app, environment["first"]) as client:
        env = {
            **os.environ,
            "TOKEN_AGENT_TEST_URL": str(client.base_url),
            "TOKEN_AGENT_TEST_TOKEN": environment["first"]["Authorization"].split()[1],
            "TOKEN_AGENT_TEST_REPO": str(environment["repo"]),
        }
        result = subprocess.run(
            ["node", str(root / "clients/vscode/test/core-smoke.cjs")],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["outcome"] == "completed_unverified"
        assert env["TOKEN_AGENT_TEST_TOKEN"] not in result.stdout + result.stderr
    assert "return a + b" in (environment["repo"] / "calculator.py").read_text()
