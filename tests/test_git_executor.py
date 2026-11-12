"""Executor-side git safety: argv only, hardened config, bounded output, exact binding.

Unlike ``test_git_contract.py`` (which uses a recording test double for the core), these
tests run a real ``git`` binary in temporary repositories.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from token_agent.api import create_app
from token_agent.config import GitSettings
from token_agent.git_executor import CommandGitExecutor
from token_agent.models import GitCheckout, GitCommit, GitDiff, GitLog, GitPush, GitStatus
from token_agent.repo import PolicyError, Repository

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

REMOTE = "https://github.com/example/repo.git"


def run(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def make_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    run(root, "init", "-b", "main")
    run(root, "config", "user.email", "bench@example.com")
    run(root, "config", "user.name", "Bench")
    (root / "a.txt").write_text("one\n")
    (root / "b.txt").write_text("bee\n")
    run(root, "add", ".")
    run(root, "commit", "-m", "init")
    return root


def settings_for(git=None):
    """Only ``.git`` is read by the executor, so a minimal stand-in is enough here."""
    return SimpleNamespace(git=git or GitSettings(enabled=True, push_remotes={"origin": REMOTE}))


def repository(root, output_chars=100_000):
    return Repository(str(root), ".confidential", [], output_chars)


def executor(git=None, **kwargs):
    return CommandGitExecutor(settings_for(git), **kwargs)


def head_of(root):
    return run(root, "rev-parse", "HEAD").strip()


def commit_action(root, paths, message="Change files"):
    return GitCommit(tool="git_commit", message=message, paths=paths, expected_head=head_of(root))


# --- read tools ---------------------------------------------------------------------


def test_status_reports_full_head_and_branch(tmp_path):
    root = make_repo(tmp_path)
    result = asyncio.run(executor().read(repository(root), GitStatus(tool="git_status")))
    assert result["head"] == head_of(root)
    assert result["branch"] == "main"


def test_status_omits_protected_paths(tmp_path):
    root = make_repo(tmp_path)
    (root / ".env").write_text("SECRET=1\n")
    (root / "visible.txt").write_text("hi\n")
    result = asyncio.run(executor().read(repository(root), GitStatus(tool="git_status")))
    assert ".env" not in result["text"]
    assert "visible.txt" in result["text"]
    assert result["omitted"] == 1


def test_diff_and_log_are_bounded(tmp_path):
    root = make_repo(tmp_path)
    (root / "a.txt").write_text("x\n" * 200)
    bounded = asyncio.run(
        executor().read(repository(root, output_chars=80), GitDiff(tool="git_diff"))
    )
    assert bounded["truncated"] is True
    assert len(bounded["text"]) <= 80 + len("\n...[output truncated]...\n")
    log = asyncio.run(executor().read(repository(root), GitLog(tool="git_log", max_count=1)))
    assert head_of(root) in log["text"]


def test_diff_rejects_paths_outside_the_repository(tmp_path):
    root = make_repo(tmp_path)
    with pytest.raises(PolicyError):
        asyncio.run(
            executor().read(repository(root), GitDiff(tool="git_diff", paths=["../outside.txt"]))
        )


# --- commit -------------------------------------------------------------------------


def test_commit_commits_only_listed_paths(tmp_path):
    root = make_repo(tmp_path)
    (root / "a.txt").write_text("fixed\n")
    (root / "b.txt").write_text("also changed\n")
    action = commit_action(root, ["a.txt"])
    git = executor()
    preview = asyncio.run(git.prepare(repository(root), action))
    assert "a.txt" in preview.diff
    result = asyncio.run(git.apply(repository(root), action, preview.state))
    assert result["applied"] is True
    committed = run(root, "show", "--name-only", "--format=", "HEAD")
    assert committed.split() == ["a.txt"]
    assert " M b.txt" in run(root, "status", "--porcelain")


def test_commit_refuses_stale_expected_head(tmp_path):
    root = make_repo(tmp_path)
    action = commit_action(root, ["a.txt"])
    (root / "a.txt").write_text("moved on\n")
    run(root, "commit", "-am", "later")
    with pytest.raises(PolicyError):
        asyncio.run(executor().prepare(repository(root), action))


def test_apply_refuses_when_content_changed_after_approval(tmp_path):
    root = make_repo(tmp_path)
    (root / "a.txt").write_text("approved\n")
    action = commit_action(root, ["a.txt"])
    git = executor()
    preview = asyncio.run(git.prepare(repository(root), action))
    (root / "a.txt").write_text("swapped after approval\n")
    with pytest.raises(PolicyError):
        asyncio.run(git.apply(repository(root), action, preview.state))


# --- checkout -----------------------------------------------------------------------


def test_checkout_creates_and_switches(tmp_path):
    root = make_repo(tmp_path)
    action = GitCheckout(
        tool="git_checkout", branch="feature/x", create=True, expected_head=head_of(root)
    )
    git = executor()
    preview = asyncio.run(git.prepare(repository(root), action))
    asyncio.run(git.apply(repository(root), action, preview.state))
    assert run(root, "branch", "--show-current").strip() == "feature/x"


def test_checkout_refuses_to_overwrite_dirty_files(tmp_path):
    root = make_repo(tmp_path)
    run(root, "checkout", "-b", "other")
    (root / "a.txt").write_text("other version\n")
    run(root, "commit", "-am", "other")
    run(root, "checkout", "main")
    (root / "a.txt").write_text("uncommitted work\n")
    action = GitCheckout(tool="git_checkout", branch="other", expected_head=head_of(root))
    git = executor()
    preview = asyncio.run(git.prepare(repository(root), action))
    with pytest.raises(PolicyError, match="overwritten"):
        asyncio.run(git.apply(repository(root), action, preview.state))


# --- push ---------------------------------------------------------------------------


def test_push_refuses_a_remote_that_does_not_match_config(tmp_path):
    root = make_repo(tmp_path)
    run(root, "remote", "add", "origin", "https://github.com/attacker/repo.git")
    action = GitPush(
        tool="git_push", remote="origin", branch="feature/x", expected_head=head_of(root)
    )
    with pytest.raises(PolicyError, match="does not match"):
        asyncio.run(executor().prepare(repository(root), action))


def test_push_refuses_an_unconfigured_remote(tmp_path):
    root = make_repo(tmp_path)
    run(root, "remote", "add", "origin", REMOTE)
    action = GitPush(
        tool="git_push", remote="upstream", branch="feature/x", expected_head=head_of(root)
    )
    with pytest.raises(PolicyError, match="not configured"):
        asyncio.run(executor().prepare(repository(root), action))


def test_push_args_never_force_and_target_one_ref(tmp_path):
    action = GitPush(tool="git_push", remote="origin", branch="feature/x", expected_head="a" * 40)
    args = CommandGitExecutor.push_args(action)
    assert args[:2] == ["push", "--no-verify"]
    assert not any(arg.startswith("--force") or arg == "-f" for arg in args)
    assert args[-2] == "origin"
    assert args[-1] == f"{'a' * 40}:refs/heads/feature/x"


# --- command hardening --------------------------------------------------------------


def test_every_command_is_argv_with_hardened_config(tmp_path):
    root = make_repo(tmp_path)
    seen = []

    async def recording(argv, cwd):
        seen.append((list(argv), cwd))
        return 0, "", ""

    asyncio.run(executor(runner=recording).read(repository(root), GitStatus(tool="git_status")))
    assert seen
    for argv, cwd in seen:
        assert argv[0] == "git"
        assert argv[1] == "--no-pager"
        assert any(part.startswith("core.hooksPath=") for part in argv)
        assert not any(part.startswith("diff.external") for part in argv)
        assert not any(";" in part or "&&" in part for part in argv)
        assert cwd == str(root)


def test_environment_disables_prompts_and_user_config(tmp_path):
    env = executor()._environment()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"].endswith("nul") or env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert "GIT_ASKPASS" not in env or "secret" not in env.get("GIT_ASKPASS", "")


def test_git_file_pointer_is_refused(tmp_path):
    root = tmp_path / "fake"
    root.mkdir()
    (root / ".git").write_text("gitdir: ../elsewhere\n")
    with pytest.raises(PolicyError, match="worktree"):
        asyncio.run(executor().read(repository(root), GitStatus(tool="git_status")))


# --- wiring -------------------------------------------------------------------------


def test_executor_is_created_only_when_git_is_enabled(environment):
    settings = environment["settings"]
    enabled = create_app(settings.model_copy(update={"git": GitSettings(enabled=True)}))
    try:
        assert isinstance(enabled.state.engine.git, CommandGitExecutor)
    finally:
        enabled.state.store.close()
    disabled = create_app(settings)
    try:
        assert disabled.state.engine.git is None
    finally:
        disabled.state.store.close()


# --- regression tests from review ------------------------------------------------


def run_with_identity(root, *args):
    """Run git with an author and committer for this command only, storing no config."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Seed",
        "GIT_AUTHOR_EMAIL": "seed@example.com",
        "GIT_COMMITTER_NAME": "Seed",
        "GIT_COMMITTER_EMAIL": "seed@example.com",
    }
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, env=env
    ).stdout


