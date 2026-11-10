# Token-Efficient Coding Agent

Confirmed hackathon FSD implementation. **Version 0.3.0: M3 editor and PR clients.**
This is not yet the complete hackathon submission. The remaining scope is tracked
in [STATUS](docs/STATUS.md), with human ownership in [TEAM_PLAN](docs/TEAM_PLAN.md).

## What works now

- Authenticated REST API and resumable SSE events; owner checks on every session endpoint.
- Persistent SQLite sessions, messages, runs, single-use approvals and accounting.
- Targeted reads, repository outline, bounded literal search and exact small patches.
- Diff approval, rejection, feedback, expiry, cancellation and stale-file detection.
- Immutable confidential classification; core-side local allowlist and loopback gateway check.
- Atomic per-user budget reservation before provider requests, including concurrent runs.
- Offline scripted fixture and a Bifrost HTTP adapter with transport-level tests.
- Interactive CLI and a disposable demo with independent behavior checks.
- Approved file creation/replacement and an isolated Docker command adapter with no host fallback.
- Sanitized Tavily research, source/credit/context accounting and a session-scoped cache.
- History compaction, tool pruning, configured routing, direct-cache controls and fallback metadata.
- Verified termination only after all configured checks pass on an unchanged sanitized snapshot.
- `agent doctor` for safe local setup diagnostics. See [M2_USAGE](docs/M2_USAGE.md).
- Installable VS Code extension with session chat, native diffs, approval decisions,
  model/budget/privacy status, reconnect, cancel and reports. See [M3_USAGE](docs/M3_USAGE.md).
- Backend read-only sessions and identities; owner-checked, non-mutating diff previews.
- Read-only PR analysis of bounded patches/excerpts, complete-read evidence and optional
  stale-safe comment publisher. The supplied GitHub workflow is disabled until configured.

The fixture is explicitly scripted. It supports `demo: fix calculator addition` only.
It is not an AI model and its synthetic token counts are not benchmark evidence.
The real Bifrost adapter is implemented but has not been tested against a running
team gateway or paid provider in this environment.

## Frozen benchmark suite

The benchmark task suite is frozen in [`benchmarks/tasks/`](benchmarks/tasks/) at commit `7d00bc4` on branch `test/evaluation-freeze`.
It defines 18 tasks across 3 repositories (`pallets/click`, `pallets/jinja`, and `expressjs/express`)
and 2 languages (Python, JavaScript), covering bug fixes, small features, refactors, and test additions.
Each task specifies a pinned commit, exact prompt, target files, and automated pass/fail command.
Per handoff G01 and FSD 10.3, core optimization features were developed prior to benchmark freezing;
this held-out suite serves as the fixed ground truth for all comparative evaluations. See
[`benchmarks/README.md`](benchmarks/README.md) for full task definitions and protocol.

## Install on Windows PowerShell

Open PowerShell in the extracted `token-efficient-coding-agent` directory.
Python 3.11+ is declared; Python 3.12 was exercised here. Internet access is needed
for the initial package installation. No model keys are needed for the fixture.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe scripts\demo.py
```

Read the diff and enter `a` to approve it once, `d` to deny, or `f` to deny with
feedback. The demo creates an ephemeral API token in process environment, starts
a local core, runs the CLI, checks the authored fixture if changed, and shuts down.
No credential needs to be pasted into chat or saved in source code.

## Install on Linux/macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python scripts/demo.py
```

The hash-locked runtime-only dependencies are in `requirements.lock`; development
dependencies and the build backend are in `requirements-dev.lock`. `uv.lock` is
also included. Direct dependencies and license review are in [DEPENDENCIES](docs/DEPENDENCIES.md).

## Verify

Install Node 22+ and compile/test the extension first; Python integration tests use its
actual compiled client. The release includes its compiled output and VSIX, but source
checkouts build them using these commands on either platform:

```bash
cd clients/vscode
npm ci --ignore-scripts
npm test
npm run package
npm audit
cd ../..
```

