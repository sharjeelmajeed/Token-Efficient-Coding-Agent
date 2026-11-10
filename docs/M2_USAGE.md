# M2 setup and module handoff

M2 adds no runtime dependency. Development-only `coverage==7.17.0` is locked and
installed with the existing requirements. This increment was exercised with Python
3.12.14. Keep the default fixture usable while preparing optional integrations.

## Install and verify

From the extracted project root on Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/python -m coverage run --branch --source=token_agent -m pytest
.venv/bin/python -m coverage report --include=src/token_agent/policy.py --fail-under=80
.venv/bin/python -m pip_audit --skip-editable
.venv/bin/python scripts/demo.py
```

On Windows use `.venv\Scripts\python.exe` and `.venv\Scripts\ruff.exe`; create the
venv with `py -3.12 -m venv .venv`. The Docker adapter targets Linux containers;
Windows Docker Desktop/WSL acceptance has not been exercised here.

`agent doctor --config agent.local.toml` exits 0 when the configured local prerequisites
are present, or 1 with safe missing-check information. It never claims a model request
succeeded. API tokens must already be supplied in the environment. Live optional
features are disabled by default and are checked as required only when enabled.

## Configuration

Copy `agent.toml` to ignored `agent.local.toml`. Top-level model/fallback/pricing
fields must be placed before TOML section headers. Use actual allowed models and
operator-reviewed price ceilings; no sample model name or rate should be treated
as a verified provider configuration.

The independent optimization switches are:

```toml
[optimization]
history_compaction = true
tool_pruning = true
tiered_routing = false
gateway_direct_cache = false
early_termination = true
```

Tool output is cut to `output_chars` (default 8,000) with an explicit
`...[output truncated]...` marker. To cut it to a token budget instead (FR-12), set
`tool_output_tokens` (100 to 20,000) at the top level. On each step the core converts the
budget to characters using the characters-per-token measured from the model's own
`input_tokens` on the request it just answered, so the limit follows each model's tokenizer;
a request that reports no tokens uses a cautious 3 characters per token. The limit stays
between 200 characters and the 128,000-character file ceiling, and each usage row records
the `tool_output_char_limit` it used. The split of code and prose within one output is not
tokenized separately, so the budget is an approximation of real tokens, not an exact cap.

`fast_model` selects the first inspection model when tiered routing is enabled;
a successful file read or observed error switches later steps to `standard_model`.
Confidential runs always use the configured local model/allowlist. This is a simple,
measurable routing rule, not an LLM confidence classifier.

For research, the following is a usable public catalog entry:

```toml
[research]
enabled = true
allow_confidential = false
key_env = "TAVILY_API_KEY"
max_calls_per_run = 3
max_sources = 3
cache_seconds = 3600
result_chars = 3000

[research.packages.python]
name = "Python"
version = "3.12"
domains = ["docs.python.org"]
```

Supply `TAVILY_API_KEY` through the environment. Start an explicit research-enabled
run with `agent run --research "Your repository task"`. API callers send the real
boolean `allow_research: true`; text that claims permission grants none. Automatic
research currently requires two failed tools reporting the same recognized generic
exception name. Unknown-package/new-version triggers remain pending. The model can
select only a catalog ID and enum topic/error; code, paths, task text and arbitrary
queries are never sent to search. Configure only public package names and versions.

For commands, set `[sandbox] enabled=true`, an operator-approved image reference
ending in `@sha256:<actual 64-character digest>`, and `verification_commands` as
exact argv arrays. For a stdlib Python unittest repository, a suitable check is
`["python", "-m", "unittest", "discover", "-v"]`. The image must already be installed
and contain the required language/runtime/dependencies. The runner never pulls an
image or installs packages. `agent doctor` checks the daemon and pinned image.
Do not copy a fabricated digest or use a mutable image tag.

All commands require a separate approve-once decision showing argv, image, snapshot
hash, time/memory limits and isolation. A command cannot change the original repo;
file edits happen only through separate approved patch/write tools. Tests requiring
network, `.git`, hidden configuration or in-repo writes need an explicitly designed
future execution profile; do not disable isolation to make them pass.

## File connections

| File | Responsibility |
| --- | --- |
| `src/token_agent/config.py` | Operator settings for optional research, sandbox, routing and limits |
| `src/token_agent/context.py` | Prompt schema pruning and deterministic observation compaction |
| `src/token_agent/research.py` | Fixed Tavily transport, query/result validation and cache |
| `src/token_agent/sandbox.py` | Ephemeral Docker snapshot execution and cleanup |
| `src/token_agent/engine.py` | Routes, reserves budget, requests approvals and validates outcomes |
| `src/token_agent/store.py` | Durable options, research calls/cache, usage metadata and migration |
| `src/token_agent/providers.py` | Bifrost fallback/cache headers and actual served-model metadata |
| `src/token_agent/diagnostics.py` | Local setup checks without paid requests |
| `src/token_agent/api.py`, `cli.py` | Typed research option, command approval display and metrics |
| `tests/test_research.py`, `test_sandbox.py`, `test_m2_integration.py` | Failure, privacy and end-to-end policy cases |

The CLI sends requests; it cannot authorize its own tool execution. The engine
owns decisions, the store owns durable approval/usage state, and each adapter
validates its boundary again. Provider/research output stays untrusted data.

## Live acceptance for WS1/WS2

1. Confirm gateway version, exact response model identifiers and virtual-key restrictions.
   Exercise normal, fallback, rate-limited, invalid-key and all-provider failure paths.
2. Set `gateway_attempt_ceiling` high enough for every gateway retry/fallback attempt,
   and enforce that same upper bound at the gateway. Core does not perform hidden retries.
3. Confirm confidential primary and fallback traffic stays physically local, with
   gateway-side cache/embedding egress disabled. Core headers are only one layer.
4. Exercise the Docker runner on disposable authored files: denial, pass/failure,
   timeout, cancellation, changed snapshot, missing image and forbidden network/write.
   Confirm containers are removed, and investigate any cleanup-failure identifier.
5. Research a public catalog package with `--research`, confirm source bounds and
   one billed request followed by a cache hit. Check default denial for private repos.
6. Retain raw gateway/Tavily evidence and reconcile costs before benchmarking. No
   release-quality live acceptance is inferred from mocked transport tests.
