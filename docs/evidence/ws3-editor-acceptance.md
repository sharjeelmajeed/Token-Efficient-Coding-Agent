# WS3 VS Code editor acceptance

**This is a fixture run. It is not live-model evidence.** The core runs in `mode = "fixture"`:
a scripted calculator task with synthetic token counts. It shows client behavior only.
Live Bifrost model switches are not covered (they need WS2's live setup), and neither are
git approvals in the editor (Item 6b).

| Field | Value |
| --- | --- |
| Date | 2026-11-10 |
| Client commit | `523618d` (branch `feature/clients-vscode-streaming`: Items 4 + 5) |
| VSIX | `token-efficient-coding-agent-0.3.0.vsix`, SHA-256 prefix `ACF5BAD2B75F00A7`, built locally from `523618d` (not committed), which is older than the client now on `main`. It does not include `af82bfd` (PR #11: model/budget notices no longer end a streamed reply) or PR #19 (budget options in plain words). Neither affects this fixture run, which has no model switches or budget hits; real model switches are covered by WS2's live streaming test through both clients. The version stays `0.3.0`, so a VSIX built from `main` has the same version number. The 7 and 8 re-run used this same VSIX |
| Core config | `.agent/acceptance.toml` (git-ignored): `agent.toml` + `streaming = true`, fixture mode |
| OS | Windows 10 Pro 10.0.19045 |
| Tester | Areeba |

## 1. Automated (done)

| Check | Result |
| --- | --- |
| `npm test` (Node: transport, controller, webview, budget, stream) | 24/24 pass |
| `npm run test:host`: real VS Code **1.144.0** host (the minimum version): activation, all 6 commands registered, read-only virtual preview document, chat opens | Pass |
| `pytest tests/test_m3_api.py` (compiled client against a real loopback core) | 8/8 pass |
| `agent doctor --config .agent/acceptance.toml` | `ready: true` |

## 2. Manual: installed VSIX in VS Code 1.145.2

Setup: see "How to run" below. Mark each row ✅ or ❌, and add a note on anything odd.
Screenshots must not show the token.

| # | Step | Expected | Result | Note |
| --- | --- | --- | --- | --- |
| 1 | Install VSIX, open `sandbox-repos/demo`, trust the workspace | Status bar shows `Token Agent` | ✅ | |
| 2 | **Token Agent: Configure Connection** → `http://127.0.0.1:8765` + token | "Connection saved" in chat | ✅ | |
| 3 | **Token Agent: Start Session** | Chat opens; status shows `fixture · standard · idle` | ✅ | |
| 4 | Send `demo: fix calculator addition` | Tool lines appear; approval card `apply_patch` | ✅ | |
| 5 | Click **Review and decide** | Native diff opens: `return a - b` → `return a + b` | ✅ | |
| 6 | Choose **Reject with feedback**, type a reason | File unchanged; run continues or pauses with a readable message (no raw JSON) | ✅ | |
| 7 | In the **same session**, send the task again; the approval card appears again; while it waits, click **Cancel run** | `Run finished: user_cancelled`; the approval card disappears; file unchanged | ✅ | Re-run in the same session (see "Steps 7 and 8 re-run"). First run used **New session** (F1) and also passed |
| 8 | In the **same session**, send the task again, choose **Accept once** | File shows `return a + b`; the final reply appears as **one** assistant bubble (streaming) | ✅ | Re-run in the same session. First run used **New session** and also passed |
| 9 | Status bar | Model, `$…` remaining, `standard`; no budget warning (fixture budget is large) | ✅ | |
| 10 | **Run report** button | Read-only JSON report opens; shows `benchmark_evidence: false` | ✅ | |
| 11 | Close the chat panel, run **Token Agent: Reconnect** | Chat reopens; nothing is repeated or sent again | ✅ | |
| 12 | Theme: switch to Light, then Dark | Text, buttons and the warning border are readable in both | ✅ | |
| 13 | Keyboard only: Tab through the buttons, type a task, Enter or Space to send | Every control is reachable; the focus outline is visible | ✅ | |
| 14 | Stop the core (Ctrl+C), click **Reconnect** | Readable error, no crash; it works again once the core restarts | ✅ | Core restarted in the same PowerShell window (same token) |

**Result: 14/14 pass** (Areeba, 2026-11-10, fixture mode). No issues reported.

**Steps 7 and 8 re-run (2026-11-10, Areeba), same session, no workaround.** Core restarted
from branch `feature/clients-vscode-acceptance` @ `b61ecab` (`main` with PR #12), demo reset
with `scripts/make_demo.py`, same installed VSIX. In one session: reject with feedback → task
sent again, the approval card appeared again → **Cancel run**: `user_cancelled`, card gone,
file still `return a - b` (7 ✅) → task sent a third time → **Accept once**: `return a + b`,
reply in one bubble (8 ✅).

## How to run

In **your own** PowerShell, from the project folder. The token never goes into a file or chat.

```powershell
$env:AGENT_API_TOKEN = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(40))"
Set-Clipboard $env:AGENT_API_TOKEN      # so you can paste it into VS Code's password box
.\.venv\Scripts\agent.exe serve --config .agent\acceptance.toml
```

Then in VS Code: **Extensions: Install from VSIX…** →
`clients\vscode\token-efficient-coding-agent-0.3.0.vsix` → **File: Open Folder** →
`sandbox-repos\demo` → follow the table. To reset the demo afterwards, delete
`sandbox-repos\demo` and run `.\.venv\Scripts\python.exe scripts\make_demo.py`.

## Findings

**API dry run of the manual steps (2026-11-10, same config, through HTTP, not through the editor):**
reject with feedback → `completed_unverified`, file unchanged; cancel while waiting →
`user_cancelled`, no approvals left pending, file unchanged; accept → `return a + b`,
`completed_unverified`; run report `benchmark_evidence: false`. With streaming on, the
fixture reply arrives as one `partial: true` piece and one `partial: false` piece.

**F1: fixture limitation, not a client bug.** Before PR #12, in the **same session**, after a
rejected or cancelled patch, the scripted fixture did not propose the patch again. The next
run ended with `completed_unverified`, or paused with "Tool is disabled or requires a file
inspection first". That's why the first run of steps 7 and 8 started a new session. It
applied only to the fixture and was reported to WS1 (Usman).

**F1 status: fixed by WS1 in PR #12** (`feature/core-fixture-repeatable`, merged to `main`).
The fixture now reads only the current run's results, so each new run reads the file and
offers the patch again. Confirmed in the editor: steps 7 and 8 re-run in the same session
pass (above), so the "New session" workaround is removed from the checklist.

**Manual editor run (2026-11-10, Areeba):** all 14 steps pass in the installed VSIX on
VS Code 1.145.2. No new client bugs. F1 is fixed by PR #12.
