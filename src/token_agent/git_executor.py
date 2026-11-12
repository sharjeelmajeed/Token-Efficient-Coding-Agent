"""Concrete ``GitExecutor``: runs git only for actions the core already allowed.

The core owns schemas, classification, push policy and approval binding. This module only
turns an allowed action into one argv-only ``git`` call, then reports what happened. It never
decides whether an action is permitted.

Safety rules from ``docs/GIT_TOOLS.md`` that the core cannot check and are enforced here:

* argv only, never a shell, with ``--`` before paths;
* the user's global and system git configuration is not loaded into the command, and hooks,
  pagers, external diffs, text conversion, GPG signing, tag pushing and prompts are turned
  off. Repository-local filter drivers and ``core.sshCommand`` are *not* disabled, so a
  command can still run code the repository configures; treating a cloned repository as
  trusted is the remaining assumption;
* only ``repository.root`` is used, and a ``.git`` file (worktree pointer) is refused;
* protected paths are matched with ``Repository.path`` (whose rules include the confidential
  marker, case folding, trailing dots and spaces, and links), and their content is removed
  from diff output and refused in commit and checkout paths;
* push resolves the remote's *push* URL, requires exactly one URL equal to the configured
  one, then pushes exactly one commit to ``refs/heads/<branch>`` with no force;
* every command is time-limited, the whole process tree is killed on timeout, and every
  result is bounded and secret-checked by the core.
"""

import asyncio
import contextlib
import hashlib
import os
import signal
import subprocess
from collections.abc import Awaitable, Callable

from .config import Settings
from .git_contract import GitPreview
from .models import GitCheckout, GitCommit, GitDiff, GitLog, GitPush, GitStatus
from .repo import PolicyError, Repository, bounded

RunResult = tuple[int, str, str]
Runner = Callable[[list[str], str], Awaitable[RunResult]]

