"""FR-12: tool output cut to a configured token budget, sized from the model's own counts.

Providers here are scripted, so the "measured" density is whatever the script reports. The
tests prove the arithmetic and that the limit is applied and recorded, not tokenizer accuracy.
"""

import json

import pytest
from pydantic import ValidationError

from token_agent.api import create_app
from token_agent.config import Settings
from token_agent.context import (
    FALLBACK_CHARS_PER_TOKEN,
    MIN_OUTPUT_CHARS,
    chars_per_token,
    output_char_limit,
)
from token_agent.providers import ProviderResult
from token_agent.repo import ABSOLUTE_OUTPUT_CHARS, MAX_FILE_BYTES

from .conftest import live_client, session, start, wait_status

READ = {"tool": "read_file", "path": "big.py", "start_line": 1, "end_line": 150}
FINISH = {"tool": "finish", "summary": "Done"}


class Scripted:
    def __init__(self, actions, input_tokens):
        self.actions, self.input_tokens, self.prompts = list(actions), input_tokens, []

    async def complete(self, messages, model, user, confidential, request_context=None):
        self.prompts.append(messages)
        action = self.actions.pop(0) if self.actions else FINISH
        return ProviderResult(
            json.dumps({"action": action}), model, self.input_tokens, 10, "test_transport"
        )


@pytest.fixture
def big_file(environment):
    path = environment["repo"] / "big.py"
    path.write_text("".join(f"value_number_{i:04d} = {i * 7}\n" for i in range(1, 151)))
    return path


def run(environment, settings, input_tokens):
    provider = Scripted([READ, FINISH], input_tokens)
    app = create_app(settings, provider)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        run_id = start(client, sid)
        wait_status(client, sid, {"completed"})
        events = app.state.store.events(sid)
        usage = app.state.store.usage(run_id)
    result = next(e["payload"]["result"] for e in events if e["type"] == "tool_result")
    return result, usage, provider


def budget(environment, tokens):
    return environment["settings"].model_copy(update={"tool_output_tokens": tokens})


# --- Arithmetic -------------------------------------------------------------------------


def test_density_is_measured_from_the_request():
    messages = [{"role": "system", "content": "x" * 900}, {"role": "user", "content": "y" * 100}]
    assert chars_per_token(messages, 250) == 4.0
    assert chars_per_token(messages, 500) == 2.0


@pytest.mark.parametrize("tokens", [0, -1])
def test_missing_token_count_uses_the_cautious_fallback(tokens):
    assert chars_per_token([{"role": "user", "content": "abc"}], tokens) == FALLBACK_CHARS_PER_TOKEN
    assert chars_per_token([], 100) == FALLBACK_CHARS_PER_TOKEN


@pytest.mark.parametrize(
    ("tokens", "ratio", "expected"),
    [
        (500, 4.0, 2000),
        (500, 2.5, 1250),
        (100, 1.0, MIN_OUTPUT_CHARS),
        (20000, 100.0, MAX_FILE_BYTES),
    ],
)
def test_limit_follows_the_budget_within_fixed_bounds(tokens, ratio, expected):
    assert output_char_limit(tokens, ratio, MAX_FILE_BYTES) == expected


@pytest.mark.parametrize("bad", [99, 20001, 0])
def test_budget_setting_is_bounded(environment, bad):
    base = environment["settings"].model_dump()
    with pytest.raises(ValidationError):
        Settings(**{**base, "tool_output_tokens": bad})
    assert Settings(**base).tool_output_tokens is None


# --- Applied on every step --------------------------------------------------------------


def test_without_a_budget_the_character_limit_is_unchanged(environment, big_file):
    result, usage, _ = run(environment, environment["settings"], input_tokens=1000)
    assert all(row["details"]["tool_output_char_limit"] == 8000 for row in usage)
    assert result["truncated"] is False


def test_output_is_cut_to_the_token_budget_with_a_marker(environment, big_file):
    result, usage, provider = run(environment, budget(environment, 300), input_tokens=1000)
    characters = sum(len(m["content"]) for m in provider.prompts[0])
    expected = output_char_limit(300, characters / 1000, MAX_FILE_BYTES)
    assert usage[0]["details"]["tool_output_char_limit"] == expected
    assert result["truncated"] is True
    assert "[output truncated]" in result["text"]
    assert len(result["text"]) <= expected


def test_denser_models_get_fewer_characters_for_the_same_budget(environment, big_file):
    _, sparse, _ = run(environment, budget(environment, 300), input_tokens=500)
    _, dense, _ = run(environment, budget(environment, 300), input_tokens=2000)
    assert (
        dense[0]["details"]["tool_output_char_limit"]
        < sparse[0]["details"]["tool_output_char_limit"]
    )


def test_zero_reported_tokens_falls_back_cautiously(environment, big_file):
    _, usage, _ = run(environment, budget(environment, 300), input_tokens=0)
    assert usage[0]["details"]["tool_output_char_limit"] == int(300 * FALLBACK_CHARS_PER_TOKEN)


def test_truncation_off_ignores_token_budget_and_uses_fixed_ceiling(environment, big_file):
    opt = environment["settings"].optimization.model_copy(update={"output_truncation": False})
    settings = environment["settings"].model_copy(
        update={"tool_output_tokens": 300, "optimization": opt}
    )
    result, usage, _ = run(environment, settings, input_tokens=1000)
    assert all(row["details"]["tool_output_char_limit"] == ABSOLUTE_OUTPUT_CHARS for row in usage)
    assert result["truncated"] is False
    assert "[output truncated]" not in result["text"]
    assert "value_number_0150" in result["text"]