def make_repo_without_local_identity(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    run(root, "init", "-b", "main")
    (root / "a.txt").write_text("one\n")
    run(root, "add", "a.txt")
    run_with_identity(root, "commit", "-m", "init")
    return root


def test_full_diff_omits_a_tracked_protected_file(tmp_path):
    root = make_repo(tmp_path)
    (root / ".env").write_text("SECRET_TOKEN=initial\n")
    run(root, "add", ".env")
    run(root, "commit", "-m", "env")
    (root / ".env").write_text("SECRET_TOKEN=leaked-value\n")
    (root / "a.txt").write_text("visible change\n")
    result = asyncio.run(executor().read(repository(root), GitDiff(tool="git_diff")))
    assert "a.txt" in result["text"]
    assert "leaked-value" not in result["text"]
    assert ".env" not in result["text"]


def test_staged_diff_omits_a_rename_away_from_a_protected_name(tmp_path):
    root = make_repo(tmp_path)
    (root / ".env").write_text("SECRET_TOKEN=leaked-value\n")
    run(root, "add", ".env")
    run(root, "commit", "-m", "env")
    run(root, "mv", ".env", "plain.txt")
    result = asyncio.run(executor().read(repository(root), GitDiff(tool="git_diff", staged=True)))
    assert "leaked-value" not in result["text"]
    assert "plain.txt" not in result["text"]
    assert ".env" not in result["text"]


def test_commit_refuses_a_directory_holding_a_protected_file(tmp_path):
    root = make_repo(tmp_path)
    cfg = root / "cfg"
    cfg.mkdir()
    (cfg / "app.py").write_text("value = 1\n")
    (cfg / ".env").write_text("SECRET_TOKEN=initial\n")
    run(root, "add", "cfg")
    run(root, "commit", "-m", "cfg")
    (cfg / "app.py").write_text("value = 2\n")
    (cfg / ".env").write_text("SECRET_TOKEN=leaked-value\n")
    action = commit_action(root, ["cfg"])
    with pytest.raises(PolicyError, match="[Pp]rotected"):
        asyncio.run(executor().prepare(repository(root), action))


def test_push_refuses_a_mismatched_push_url(tmp_path):
    root = make_repo(tmp_path)
    run(root, "remote", "add", "origin", REMOTE)
    run(root, "remote", "set-url", "--push", "origin", "https://github.com/attacker/repo.git")
    action = GitPush(
        tool="git_push", remote="origin", branch="feature/x", expected_head=head_of(root)
    )
    with pytest.raises(PolicyError, match="does not match"):
        asyncio.run(executor().prepare(repository(root), action))


def test_commit_includes_a_file_the_agent_created(tmp_path):
    root = make_repo(tmp_path)
    (root / "new.txt").write_text("created by the agent\n")
    action = commit_action(root, ["new.txt"])
    git = executor()
    preview = asyncio.run(git.prepare(repository(root), action))
    assert "new.txt" in preview.diff
    assert "created by the agent" in preview.diff
    result = asyncio.run(git.apply(repository(root), action, preview.state))
    assert result["applied"] is True
    assert run(root, "show", "--name-only", "--format=", "HEAD").split() == ["new.txt"]
    status = run(root, "status", "--porcelain")
    assert status.strip() == ""


def test_commit_uses_the_user_identity_when_the_repository_has_none(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Config User\n\temail = config@example.com\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    root = make_repo_without_local_identity(tmp_path)
    (root / "a.txt").write_text("two\n")
    action = commit_action(root, ["a.txt"])
    git = executor()
    preview = asyncio.run(git.prepare(repository(root), action))
    result = asyncio.run(git.apply(repository(root), action, preview.state))
    assert result["applied"] is True
    assert run(root, "log", "-1", "--format=%an <%ae>").strip() == (
        "Config User <config@example.com>"
    )


def test_status_reads_non_ascii_names_without_quoting(tmp_path):
    root = make_repo(tmp_path)
    (root / "sécrét.pem").write_text("key\n")
    (root / "café.txt").write_text("visible\n")
    result = asyncio.run(executor().read(repository(root), GitStatus(tool="git_status")))
    assert result["omitted"] == 1
    assert "pem" not in result["text"]
    assert "café.txt" in result["text"]


def test_timeout_kills_the_whole_process_tree(tmp_path):
    marker = tmp_path / "marker.txt"
    child = tmp_path / "child.py"
    child.write_text(
        f"import pathlib, time\ntime.sleep(3)\npathlib.Path({str(marker)!r}).write_text('alive')\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    git = executor(timeout=1.0)
    with pytest.raises(PolicyError, match="timed out"):
        asyncio.run(git._spawn([sys.executable, str(parent)], str(tmp_path)))
    time.sleep(5)
    assert not marker.exists()