# Turned off for every command: hooks, pager, filesystem monitor, signing, tag pushing and
# the "ext" transport. This does not cover repository-local filter drivers or core.sshCommand.
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
# The only keys read from the user's own git configuration.
IDENTITY_KEYS = ("user.name", "user.email")


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
        self._user_values: dict[str, str] = {}

    # --- process handling -------------------------------------------------------------

    def _environment(self, extra: dict[str, str] | None = None) -> dict[str, str]:
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
        if extra:
            env.update(extra)
        return env

    @staticmethod
    def _process_options() -> dict[str, object]:
        """Put the child in its own session or process group so its children can be killed."""
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    async def _kill_tree(self, process) -> None:
        """Kill the command and every process it spawned, not just the one git process."""
        try:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/T",
                    "/F",
                    "/PID",
                    str(process.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        with contextlib.suppress(ProcessLookupError):
            await process.wait()

    async def _spawn(
        self, argv: list[str], cwd: str, env: dict[str, str] | None = None
    ) -> RunResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env or self._environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **self._process_options(),
        )
        try:
            async with asyncio.timeout(self.timeout):
                out, err = await process.communicate()
        except TimeoutError:
            await self._kill_tree(process)
            raise PolicyError("Git command timed out") from None
        return (
            process.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )

    async def _git(
        self,
        repository: Repository,
        args: list[str],
        *,
        check: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> RunResult:
        argv = ["git", "--no-pager", *HARDENING, *args]
        if self._runner is not None:
            code, out, err = await self._runner(argv, str(repository.root))
        else:
            code, out, err = await self._spawn(
                argv, str(repository.root), self._environment(extra_env)
            )
        if check and code != 0:
            message = err.strip() or out.strip() or "git command failed"
            raise PolicyError(bounded(message, repository.output_chars)["text"])
        return code, out, err

    async def _user_value(self, key: str) -> str:
        """Read one key from the user's own configuration, never from the repository.

        ``git config --global`` and ``--system`` ignore repository-local configuration, and
        this call runs with the ordinary environment so those files can be read at all.
        """
        if key in self._user_values:
            return self._user_values[key]
        if key not in IDENTITY_KEYS:
            raise PolicyError("Only user.name and user.email may be read from git config")
        value = ""
        env = {
            name: os.environ[name]
            for name in ("PATH", "SystemRoot", "SYSTEMROOT", "SystemDrive", "HOME", "USERPROFILE")
            if name in os.environ
        }
        env["GIT_TERMINAL_PROMPT"] = "0"
        for scope in ("--global", "--system"):
            process = await asyncio.create_subprocess_exec(
                "git",
                "config",
                scope,
                "--get",
                key,
                cwd=os.path.expanduser("~"),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                async with asyncio.timeout(self.timeout):
                    out, _ = await process.communicate()
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(ProcessLookupError):
                    await process.wait()
                continue
            text = out.decode("utf-8", "replace").strip()
            if process.returncode == 0 and text:
                value = text
                break
        self._user_values[key] = value
        return value

    async def _identity(self) -> tuple[str, str]:
        name = await self._user_value("user.name")
        email = await self._user_value("user.email")
        if not name or not email:
            raise PolicyError(
                "No git user.name and user.email in the user's git configuration; "
                "a commit needs an author identity"
            )
        return name, email

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

    @staticmethod
    def _is_protected(repository: Repository, relative: str) -> bool:
        """Reuse ``Repository.path``: a path it refuses counts as protected.

        That one function already covers case folding, trailing dots and spaces, the
        confidential marker, links and paths that leave the repository.
        """
        try:
            repository.path(relative)
        except PolicyError:
            return True
        return False

    async def _status_entries(
        self, repository: Repository, untracked: str = "all"
    ) -> list[tuple[str, str, str]]:
        """Parse ``--porcelain=v1 -z``: NUL separated and never quoted or escaped.

        ``-z`` is required: without it git quotes unusual names, and a quoted protected name
        would not match the protected-path check.
        """
        _, out, _ = await self._git(
            repository,
            ["status", "--porcelain=v1", "-z", f"--untracked-files={untracked}"],
        )
        fields = out.split("\x00")
        entries: list[tuple[str, str, str]] = []
        index = 0
        while index < len(fields):
            field = fields[index]
            index += 1
            if len(field) < 4 or field.startswith("##"):
                continue
            code, path = field[:2], field[3:]
            original = ""
            if code[0] in "RC" or code[1] in "RC":
                if index < len(fields) and fields[index]:
                    original = fields[index]
                    index += 1
            entries.append((code, path, original))
        return entries

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
        kept, omitted = [], 0
        for code, path, original in await self._status_entries(repository):
            names = [path] + ([original] if original else [])
            if any(self._is_protected(repository, name) for name in names):
                omitted += 1
                continue
            kept.append(f"{code} {path}")
        if omitted:
            kept.append(f"[{omitted} protected paths omitted]")
        result: dict = {"head": head, "branch": branch, "omitted": omitted}
        result.update(bounded("\n".join(kept), repository.output_chars))
        return result

    async def _diff_entries(
        self, repository: Repository, base: list[str], paths: list[str]
    ) -> list[tuple[str, str, str]]:
        """Parse ``git diff --name-status -z`` as (status, new path, old path).

        ``-z`` keeps non-ASCII names unquoted, and the old path of a rename matters: a
        rename away from a protected name must not show that name or its content.
        """
        args = ["diff", "--name-status", "-z", "--no-ext-diff", "--no-textconv", *base]
        if paths:
            args.extend(["--", *paths])
        _, out, _ = await self._git(repository, args)
        fields = out.split("\x00")
        entries: list[tuple[str, str, str]] = []
        index = 0
        while index < len(fields):
            status = fields[index]
            index += 1
            if not status:
                continue
            if status[0] in "RC":
                old = fields[index] if index < len(fields) else ""
                index += 1
                new = fields[index] if index < len(fields) else ""
                index += 1
                if new:
                    entries.append((status, new, old))
            else:
                name = fields[index] if index < len(fields) else ""
                index += 1
                if name:
                    entries.append((status, name, name))
        return entries

    def _entry_is_protected(self, repository: Repository, entry: tuple[str, str, str]) -> bool:
        _, new, old = entry
        return self._is_protected(repository, old) or self._is_protected(repository, new)

    async def _render_diff(self, repository: Repository, base: list[str], paths: list[str]) -> str:
        """Diff that never contains a protected file's contents.

        Protected names are resolved through ``Repository.path`` and excluded with
        ``:(exclude,literal)`` pathspecs, so a full diff with no paths is covered as well.
        A rename whose old name was protected is excluded under its new name too.
        """
        entries = await self._diff_entries(repository, base, paths)
        protected: list[str] = []
        for entry in entries:
            if not self._entry_is_protected(repository, entry):
                continue
            # Exclude both sides of a rename: excluding only the new name makes git fall
            # back to showing the old path as a deletion, with its content.
            for name in (entry[1], entry[2]):
                if name and name not in protected:
                    protected.append(name)
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", *base]
        if paths or protected:
            args.append("--")
            args.extend(paths)
            args.extend(f":(exclude,literal){name}" for name in protected)
        _, out, _ = await self._git(repository, args)
        return out

    async def _diff(self, repository: Repository, action: GitDiff) -> dict:
        paths = [self._inside(repository, path) for path in action.paths]
        base = ["--cached"] if action.staged else []
        text = await self._render_diff(repository, base, paths)
        return {"staged": action.staged, **bounded(text, repository.output_chars)}

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

    async def _files_under(self, repository: Repository, relative: str) -> list[str]:
        """Tracked and untracked-but-not-ignored files below a directory."""
        tracked = await self._git(repository, ["ls-files", "-z", "--", relative])
        others = await self._git(
            repository,
            ["ls-files", "-z", "--others", "--exclude-standard", "--", relative],
        )
        names = [name for name in tracked[1].split("\x00") if name]
        names.extend(name for name in others[1].split("\x00") if name)
        return names

    async def _expand_commit_paths(self, repository: Repository, paths: list[str]) -> list[str]:
        """Files to commit. A directory is expanded, and refused if anything inside is
        protected, so a protected file can never be committed through its parent."""
        expanded: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            target = repository.path(raw)
            if target.is_dir():
                relative = raw.rstrip("/")
                files = await self._files_under(repository, relative)
                if not files:
                    raise PolicyError(f"No files to commit in {relative}")
                blocked = [name for name in files if self._is_protected(repository, name)]
                if blocked:
                    raise PolicyError(
                        "Directory contains protected paths: " + ", ".join(blocked[:5])
                    )
                candidates = files
            else:
                candidates = [raw]
            for name in candidates:
                if name not in seen:
                    seen.add(name)
                    expanded.append(name)
        return expanded

    async def _preview_new_file(self, repository: Repository, relative: str) -> str:
        """Show exactly what would be committed, or refuse. Never commit unseen content."""
        content = repository.read(relative)
        return f"new file: {relative}\n{content}\n"

    async def _prepare_commit(
        self, repository: Repository, action: GitCommit, head: str, branch: str
    ) -> GitPreview:
        paths = await self._expand_commit_paths(repository, action.paths)
        text = await self._render_diff(repository, ["HEAD"], paths)
        new_files = await self._untracked(repository, paths)
        for name in new_files:
            text += await self._preview_new_file(repository, name)
        if not text.strip():
            raise PolicyError("No changes to commit in the listed paths")
        digest = await self._paths_digest(repository, paths)
        return GitPreview(
            text,
            {
                "head": head,
                "branch": branch,
                "paths": "\n".join(paths),
                "new": "\n".join(new_files),
                "paths_digest": digest,
            },
        )

    async def _untracked(self, repository: Repository, paths: list[str]) -> list[str]:
        _, out, _ = await self._git(
            repository, ["ls-files", "-z", "--others", "--exclude-standard", "--", *paths]
        )
        return [name for name in out.split("\x00") if name]

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
        entries = await self._diff_entries(repository, [head, sha], [])
        changed = [
            f"{status} {new}"
            for status, new, old in entries
            if not self._entry_is_protected(repository, (status, new, old))
        ]
        diff = f"Switch to branch {action.branch} at {sha}"
        if changed:
            diff = f"{diff}\nFiles that would change:\n" + "\n".join(changed)
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
        _, out, _ = await self._git(
            repository, ["remote", "get-url", "--push", "--all", action.remote], check=False
        )
        urls = [line.strip() for line in out.splitlines() if line.strip()]
        if len(urls) != 1 or urls[0] != url:
            raise PolicyError("Remote push URL does not match the configured push remote")
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
            return await self._apply_commit(repository, action, state)
        if isinstance(action, GitCheckout):
            return await self._apply_checkout(repository, action, state)
        if isinstance(action, GitPush):
            return await self._apply_push(repository, action)
        raise PolicyError("Unsupported git gated action")

    async def _apply_commit(
        self, repository: Repository, action: GitCommit, state: dict[str, str]
    ) -> dict:
        paths = [name for name in state["paths"].split("\n") if name]
        new_files = [name for name in state.get("new", "").split("\n") if name]
        name, email = await self._identity()
        if new_files:
            # Records the path in the index without content, so ``commit --only`` can name a
            # file that git does not know yet. Nothing is staged for any other path.
            await self._git(repository, ["add", "--intent-to-add", "--", *new_files])
        _, out, err = await self._git(
            repository,
            ["commit", "--no-verify", "--only", "-m", action.message, "--", *paths],
            extra_env={
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
            },
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
            entries = await self._status_entries(repository, untracked="no")
            dirty = {path for _, path, _ in entries}
            changed: set[str] = set()
            if state.get("target_sha"):
                _, names, _ = await self._git(
                    repository,
                    ["diff", "--name-only", "-z", state["head"], state["target_sha"], "--"],
                )
                changed = {name for name in names.split("\x00") if name}
            overwritten = sorted(dirty & changed)
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
