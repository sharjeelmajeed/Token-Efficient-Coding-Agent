# M3: editor and pull request review

Version 0.3.0 adds two clients to the existing Python core. Read-only permission is
stored in SQLite and enforced by the core even when tool pruning is disabled.
Previewing a patch never applies it; an owner must decide its current approval ID.

## Build and run the VS Code client

Required: Python 3.11+ (tested 3.12.14), Node 22+ (tested 24.22.1), VS Code 1.144+.
From the project root, install the Python environment as described in the README.
Then build the extension; it has no runtime npm dependencies:

```bash
cd clients/vscode
npm ci --ignore-scripts
npm test
npm run package
cd ../..
```

Exact development packages and integrity hashes are in `clients/vscode/package-lock.json`.
The installable file is `clients/vscode/token-efficient-coding-agent-0.3.0.vsix`;
it is also included prebuilt in the release ZIP. Use VS Code's **Extensions: Install
from VSIX**, or `code --install-extension clients/vscode/token-efficient-coding-agent-0.3.0.vsix`.

Prepare an authored demo repository and start the fixture core in a terminal:

```bash
.venv/bin/python scripts/make_demo.py
export AGENT_API_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(40))')"
.venv/bin/agent serve --config agent.toml
```

PowerShell equivalents:

```powershell
.\.venv\Scripts\python.exe scripts\make_demo.py
$env:AGENT_API_TOKEN = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(40))"
.\.venv\Scripts\agent.exe serve --config agent.toml
```

Open `sandbox-repos/demo` in VS Code. In the command palette run **Token Agent:
Configure Connection**, use `http://127.0.0.1:8765`, and enter the token from your
own terminal environment into its password field. Never paste that value into chat
or a repository file. Then run **Token Agent: Start Session** and submit
`demo: fix calculator addition` in the chat panel.

Select **Review** on the pending approval. Inspect the native before/after diff,
then select **Accept once**, **Reject**, or **Reject with feedback**. Accepting the
fixture changes subtraction to addition. Its authoritative outcome remains
`completed_unverified` because the fixture core has no configured container checks.
CLI behavior tests separately verify this authored example; that is not a real-model benchmark.

The status bar shows the observed model, remaining micro-USD converted to dollars,
and standard/local-only mode. Fallback notices appear in chat and a warning. Chat
receives core events; provider token-by-token streaming remains pending. **Reconnect
Session** fetches current status and continues from its saved event cursor without
resubmitting the task. The current displayed timeline is in memory; the backend
retains the full event history. **Cancel Run** cancels the core run; **Open Run Report**
opens an immutable JSON document with usage, holds and evidence labels.

The core and extension host must see the same absolute repository path. For SSH/WSL,
run the core on that trusted workspace host. No file synchronization is performed.
A missing/expired token, invalid path, stale preview or paused backend produces a
visible error; the extension never writes the repository directly. Configure a new
connection to discard an unusable saved session. Workspace settings cannot redirect
the credential, and changing the trusted user connection invalidates the old session.

## PR analysis setup

The runner uses `src/token_agent/github_client.py` for fixed-origin GitHub reads,
copies patches/excerpts into a temporary data bundle, and calls the shared REST/SSE
application in-process. `pr_review.py` does not implement its own tool authority.
There is no PR checkout, import, dependency install, shell command, or test execution.

Use a working, operator-owned M2 Bifrost configuration as `agent.review.toml`.
Configure actual gateway URL, allowed model IDs, conservative prices, attempt limits
and a finite user budget before running. The first configured user's budget/key-env
names are used. Its repository roots are replaced by the one temporary bundle and
its identity is always read-only; research and command execution are disabled.
Use an adequate `max_steps` for the number/size of patches (maximum 100). A short
step/budget limit produces a paused review, never a fabricated complete report.
The fixture is rejected as a PR reviewer. See `LIVE_INTEGRATION.md` for live setup.

Supply `GITHUB_TOKEN` and configured Bifrost keys through environment variables.
GitHub read permission must cover repository contents and PR metadata, including
the head repository. Run this from the project root (use `.venv\Scripts\python.exe`
on Windows):

```bash
.venv/bin/python -m token_agent.pr_review review \
  --config agent.review.toml --repository OWNER/REPOSITORY --number 7 --output review.json
```

