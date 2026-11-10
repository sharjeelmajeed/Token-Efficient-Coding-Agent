# WS1 and WS2 handoff: core, gateway and live integration

Owner: Usman (WS1 core lead and WS2 AI/integrations). State on 2026-11-10, `main` at `4014753`.

Under `docs/TEAM_PLAN.md`, WS1 submits runnable source and architecture, and WS2 submits service
setup and integration evidence. This document indexes both.

**Evidence boundary.** Live runs below used real Bifrost, Ollama, Gemini, Groq and Tavily, on one
disposable two-line task. They prove integration, safety and accounting. They are **not**
benchmark evidence: no token-saving or cost-saving percentage is claimed. The benchmark is WS4's
frozen task suite (FSD 10).

## 1. Pull requests

| PR | Branch | What it delivers | Brief task |
|---|---|---|---|
| #2 | `feature/core-security-tests` | 52 attack tests. Fixes for confidential detection above and below a marked folder, and Windows path spellings that bypassed protected files | 2 |
| #3 | `feature/core-streaming` | Real streamed `message_delta` pieces behind `streaming = true`; a resume message on every pause type | 1 |
| #4 | `feature/core-budget-status` | Budget limit and warning fields in status and events (FSD 9.2), one warning per run | 4, 6 |
| #5 | `feature/core-git-contracts` | Git tool schemas, classification, approval binding and push policy (`docs/GIT_TOOLS.md`); the `GitExecutor` interface for WS4 | 6 |
| #6 | `feature/core-recoverable-tool-errors` | The model's own input mistakes become correctable `tool_result` errors (three in a row pause). `read_file` returns plain text. A read earlier in the session counts | 1 |
| #7 | `feature/gateway-timeout-setting` | `gateway_timeout_seconds` (5 to 900) for slow local models | 3 |
| #12 | `feature/core-fixture-repeatable` | Offline fixture offers its patch again after reject or cancel (F1 from WS3 acceptance) | 1 |
| #13 | `feature/gateway-cost-reconciliation` | `python -m token_agent.reconcile` against Bifrost logs. A gateway 402 becomes `budget_hit` with `gateway_budget_hit` | 4 |
| #14 | `feature/core-optimization-switches` | All nine FSD 8 techniques switchable (TO-01 to TO-09), recorded in `run_started` | 5 |
| #15 | `feature/core-token-attribution` | Real input tokens shared out by prompt part; `token_attribution` in run metrics | 5 |
| #16 | `feature/gateway-research-triggers` | The two missing FSD 7.2 triggers: `unknown_dependency` and `newer_version` | 5 |
| #17 | `feature/core-token-output-budget` | `tool_output_tokens` budget (FR-12), converted with measured characters per token | 5 |
| #18 | `feature/core-switch-notices` | A lasting fallback is reported once, plus `gateway_primary_restored`. A malformed reply becomes a correctable `invalid_reply` | 1 |

Each PR description has its own verification and merge notes.

## 2. Tests

| Suite | Count on `main` (`4014753`) | Result |
|---|---|---|
| Python (`pytest`) | 475 tests in 23 files | Pass; CI green on Python 3.11 and 3.12 |
| VS Code client (`npm test`) | 26 | Pass |
| `policy.py` coverage | 99% (required 80%) | Pass |
| Lint and format (`ruff`) | | Clean |
| Contracts (`scripts/export_contracts.py`) | | `openapi.json` and `event.schema.json` unchanged |
| `scripts/demo.py` | 3 checks | Pass |

Tests added by this workstream include security (52), streaming (54), budget (16), git contracts
(61), reconciliation (25), switches (17), token attribution (21), research triggers (31),
tool-output budget (14) and switch notices (9). Gateway, research and container tests use
controlled transports; they prove the core's rules, not provider behaviour.

## 3. Service setup

Files in `infra/bifrost/`:

| File | Purpose |
|---|---|
| `config.example.json` | The Bifrost configuration used for every live run, with virtual-key values replaced by placeholders. Provider keys are read from the container's environment (`env.GEMINI_API_KEY`, `env.GROQ_API_KEY`) |
| `start-bifrost.ps1` | Starts Bifrost v2.5.0, pinned by digest, on `127.0.0.1:8080`. Passes only the three variables Bifrost needs from `.env` |
| `agent.live.example.toml` | Core configuration for the live runs: Gemini with Groq fallback, local 7B for confidential repositories, streaming on |

