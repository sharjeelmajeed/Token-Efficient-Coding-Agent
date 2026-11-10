"""Where a request's real prompt tokens went, and the real token cost of research (FR-62).

Totals are the model's own counts. The split across prompt parts is by character share,
which is an estimate; tests check the arithmetic and the bookkeeping, not tokenizer accuracy.
"""

import json

import httpx
import pytest

from token_agent.api import create_app, token_attribution
from token_agent.config import ResearchPackage, ResearchSettings
from token_agent.context import attribute_tokens, prompt_part, prompt_parts
from token_agent.providers import ProviderResult
from token_agent.research import Research

from .conftest import live_client, session, start, wait_status

FINISH = {"tool": "finish", "summary": "Done"}


class Scripted:
    """Reports a fixed real prompt-token count per request and records each prompt."""

    def __init__(self, actions, input_tokens=1000):
        self.actions, self.input_tokens, self.prompts = list(actions), input_tokens, []

    async def complete(self, messages, model, user, confidential, request_context=None):
        self.prompts.append(messages)
        action = self.actions.pop(0) if self.actions else FINISH
        return ProviderResult(
            json.dumps({"action": action}), model, self.input_tokens, 10, "test_transport"
        )


# --- Splitting a prompt -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "part"),
    [
        ({"role": "system", "content": "rules"}, "system_prompt"),
        ({"role": "assistant", "content": '{"action": {}}'}, "model_actions"),
        ({"role": "user", "content": "Fix the bug"}, "instructions"),
        (
            {"role": "user", "content": json.dumps({"tool": "read_file", "tool_result": {}})},
            "tool:read_file",
        ),
        (
            {"role": "user", "content": json.dumps({"tool": "web_research", "tool_result": {}})},
            "tool:web_research",
        ),
        (
            {"role": "user", "content": json.dumps({"untrusted_history_digest": []})},
            "history_digest",
        ),
        ({"role": "user", "content": '{"looks": "like json"}'}, "instructions"),
    ],
)
def test_each_message_is_named_by_its_part(message, part):
    assert prompt_part(message) == part


def test_prompt_parts_count_characters_per_part():
    messages = [
        {"role": "system", "content": "s" * 300},
        {"role": "user", "content": "task"},
        {"role": "user", "content": json.dumps({"tool": "read_file", "tool_result": {"t": "x"}})},
    ]
    parts = prompt_parts(messages)
    assert parts["system_prompt"] == 300 and parts["instructions"] == 4
    assert parts["tool:read_file"] == len(messages[2]["content"])


@pytest.mark.parametrize("total", [1, 7, 999, 1000, 12345])
def test_shares_always_add_up_to_the_real_total(total):
    parts = {"system_prompt": 1234, "instructions": 77, "tool:read_file": 3333, "x": 1}
    shares = attribute_tokens(parts, total)
    assert sum(shares.values()) == total
    assert all(value >= 0 for value in shares.values())


def test_shares_follow_character_share():
    shares = attribute_tokens({"a": 750, "b": 250}, 1000)
    assert shares == {"a": 750, "b": 250}


def test_rounding_is_deterministic():
    parts = {"a": 1, "b": 1, "c": 1}
    assert attribute_tokens(parts, 2) == attribute_tokens(dict(reversed(parts.items())), 2)


@pytest.mark.parametrize(("parts", "total"), [({}, 100), ({"a": 0}, 100), ({"a": 5}, 0)])
def test_nothing_to_share_gives_no_attribution(parts, total):
    assert attribute_tokens(parts, total) == {}


# --- Recorded on every request ----------------------------------------------------------


def test_every_request_records_where_its_real_tokens_went(environment):
    provider = Scripted([{"tool": "read_file", "path": "calculator.py"}, FINISH], input_tokens=900)
    app = create_app(environment["settings"], provider)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        run = start(client, sid)
        wait_status(client, sid, {"completed"})
        usage = app.state.store.usage(run)
        metrics = client.get(f"/metrics/runs/{run}").json()
    assert len(usage) == 2
    for row in usage:
        shares = row["details"]["prompt_tokens_by_part"]
        assert sum(shares.values()) == row["input_tokens"] == 900
        assert row["details"]["prompt_chars_by_part"].keys() == shares.keys()
        assert "character" in row["details"]["attribution"]
    assert "tool:read_file" not in usage[0]["details"]["prompt_tokens_by_part"]
    assert usage[1]["details"]["prompt_tokens_by_part"]["tool:read_file"] > 0

    totals = metrics["token_attribution"]
    assert totals["input_tokens_total"] == 1800
    assert sum(totals["input_tokens_by_part"].values()) == 1800
    assert totals["requests_without_attribution"] == 0


def test_research_is_charged_for_every_request_that_resends_it(environment, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "unit-test-only-key")
    research_settings = ResearchSettings(
        enabled=True,
        packages={"python": ResearchPackage(name="Python", domains=["docs.python.org"])},
    )
    settings = environment["settings"].model_copy(update={"research": research_settings})
    actions = [
        {"tool": "web_research", "package": "python", "topic": "usage"},
        {"tool": "list_tree"},
        {"tool": "repo_map"},
        FINISH,
    ]
    provider = Scripted(actions, input_tokens=2000)

    def tavily(_request):
        content = "Official documentation text. " * 40
        return httpx.Response(
            200,
            json={
                "results": [{"url": "https://docs.python.org/3/", "content": content}],
                "usage": {"credits": 1},
            },
        )

    app = create_app(settings, provider)
    app.state.engine.research = Research(
        research_settings, app.state.store, httpx.AsyncClient(transport=httpx.MockTransport(tavily))
    )
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        response = client.post(
            f"/sessions/{sid}/messages", json={"text": "Check the docs", "allow_research": True}
        )
        run = response.json()["run_id"]
        wait_status(client, sid, {"completed"})
        totals = client.get(f"/metrics/runs/{run}").json()["token_attribution"]
        usage = app.state.store.usage(run)
    per_request = [
        row["details"]["prompt_tokens_by_part"].get("tool:web_research", 0) for row in usage
    ]
    # Not in the first prompt, then present in all three later prompts.
    assert per_request[0] == 0 and all(tokens > 0 for tokens in per_request[1:])
    assert totals["research_input_tokens"] == sum(per_request)
    assert totals["research_input_tokens"] > max(per_request)


def test_rows_without_attribution_are_counted_not_guessed():
    rows = [
        {"input_tokens": 100, "output_tokens": 5, "details": {}},
        {
            "input_tokens": 50,
            "output_tokens": 5,
            "details": {"prompt_tokens_by_part": {"system_prompt": 50}},
        },
    ]
    totals = token_attribution(rows)
    assert totals["requests_without_attribution"] == 1
    assert totals["input_tokens_total"] == 150
    assert totals["input_tokens_by_part"] == {"system_prompt": 50}