Windows:

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\ruff.exe format --check src tests scripts
.\.venv\Scripts\python.exe -m coverage run --branch --source=token_agent -m pytest
.\.venv\Scripts\python.exe -m coverage report --include=src/token_agent/policy.py --fail-under=80
.\.venv\Scripts\python.exe -m pip_audit --skip-editable
```

Linux/macOS:

```bash
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/python -m coverage run --branch --source=token_agent -m pytest
.venv/bin/python -m coverage report --include=src/token_agent/policy.py --fail-under=80
.venv/bin/python -m pip_audit --skip-editable
```

Tests exercise a real loopback HTTP server and a real CLI subprocess. Gateway
and research tests use deterministic HTTP transports. Container tests use a process
transport double; Docker isolation has not been exercised on this host. Re-running
the dependency audit needs network access. The CI workflow is supplied but has
not run on GitHub; no remote repository has been created or pushed.

## Persistent local use

`agent.toml` is the default trusted configuration. Run `python scripts/make_demo.py`
once to create `sandbox-repos/demo`. Supply a random `AGENT_API_TOKEN` (32+ characters)
to both server and client processes through the environment. `.env.example` documents
names; the application does not automatically load `.env` files.

Run `agent serve --config agent.toml` in one terminal, then these commands in a
terminal with the same API token:

```text
agent init --repo sandbox-repos/demo
agent run "demo: fix calculator addition"
agent status
agent resume
agent cancel
```

Use the platform-specific `.venv` executable path unless the environment is activated.
`resume` reconnects to an existing run/event cursor; it does not silently restart a
paused model loop. After resolving a pause, submit another explicit instruction.
The core saves history and never automatically replays an uncertain write.

`agent serve` binds to loopback and uses one process. Do not run multiple server
instances against the same SQLite database. An application restart invalidates
pending approvals and pauses incomplete runs for inspection.

## File map

| Path | Purpose |
| --- | --- |
| `docs/source/FSD_Token_Efficient_Coding_Agent.pdf` | Accepted source document, unchanged |
| `docs/SCOPE.md` | Scope, architecture, permissions and milestone boundaries |
| `src/token_agent/config.py` | Trusted TOML configuration and environment credentials |
| `src/token_agent/models.py` | Validated request, action and event schemas |
| `src/token_agent/api.py` | Authentication, ownership and REST/SSE endpoints |
| `src/token_agent/engine.py` | Persisted agent loop and exact approval lifecycle |
| `src/token_agent/policy.py` | Classification, repository/model selection and budget arithmetic |
| `src/token_agent/repo.py` | Bounded repository inspection and checked patches |
| `src/token_agent/store.py` | SQLite state, append-only events and atomic budget ledger |
| `src/token_agent/providers.py` | Fixture and Bifrost transport adapters |
| `src/token_agent/research.py`, `sandbox.py` | Selective public research and approved isolated commands |
| `src/token_agent/context.py`, `diagnostics.py` | Context optimization and local setup checks |
| `src/token_agent/cli.py` | Thin terminal client |
| `clients/vscode/` | TypeScript editor extension, tests and local-install VSIX |
| `src/token_agent/github_client.py`, `pr_review.py` | Bounded GitHub data snapshot and shared-core read-only review |
| `.github/workflows/` | Core/extension checks and opt-in read-only PR workflow |
| `scripts/demo.py` | Isolated interactive demonstration |
| `tests/` | Policy, API, persistence, gateway and CLI tests |
| `benchmarks/` | Frozen evaluation task suite and manifests |
| `docs/openapi.json`, `docs/event.schema.json` | Generated client contracts |
| `docs/evidence/` | Captured local verification evidence |

The CLI only submits requests and displays decisions. The API resolves the authenticated
owner, the engine asks policy before action, the store records state, and repository
tools perform only permitted reads or the exact reviewed patch. The gateway adapter
cannot select a provider URL from a task or repository file.

## Before live integration

See [LIVE_INTEGRATION](docs/LIVE_INTEGRATION.md). Bifrost, Ollama and Tavily were not
installed here. This host has no Docker executable. Research/sandbox features are
disabled by default and require explicitly configured prerequisites. Missing Docker
pauses command execution; it never falls back to host execution. Git tooling,
live acceptance and billing reconciliation remain in scope. M3 client source/tests
are delivered; real-editor visual acceptance and actual GitHub deployment are pending.

Read [SECURITY](docs/SECURITY.md) for actual protections and remaining boundaries.
There are no measured savings or full benchmark results yet.
