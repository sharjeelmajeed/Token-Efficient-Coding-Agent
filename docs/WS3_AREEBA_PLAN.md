# WS3 Areeba Plan

Owner: Areeba (WS3 — clients). Scope: `src/token_agent/cli.py`, `clients/vscode/`,
`src/token_agent/github_client.py`, `src/token_agent/pr_review.py` and their tests.
Created 2026-11-10 on branch `feature/clients-ws3-plan` from `main` @ `3285b56`.
Sources: handoff PDF (03 Nov), `docs/STATUS.md`, `docs/API.md`, `docs/GIT_TOOLS.md`,
`docs/TEAM_PLAN.md`, and the code on `main`.

## Rules: SPEC before code, minimal diffs, WS3 scope only

1. Every item starts as a short SPEC (problem, behavior, files, tests, out of scope).
   Areeba approves the SPEC before any code is written.
2. Smallest change that meets the SPEC. No refactors along the way.
3. WS3 files only. Anything in `api.py`, `engine.py`, `models.py`, `policy.py`,
   `store.py`, `streaming.py`, `providers.py` is Usman's; `repo.py`, `sandbox.py`,
   `benchmarks/`, workflows are Sharjeel's. Need a change there → write a request, don't edit.
4. Clients never decide policy. Hiding a button is not security; the core decides.
5. Show budget/warning from core `status` fields, never from state the client invented.
6. No secrets in source, logs, screenshots or the plan. Fixture numbers are never
   presented as benchmark evidence.
7. One branch per item (`feature/clients-*`), one PR, Usman reviews (API compatibility).
   No force-push, no reset --hard. Push/PR only when Areeba says so.
8. After each item: run the checks, mark it done here with evidence, propose the next item.

## Current baseline (from updated repo)

