# Confirmed scope

Source: `docs/source/FSD_Token_Efficient_Coding_Agent.pdf`.
The user confirmed this FSD on 2026-11-02. Treat its requirements as accepted;
do not ask for reconfirmation or silently drop an interface or safety requirement.

## Product

A coding agent that completes repository tasks correctly with fewer tokens.
One Python/FastAPI core owns policy, execution, state and accounting. CLI,
VS Code extension and GitHub PR reviewer are thin clients of that core.
All real model traffic goes through Maxim AI Bifrost. Tavily supplies selective,
sanitized research. Confidential repositories remain local, including fallbacks.
This is a hackathon application, not a public multi-tenant SaaS or a new model.

## Functional requirements

- Persist sessions, history, steps, cancellation and resumable event cursors.
- Validate every action; classify tools in code; constrain repository access.
- Require specific human approval for writes, commands and mutating git actions.
- Use minimal context, targeted reads, capped outputs, patches, compaction,
  relevant tool schemas, cache controls, routing tiers and verified termination.
- Route via Bifrost with failover notices, virtual keys, budget checks and logs.
- Block cloud egress for confidential repositories independently in core and gateway.
- Use Tavily selectively and record its added context, sources and costs separately.
- Deliver CLI, VS Code extension and a read-only PR analysis workflow.
- Measure baseline, optimized and per-technique ablations on a frozen task suite.

## Non-functional requirements

Policy overhead target <50 ms excluding network; clean setup target <15 minutes;
policy coverage target >=80%; structured audit events; configuration plus environment
secrets; deterministic offline test provider. These are targets until measured.

## Architecture and data

Clients -> authenticated REST/SSE -> agent service -> policy -> repository tools
or Bifrost/Tavily. SQLite stores users' accounting, sessions, runs, messages,
approvals and append-only events. Relationships are user -> sessions -> runs;
session -> messages/events; run -> approvals/usage. Monetary units are integer
micro-USD, avoiding floating point budget comparisons.

The trusted configuration assigns users, allowed repository roots, gateway keys,
model allowlists, rate ceilings and limits. Request bodies cannot assign ownership,
classification, permissions or budgets. Single process deployment is the initial
operating model; multi-worker execution requires a durable worker/lease design.

## Permission matrix

| Actor | Allowed |
| --- | --- |
| Config operator | Set repository roots, users, model/key policy and budgets outside model context |
| Authenticated session owner | Run/read/cancel their session; decide one exact pending approval |
| Another authenticated user | No access to someone else's sessions, events, metrics or approvals |
| Model and repository content | Propose validated actions only; never grant or change permissions |
| PR workflow identity | Read-only repository analysis; separate minimal GitHub permissions |

## UI direction

CLI first: readable task timeline, persistent model/budget/mode status, inline diff,
clear approve/deny, explicit cancellation and recoverable errors. VS Code uses native
diff views and status bar. Keep a restrained dark-neutral palette and one accent.
No custom web dashboard or separate Figma file is required by this FSD.

## Delivery milestones

1. M1: secure core/CLI vertical slice, schemas, approvals, persistence, bounded tools,
   a Bifrost adapter, deterministic fixture, tests and four-person work plan.
2. M2: live Bifrost/Ollama setup, fallback and usage reconciliation, Tavily, sandboxed
   commands/git, optimization toggles and full restart behavior.
3. M3: VS Code client and read-only GitHub workflow; integrated demo rehearsals.
4. M4: complete frozen 15-20 task benchmark over >=3 repos and >=2 languages,
   >=3 runs/system/task, ablations, quality parity and reproducible submission.

All milestones remain in scope. A successful M1 demo is not hackathon completion.
No n8n workflow, billing UI, GraphQL, model training or SaaS hosting is required.

## Engineering decisions

- Python 3.11+; validate here with the available Python 3.12 environment.
- FastAPI/Pydantic; HTTPX for one controlled gateway transport; SQLite stdlib.
- Typer/Rich for CLI; TypeScript/VS Code API in M3.
- Default budget exhaustion behavior: pause, as FR-34 specifies.
- Generated credentials belong in environment variables and are never committed.
- Real provider validation needs team-owned keys and local-model hardware.
- Scope is confirmed. Deadline and participant names can be added without blocking M1.

## Release gate

Trace all FSD Must requirements to passing tests; no policy bypass; benchmark quality
at least baseline with token/cost evidence; both interactive clients demonstrate
approvals/model switches; judges can reproduce from a fresh checkout. Failures and
unverified integrations must remain visible in the status report.
