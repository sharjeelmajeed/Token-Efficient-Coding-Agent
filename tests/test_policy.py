from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from token_agent.models import AgentReply, ApplyPatch, ReadFile, RepoMap
from token_agent.policy import model_policy
from token_agent.repo import PolicyError, Repository, bounded
from token_agent.store import Store


@pytest.fixture
def repo(environment):
    return Repository(str(environment["repo"]), ".agent-confidential", [], 1000)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "/etc/passwd",
        "C:\\Windows\\file",
        "nested/../../file",
        ".env",
        ".env.production",
        ".git/config",
        ".agent-confidential",
        "private.pem",
    ],
)
def test_unsafe_paths_are_rejected(repo, path):
    with pytest.raises(PolicyError):
        repo.path(path)


def test_symlink_and_parent_symlink_rejected(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("outside")
    (repo.root / "link.py").symlink_to(outside / "secret.py")
    (repo.root / "linked").symlink_to(outside, target_is_directory=True)
    for path in ["link.py", "linked/secret.py"]:
        with pytest.raises(PolicyError):
            repo.read(path)


def test_hard_link_rejected(repo, tmp_path):
    outside = tmp_path / "shared.py"
    outside.write_text("outside")
    (repo.root / "linked.py").hardlink_to(outside)
    with pytest.raises(PolicyError):
        repo.read("linked.py")


def test_secret_detection(repo):
    (repo.root / "settings.py").write_text('api_key = "this_is_a_test_secret_value"')
    with pytest.raises(PolicyError):
        repo.read("settings.py")
    with pytest.raises(PolicyError):
        repo.path(".npmrc")


def test_bounded_reads(repo):
    with pytest.raises(PolicyError):
        repo.execute_read(ReadFile(tool="read_file", path="calculator.py", end_line=201))
    (repo.root / "large.txt").write_text("a" * 128001)
    with pytest.raises(PolicyError):
        repo.read("large.txt")
    result = bounded("x" * 4000, 1000)
    assert len(result["text"]) == 1000 and result["truncated"]


def test_repo_map_contains_symbols(repo):
    result = repo.execute_read(RepoMap(tool="repo_map"))
    assert "add:1" in result["text"]
    assert "return a - b" not in result["text"]


def test_patch_requires_unique_match(repo):
    text = "x = 1\nx = 1\n"
    (repo.root / "duplicate.py").write_text(text)
    action = ApplyPatch(
        tool="apply_patch",
        path="duplicate.py",
        expected_sha256=repo.digest(text),
        old="x = 1",
        new="x = 2",
    )
    with pytest.raises(PolicyError):
        repo.prepare_patch(action)


def test_unknown_tools_and_permission_injection_rejected():
    with pytest.raises(ValidationError):
        AgentReply.model_validate({"action": {"tool": "run_shell", "command": "echo unsafe"}})
    with pytest.raises(ValidationError):
        AgentReply.model_validate({"action": {"tool": "repo_map", "approval": True}})


def test_locality_cannot_fallback_to_cloud(environment):
    settings = environment["settings"].model_copy(
        update={
            "mode": "bifrost",
            "standard_model": "cloud/code",
            "standard_allowlist": ["cloud/code"],
            "local_model": "ollama/code",
            "local_allowlist": ["ollama/code"],
        }
    )
    assert model_policy(settings, True) == ("ollama/code", ["ollama/code"])
    with pytest.raises(PolicyError):
        model_policy(settings.model_copy(update={"local_allowlist": []}), True)
    with pytest.raises(PolicyError, match="loopback"):
        model_policy(settings.model_copy(update={"gateway_url": "https://remote.invalid/v1"}), True)


def test_atomic_budget_reservations_across_concurrent_runs(environment):
    user = environment["settings"].users[0].model_copy(update={"budget_micro_usd": 100})
    store = Store(environment["settings"].database)
    store.register_accounts([user])
    sid1 = store.create_session(user.id, str(environment["repo"]), False)["id"]
    sid2 = store.create_session(user.id, str(environment["repo"]), False)["id"]
    runs = [store.start_run(sid1), store.start_run(sid2)]

    def reserve(run):
        try:
            store.reserve(user.id, run, 80, "test", "test")
            return True
        except PolicyError:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(reserve, runs)) == [False, True]
    assert store.remaining(user.id) == 20


def test_restart_expires_approval_and_retains_uncertain_spend(environment):
    store = Store(environment["settings"].database)
    store.register_accounts(environment["settings"].users)
    sid = store.create_session("first", str(environment["repo"]), False)["id"]
    run = store.start_run(sid)
    approval = store.make_approval(sid, run, {"tool": "test"}, "diff", 60)
    store.reserve("first", run, 500, "test", "test")
    store.recover()
    assert store.run(run)["status"] == "paused"
    assert store.approval(approval["id"])["status"] == "expired"
    assert store.usage(run)[0]["state"] == "uncertain"
    assert store.remaining("first") == 999500


def test_confidential_roots_count_only_when_they_overlap_the_repository(environment, tmp_path):
    """A configured confidential root marks a repository at, above or below it, and nothing else."""
    from pathlib import Path

    from token_agent.policy import confidential_sources

    repo = Path(environment["repo"]).resolve()
    unrelated = tmp_path / "elsewhere" / "private"
    unrelated.mkdir(parents=True)
    above, below = repo.parent, repo / "sub"
    below.mkdir()

    def sources(*roots):
        settings = environment["settings"].model_copy(
            update={"confidential_roots": [str(r) for r in roots]}
        )
        return confidential_sources(repo, settings)

    assert sources(unrelated) == []  # neither above nor inside: ignored
    assert sources(above) == [str(above)]
    assert sources(below) == [str(below)]
    assert sources(unrelated, above) == [str(above)]
