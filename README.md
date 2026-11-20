# Token-Efficient Coding Agent

[![CI](https://github.com/sharjeelmajeed/Token-Efficient-Coding-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/sharjeelmajeed/Token-Efficient-Coding-Agent/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
![Version](https://img.shields.io/badge/version-0.3.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![Node](https://img.shields.io/badge/node-22%2B-339933)

A **policy-enforced, token-efficient coding agent** built around a single Python/FastAPI core that owns security policy, execution, persistence, and budget accounting—paired with thin **CLI**, **VS Code**, and **read-only GitHub PR review** clients.

All real model traffic routes through [Maxim AI Bifrost](https://www.getmaxim.ai/) (with local Ollama support for confidential repositories), selective public research uses [Tavily](https://tavily.com/), and approved command execution runs inside an ephemeral, network-disabled Docker sandbox with no host fallback.

---

## Architecture

```text
┌───────────────────┐   ┌────────────────────┐   ┌─────────────────────────┐
│    CLI Client     │   │  VS Code Extension │   │  Read-Only PR Reviewer  │
│  (Typer / Rich)   │   │  (TypeScript VSIX) │   │   (GitHub API Runner)   │
└─────────┬─────────┘   └──────────┬─────────┘   └────────────┬────────────┘
          │                        │                          │
          └────────────────────────┼──────────────────────────┘
                                   ▼
                 Authenticated REST + Resumable SSE
                                   │
┌──────────────────────────────────▼───────────────────────────────────────┐
│                      Python / FastAPI Agent Core                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │   Policy    │  │ Agent Engine │  │ SQLite Store │  │  Context &    │  │
│  │ (Ownership, │  │ (Step Loop,  │  │ (Append-Only │  │ Optimizations │  │
│  │  Locality,  │  │  Approvals,  │  │  Events &    │  │  (9 Switches) │  │
│  │   Budgets)  │  │  Cancel/SSE) │  │  Micro-USD)  │  │               │  │
│  └─────────────┘  └──────────────┘  └──────────────┘  └───────────────┘  │
└──────────┬───────────────────────┬──────────────────────────┬────────────┘
           ▼                       ▼                          ▼
   Bounded Repo Tools       Docker Sandbox           Bifrost / Ollama &
   & Checked Patches     (No Network, Digest-         Sanitized Tavily
                          Pinned, No Fallback)            Research
```

No client, prompt, or repository file can grant permissions, change budgets, redirect credentials, or bypass backend policy checks.

---

## Key Features

### Backend-Enforced Security & Governance
- **Exact-action human approvals:** File edits, file creations/replacements, command executions, and gated git actions require single-use approval bound to the exact hash/diff/argv. Stale files are rejected before writing.
- **Sticky confidential routing:** Confidential repository markers promote sessions to local-only mode (loopback gateway + local model allowlist, including fallbacks) and cannot be downgraded by removing the marker.
- **Atomic budget ledger:** Per-user budgets are tracked in integer micro-USD inside SQLite write transactions, preventing concurrent reservation races.
- **Isolated command sandbox:** Approved commands run in a digest-pinned, read-only, network-disabled, non-root Docker container with dropped capabilities and resource/time caps. Missing Docker pauses execution rather than falling back to the host.
- **Resumable, auditable state:** Sessions, messages, runs, approvals, and append-only audit events persist in SQLite. Server restarts expire pending approvals and pause incomplete runs rather than replaying uncertain writes.

### Nine Switchable Token-Efficiency Techniques
Every optimization technique is independently toggleable in `[optimization]` for controlled ablation studies (see [docs/OPTIMIZATIONS.md](docs/OPTIMIZATIONS.md)):

| ID | Technique | Config Switch | Default |
| --- | --- | --- | --- |
| TO-01 | Repository structure map | `repo_map` | `true` |
| TO-02 | Targeted line-range reads & bounded literal grep | `targeted_reads` | `true` |
| TO-03 | Bounded tool output truncation | `output_truncation` | `true` |
| TO-04 | Exact small-diff patching (`apply_patch`) | `diff_edits` | `true` |
| TO-05 | Untrusted-labeled history compaction | `history_compaction` | `true` |
| TO-06 | Phase-aware tool schema pruning | `tool_pruning` | `true` |
| TO-07 | Gateway prompt/response cache controls | `gateway_direct_cache` | `false` |
| TO-08 | Fast/strong tiered model routing | `tiered_routing` | `false` |
| TO-09 | Verified early termination on passing checks | `early_termination` | `true` |

### Multi-Client Ecosystem
- **Interactive CLI (`agent`):** Streaming timeline, inline diffs, approve/deny/feedback prompts, budget warnings, `agent status`, `agent resume`, `agent cancel`, and `agent doctor` diagnostics.
- **VS Code Extension (`clients/vscode/`):** Local-install VSIX with session chat, native before/after diff review, approval cards, status bar model/budget/mode indicators, SecretStorage credentials, reconnect, cancellation, and immutable JSON run reports. See [docs/M3_USAGE.md](docs/M3_USAGE.md).
- **Read-Only GitHub PR Review:** Fetches bounded patches and excerpts via fixed GitHub API routes, never checks out or executes PR code, and routes through the same read-only core with optional, separately permissioned comment publication.

---

## Quick Start

### Prerequisites
- **Python 3.11+** (tested on Python 3.12)
- **Node.js 22+** (for building/testing the VS Code extension)
- **Docker** (optional; only required when enabling the command sandbox via `agent.sandbox.toml`)

No API keys are needed to run the offline scripted fixture demo.

### 1. Install & Run the Offline Demo

**Windows (PowerShell):**
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe scripts\demo.py
```

**Linux / macOS:**
```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python scripts/demo.py
```

The interactive demo creates an ephemeral token in memory, starts a local fixture core, runs `demo: fix calculator addition` through the CLI, prompts you to approve (`a`), deny (`d`), or deny with feedback (`f`), and shuts down cleanly.

### 2. Run Persistent Local Core & CLI

Create the demo repository once, generate a random `AGENT_API_TOKEN` (32+ characters) in your shell environment, and start the core:

```bash
# Terminal 1 — Start the loopback core
.venv/bin/python scripts/make_demo.py
export AGENT_API_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(40))')"
.venv/bin/agent serve --config agent.toml
```

In a second terminal with the same `AGENT_API_TOKEN`:

```bash
.venv/bin/agent init --repo sandbox-repos/demo
.venv/bin/agent run "demo: fix calculator addition"
.venv/bin/agent status
.venv/bin/agent resume
.venv/bin/agent cancel
```

> **Note:** On Windows PowerShell, replace `.venv/bin/...` with `.\.venv\Scripts\...` and set `$env:AGENT_API_TOKEN`. The application intentionally does not auto-load `.env` files; see `.env.example` for environment variable names and `agent doctor --config agent.toml` for local diagnostics.

### 3. Build & Install the VS Code Extension

```bash
cd clients/vscode
npm ci --ignore-scripts
npm test
npm run package
code --install-extension token-efficient-coding-agent-0.3.0.vsix
```

Open `sandbox-repos/demo` in VS Code, run **Token Agent: Configure Connection** (`http://127.0.0.1:8765` + your `AGENT_API_TOKEN`), then run **Token Agent: Start Session**. See [docs/M3_USAGE.md](docs/M3_USAGE.md) and [docs/DEMO_WS3.md](docs/DEMO_WS3.md) for the full walkthrough.

---

## Configuration & Live Providers

- **`agent.toml`**: Default trusted configuration with the Docker sandbox disabled (no image pull required for local/fixture use; command execution pauses instead of running on the host).
- **`agent.sandbox.toml`**: Enables the Docker sandbox with the digest-pinned benchmark image (`ghcr.io/sharjeelmajeed/token-efficient-coding-agent-bench@sha256:...`).
- **Live gateway & research**: See [docs/LIVE_INTEGRATION.md](docs/LIVE_INTEGRATION.md) and [docs/M2_USAGE.md](docs/M2_USAGE.md) for configuring Maxim AI Bifrost, local Ollama confidential routing, Tavily public research, and gateway cost reconciliation.
- **Single-process model**: `agent serve` binds to loopback (`127.0.0.1:8765`) and runs as a single process against its SQLite database.

---

## Frozen Benchmark Suite

The held-out evaluation suite in [`benchmarks/tasks/`](benchmarks/tasks/) is frozen at commit `7ac9085` (with the TASK-03 Linux portability revision in PR #50) and contains **18 tasks** across 3 repositories (`pallets/click`, `pallets/jinja`, `expressjs/express`) and 2 languages (Python, JavaScript). Every task is validated with [`benchmarks/validate_tasks.py`](benchmarks/validate_tasks.py) to fail before the fix and pass with the reference fix ([`validation.json`](benchmarks/tasks/validation.json)). See [`benchmarks/README.md`](benchmarks/README.md) and [docs/BENCHMARK_REPORT.md](docs/BENCHMARK_REPORT.md) for the protocol and current limitations.

---

## Development & Verification

Build and test the VS Code extension first (Python integration tests exercise the compiled Node client):

```bash
cd clients/vscode
npm ci --ignore-scripts
npm test
npm run package
npm audit
cd ../..
```

Then run linting, formatting, tests, policy branch coverage, and dependency audits:

**Linux / macOS:**
```bash
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/python -m coverage run --branch --source=token_agent -m pytest
.venv/bin/python -m coverage report --include=src/token_agent/policy.py --fail-under=80
.venv/bin/python -m pip_audit --skip-editable
```

**Windows (PowerShell):**
```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\ruff.exe format --check src tests scripts
.\.venv\Scripts\python.exe -m coverage run --branch --source=token_agent -m pytest
.\.venv\Scripts\python.exe -m coverage report --include=src/token_agent/policy.py --fail-under=80
.\.venv\Scripts\python.exe -m pip_audit --skip-editable
```

All runtime and development dependencies are hash-locked in `requirements.lock`, `requirements-dev.lock`, `uv.lock`, and `clients/vscode/package-lock.json`. See [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) for the dependency and license review.

---

## Project Layout

| Path | Description |
| --- | --- |
| `src/token_agent/api.py` | Authentication, ownership checks, and REST/SSE endpoints |
| `src/token_agent/engine.py` | Persisted agent loop and exact-action approval lifecycle |
| `src/token_agent/policy.py` | Classification, repository/model routing, and micro-USD budget policy |
| `src/token_agent/repo.py` | Bounded repository inspection, secret checks, and verified patching |
| `src/token_agent/store.py` | SQLite persistence, append-only audit events, and atomic budget ledger |
| `src/token_agent/providers.py` | Deterministic fixture and Maxim AI Bifrost transport adapters |
| `src/token_agent/sandbox.py`, `research.py` | Isolated Docker command execution and sanitized Tavily research |
| `src/token_agent/context.py`, `diagnostics.py` | Token-efficiency context controls and `agent doctor` checks |
| `src/token_agent/github_client.py`, `pr_review.py` | Bounded GitHub PR data fetcher and read-only review runner |
| `src/token_agent/cli.py` | Thin interactive terminal client (`agent`) |
| `clients/vscode/` | TypeScript VS Code extension, tests, and local-install VSIX |
| `benchmarks/` | Frozen 18-task benchmark suite, validation, and harness |
| `docs/` | Architecture, security model, usage guides, API schemas, and evidence |
| `tests/` | Policy, API, persistence, gateway, sandbox, git, and CLI test suites |
| `.github/workflows/` | Core/extension CI, benchmark image build, and opt-in PR review workflow |

---

## Current Status & Evidence Boundaries

This repository is at **v0.3.0**. We maintain strict separation between verified evidence and unmeasured claims:

- **Offline fixture:** The scripted fixture supports `demo: fix calculator addition` only. Its synthetic token counts demonstrate client and policy behavior and are **never** benchmark evidence.
- **Live integrations:** Bifrost, local Ollama confidential routing, Tavily research, Docker sandbox isolation (18/18 checks), live CLI git approvals, and a live `completed_verified` scratch run are recorded in `docs/evidence/live/`.
- **Benchmark campaign:** **No token-saving percentage is claimed yet.** The 594-run comparative benchmark campaign has not been executed due to paid gateway credit requirements and the strict `Repository.snapshot()` limitation documented in [docs/STATUS.md](docs/STATUS.md) and [docs/BENCHMARK_REPORT.md](docs/BENCHMARK_REPORT.md).

See [docs/STATUS.md](docs/STATUS.md) and [docs/RELEASE.md](docs/RELEASE.md) for the full release checklist and known limitations.

---

## Security & Documentation

- **Security Model & Trust Boundaries:** [docs/SECURITY.md](docs/SECURITY.md)
- **API & Event Contracts:** [docs/API.md](docs/API.md), [docs/openapi.json](docs/openapi.json), [docs/event.schema.json](docs/event.schema.json)
- **Usage Guides:** [docs/M2_USAGE.md](docs/M2_USAGE.md) (Core/Gateway/Sandbox), [docs/M3_USAGE.md](docs/M3_USAGE.md) (VS Code & PR Review), [docs/GIT_TOOLS.md](docs/GIT_TOOLS.md)
- **Scope & Specifications:** [docs/SCOPE.md](docs/SCOPE.md)

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
