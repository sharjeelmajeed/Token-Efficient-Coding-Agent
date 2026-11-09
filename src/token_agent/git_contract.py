"""Interface between the core and the git executor that runs real git commands.

The core owns the schemas, classification, approval and policy checks. The executor only
runs a command that the core has already allowed. With no executor installed, git tools
are never offered to the model, and any git action pauses the run.
"""

from dataclasses import dataclass
from typing import Protocol

from .models import GitCheckout, GitCommit, GitDiff, GitLog, GitPush, GitStatus
from .repo import Repository

GitRead = GitStatus | GitDiff | GitLog
GitGated = GitCommit | GitCheckout | GitPush
GIT_READ_TYPES = (GitStatus, GitDiff, GitLog)
GIT_GATED_TYPES = (GitCommit, GitCheckout, GitPush)


@dataclass(frozen=True)
class GitPreview:
    """What a person approves.

    diff: exact human-readable change, such as the staged diff, branch switch or commits
    to push. state: every fact the approval depends on, such as HEAD, branch, index tree
    or remote URL. apply() must refuse if any value has changed since prepare().
    """

    diff: str
    state: dict[str, str]


class GitExecutor(Protocol):
    async def read(self, repository: Repository, action: GitRead) -> dict:
        """Run a read-only git command and return bounded, secret-free output."""

    async def prepare(self, repository: Repository, action: GitGated) -> GitPreview:
        """Describe a gated action exactly, changing nothing."""

    async def apply(self, repository: Repository, action: GitGated, state: dict[str, str]) -> dict:
        """Run an approved action once, only if the state still matches."""