Replace `OWNER/REPOSITORY` and `7` with the actual target. This command writes a local
report and does not post a comment. The report identifies head/base commits, exact
coverage, completion state, usage/holds and model switches. Completion requires the
entire manifest and every supplied patch to have been delivered through successful,
untruncated core reads. This is delivery evidence, not proof of semantic understanding.
Context excerpts are counted separately. Binary/missing patches are explicitly
excluded. Limits are 30 changed files, 60,000 patch characters total, 128 KB per
UTF-8 source blob, and bounded context around the first four hunks. Split larger PRs.

A private base repository or confidential marker at either immutable base/head
commit makes the bundle confidential. Core then requires a loopback Bifrost gateway
and local-only models/keys. A normal hosted GitHub runner has no such local service;
confidential analysis must pause unless that environment has independently verified
local routing. Do not change classification to force a cloud review. Fetch failures,
ambiguous metadata, protected paths, detected secrets and changed head/base fail closed.
Any `confidential_roots` in the trusted review config also conservatively makes the
entire temporary bundle confidential; host paths cannot be mapped reliably to a
remote PR. The runner never drops that operator restriction when creating its snapshot.

## Workflow deployment and optional comments

`.github/workflows/pr-review.yml` is supplied but was not deployed. It is inactive
until a maintainer sets `AGENT_REVIEW_ENABLED=true`. It uses `pull_request_target`
only to run trusted base-commit code; PR content is bounded data fetched via API.
Never change checkout to the PR head or run PR-provided setup/configuration.

Before enabling it, put the application and non-secret `agent.review.toml` on the
trusted base branch; configure `BIFROST_REVIEW_VIRTUAL_KEY` as a repository secret.
Use `BIFROST_VIRTUAL_KEY` as its config key-env name. The workflow deliberately
does not supply a local-model key on a hosted runner. Set gateway-side per-key
spending limits: the local review ledger is per job and does not cap all workflow
runs combined. Concurrency cancellation prevents overlapping runs for one PR.

The review job has only contents/PR read access. It stores a completed JSON report
as an artifact with one-day retention. Repository visibility controls artifact access.
`AGENT_REVIEW_POST_COMMENT=true` explicitly enables the separate publish job, which
has PR-write permission and receives no Bifrost credential. That job validates report
identity/completeness, escapes output inside a text fence, scans possible credentials,
checks bot-comment duplicates by head/base, and rechecks commits immediately before posting.
GitHub's issue-comment endpoint has no atomic comparison with the PR head: a push
after the last check can still occur; every comment visibly names its reviewed commit.

For a deliberate manual publication after inspecting a report:

```bash
.venv/bin/python -m token_agent.pr_review publish \
  --report review.json --repository OWNER/REPOSITORY --number 7
```

This last command posts externally and requires an appropriately scoped token.
It has not been executed against GitHub here. Duplicate suppression recognizes
`github-actions[bot]`; manually published comments using another identity need operator
coordination. Network failures after POST can have uncertain outcomes; inspect GitHub
before retrying manually. Review output is advisory and does not authorize a merge.

## Verification and acceptance

Run `npm test` before the Python test suite; Python integration uses the compiled
Node client against a real loopback core. Run `npm audit` and the README's Python
lint/coverage/audit checks. The ZIP contains captured results, not remote CI claims.

On a desktop with VS Code's test-host prerequisites:

```bash
cd clients/vscode
npm run test:host
```

Linux CI uses `xvfb-run -a npm run test:host`. The script downloads official VS Code
1.144.0 and checks activation, commands, read-only virtual documents and chat opening.
It was not run here because no desktop/Xvfb or usable VS Code binary was available.
Manual acceptance still needs a real editor: approve/reject the fixture diff, cancel
a waiting run, reload and reconnect, change connection, inspect narrow/light/dark
layouts, and observe a real live model switch. Also perform an authorized test PR,
fork-injection attempt, private-routing failure and comment publication before release.

## Four-person handoff

| Owner | M3 ownership | Next acceptance |
| --- | --- | --- |
| WS1 | Core permission migration and approval-preview API | Review security changes and Docker/git remaining scope |
| WS2 | Gateway keys, models, rate ceilings and actual usage | Live routing, fallback and billing reconciliation |
| WS3 | `clients/vscode/`, `github_client.py`, `pr_review.py` | Real-editor approval/model-switch rehearsal |
| WS4 | Cross-client tests, `.github/workflows/`, evidence | Authorized GitHub workflow trial, frozen benchmark and submission |

Use the branch/review/merge procedure in `TEAM_PLAN.md`; do not copy four divergent
project folders together. `main` must pass Python + extension checks after integration.
M4 results, final demo and actual submission remain outstanding.
