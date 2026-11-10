"""Independent switches for every FSD section 8 technique (TO-01 to TO-09).

Ablation runs flip one switch at a time, and the naive baseline turns most of them off.
Switching an optimization off must never switch off a safety limit. Providers here are
scripted, so these tests prove behaviour and limits, not token savings.
"""

import hashlib
import json

import pytest

from token_agent.api import create_app
from token_agent.config import OptimizationSettings, Settings
from token_agent.context import enabled_tools, system_prompt
from token_agent.models import ReadFile
from token_agent.providers import ProviderResult
from token_agent.repo import ABSOLUTE_OUTPUT_CHARS, MAX_FILE_BYTES, PolicyError, Repository

from .conftest import live_client, session, start, wait_status

NEW_SWITCHES = ("repo_map", "targeted_reads", "output_truncation", "diff_edits")
ALL_OFF = OptimizationSettings(
    repo_map=False,
    targeted_reads=False,
    output_truncation=False,
    diff_edits=False,
    history_compaction=False,
    tool_pruning=False,
    tiered_routing=False,
    gateway_direct_cache=False,
    early_termination=False,
)
FINISH = {"tool": "finish", "summary": "Done"}


class Scripted:
    def __init__(self, actions):
        self.actions, self.prompts = list(actions), []

    async def complete(self, messages, model, user, confidential, request_context=None):
        self.prompts.append(messages[0]["content"])
        action = self.actions.pop(0) if self.actions else FINISH
        return ProviderResult(json.dumps({"action": action}), model, 20, 10, "test_transport")


def with_optimization(environment, **switches) -> Settings:
    return environment["settings"].model_copy(
        update={"optimization": OptimizationSettings(**switches)}
    )


def run(environment, settings, actions, task="Look at the code"):
    provider = Scripted(actions)
    app = create_app(settings, provider)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid, task)
        data = wait_status(client, sid, {"completed", "paused", "waiting_approval"})
        if data["run"]["status"] == "waiting_approval":
            client.post(f"/sessions/{sid}/cancel")
        events = app.state.store.events(sid)
    return data, events, provider


def results(events):
    return [e["payload"]["result"] for e in events if e["type"] == "tool_result"]


# --- Defaults ---------------------------------------------------------------------------


def test_new_switches_default_to_the_current_behaviour(environment):
    defaults = OptimizationSettings()
    assert all(getattr(defaults, name) is True for name in NEW_SWITCHES)
    names = enabled_tools(environment["settings"], inspected=True)
    assert {"repo_map", "grep", "apply_patch", "write_file"} <= names
    assert "Use exact patches and the latest file hash." in system_prompt(names)


def test_unknown_switch_names_are_rejected():
    with pytest.raises(ValueError):
        OptimizationSettings(targeted_read=False)


def test_every_run_records_its_switch_configuration(environment):
    settings = with_optimization(environment, repo_map=False, diff_edits=False)
    _, events, _ = run(environment, settings, [FINISH])
    started = next(e["payload"] for e in events if e["type"] == "run_started")
    assert started["optimization"] == settings.optimization.model_dump()
    assert len(started["optimization"]) == 9


# --- TO-01 repo map ---------------------------------------------------------------------


def test_repo_map_off_hides_the_tool_and_refuses_it(environment):
    settings = with_optimization(environment, repo_map=False)
    names = enabled_tools(settings, inspected=True)
    assert "repo_map" not in names
    assert '"repo_map"' not in system_prompt(names)
    data, _, _ = run(environment, settings, [{"tool": "repo_map"}])
    assert data["run"]["status"] == "paused"
    assert "disabled" in data["run"]["outcome"]


# --- TO-02 targeted reads ---------------------------------------------------------------


@pytest.fixture
def long_file(environment):
    path = environment["repo"] / "long.py"
    path.write_text("".join(f"value_{i} = {i}\n" for i in range(1, 301)))
    return path


def test_targeted_reads_on_limit_ranges(environment, long_file):
    repo = Repository(str(environment["repo"]), ".agent-confidential", [], ABSOLUTE_OUTPUT_CHARS)
    result = repo.execute_read(
        ReadFile(tool="read_file", path="long.py", start_line=10, end_line=12)
    )
    assert result["text"] == "10: value_10 = 10\n11: value_11 = 11\n12: value_12 = 12"
    with pytest.raises(PolicyError, match="at most 200 lines"):
        repo.execute_read(ReadFile(tool="read_file", path="long.py", start_line=1, end_line=300))


def test_targeted_reads_off_reads_whole_files_and_hides_grep(environment, long_file):
    settings = with_optimization(environment, targeted_reads=False)
    assert "grep" not in enabled_tools(settings, inspected=True)
    action = {"tool": "read_file", "path": "long.py", "start_line": 10, "end_line": 12}
    data, events, _ = run(environment, settings, [action, FINISH])
    assert data["run"]["status"] == "completed"
    text = results(events)[0]["text"]
    assert text.splitlines()[0] == "1: value_1 = 1"
    assert text.splitlines()[-1] == "300: value_300 = 300"


def test_whole_file_reads_still_respect_the_file_size_ceiling(environment):
    (environment["repo"] / "huge.py").write_text("x" * (MAX_FILE_BYTES + 1))
    settings = with_optimization(environment, targeted_reads=False, output_truncation=False)
    data, _, _ = run(environment, settings, [{"tool": "read_file", "path": "huge.py"}])
    assert data["run"]["status"] == "paused"
    assert "bounded-read limit" in data["run"]["outcome"]


