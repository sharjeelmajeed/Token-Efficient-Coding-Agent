# Project checkpoint — 2026-11-03

The FSD is confirmed. Application 0.3.0 delivers the M3 editor and read-only PR
clients on top of M1/M2. This is not complete live acceptance or hackathon submission.

## Implemented and locally tested

- VS Code client compiles and packages into a local-install VSIX. It has session chat,
  native diff/command approval, status, model-switch notices, cancellation, reconnect
  and immutable reports; credentials use SecretStorage and workspace settings cannot
  redirect them. The client also passes an actual Node-to-core HTTP integration flow.
- Backend read-only identity/session policy blocks gated actions and approval decisions.
  Owner-only file preview is derived from the persisted action and rechecks its hash.
- PR runner fetches bounded patches and targeted excerpts from fixed GitHub API routes,
  never checks out or executes PR code, and routes through the same read-only core.
  Complete-read evidence, classification, secrets, stale commits and publish identity
  are checked. Comment publication is separate and explicitly enabled at deployment.
- Core/extension CI and a trusted-base PR workflow are supplied. Review and publish
  jobs are separately permissioned; review is disabled until a maintainer configures it.

- M1 authenticated API/CLI, ownership, durable events, approvals, cancellation,
  repository boundaries and transactional budget reservations remain operational.
- New-file creation and whole-file replacement use exact human approval, current
  hashes or atomic absent-file creation; changed files are rejected.
- Docker adapter executes approved argv in an ephemeral, read-only sanitized
  snapshot. Network is disabled, capabilities dropped, secrets excluded, image
  digest pinned, resources/time/output bounded. No host execution fallback exists.
- Verification requires every configured exact command to pass on the same unchanged
  snapshot. Syntax checks and a model's success claim cannot establish verification.
- Tavily adapter uses trusted public package/version/domain entries and fixed query
  enums; explicit typed user permission or two observed generic errors enables a call.
  Private research is off by default. Sources, credit usage and context byte bounds
  are recorded separately; results are bounded and cached per session.
- History compaction preserves original instructions and labels old observations as
  untrusted data. Tool schemas are pruned and enforced by the backend. Fast/strong
  routing, cache control and early termination have independent switches.
- Bifrost requests include explicit standard/local fallbacks and correlation IDs.
  Actual routing headers, fallback notices, cache tokens and latency are captured.
  Multiple-attempt reservations remain held pending gateway billing reconciliation.
- Confidential markers are rechecked before requests. Observing confidentiality
  promotes existing sessions for that repository; deleting a marker cannot downgrade them.
- `agent doctor` checks local prerequisites without model/research requests or secret output.
- Existing databases migrate in place; server restart still pauses unfinished work,
  expires approvals and retains uncertain spend rather than replaying side effects.

Exact verification counts, audit and coverage appear in `evidence/verification.json`.
API/CLI tests use a real loopback server. Gateway/research/container adapters use
controlled test transports. Authored host-process tests cover timeout/output handling;
no model-generated or repository command was executed on the host.

## Remaining confirmed scope

| Owner / milestone | Work still required |
| --- | --- |
| WS2 / M2 | Live Bifrost, local Ollama, restricted virtual keys, cross-provider fallback (TC-FO-01), confidential routing (TC-CF-01), gateway budget refusal and provider metadata are validated ([HANDOFF_WS1_WS2](HANDOFF_WS1_WS2.md)). Remaining: live cache behavior and rate-limit handling under benchmark load |
| WS1+2 / M2 | Live Docker isolation/cancellation acceptance; pinned image containing actual task dependencies; process-crash/orphan-container recovery |
| WS2 / M2 | Measured research cost-benefit accounting. Bifrost log reconciliation (`token_agent.reconcile`) and all four FSD 7.2 research triggers are implemented |
| WS1 / M2 | Remaining FSD tools including git, fuller restart recovery, native provider tool calls/streaming where needed |
| WS1+4 / M2–M4 | All nine optimization techniques are switchable (`docs/OPTIMIZATIONS.md`); measure overhead and setup targets |
| WS3 / M3 | Done: VS Code host test and installed-editor acceptance 14/14 on the fixture ([ws3-editor-acceptance](evidence/ws3-editor-acceptance.md)); CLI and VS Code streaming, budget status, readable notices and CLI git approvals; live model switch between streamed pieces in both clients ([live-streaming-clients](evidence/live/live-streaming-clients.json), recorded by WS2). Remaining: git approvals in VS Code (waits for the git executor and a preview decision), `agent bench` wiring (waits for the benchmark harness), live PR review on a team PR (waits for the workflow and its secrets) |
| WS4 / M3 | Authorized GitHub workflow deployment, real fork/private-routing trials and opt-in comment publication |
| WS4 / M4 | Frozen 15–20 tasks across >=3 repositories and >=2 languages, >=3 repeats/system/task; baseline, optimized and ablations; medians/spread and quality parity |
| Team / M4 | Fresh-machine rehearsal, demo recording, final evidence and actual submission confirmation |

## Evidence boundaries

`completed_verified` means configured commands passed against the unchanged sanitized
snapshot. It does not prove correctness beyond those checks or the full original
repository environment. Hidden/protected files, symlinks and dependency directories
are excluded; unsupported/binary/oversized accessible files cause rejection.

Fixture tokens are synthetic and `benchmark_evidence` remains false. UTF-8 byte bounds
for research are not actual tokenizer measurements. Gateway response usage and price
ceilings are not reconciled provider invoices. No token-saving percentage is claimed.

VS Code development/packaging packages were installed and the VSIX was built. The host
smoke test passed on VS Code 1.144.0, and the installed VSIX passed manual acceptance in
VS Code 1.145.2 against the fixture core only; that run is not live-model evidence.
Bifrost, Ollama and Tavily were exercised live for WS2 acceptance (`docs/evidence/live/`); no Docker
sandbox, external GitHub repository or workflow was deployed. No real comment was posted. Supply real-service credentials only through the team environment, never through
chat/source.

The deadline, portal and team names remain unspecified. These do not reopen scope.
Next joint checkpoint: WS1 validates Docker/git remaining scope, WS2 validates
Bifrost/local routing and real research, WS3 rehearses the packaged editor client,
and WS4 trials the PR workflow and freezes the benchmark before any reported savings.
