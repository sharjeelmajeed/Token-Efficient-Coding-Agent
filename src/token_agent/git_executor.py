"""Concrete ``GitExecutor``: runs git only for actions the core already allowed.

The core owns schemas, classification, push policy and approval binding. This module only
turns an allowed action into one argv-only ``git`` call, then reports what happened. It never
decides whether an action is permitted.

Safety rules from ``docs/GIT_TOOLS.md`` that the core cannot check and are enforced here:

* argv only, never a shell, with ``--`` before paths;
* inherited and system git config disabled, hooks, pagers, external diffs, GPG signing and
  tag pushing turned off, and ``GIT_TERMINAL_PROMPT=0`` so nothing can wait on a prompt;
* only ``repository.root`` is used, and a ``.git`` file (worktree pointer) is refused;
* push re-resolves the remote URL and requires an exact match with the configured URL, then
  pushes exactly one commit to ``refs/heads/<branch>`` with no force;
* every command is time-limited and every result is bounded and secret-checked by the core.
"""

import asyncio
import hashlib
import os
from collections.abc import Awaitable, Callable

from .config import Settings
from .git_contract import GitPreview
from .models import GitCheckout, GitCommit, GitDiff, GitLog, GitPush, GitStatus
from .repo import PolicyError, Repository, bounded

RunResult = tuple[int, str, str]
Runner = Callable[[list[str], str], Awaitable[RunResult]]

# Removes every way a repository can make git run unrelated code or block on input.
HARDENING = [
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "core.pager=cat",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "push.followTags=false",
    "-c",
    "protocol.ext.allow=never",
]
# Credentials may arrive through these variables or an ssh agent, never through argv.
PASSTHROUGH = (
    "PATH",
    "SystemRoot",
    "SYSTEMROOT",
    "SystemDrive",
    "TEMP",
    "TMP",
    "TMPDIR",
    "SSH_AUTH_SOCK",
    "GIT_ASKPASS",
    "GIT_SSH_COMMAND",
    "HOME",
    "USERPROFILE",
)