# --- TO-03 output truncation ------------------------------------------------------------


@pytest.fixture
def wide_file(environment):
    path = environment["repo"] / "wide.py"
    path.write_text("".join(f"line_{i} = '{'y' * 90}'\n" for i in range(1, 151)))
    return path


def test_truncation_on_cuts_output_with_a_marker(environment, wide_file):
    settings = with_optimization(environment)
    _, events, _ = run(environment, settings, [{"tool": "read_file", "path": "wide.py"}, FINISH])
    result = results(events)[0]
    assert result["truncated"] is True
    assert "[output truncated]" in result["text"]
    assert len(result["text"]) <= settings.output_chars


def test_truncation_off_keeps_full_output_up_to_the_fixed_ceiling(environment, wide_file):
    settings = with_optimization(environment, output_truncation=False)
    read = {"tool": "read_file", "path": "wide.py", "start_line": 1, "end_line": 150}
    _, events, _ = run(environment, settings, [read, FINISH])
    result = results(events)[0]
    assert result["truncated"] is False
    assert len(result["text"]) > settings.output_chars
    assert result["text"].endswith("150: line_150 = '" + "y" * 90 + "'")


def test_fixed_ceiling_still_truncates_when_the_switch_is_off(environment):
    repo = Repository(str(environment["repo"]), ".agent-confidential", [], ABSOLUTE_OUTPUT_CHARS)
    for i in range(600):
        (environment["repo"] / f"file_with_a_long_name_number_{i:04d}.py").write_text("")
    from token_agent.models import ListTree

    result = repo.execute_read(ListTree(tool="list_tree", depth=1))
    assert len(result["text"]) <= ABSOLUTE_OUTPUT_CHARS


# --- TO-04 diff-style edits -------------------------------------------------------------


def test_diff_edits_off_offers_whole_file_writes_only(environment):
    settings = with_optimization(environment, diff_edits=False)
    names = enabled_tools(settings, inspected=True)
    assert "write_file" in names and "apply_patch" not in names
    prompt = system_prompt(names)
    assert "Edit by writing the whole file with write_file" in prompt
    assert "Use exact patches" not in prompt


def test_diff_edits_off_refuses_patches_and_still_requires_approval(environment):
    settings = with_optimization(environment, diff_edits=False)
    original = (environment["repo"] / "calculator.py").read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    patch = {
        "tool": "apply_patch",
        "path": "calculator.py",
        "expected_sha256": digest,
        "old": "return a - b",
        "new": "return a + b",
    }
    read = {"tool": "read_file", "path": "calculator.py"}
    data, _, _ = run(environment, settings, [read, patch])
    assert data["run"]["status"] == "paused" and "disabled" in data["run"]["outcome"]

    write = {
        "tool": "write_file",
        "path": "calculator.py",
        "expected_sha256": digest,
        "content": "def add(a, b):\n    return a + b\n",
    }
    data, events, _ = run(environment, settings, [read, write])
    assert data["run"]["status"] == "waiting_approval"
    assert any(e["type"] == "approval_request" for e in events)
    assert (environment["repo"] / "calculator.py").read_bytes() == original


# --- Safety never depends on the switches -----------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        {"tool": "read_file", "path": ".env"},
        {"tool": "read_file", "path": "../outside.py"},
    ],
)
def test_all_switches_off_keeps_repository_boundaries(environment, action):
    (environment["repo"] / ".env").write_text("X=1\n")
    settings = environment["settings"].model_copy(update={"optimization": ALL_OFF})
    data, _, _ = run(environment, settings, [action])
    assert data["run"]["status"] == "paused"


def test_all_switches_off_keeps_secret_checks(environment):
    (environment["repo"] / "config.py").write_text('password = "correct-horse-battery-staple"\n')
    settings = environment["settings"].model_copy(update={"optimization": ALL_OFF})
    data, events, _ = run(environment, settings, [{"tool": "read_file", "path": "config.py"}])
    assert "secret" in data["run"]["outcome"]
    assert "correct-horse" not in json.dumps(events)


def test_all_switches_off_still_needs_approval_for_writes(environment):
    settings = environment["settings"].model_copy(update={"optimization": ALL_OFF})
    write = {
        "tool": "write_file",
        "path": "new.py",
        "expected_sha256": "absent",
        "content": "x = 1\n",
    }
    data, _, _ = run(environment, settings, [write])
    assert data["run"]["status"] == "waiting_approval"
    assert not (environment["repo"] / "new.py").exists()


def test_all_switches_off_keeps_read_only_sessions_read_only(environment):
    settings = environment["settings"].model_copy(update={"optimization": ALL_OFF})
    write = {"tool": "write_file", "path": "new.py", "expected_sha256": "absent", "content": ""}
    app = create_app(settings, Scripted([write]))
    with live_client(app, environment["first"]) as client:
        created = client.post(
            "/sessions", json={"repo_path": str(environment["repo"]), "mode": "read_only"}
        ).json()
        start(client, created["session_id"])
        data = wait_status(client, created["session_id"], {"paused"})
    assert "Read-only" in data["run"]["outcome"] or "disabled" in data["run"]["outcome"]
    assert not data["pending_approvals"]
