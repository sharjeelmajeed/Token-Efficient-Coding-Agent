"""The offline fixture must offer its patch again in a later run of the same session.

Found during client acceptance: after one deny or cancel, the demo session never showed the
patch again. The fixture is scripted and never benchmark evidence.
"""

import pytest

from .conftest import session, start, wait_status


@pytest.mark.parametrize("first", ["deny", "deny_with_feedback", "cancel"])
def test_patch_is_offered_again_after_deny_or_cancel(connected, first):
    client, app, env = connected
    sid = session(client, env)
    start(client, sid)
    aid = wait_status(client, sid, {"waiting_approval"})["pending_approvals"][0]["id"]
    if first == "cancel":
        client.post(f"/sessions/{sid}/cancel")
    else:
        client.post(
            f"/sessions/{sid}/approvals/{aid}", json={"decision": first, "feedback": "Not now"}
        )
        denied = wait_status(client, sid, {"completed"})
        assert denied["run"]["outcome"] == "completed_unverified"
    assert "return a - b" in (env["repo"] / "calculator.py").read_text()

    start(client, sid)
    again = wait_status(client, sid, {"waiting_approval"})
    assert len(again["pending_approvals"]) == 1
    assert again["pending_approvals"][0]["id"] != aid
    approval = again["pending_approvals"][0]["id"]
    client.post(f"/sessions/{sid}/approvals/{approval}", json={"decision": "approve_once"})
    wait_status(client, sid, {"completed"})
    assert "return a + b" in (env["repo"] / "calculator.py").read_text()


def test_denial_in_the_same_run_still_finishes_honestly(connected):
    client, app, env = connected
    sid = session(client, env)
    start(client, sid)
    aid = wait_status(client, sid, {"waiting_approval"})["pending_approvals"][0]["id"]
    client.post(f"/sessions/{sid}/approvals/{aid}", json={"decision": "deny"})
    wait_status(client, sid, {"completed"})
    summaries = [
        e["payload"]["text"] for e in app.state.store.events(sid) if e["type"] == "message_delta"
    ]
    assert summaries == ["Change was denied. No patch was applied."]