class CommandGitExecutor:
    """Runs the six git tools with argv-only, hardened, bounded commands."""

    def __init__(
        self,
        settings: Settings,
        timeout: float = 60.0,
        runner: Runner | None = None,
    ):
        self.settings = settings
        self.timeout = timeout
        self._runner = runner

    # --- process handling -------------------------------------------------------------

    def _environment(self) -> dict[str, str]:
        env = {name: os.environ[name] for name in PASSTHROUGH if name in os.environ}
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_OPTIONAL_LOCKS="0",
            GIT_PAGER="cat",
            PAGER="cat",
            LC_ALL="C",
        )
        return env

    async def _spawn(self, argv: list[str], cwd: str) -> RunResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=self._environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(self.timeout):
                out, err = await process.communicate()
        except TimeoutError:
            process.kill()
            await process.wait()
            raise PolicyError("Git command timed out") from None
        return (
            process.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )

    async def _git(
        self, repository: Repository, args: list[str], *, check: bool = True
    ) -> RunResult:
        argv = ["git", "--no-pager", *HARDENING, *args]
        code, out, err = await (self._runner or self._spawn)(argv, str(repository.root))
        if check and code != 0:
            message = err.strip() or out.strip() or "git command failed"
            raise PolicyError(bounded(message, repository.output_chars)["text"])
        return code, out, err

    # --- repository facts --------------------------------------------------------------

    @staticmethod
    def _check_repository(repository: Repository) -> None:
        dot = repository.root / ".git"
        if dot.is_file():
            raise PolicyError("Git worktrees (.git file) are not supported")
        if not dot.is_dir():
            raise PolicyError("Repository is not a git repository")
        if not dot.resolve().is_relative_to(repository.root):
            raise PolicyError("Repository .git directory points outside the repository")

    async def _revision(self, repository: Repository, ref: str) -> str:
        code, out, _ = await self._git(
            repository, ["rev-parse", "--verify", "--quiet", ref], check=False
        )
        return out.strip() if code == 0 else ""

    async def _head(self, repository: Repository) -> str:
        head = await self._revision(repository, "HEAD")
        if len(head) not in {40, 64}:
            raise PolicyError("Repository has no HEAD commit to work from")
        return head

    async def _branch(self, repository: Repository) -> str:
        _, out, _ = await self._git(repository, ["branch", "--show-current"], check=False)
        return out.strip()

    async def _require_unchanged(self, repository: Repository, expected: str) -> str:
        head = await self._head(repository)
        if head.lower() != expected.lower():
            raise PolicyError("HEAD changed since git_status; request a new action")
        return head

    @staticmethod
    def _inside(repository: Repository, relative: str) -> str:
        """Validate a model-supplied path through the repository boundary rules."""
        repository.path(relative)
        return relative

    # --- read tools --------------------------------------------------------------------

    async def read(self, repository: Repository, action) -> dict:
        self._check_repository(repository)
        if isinstance(action, GitStatus):
            return await self._status(repository)
        if isinstance(action, GitDiff):
            return await self._diff(repository, action)
        if isinstance(action, GitLog):
            return await self._log(repository, action)
        raise PolicyError("Unsupported git read action")

    async def _status(self, repository: Repository) -> dict:
        head = await self._revision(repository, "HEAD")
        branch = await self._branch(repository)
        _, porcelain, _ = await self._git(
            repository, ["status", "--porcelain=v1", "--branch", "--untracked-files=all"]
        )
        kept, omitted = [], 0
        for line in porcelain.splitlines():
            if line.startswith("##"):
                continue
            candidates = [part.strip() for part in line[3:].split(" -> ")]
            if any(self._is_protected(repository, candidate) for candidate in candidates):
                omitted += 1
                continue
            kept.append(line)
        if omitted:
            kept.append(f"[{omitted} protected paths omitted]")
        result: dict = {"head": head, "branch": branch, "omitted": omitted}
        result.update(bounded("\n".join(kept), repository.output_chars))
        return result

    @staticmethod
    def _is_protected(repository: Repository, relative: str) -> bool:
        try:
            repository.path(relative)
        except PolicyError:
            return True
        return any(repository.protected(part) for part in relative.split("/"))

    async def _diff(self, repository: Repository, action: GitDiff) -> dict:
        paths = [self._inside(repository, path) for path in action.paths]
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
        if action.staged:
            args.append("--cached")
        if paths:
            args.extend(["--", *paths])
        _, out, _ = await self._git(repository, args)
        return {"staged": action.staged, **bounded(out, repository.output_chars)}

    async def _log(self, repository: Repository, action: GitLog) -> dict:
        paths = [self._inside(repository, path) for path in action.paths]
        args = [
            "log",
            f"-n{action.max_count}",
            "--no-color",
            "--format=%H %ad %an: %s",
            "--date=short",
        ]
        if paths:
            args.extend(["--", *paths])
        _, out, _ = await self._git(repository, args)
        return {"max_count": action.max_count, **bounded(out, repository.output_chars)}

    # --- gated tools: preview ----------------------------------------------------------

    async def prepare(self, repository: Repository, action) -> GitPreview:
        self._check_repository(repository)
        head = await self._require_unchanged(repository, action.expected_head)
        branch = await self._branch(repository)
        if isinstance(action, GitCommit):
            return await self._prepare_commit(repository, action, head, branch)
        if isinstance(action, GitCheckout):
            return await self._prepare_checkout(repository, action, head, branch)
        if isinstance(action, GitPush):
            return await self._prepare_push(repository, action, head, branch)
        raise PolicyError("Unsupported git gated action")

    async def _prepare_commit(
        self, repository: Repository, action: GitCommit, head: str, branch: str
    ) -> GitPreview:
        paths = [self._inside(repository, path) for path in action.paths]
        _, out, _ = await self._git(
            repository,
            ["diff", "HEAD", "--no-ext-diff", "--no-textconv", "--no-color", "--", *paths],
        )
        digest = await self._paths_digest(repository, paths)
        text = out if out.strip() else "(no working or staged changes in the listed paths)"
        state = {
            "head": head,
            "branch": branch,
            "paths": "\n".join(paths),
            "paths_digest": digest,
        }
        return GitPreview(text, state)

    async def _prepare_checkout(
        self, repository: Repository, action: GitCheckout, head: str, branch: str
    ) -> GitPreview:
        sha = await self._revision(repository, f"refs/heads/{action.branch}")
        if action.create:
            if sha:
                raise PolicyError("Branch already exists")
            return GitPreview(
                f"Create branch {action.branch} at {head}",
                {
                    "head": head,
                    "branch": branch,
                    "target": action.branch,
                    "target_sha": "",
                    "create": "yes",
                },
            )
        if not sha:
            raise PolicyError("Branch does not exist")
        _, changed, _ = await self._git(
            repository, ["diff", "--name-status", head, sha, "--"], check=False
        )
        diff = f"Switch to branch {action.branch} at {sha}"
        if changed.strip():
            diff = f"{diff}\nFiles that would change:\n{changed}"
        return GitPreview(
            diff,
            {
                "head": head,
                "branch": branch,
                "target": action.branch,
                "target_sha": sha,
                "create": "no",
            },
        )

    async def _prepare_push(
        self, repository: Repository, action: GitPush, head: str, branch: str
    ) -> GitPreview:
        url = self.settings.git.push_remotes.get(action.remote)
        if not url:
            raise PolicyError("Remote is not configured for push")
        _, actual, _ = await self._git(
            repository, ["remote", "get-url", action.remote], check=False
        )
        if actual.strip() != url:
            raise PolicyError("Remote URL does not match the configured push remote")
        remote_sha = await self._revision(
            repository, f"refs/remotes/{action.remote}/{action.branch}"
        )
        code, commits, _ = await self._git(
            repository,
            ["log", "--oneline", "-n20", head, "--not", f"--remotes={action.remote}"],
            check=False,
        )
        if code != 0:
            _, commits, _ = await self._git(
                repository, ["log", "--oneline", "-n20", head], check=False
            )
        text = f"Push {head} to {action.remote} ({url}) as refs/heads/{action.branch}"
        if commits.strip():
            text = f"{text}\nCommits:\n{commits}"
        return GitPreview(
            text,
            {
                "head": head,
                "branch": branch,
                "remote": action.remote,
                "remote_url": url,
                "remote_sha": remote_sha,
            },
        )

    async def _paths_digest(self, repository: Repository, paths: list[str]) -> str:
        parts = []
        for path in paths:
            target = repository.path(path)
            if target.is_file():
                _, blob, _ = await self._git(repository, ["hash-object", "--", path], check=False)
                parts.append(f"{path}:{blob.strip()}")
            else:
                parts.append(f"{path}:absent")
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    # --- gated tools: apply ------------------------------------------------------------

    async def apply(self, repository: Repository, action, state: dict[str, str]) -> dict:
        fresh = await self.prepare(repository, action)
        if fresh.state != state:
            raise PolicyError("Repository state changed after approval; request a new action")
        if isinstance(action, GitCommit):
            return await self._apply_commit(repository, action)
        if isinstance(action, GitCheckout):
            return await self._apply_checkout(repository, action, state)
        if isinstance(action, GitPush):
            return await self._apply_push(repository, action)
        raise PolicyError("Unsupported git gated action")

    async def _apply_commit(self, repository: Repository, action: GitCommit) -> dict:
        _, out, err = await self._git(
            repository,
            ["commit", "--no-verify", "--only", "-m", action.message, "--", *action.paths],
        )
        head = await self._head(repository)
        return {
            "text": bounded(f"{out}{err}".strip(), repository.output_chars)["text"],
            "head": head,
            "applied": True,
        }

    async def _apply_checkout(
        self, repository: Repository, action: GitCheckout, state: dict[str, str]
    ) -> dict:
        if action.create:
            args = ["checkout", "-b", action.branch]
        else:
            _, dirty, _ = await self._git(
                repository, ["status", "--porcelain=v1", "--untracked-files=no"]
            )
            changed: set[str] = set()
            if state.get("target_sha"):
                _, names, _ = await self._git(
                    repository, ["diff", "--name-only", state["head"], state["target_sha"], "--"]
                )
                changed = {line.strip() for line in names.splitlines() if line.strip()}
            overwritten = sorted(
                {
                    line[3:].split(" -> ")[-1].strip()
                    for line in dirty.splitlines()
                    if len(line) > 3 and line[3:].split(" -> ")[-1].strip() in changed
                }
            )
            if overwritten:
                raise PolicyError(
                    "Uncommitted changes would be overwritten: " + ", ".join(overwritten[:5])
                )
            args = ["checkout", action.branch]
        _, out, err = await self._git(repository, args)
        head = await self._head(repository)
        return {
            "text": bounded(f"{out}{err}".strip(), repository.output_chars)["text"],
            "head": head,
            "branch": action.branch,
            "applied": True,
        }

    @staticmethod
    def push_args(action: GitPush) -> list[str]:
        """One commit to one branch; never a force, never another ref."""
        return [
            "push",
            "--no-verify",
            "--no-follow-tags",
            action.remote,
            f"{action.expected_head}:refs/heads/{action.branch}",
        ]

    async def _apply_push(self, repository: Repository, action: GitPush) -> dict:
        _, out, err = await self._git(repository, self.push_args(action))
        return {
            "text": bounded(f"{out}{err}".strip(), repository.output_chars)["text"],
            "remote": action.remote,
            "branch": action.branch,
            "applied": False,
        }
