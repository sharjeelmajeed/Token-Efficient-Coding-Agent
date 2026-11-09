# Git tools contract

Draft for review by the git executor owner. Status: core side built and tested with a
test double. **No git executor exists yet, so git tools are off and never offered to the model.**

FSD 5.2 defines six tools. `git_status`, `git_diff` and `git_log` are read tools and run
without approval. `git_commit`, `git_checkout` and `git_push` are gated: every call needs
human approval of that exact action.

## Who owns what

| Part | Owner | File |
|---|---|---|
| Tool inputs (schemas) | Core | `models.py` |
| Read or gated classification | Core | `policy.py` |
| Push remote, protected-branch and confidential rules | Core | `policy.py`, `config.py` |
| Approval, expiry, single use, ownership | Core | `engine.py`, `store.py` |
| Running git commands safely | Git executor | A class implementing `GitExecutor` |

The executor never decides whether an action is allowed. When it is called, the core has
already validated the input, checked policy and, for gated tools, recorded approval.

## Tool inputs

All fields are required unless a default is shown. Unknown fields are rejected, so there is
no `force`, `amend`, `no_verify` or similar.

| Tool | Fields |
|---|---|
| `git_status` | none |
| `git_diff` | `staged` (default `false`), `paths` (0-20 repository-relative paths) |
| `git_log` | `max_count` (1-50, default 10), `paths` (0-20) |
| `git_commit` | `message` (1-2000 chars), `paths` (1-50), `expected_head` |
| `git_checkout` | `branch`, `create` (default `false`), `expected_head` |
| `git_push` | `remote` (a configured name, never a URL), `branch`, `expected_head` |

- **`expected_head`** is a full 40 or 64 character commit ID. The model gets it from `git_status`.
- **`branch`** allows letters, digits and `._/-` only. It may not start with `-`, `/` or `.`,
  end with `/` or `.`, or contain `..`, `//`, `@{` or a `.lock` part.
- **`paths`** are refused by the core if they start with `-` or fail the repository path rules,
  which cover traversal, links and protected names such as `.env`, `.git` and the
  confidential marker.

## Core policy, enforced before the executor runs

- Push is offered only when `[git].push_remotes` is set.
- `remote` must be a key in `push_remotes`.
- `branch` may not be in `protected_branches` (default `main`, `master`).
- A confidential repository may push only to a remote in `confidential_push_remotes` (default
  none).
- Read-only sessions get read tools only.
- Commit messages, previews and all results are secret-checked.

```toml
[git]
enabled = true
protected_branches = ["main", "master"]
confidential_push_remotes = []

[git.push_remotes]
origin = "https://github.com/team/repo.git"
```

Remote URLs must be `https://` without credentials, or `ssh://`.

## The interface to implement

Defined in `src/token_agent/git_contract.py`. Pass an instance to `create_app(..., git=...)`.

```python
class GitExecutor(Protocol):
    async def read(self, repository, action) -> dict: ...
    async def prepare(self, repository, action) -> GitPreview: ...
    async def apply(self, repository, action, state: dict[str, str]) -> dict: ...
```

**`read(repository, action)`** for the three read tools.

- Change nothing: no fetch, no index refresh writes, no hooks.
- Return a dict with bounded text, using `repo.bounded(text, repository.output_chars)`.
- `git_status` must include the full HEAD commit ID and current branch, so the model can
  supply `expected_head`.
- Leave out protected paths and their contents.

**`prepare(repository, action)`** for the three gated tools.

- Change nothing.
- Return `GitPreview(diff, state)`.
- `diff` is exactly what the person approves: the staged diff of `paths` for a commit, the
  target branch and any files that would change for a checkout, or the commits and remote
  URL for a push.
- `state` holds every fact the approval depends on, such as `head`, `branch`, the tree hash
  of `paths`, and the remote URL.
- Refuse with `PolicyError` if `expected_head` does not match HEAD.

**`apply(repository, action, state)`**, called once after approval.

- First recheck every value in `state`. If anything differs, raise `PolicyError` and change
  nothing.
- Run the command once. Return a dict and include `"applied": true` if files in the working
  tree may have changed, which resets verification.
- Never retry a push.

## Executor safety rules

The core cannot check these, so the executor's own tests must cover them.

- Run `git` with an argv list, never a shell. Put `--` before paths.
- Set `GIT_TERMINAL_PROMPT=0`, ignore the user's global and system config, and disable
  hooks, aliases, external diff tools, pagers and editors.
- Operate only on `repository.root`. Refuse a `.git` file that points elsewhere.
- For push, resolve the remote's URL from git config and refuse unless it equals the
  configured URL. Push exactly `expected_head` to `refs/heads/<branch>`, with no force and
  no other refs.
- Commit only the listed `paths`, as their content was when previewed. Never stage anything
  else.
- Checkout must refuse when uncommitted changes would be overwritten.
- Credentials come from the environment or a credential helper. Never put them in argv,
  output or results.
- Time-limit every command and bound its output.

## Not decided yet

- Whether a confidential repository may ever push. The default is no.
- Commits with no parent, which `expected_head` cannot represent.
- Showing git previews in VS Code. The editor's native diff preview works only for file changes.
