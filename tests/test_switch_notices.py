"""Model switch notices that match what really happened, and recoverable malformed replies.

Found in the final live run: after a gateway fallback, every later step also reported a
switch back to the requested model, and one malformed reply ended the run. Providers here are
scripted, so they prove the notices and limits, not provider behaviour.
"""

import json

from token_agent.api import create_app
from token_agent.config import OptimizationSettings
from token_agent.engine import MAX_TOOL_ERRORS
from token_agent.providers import ProviderResult

from .conftest import live_client, session, start, wait_status
from .test_providers import live_settings

FINISH = {"tool": "finish", "summary": "Done"}
LIST = {"tool": "list_tree"}


class Scripted:
    """Each step: (action or raw text, serving model or None for the requested one, fallback)."""

    def __init__(self, steps):
        self.steps, self.requested = list(steps), []

    async def complete(self, messages, model, user, confidential, request_context=None):
        self.requested.append(model)
        content, served, fallback = self.steps.pop(0) if self.steps else (FINISH, None, False)
        text = content if isinstance(content, str) else json.dumps({"action": content})
        return ProviderResult(text, served or model, 20, 10, "test_transport", is_fallback=fallback)


def run(environment, steps, **updates):
    provider = Scripted(steps)
    app = create_app(live_settings(environment, **updates), provider)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"completed", "paused"})
        events = app.state.store.events(sid)
    switches = [
        (e["payload"]["from"], e["payload"]["to"], e["payload"]["reason"])
        for e in events
        if e["type"] == "model_switched"
    ]
    return data, events, switches, provider


# --- Switch notices ---------------------------------------------------------------------


def test_lasting_fallback_is_reported_once(environment):
    steps = [(LIST, "cloud/fallback", True), (LIST, "cloud/fallback", True)]
    steps.append((FINISH, "cloud/fallback", True))
    data, _, switches, _ = run(environment, steps)
    assert data["run"]["status"] == "completed"
    assert switches == [("cloud/primary", "cloud/fallback", "gateway_fallback")]


def test_no_false_switch_back_to_the_requested_model(environment):
    steps = [(LIST, "cloud/fallback", True), (FINISH, "cloud/fallback", True)]
    _, _, switches, _ = run(environment, steps)
    assert not any(reason == "configured_step_routing" for *_, reason in switches)


def test_primary_coming_back_is_reported(environment):
    steps = [(LIST, "cloud/fallback", True), (LIST, None, False), (FINISH, None, False)]
    _, _, switches, _ = run(environment, steps)
    assert switches == [
        ("cloud/primary", "cloud/fallback", "gateway_fallback"),
        ("cloud/fallback", "cloud/primary", "gateway_primary_restored"),
    ]


def test_a_new_fallback_after_recovery_is_reported_again(environment):
    steps = [
        (LIST, "cloud/fallback", True),
        (LIST, None, False),
        (FINISH, "cloud/fallback", True),
    ]
    _, _, switches, _ = run(environment, steps)
    assert [reason for *_, reason in switches] == [
        "gateway_fallback",
        "gateway_primary_restored",
        "gateway_fallback",
    ]


def test_no_notice_when_nothing_changes(environment):
    _, _, switches, _ = run(environment, [(LIST, None, False), (FINISH, None, False)])
    assert switches == []


def test_configured_routing_change_is_still_reported(environment):
    settings = dict(
        fast_model="cloud/fast",
        standard_allowlist=["cloud/primary", "cloud/fallback", "cloud/fast"],
        optimization=OptimizationSettings(tiered_routing=True),
    )
    steps = [({"tool": "read_file", "path": "calculator.py"}, None, False), (FINISH, None, False)]
    _, _, switches, provider = run(environment, steps, **settings)
    assert provider.requested == ["cloud/fast", "cloud/primary"]
    assert switches == [("cloud/fast", "cloud/primary", "configured_step_routing")]


# --- Malformed replies ------------------------------------------------------------------


def test_one_malformed_reply_is_corrected_not_fatal(environment):
    steps = [('{"action": {"tool": "read_file"}}', None, False), (FINISH, None, False)]
    data, events, _, provider = run(environment, steps)
    assert data["run"]["status"] == "completed"
    results = [e["payload"] for e in events if e["type"] == "tool_result"]
    assert results[0]["tool"] == "invalid_reply"
    assert results[0]["result"]["recoverable"] is True
    assert results[0]["result"]["changed"] is False
    assert not any(e["type"] == "tool_call" for e in events)
    assert len(provider.requested) == 2


def test_repeated_malformed_replies_still_pause(environment):
    steps = [("not json at all", None, False)] * MAX_TOOL_ERRORS
    data, events, _, provider = run(environment, steps)
    assert data["run"]["status"] == "paused"
    assert "schema" in data["run"]["outcome"]
    assert len(provider.requested) == MAX_TOOL_ERRORS
    assert not any(e["type"] == "tool_call" for e in events)


def test_permission_fields_in_a_reply_are_never_accepted(environment):
    smuggled = '{"action": {"tool": "list_tree", "approved": true}}'
    data, events, _, _ = run(environment, [(smuggled, None, False)] * MAX_TOOL_ERRORS)
    assert data["run"]["status"] == "paused"
    assert not any(e["type"] == "tool_call" for e in events)


# --- Routing during a fallback (review note on #18) ---------------------------------------

ROUTED = dict(
    fast_model="cloud/fast",
    standard_allowlist=["cloud/primary", "cloud/fallback", "cloud/fast"],
    optimization=OptimizationSettings(tiered_routing=True),
)
READ = {"tool": "read_file", "path": "calculator.py"}


def test_routing_during_a_lasting_fallback_still_names_the_serving_model(environment):
    steps = [(READ, "cloud/fallback", True), (FINISH, "cloud/fallback", True)]
    data, _, switches, provider = run(environment, steps, **ROUTED)
    assert data["run"]["status"] == "completed"
    assert provider.requested == ["cloud/fast", "cloud/primary"]
    assert switches == [
        ("cloud/fast", "cloud/fallback", "gateway_fallback"),
        ("cloud/fast", "cloud/primary", "configured_step_routing"),
        ("cloud/primary", "cloud/fallback", "gateway_fallback"),
    ]


def test_routing_after_a_fallback_to_a_model_that_serves_needs_only_the_routing_notice(
    environment,
):
    steps = [(READ, "cloud/fallback", True), (FINISH, None, False)]
    _, _, switches, _ = run(environment, steps, **ROUTED)
    assert switches == [
        ("cloud/fast", "cloud/fallback", "gateway_fallback"),
        ("cloud/fast", "cloud/primary", "configured_step_routing"),
    ]
