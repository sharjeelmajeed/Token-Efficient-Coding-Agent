# WS3 demo script: CLI and VS Code

A presenter script for the two clients on the **offline fixture core**. The fixture is a
scripted calculator task with synthetic token counts. Say so on screen: it shows how the
clients behave, not model quality or token savings, and it is never benchmark evidence.
Live model behaviour is in WS2's evidence (`docs/evidence/live/`).

Rehearsed on Windows 10 with VS Code 1.145.2 (see
[evidence/ws3-editor-acceptance.md](evidence/ws3-editor-acceptance.md)).

## Before the demo

1. **Config with streaming.** Create `.agent/demo.toml` (`.agent/` is git-ignored). Paths in
   a config are relative to the config file, so the database and repository root differ
   from `agent.toml`:

   ```toml
   mode = "fixture"
   streaming = true
   database = "demo-state.sqlite3"
   gateway_url = "http://127.0.0.1:8080/v1/chat/completions"
   max_steps = 12
   max_output_tokens = 2048
   approval_timeout_seconds = 120
   context_chars = 40000
   output_chars = 8000

   [[users]]
   id = "developer"
   token_env = "AGENT_API_TOKEN"
   repo_roots = ["../sandbox-repos"]
   budget_micro_usd = 1000000
   bifrost_key_env = "BIFROST_VIRTUAL_KEY"
   local_key_env = "BIFROST_LOCAL_VIRTUAL_KEY"
   ```

   Without `streaming = true` everything works the same, except replies arrive whole.
2. **Fresh demo repository.** Delete `sandbox-repos/demo`, then run
   `.\.venv\Scripts\python.exe scripts\make_demo.py`. Check that `calculator.py` says
   `return a - b`. Repeat this between rehearsals.
3. **Start the core** in its own PowerShell window and leave it open:

   ```powershell
   $env:AGENT_API_TOKEN = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(40))"
   Set-Clipboard $env:AGENT_API_TOKEN
   .\.venv\Scripts\agent.exe serve --config .agent\demo.toml
   ```

   The token goes to the clipboard so it is never shown. Never type, print or paste it
   anywhere visible, and keep this window off the shared screen.
4. **VS Code.** Install `clients\vscode\token-efficient-coding-agent-0.3.0.vsix` (build it
   from `main` first; see [M3_USAGE.md](M3_USAGE.md)), open `sandbox-repos\demo` and trust it.

## Part 1: CLI (about 3 minutes)

In a second PowerShell window in the project folder, set the same token in that window
without showing it (for example `$env:AGENT_API_TOKEN = Get-Clipboard`), then:

| Step | Do | What the audience sees |
| --- | --- | --- |
| 1 | `.\.venv\Scripts\agent.exe init --repo sandbox-repos\demo` | `Session created: <id>`, `Mode: fixture` |
| 2 | `.\.venv\Scripts\agent.exe run "demo: fix calculator addition"` | The diff `return a - b` → `return a + b` and the prompt `Approve once / deny / deny with feedback [a/d/f]` |
| 3 | Answer `f`, type a reason | `Change was denied. No patch was applied.`, `Run finished: completed_unverified`, the status table and the `OFFLINE FIXTURE` line. The file is unchanged |
| 4 | Run the same task again, answer `a` | The reply prints as one line, `Run finished: completed_unverified`, and `calculator.py` now says `return a + b` |
| 5 | `.\.venv\Scripts\agent.exe status` | Mode, run, model, steps, budget remaining and tokens, with `OFFLINE FIXTURE` |

Talking points: every approval is for one exact action, which the core rechecks; the client
only sends the approval ID. `completed_unverified` is honest: no container checks are
configured in the fixture. Reset the demo repository (step 2 above) before Part 2.

## Part 2: VS Code (about 5 minutes)