Steps:

1. **Install and start services.** Install Docker Desktop and Ollama, then run
   `ollama pull qwen2.5-coder:7b` (4.7 GB). Set `OLLAMA_MODELS` to move model storage.
2. **Create the data folder.** Make a folder outside the repository, for example `D:\bifrost-data`.
   Copy `config.example.json` there as `config.json`.
3. **Create the two virtual keys.** Generate each with
   `python -c "import secrets; print('sk-bf-' + secrets.token_urlsafe(32))"`, and put them in
   `config.json`:

   | Virtual key | Can reach | Budget |
   |---|---|---|
   | `vk-local` | Ollama only | None |
   | `vk-standard` | Gemini and Groq | $1 per month |

   Put the same values in `.env` as `BIFROST_LOCAL_VIRTUAL_KEY` and `BIFROST_VIRTUAL_KEY`.
4. **Fill in the rest of `.env`** (ignored by git; every variable is listed in `.env.example`):
   `AGENT_API_TOKEN` and `TAVILY_API_KEY` for the core, plus `GEMINI_API_KEY`, `GROQ_API_KEY` and
   `BIFROST_SETUP_TOKEN` for Bifrost.
5. **Start Bifrost:**
   `powershell -ExecutionPolicy Bypass -File infra\bifrost\start-bifrost.ps1 -DataDir D:\bifrost-data`
6. **Start the core:** `agent serve --config agent.live.toml`, using a copy of
   `agent.live.example.toml`. The core does not read `.env` itself, so set `AGENT_API_TOKEN`,
   `BIFROST_VIRTUAL_KEY`, `BIFROST_LOCAL_VIRTUAL_KEY` and `TAVILY_API_KEY` in that terminal's
   environment first.
7. **Reconcile after a batch of runs:**
   `python -m token_agent.reconcile --config agent.live.toml --gateway-log D:\bifrost-data\logs.db --report report.json`

**Configuration details that were necessary:**
- `--add-host=host.docker.internal:host-gateway`, because Ollama listens on IPv4 only.
- `network_config.base_url` and `allow_private_network` for the Ollama provider.
- `key_ids: ["*"]` on each virtual-key provider entry.
- `max_output_tokens` of at least 1,024 for reasoning models: `gpt-oss` spent most of a 256-token
  cap on reasoning.

## 4. Live evidence

All files are in `docs/evidence/live/`. None contains a key or token. "Matched" means the core's
recorded tokens equal Bifrost's log exactly.

| Test | File | Result |
|---|---|---|
| Confidential repo, local 3B | `confidential-local-3b.json` | Local only. 6 of 6 requests matched, 0 cost. The task failed: the model copied line labels into edits. This led to PR #6 |
| Gateway fallback probe | `gateway-fallback-probe.json` | Gemini failed and Groq answered (`is-fallback: true`). The core's adapter recorded the serving model and the fallback flag |
| **TC-FO-01**: provider fails mid-task | `tc-fo-01-failover.json` | Gemini's key was invalidated while the core waited for approval, and Groq took over. `model_switched` shown, history kept, patch applied once. 4 of 4 matched |
| Reconciliation of TC-FO-01 | `tc-fo-01-reconciliation.json` | Held $0.3128, reconciled to **$0.0042**, equal to Bifrost's recorded cost |
| Confidential repo, local 7B | `confidential-local-7b.json` | Correct fix completed. Ollama only, 3 of 3 matched |
| Tavily through the research adapter | `tavily-adapter.json` | Trusted domains only, cached repeat at 0 credits. Refused without permission, for unknown packages and for confidential sessions |
| New research triggers | `tavily-triggers.json` | `unknown_dependency` and `newer_version` fired from repository files. Refused when the README documents the package |
| Gateway budget cap | `gateway-budget-cap.json` | 402 `budget_exceeded` after the cap. The gateway cap is soft by up to one request; the core's reservation is the hard limit |
| **TC-CF-01**: prompt injection, confidential repo | `tc-cf-01-injection.json` | Injection in the task, a file and a file name, with cloud models configured. **0 non-local requests** in Bifrost's log |
| Token attribution | `token-attribution.json` | The system prompt was 67% of input tokens and research text 27%. 3 of 3 matched |
| Tool-output token budget | `tool-output-token-budget.json` | 300-token budget, measured 3.68 characters per token, file cut to 1,104 characters |
| **Final run on `main`**, standard repo | `final-main-standard.json`, `final-main-standard-reconciliation.json` | Every step fell back to Groq. Correct fix applied once, summary streamed. Reconciled 4 of 4: $0.2103 held, **$0.000947** real |
| **Final run on `main`**, confidential repo | `final-main-confidential.json`, `final-main-confidential-reconciliation.json` | Local 7B, correct fix first time. **0 non-local requests**, 3 of 3 matched |
| **Streaming through both clients**, notices mid-reply | `live-streaming-clients.json` | On `main` `f453112`, two `model_switched` notices (routing, then Gemini to Groq fallback) arrived between the pieces of one streamed reply. The real CLI printed one paragraph; the VS Code client's compiled code joined it into one chat bubble. No "(reply incomplete)". 2 of 2 matched, $0.000979 real cost. The editor window itself was not driven |

