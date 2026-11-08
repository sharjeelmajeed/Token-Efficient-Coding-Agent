# M3 client and PR review scope — 2026-11-03

Continue the confirmed FSD sections 9.4 and 9.5 from application 0.2.0. Preserve
M1/M2 behavior and keep all policy in the shared FastAPI core.

## Deliverables and boundaries

- TypeScript VS Code extension: session-bound chat, resumable event timeline,
  status bar with model/budget/privacy, model-switch notices, native read-only
  before/after diff previews, approve once/deny/feedback, command approval detail,
  cancellation and a run report. No direct provider calls or client-side file edits.
- Credentials in VS Code SecretStorage; trusted workspace required; operator/user
  connection settings cannot be overridden by repository settings. Untrusted model
  text is rendered as text, with a restrictive webview CSP and no external resources.
- Backend read-only identity/session mode blocks every gated tool and approval.
  A caller can reduce its permissions, never upgrade an operator's read-only policy.
- Owner-checked approval preview endpoint derives current before/after text using
  the already-persisted action/hash. It never changes the repository.
- PR runner consumes GitHub PR metadata, bounded changed-file patches and targeted
  file snapshots; honors immutable head identity and repository confidentiality.
  It calls the same core in read-only mode and emits a review summary and usage report.
- GitHub workflow triggered on opened/updated PRs: trusted base code only, minimal
  permissions, no checkout/execution of PR code, no direct provider secrets in the
  review client. Comment publication is a separate explicit deployment permission;
  no actual GitHub comment, repository or workflow deployment is authorized here.
- Complete source, locked dependencies, installable local VSIX if packaging succeeds,
  meaningful Python/TypeScript integration and security tests, setup/manual acceptance
  docs and recorded evidence. Do not call mocked or headless checks visual acceptance.

## Architecture and interfaces

VS Code and PR runner -> authenticated REST/SSE -> shared policy/agent/store ->
Bifrost/repository tools. New data: persisted session read_only flag. New trusted
user policy: read_only. Session creation may request mode=read_only. Approval preview
is owner-checked; all decisions still reference one current approval ID.

The extension uses VS Code API and native Node HTTP support; TypeScript and Microsoft
extension packaging/testing tools are development dependencies. The Python PR runner
reuses HTTPX and core schemas. No new runtime framework or SaaS service is needed.

## Verification

Test denied/read-only mutations, privilege downgrade, preview ownership/staleness,
SSE fragmentation/cursor handling, redirect/token boundaries, XSS-safe rendering,
connection changes, missing credentials, PR path/link/size limits, head changes,
comment scope and error handling. Compile and package against official VS Code APIs.
A live editor and GitHub integration are claimed only if actually exercised.

M2 live provider/Docker acceptance and billing reconciliation, remaining git tools,
benchmark results, overhead/setup timing and final submission remain visible work.
