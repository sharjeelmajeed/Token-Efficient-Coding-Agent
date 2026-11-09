"""Budget fields clients rely on (FSD FR-32 to FR-35, section 9.2). Providers are scripted."""

import json

import pytest

from token_agent.api import create_app
from token_agent.policy import budget_warning_active
from token_agent.providers import ProviderResult

from .conftest import live_client, session, start, wait_status
from .test_providers import CountingProvider, live_settings


class SpendingProvider:
    """Commits part of the budget during the first request, as another session would."""

    def __init__(self, actions, spend):
        self.actions, self.spend = list(actions), spend
        self.store = None

    async def complete(self, messages, model, user, confidential, request_context=None):
        if self.spend:
            self.store.reserve(user.id, request_context["run_id"], self.spend, "other", "test")
            self.spend = 0
        action = self.actions.pop(0) if self.actions else {"tool": "finish", "summary": "Done"}
        return ProviderResult(json.dumps({"action": action}), model, 20, 10, "test_transport")


def spending_app(environment, spend, actions=(), **updates):
    provider = SpendingProvider(actions, spend)
    app = create_app(live_settings(environment, **updates), provider)
    provider.store = app.state.store
    return app


def events_of(app, sid, kind):
    return [event["payload"] for event in app.state.store.events(sid) if event["type"] == kind]


def with_budget(environment, budget):
    return [
        u.model_copy(update={"budget_micro_usd": budget}) for u in environment["settings"].users
    ]


@pytest.mark.parametrize(
    ("limit", "remaining", "expected"),
    [
        (1000, 1000, False),
        (1000, 201, False),
        (1000, 200, True),
        (1000, 0, True),
        (3, 1, False),
        (5, 1, True),
        (7, 2, False),
        (0, 0, False),
    ],
)
def test_warning_starts_at_exactly_eighty_percent(limit, remaining, expected):
    assert budget_warning_active(limit, remaining) is expected


def test_status_reports_limit_and_no_warning_below_the_threshold(connected):
    client, _, env = connected
    data = client.get(f"/sessions/{session(client, env)}/status").json()
    assert data["budget_limit_micro_usd"] == 1000000
    assert data["budget_remaining_micro_usd"] == 1000000
    assert data["budget_warning"] is False


def test_warning_event_has_remaining_limit_and_options_and_status_agrees(environment):
    app = spending_app(environment, 850000)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"completed"})
        warnings = events_of(app, sid, "budget_warning")
    assert len(warnings) == 1
    assert warnings[0]["limit_micro_usd"] == 1000000
    assert warnings[0]["remaining_micro_usd"] <= 200000
    assert warnings[0]["warning_percent"] == 80
    assert warnings[0]["options"] == ["continue", "cancel"]
    assert data["budget_warning"] is True
    assert data["budget_limit_micro_usd"] == 1000000


def test_warning_is_emitted_once_per_run_not_on_every_model_call(environment):
    actions = [{"tool": "list_tree"}, {"tool": "repo_map"}]
    app = spending_app(environment, 850000, actions)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        wait_status(client, sid, {"completed"})
        assert len(events_of(app, sid, "budget_warning")) == 1
        start(client, sid, "Continue")
        wait_status(client, sid, {"completed"})
        assert len(events_of(app, sid, "budget_warning")) == 2


def test_new_session_of_the_same_user_shows_the_warning_at_once(environment):
    app = spending_app(environment, 850000)
    with live_client(app, environment["first"]) as client:
        first = session(client, environment)
        start(client, first)
        wait_status(client, first, {"completed"})
        second = session(client, environment)
        data = client.get(f"/sessions/{second}/status").json()
    assert data["budget_warning"] is True
    assert data["run"] is None


def test_raising_the_budget_clears_the_warning(environment):
    first_app = spending_app(environment, 850000)
    with live_client(first_app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        wait_status(client, sid, {"completed"})
        assert client.get(f"/sessions/{sid}/status").json()["budget_warning"] is True

    raised = with_budget(environment, 10000000)
    second_app = create_app(live_settings(environment, users=raised), CountingProvider())
    with live_client(second_app, environment["first"]) as client:
        data = client.get(f"/sessions/{sid}/status").json()
    assert data["budget_warning"] is False
    assert data["budget_limit_micro_usd"] == 10000000


def test_warning_belongs_to_the_spending_user_only(environment):
    app = spending_app(environment, 850000)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        wait_status(client, sid, {"completed"})
        other = client.post(
            "/sessions", json={"repo_path": str(environment["repo"])}, headers=environment["second"]
        ).json()["session_id"]
        data = client.get(f"/sessions/{other}/status", headers=environment["second"]).json()
    assert data["budget_warning"] is False
    assert data["budget_remaining_micro_usd"] == 1000000


def test_budget_hit_gives_reason_amounts_and_options_before_any_request(environment):
    provider = CountingProvider()
    users = with_budget(environment, 1)
    app = create_app(live_settings(environment, users=users), provider)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"paused"})
        hits = events_of(app, sid, "budget_hit")
    assert provider.calls == []
    assert hits == [
        {
            "reason": "budget_hit",
            "remaining_micro_usd": 1,
            "limit_micro_usd": 1,
            "warning_percent": 80,
            "options": ["raise_budget_then_resubmit"],
        }
    ]
    assert data["budget_limit_micro_usd"] == 1
    assert data["budget_warning"] is False


def test_other_blocks_stay_policy_blocks_with_only_a_reason(environment):
    provider = CountingProvider(model="unapproved/model")
    app = create_app(live_settings(environment), provider)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        wait_status(client, sid, {"paused"})
        blocks = events_of(app, sid, "policy_block")
    assert len(blocks) == 1 and set(blocks[0]) == {"reason"}
    assert events_of(app, sid, "budget_hit") == []