- `main` @ `3285b56` (Merge PR #5). Local workspace now tracks
  `https://github.com/sharjeelmajeed/Token-Efficient-Coding-Agent` (it was a plain ZIP
  extract; it matched the import commit `9bb5121`, so no local work was lost. A backup is
  at `..\token-efficient-coding-agent-backup-2026-11-10.zip`).
- Toolchain on this machine: Python 3.12.10, Node 24.13.0, npm 11.6.2, Docker 29.2.1,
  VS Code 1.145.2. **No `.venv` and no `node_modules` yet** — the baseline test run has not
  been done on this machine.
- Open, unmerged Usman branches: `feature/core-recoverable-tool-errors` (adds
  `tool_result` payload `{"error", "recoverable": true, "changed": false}`) and
  `feature/gateway-timeout-setting` (no client impact).

## Already completed (with evidence)

| Item | Evidence |
| --- | --- |
| CLI: visible 80% budget warning (red banner) + status reprinted on `budget_warning`, `model_switched`, `budget_hit`, `session_paused`, `run_complete` | Commit `f8b03be` (Aliza Ghaffar), merged in PR #1 `19feac7`; `tests/test_cli.py::test_cli_shows_and_persists_budget_warning` |
| CLI baseline from M3: init/run/status/resume/cancel/serve/doctor, diff + command approvals (a/d/f), Ctrl+C cancel, SSE cursor resume | `cli.py`, `test_cli_complete_interactive_flow` |
| VS Code M3 package: chat webview, native diff for file approvals, Accept once / Reject / Reject with feedback, status bar, model-switch toast, cancel, reconnect, run report, SecretStorage token | `clients/vscode/src/*`, 12 Node tests (handoff PDF). Editor-host acceptance **not** done |
| PR review client: bounded read-only analysis, separate publisher | `pr_review.py`, `github_client.py`, `tests/test_pr_review.py`. Never run against a real PR |

## What Usman merged that affects WS3

| Commit / PR | Change | Client impact |
| --- | --- | --- |
| `fabc024` / PR #4 | `status` now returns `budget_limit_micro_usd` and `budget_warning` (computed by core on every request). `budget_warning` / `budget_hit` events carry `limit_micro_usd`, `warning_percent`, `options` (`continue`/`cancel`; `raise_budget_then_resubmit`) | **CLI must stop storing `budget_warning` in `.agent/client.json`** and read it from status (API.md says so explicitly). VS Code `Status` type/status bar ignore these fields today |
| `2548900` / PR #3 | Off-by-default `streaming` setting. `message_delta` may come as `partial: true` pieces then one `partial: false`. `session_paused` before the last piece = incomplete reply | CLI prints each piece on its own line; VS Code adds each piece as a separate chat bubble. Both need to join pieces and mark incomplete text |
| `19e33b9` / PR #5 | Git tool schemas + approval rules (`git_commit`, `git_checkout`, `git_push` gated; reads ungated). Disabled until Sharjeel's executor exists | CLI labels every non-shell approval "Exact file change". VS Code shows git approvals as raw JSON; GIT_TOOLS.md lists "git previews in VS Code" as undecided |
| `f6e8caf` / PR #2 | Confidential overlap + protected-path fixes | None for clients |

## Dependencies on Usman / Sharjeel

| Need | From | Blocks | Fallback |
| --- | --- | --- | --- |
| Confirm event/payload contract is frozen (budget fields, `partial`, recoverable `tool_result`) | Usman | Items 1–5 (low risk: already in API.md) | Code to API.md; ask before relying on anything not documented |
| A way to run the core with `streaming = true` and a fixture that produces partial deltas | Usman | Item 2/5 end-to-end check | Unit-test against recorded event sequences |
| Git executor so git approvals actually appear | Sharjeel (+Usman wiring) | Item 6 live check | Build display from contract, test with synthetic approval payloads |
| Benchmark harness API (inputs/outputs) for `agent bench` | Sharjeel | Item 8 | Do not add a placeholder command (AGENTS.md forbids fake endpoints) |
| Team test repo + PR workflow config/secrets enabled | Sharjeel | Item 9 | Keep local PR-review tests green |
| Live Bifrost with a real fallback for a real model switch | Usman | Live switch rehearsal | Controlled-transport tests only; say so in evidence |

## Remaining WS3 work (prioritized)

| # | Item | Size | Depends on |
| --- | --- | --- | --- |
| 0 | Set up `.venv` + `node_modules` and record a green baseline on this machine | S | — |
| 1 | CLI: read budget warning from core `status` (drop client-stored flag); show limit, % and `options` on warning/hit | S | — |
| 2 | CLI: true incremental streaming — print `partial` pieces inline, newline on final piece, label incomplete text on `session_paused` | S | — |
| 3 | CLI: readable notices instead of raw JSON for `model_switched`, `session_paused` (+`resume`), `budget_hit`, `run_complete`, `policy_block`, `approval_expired`, recoverable `tool_result`; clear disconnect/reconnect message | M | — |
| 4 | VS Code: add `budget_limit_micro_usd`/`budget_warning` to `Status`, warning shown in status bar (warning colour) and chat header; readable budget/pause notices | M | — |
| 5 | VS Code: join streamed `partial` deltas into one assistant bubble; mark incomplete on pause | M | — |
| 6 | Both clients: git approval display (correct label + `diff` text for commit/checkout/push; VS Code opens it as a read-only text document) | M | contract only; live needs Sharjeel |
| 7 | VS Code editor-host acceptance: run `test/run-host.cjs` + manual rehearsal script (approve/reject, cancel, reconnect, theme, keyboard, model-switch notice); save evidence | M | items 4–5 |
| 8 | `agent bench` CLI wiring | M | Sharjeel's harness |
| 9 | PR review client: live run on a real team PR (opened/updated, stale head, fork); one authorized comment | M | Sharjeel workflow/secrets |
| 10 | Docs: client sections in `M3_USAGE.md`/`STATUS.md` (WS3 rows only), demo script for CLI + VS Code | S | all above |

## Day-by-day execution plan

Dates are a proposal; the official deadline is still unknown.

### Day 1

- **Goal:** green baseline, then items 1 and 2 (CLI budget + streaming).
- **Files likely touched:** `src/token_agent/cli.py`, `tests/test_cli.py`.
- **Acceptance criteria:**
  - Baseline: Python tests and `npm test` pass on this machine before any change (counts recorded here).
  - `agent status` / `watch` show the warning only when core `status.budget_warning` is true; nothing about budget is written to `client.json`. Old `client.json` files with the flag are harmless.
  - Warning/hit lines show remaining, limit, % and the core's `options`.
  - With `partial: true` pieces, the CLI prints one continuous line; `session_paused` before the final piece prints "(reply incomplete)".
- **Verification:** `pytest tests/test_cli.py`, full `pytest`, `ruff check`, `ruff format --check`.

### Day 2

- **Goal:** item 3 (CLI notices, reconnect) and start item 4 (VS Code status/budget).
- **Files likely touched:** `cli.py`, `tests/test_cli.py`, `clients/vscode/src/protocol.ts`, `extension.ts`, `media/chat.js`, `clients/vscode/test/*.test.cjs`.
- **Acceptance criteria:**
  - Every event the CLI shows is a readable sentence; unknown events still ignored.
  - Stream drop prints "Disconnected — run `agent resume`" and keeps the cursor; resume replays nothing already shown.
  - VS Code `parseStatus` accepts the new fields (and older cores without them); status bar shows `⚠ 80%` + warning colour when `budget_warning` is true.
- **Verification:** `pytest`, `npm test` (in `clients/vscode`), ruff.

### Day 3

- **Goal:** finish item 4, item 5 (VS Code streaming), item 6 (git approval display).
- **Files likely touched:** `extension.ts`, `protocol.ts`, `media/chat.js`, `cli.py` (`decide`), Node + Python tests.
- **Acceptance criteria:**
  - Partial deltas form one assistant bubble; final piece closes it; pause marks it incomplete.
  - Git approvals: CLI says "Exact git action requires your approval" and prints the core `diff`; VS Code opens the `diff` read-only and offers the same three decisions. Clients never edit the action.
  - Tests use synthetic events/approvals shaped exactly like API.md / GIT_TOOLS.md.
- **Verification:** `npm test`, `npm run package`, `pytest`, ruff.

### Day 4

- **Goal:** item 7 (editor-host acceptance) and a full CLI rehearsal on the fixture core.
- **Files likely touched:** `clients/vscode/test/*` only if bugs are found; evidence under `docs/evidence/` (WS3 files only).
- **Acceptance criteria:**
  - Installed VSIX in VS Code 1.145: start session, task, native diff, Accept once / Reject / Reject with feedback, cancel, reconnect, report, budget warning, light + dark theme, keyboard-only use.
  - CLI: `scripts/demo.py` flow plus cancel/resume.
  - Evidence saved as a checklist with commit ID; screenshots contain no tokens. Marked clearly as fixture, not live.
- **Verification:** `node test/run-host.cjs` (editor host), manual checklist.

### Day 5 (buffer / polish)

- **Goal:** items 8–10 if dependencies have landed; otherwise spill-over, bug fixes, docs.
- **Files likely touched:** `cli.py` (bench), `pr_review.py`/`github_client.py` (only if live run finds bugs), `docs/M3_USAGE.md`, `docs/STATUS.md` (WS3 rows).
- **Acceptance criteria:**
  - `agent bench` only if Sharjeel's harness exists — it calls it, adds no logic of its own.
  - PR review on a real team PR produces a scoped summary; publication only with explicit permission.
  - Docs describe exactly what is verified and what is not. PRs opened for each finished item.
- **Verification:** full check list from the handoff (npm ci/test/package/audit, ruff, coverage, pip_audit).

## Progress log

| Date | Item | Status | Notes / evidence |
| --- | --- | --- | --- |
| 2026-11-10 | Repo sync + plan | done | Workspace tracks team repo; branch `feature/clients-ws3-plan`; this file created |
| 2026-11-10 | 0 Baseline | done (2 env issues) | `npm test` 12/12 pass, `npm ci` 0 vulnerabilities; ruff check + format clean (38 files). `pytest`: 331 pass / 3 fail by default; with `PYTHONUTF8=1`: 333 pass / 1 fail. Fail A: `test_cli.py` (2 tests) decode child output with the Windows cp1252 code page (`text=True` with no `encoding`), `stdout` becomes `None`. This is a test harness problem on Windows, not a CLI bug, and the fix belongs in Item 1. Fail B: `test_policy.py::test_symlink_and_parent_symlink_rejected` needs symlink rights (WinError 1314). Needs Windows Developer Mode or admin; it's not ours to change, so tell Sharjeel (QA) |
| 2026-11-10 | Open PRs noted | info | Usman's #6 (recoverable tool errors) and #7 (gateway timeout) are open with 5/5 checks. Neither touches WS3 files. #6 adds the recoverable `tool_result` payload, which Item 3 will show |
| 2026-11-10 | 1 CLI budget from core status | done, pushed (`e5179c8`), PR to open | Branch `feature/clients-cli-budget-status`. `cli.py`: banner only when `status.budget_warning` is true (shows % used, remaining and limit); `budget_warning`/`budget_hit` events print readable lines with the core's `options`; client no longer writes `budget_warning` to `client.json`. `test_cli.py`: UTF-8 subprocess helper (Windows fix A); warning test also checks nothing is stored and a fresh session still warns; new test for under 80% and an old state file. `pytest tests/test_cli.py` 3/3; full `pytest` 334 pass / 1 fail (symlink, env issue B, unchanged); ruff clean |
| 2026-11-10 | 2 CLI streaming | done, pushed (`cf4549e`), PR after Item 1 merges | Branch `feature/clients-cli-streaming`, stacked on Item 1 (same `watch()` function). `partial: true` pieces print on one line; the final piece ends it; any other event before the final piece prints `(reply incomplete)`. This differs from the SPEC, which said `session_paused`: the core sends `policy_block` first. Ctrl+C and disconnect end the line cleanly; streaming-off output is unchanged. Tests reuse `StreamingProvider` from `test_streaming.py`; both new tests fail on the old `cli.py`. `test_cli.py` 5/5; full `pytest` 336 pass / 1 fail (symlink, env issue B); ruff clean |
| 2026-11-10 | 3 CLI readable notices | done, pushed, PR after Item 2 merges | Branch `feature/clients-cli-notices`, stacked on Item 2. New `notice()` helper gives one readable line for `model_switched`, `session_paused` (+ `Next step`), `run_complete` (+ `Note`), `policy_block`, `approval_expired`, `research_used`, and recoverable `tool_result` (PR #6 shape, tested with a synthetic payload until #6 merges). Missing/odd fields never crash; unknown events are still ignored. Disconnect message says progress is saved and to run `agent resume` (no automatic reconnect). `test_cli.py` 26/26; full `pytest` 357 pass / 1 fail (symlink, env issue B); ruff clean |
| 2026-11-10 | 4 VS Code budget + notices | done, pushed, PR can open now | Branch `feature/clients-vscode-budget-status` from `main` (not stacked). `protocol.ts`: optional `budget_limit_micro_usd`/`budget_warning` (wrong types rejected), `budgetWarningPercent`, `noticeText` with the same wording as the CLI. Status bar `⚠ 83% budget` + error colour, tooltip details; chat header line (hidden unless the core warns); budget toasts + status refresh. New `test/budget.test.cjs` (5 tests). `npm test` 17/17, `npm run package` OK, `npm audit` 0, `pytest tests/test_m3_api.py` 8/8. Not yet seen in a real editor (Item 7) |
| 2026-11-10 | 5 VS Code streaming | done, pushed, PR after Item 4 merges | Branch `feature/clients-vscode-streaming`, stacked on Item 4 (same `receive`). `ReplyJoiner` in `protocol.ts`: partial pieces grow one assistant entry, the final piece closes it, any other event first adds `(reply incomplete)` once; streaming off is unchanged; 20,000-character limit kept. New `test/stream.test.cjs` (7 tests). `npm test` 24/24, package OK, audit 0, `pytest tests/test_m3_api.py` 8/8. Not yet seen in a real editor (Item 7) |
| 2026-11-10 | 6a CLI git approvals | done, pushed, PR after Item 3 merges | Branch `feature/clients-cli-git-approvals`, stacked on Item 3. `git_summary()` shows "Exact git action requires your approval: <tool>", the core's fields, the state the core rechecks before running, and the core's preview `diff`; unknown gated tools get a generic label. Still approves only the core's approval ID. Tests: summary text, `decide()` posts per choice (MockTransport), and an end-to-end push approve/deny through the real core with Usman's `FakeGit`. `test_cli.py` 32/32; full `pytest` 363 pass / 1 fail (symlink, env issue B); ruff clean. Live git waits for Sharjeel's executor. 6b (VS Code) waits for Usman/Sharjeel to confirm the preview approach |
| 2026-11-10 | PRs opened | info | #9 plan, #10 Item 1, #11 Item 5 (Usman requested as reviewer). Item 4 was opened and merged by Areeba as #8 (normal merge, without a second review). Waiting: Item 2 (after #10), Item 3 (after Item 2), 6a (after Item 3). Local `main` pulled to `a663979` |
| 2026-11-10 | 7 VS Code editor acceptance | done, merged (#22, follow-up #26) | `docs/evidence/ws3-editor-acceptance.md`. Installed VSIX (built from `523618d`) in VS Code 1.145.2, fixture core with `streaming = true`: **14/14 pass** (Areeba). Steps 7 and 8 first used "New session" (finding F1: the fixture did not re-offer a rejected/cancelled patch in the same session); after Usman's #12 they were re-run in the same session and passed. #26 applies Usman's wording notes (F1 as past behaviour; VSIX older than `main`). Fixture run only, not live-model evidence |
| 2026-11-10 | Streaming fix: mid-reply notices | done, merged (#11 VS Code, #20 CLI) | Reported by Usman: the core sends `model_switched` and `budget_warning` after the partial pieces and before the final piece, so both clients wrongly showed `(reply incomplete)`. VS Code (`af82bfd`): these two events keep the reply open (`MID_REPLY_EVENTS`, `ReplyJoiner.event()`), 26/26. CLI (`9844c63`): they are held and shown after the reply; new end-to-end test with a gateway model switch fails on the old code. Carried into the stacked notices and git-approvals branches. Usman's live streaming run through both clients: #23 |
| 2026-11-10 | Budget options in plain words | done, merged (#19) | Asked by Usman after #13. Both clients: `raise_budget_then_resubmit` → "raise your budget, then resend", `raise_gateway_budget_then_resubmit` → "raise the gateway budget, then resend"; unknown codes shown as sent (VS Code uses a `Map`, so `toString` is not looked up on the prototype). The core still sends the codes |
| 2026-11-10 | 3 CLI readable notices | done, merged (#24) | Usman's review notes applied (`379b1c6`, `c10aceb`): `Tool error (the agent can correct this): …` instead of "agent will retry" (retry isn't guaranteed; three mistakes in a row pause the run; also covers #18's `invalid_reply`), and `Public research (<trigger>, new\|cached): <query>`; a missing or empty trigger keeps the old line. Tests: the four triggers, missing/empty/non-string trigger, `invalid_reply` |
| 2026-11-10 | 6a CLI git approvals | done, merged (#25) | Opened with base `feature/clients-cli-notices`, base changed to `main` after #24 merged. #24's review changes merged in first (`c476038`) |
| 2026-11-10 | VS Code notice wording | PR open (#28) | Branch `feature/clients-vscode-notice-wording` (`9c1b397`): VS Code tool error and research lines match the CLI after #24. `npm test` 27/27, package OK, `pytest tests/test_m3_api.py` 8/8 |
| 2026-11-10 | Env issue B (symlink test) | resolved | Areeba turned on Windows Developer Mode. `test_symlink_and_parent_symlink_rejected` now passes on this machine, and full `pytest` passes with no failures |
| 2026-11-10 | Reviews | info | Areeba reviewed Usman's #18 (switch notices + recoverable malformed replies): approve, two non-blocking notes (a routing change during a lasting fallback reports only `configured_step_routing`; earlier "will retry" wording, since changed in #24/#28) |
| 2026-11-10 | Still open | info | 6b VS Code git approvals (waits for Usman/Sharjeel on the preview approach); 8 `agent bench` (waits for Sharjeel's harness, not on `main`); 9 live PR review (waits for Sharjeel's workflow/secrets); 10 docs (`M3_USAGE.md`/`STATUS.md` WS3 rows + demo script; SPEC first). VSIX on disk is still `0.3.0`, the same version number as one built from `main` |
| 2026-11-11 | Reviews | info | Areeba reviewed Usman's #34 (freeze ID `7ac9085`): approve. #35 (one database connection per thread): change requested, because `except Exception` leaves a reused connection inside `BEGIN IMMEDIATE` after a `BaseException` (reproduced), plus doc nits (p95 52.56 ms, policy step numbers) |
| 2026-11-11 | 8 `agent bench` | done, committed locally, push/PR when Areeba says | Branch `feature/clients-cli-bench` from `main` @ `3d62f78`. `agent bench` passes `--config` (must exist), `--task`/`--system` (repeatable), `--repeats`, `--output-dir`, `--gateway-log`, `--python`, `--node-modules ENV=PATH` straight to Sharjeel's `benchmarks.harness.run_bench()` and adds no logic of its own. The harness loads only from the checkout the agent is installed from (`pip install -e .`), never from the current folder. Prints runs, report path, and "Benchmark evidence: yes" only when the report's `benchmark_evidence` is true; otherwise "Not benchmark evidence". 10 new tests with a fake harness (no benchmark is run) plus one that loads the real harness. Full `pytest` 274 pass; ruff clean. Smoke: real `agent bench --task TASK-99` from outside the repo made 0 runs, wrote a report and said "Not benchmark evidence". For Sharjeel: an unknown `--task` ID silently gives 0 runs, and an unknown `--system` records every run as an error |