Bifrost's log shows what the gateway sent. It is not network-level egress capture.

## 5. Architecture notes for maintainers

- **Policy stays in the backend.** Every tool, approval, budget and routing rule is enforced in
  `engine.py`, `policy.py` and `repo.py`. Clients only display.
- **Routing.** A confidential session uses only the local chain and the local virtual key. The
  marker is rechecked before every request and cannot be downgraded.
- **Money.** A reservation covers configured ceilings times the attempt ceiling, before any
  request. `reconcile` replaces a hold only on proof from the gateway log, and never on a token
  mismatch.
- **Streaming.** Text is released only after the serving model is confirmed as allowed.
  `model_switched` and `budget_warning` can arrive between streamed pieces. Both clients
  handle this (#11, #20), confirmed live in `live-streaming-clients.json`.
- **Switches.** `docs/OPTIMIZATIONS.md` lists all nine, the naive baseline and the safety limits
  that no switch turns off.
- **Contracts.** `docs/API.md`, `docs/openapi.json` and `docs/event.schema.json`. Additive changes
  only.

## 6. Known limitations

1. **No live run ended `completed_verified`.** That needs the Docker sandbox and exact
   verification commands (WS4, handoff G05).
2. **The research benefit is not measured.** Research cost is recorded per request (FR-62), but
   whether it saves more than it costs needs the frozen tasks, run with research on and off.
3. **Free tiers are unreliable for benchmarks:**
   - `gemini-2.5-flash` is refused for new keys, and other Gemini models returned 503 or hung;
   - Groq `qwen/qwen3.8-27b` allows only 1,000 output tokens per minute;
   - `groq/openai/gpt-oss-120b` was reliable.
4. **Some models misreport themselves.** Once, Gemini's response body named a different model than
   Bifrost's routing header. The core trusts the header. Recording both and flagging a mismatch
   is open (FR-22).
5. **Token attribution splits the real total by character share.** The total is exact; the split
   by part is an estimate. No tokenizer was added.
6. **The reconciliation tolerance is exact by default** (`reconciliation_token_tolerance = 0`). It
   matched every live run, but the benchmark should confirm it.
7. **Slow local models.** A 7B model on a laptop CPU took 13 to 92 seconds per request, which
   needs `gateway_timeout_seconds` raised. The 3B model failed the task on the old agent loop and
   was not retried after PR #6.
8. **Not measured:**
   - policy overhead (target under 50 ms; only the confidential scan was timed, median 1.7 ms);
   - scan time on a large repository;
   - crash recovery by killing the process (tested from a prepared database).

## 7. Open items

| Item | Owner | Depends on |
|---|---|---|
| Research on-vs-off measurement | Usman | Benchmark task freeze (WS4) |
| Benchmark runs through Bifrost, with reconciliation per batch | Sharjeel, with Usman's setup | Freeze and harness |
| `GitExecutor` and the Docker sandbox | Sharjeel | — |