| Step | Do | What the audience sees |
| --- | --- | --- |
| 1 | Command palette: **Token Agent: Configure Connection** → `http://127.0.0.1:8765`, paste with Ctrl+V into the password box | "Connection saved" in chat. The token is masked |
| 2 | **Token Agent: Start Session** | Chat opens; the status bar shows the model, the budget in dollars and `standard` |
| 3 | Type `demo: fix calculator addition`, **Send task** | Tool lines, then an approval card for `apply_patch` |
| 4 | **Review and decide** | VS Code's native diff: `return a - b` → `return a + b` |
| 5 | **Reject with feedback**, type a reason | A readable message, not JSON; the file is unchanged |
| 6 | Send the task again in the same session; when the card appears, click **Cancel run** | `Run finished: user_cancelled`; the card disappears; the file is unchanged |
| 7 | Send the task again, **Review and decide** → **Accept once** | The file shows `return a + b`; the reply appears as one chat bubble |
| 8 | **Run report** | A read-only JSON report with `benchmark_evidence: false` |
| 9 | Close the chat panel, **Token Agent: Reconnect Session** | The chat comes back; nothing is sent again |

Optional: switch between a light and a dark theme, or use only Tab, Enter and Space to show
keyboard access.

## If something goes wrong

- **No approval card on a retry:** the core is older than PR #12. Restart it from current
  `main`, or click **New session** first.
- **Connection errors:** check that the core window is still running and the URL is
  `http://127.0.0.1:8765`; then click **Reconnect**. Stopping the core gives a readable error,
  not a crash, and **Reconnect** works again once it is restarted from the same window.
- **File already says `return a + b`:** reset the demo repository and start a new session.
- **CLI disconnected:** it says progress is saved; run `.\.venv\Scripts\agent.exe resume`.

## Live git approval check (after PR #41 merges)

Not part of the demo above. Run it once Sharjeel's git executor (#41) is merged, then record
the result as evidence. It checks that both clients show a real executor's previews and that
approve and deny do exactly what the core says.

**Before #41's review fixes land**, keep to the path that works today: give the test
repository its own `user.name`/`user.email` (the executor ignores global git config), and
change a file that git already tracks (new files cannot be committed yet).

1. **Test repository.** Use a fresh repository under a configured `repo_roots` folder, never
   this project. One commit on a branch that is not `main`, then change `calculator.py`.
   Set `origin` to the URL in the config below. It does not need to exist, because push is
   denied in this check.
2. **Config.** The usual config plus:

   ```toml
   [git]
   enabled = true
   protected_branches = ["main", "master"]

   [git.push_remotes]
   origin = "https://github.com/example/repo.git"
   ```

3. **CLI.** Ask the agent to commit the change to `calculator.py`, create the branch
   `feature/live-git-check`, then push it to `origin`. Check each approval and answer:

   | Approval | Check | Answer |
   | --- | --- | --- |
   | `git_commit` | `paths: calculator.py`, the head matches `git rev-parse HEAD`, the diff is `return a - b` → `return a + b` | `a` |
   | `git_checkout` | `create: True`, `target: feature/live-git-check`, the head is the new commit | `a` |
   | `git_push` | `remote_url` is the configured URL; the commit list is right | `d` |

   Afterwards `git log --oneline --all` shows the commit on both branches, and the current
   branch is `feature/live-git-check`. Nothing was pushed.
4. **VS Code.** Repeat with a fresh repository: **Review and decide** opens a read-only
   `git-commit.diff` (diff colours), then `git-checkout.txt` and `git-push.txt`, each followed
   by Accept once / Reject. Give the same answers and check the same repository state.
5. **Record** in `docs/evidence/live/`: the date, the commit of `main`, the client versions,
   the provider mode, every approval with its answer, and the repository state afterwards.
   Leave out tokens and keys. A scripted provider or the fixture shows client behaviour only.
   Say so, and never use it as benchmark evidence.

A rehearsal of steps 1–3 against #41's branch with a scripted provider is in
[WS3_AREEBA_PLAN.md](WS3_AREEBA_PLAN.md) (progress log, 2026-11-12).

## What not to claim

- No token-saving percentage, cost or quality result comes from this demo.
- Fixture token counts are synthetic.
- Git approvals (CLI and VS Code) and `agent bench` are built but not part of this demo: git
  tools stay off until the git executor exists, and bench output from this setup is not
  benchmark evidence. A live PR review has not been run yet.
