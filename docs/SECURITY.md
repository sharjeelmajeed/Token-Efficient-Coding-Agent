# Security boundaries

## Backend enforcement

Authentication, repository roots, ownership, model allowlists and budgets live in
trusted backend configuration. Request/model/repository text cannot grant authority.
Every session/event/metrics/approval route checks ownership. Credentials are read
from environment variables, never committed or echoed in validation/provider errors.

Confidential classification is sticky across sessions and checked before model
requests. Private mode requires a loopback gateway and an explicitly local allowlist,
including fallbacks. Marker detection during an in-flight request pauses further
processing; it cannot undo data already sent before the marker existed.

Reads reject traversal, protected paths, links, binary/oversized content and obvious
or known secrets. Writes require a one-use approval tied to the exact hash/diff.
File creation uses atomic no-overwrite linking; replacement rechecks the hash before
atomic replacement. No blanket approval or model-provided permission exists.

Commands require approval of argv, pinned image and sanitized snapshot hash. Docker
uses no network, read-only root/repository, a non-root user, dropped capabilities,
no-new-privileges, bounded CPU/memory/PIDs and a small temporary filesystem. Model
keys, proxy settings and Docker credentials are not inherited. There is no host
command fallback. Timeout/cancellation triggers removal; cleanup failure pauses
instead of returning a verified result. Only operator-configured commands can verify,
and every configured command must pass on the same unchanged snapshot.

Research queries contain only operator-vetted public package/version data and fixed
topics/exception names. Private research is disabled by default. Explicit typed user
permission or repeated observed errors is required; arbitrary task strings cannot
grant permission or enter a query. HTTPS official-domain results are filtered,
size/secret checked and kept as untrusted observations. Cache scope is one session.
Calls, including uncertain failures, count toward the run limit. Search credits are
separate from LLM dollars; no actual tokenizer savings are inferred from byte bounds.

Budget reservations use SQLite write transactions. Ambiguous calls and multi-attempt
requests retain conservative holds. Restart pauses work, expires approvals and
retains uncertain reservations. Audit events have append-only SQL triggers. Response
sizes, redirects, output and context are bounded; environment proxies are disabled.
History compaction relies on backend-written origin metadata, so a user message
shaped like a tool result cannot erase or promote its own authority.

## Explicit trust and deployment limits

The VS Code extension accepts connections only from user application settings, uses
SecretStorage and requires a trusted workspace. Redirects, response/event sizes and
timeouts are constrained. Model text uses DOM textContent with a restrictive CSP;
it cannot become a webview script or invoke commands. Native diff documents are
read-only; the server still owns every mutation/approval decision. The editor and
its extension host are trusted, including their network/proxy configuration.

Read-only is persisted and also derives from operator identity. It cannot be lifted
by session input or model content. The PR runner uses that identity, disables research
and sandbox execution, and never checks out or executes PR content. It validates
metadata/paths, bounds text, checks secrets, and preserves private/base/head-marker
classification. Partial/truncated observations cannot count as complete patch coverage.

The PR workflow uses trusted base code on a hosted runner. Review has contents/PR
read permission; publication is a separate opt-in job with PR-write permission and
no model key. PR titles, branches and filenames are not interpolated into shell code.
Dependencies come from the trusted base lockfile. Keep this boundary when adapting
the workflow. A private/local-only review cannot use a remote cloud gateway merely
because the runner is automated. Per-run SQLite budgets do not cap aggregate jobs;
configure separate gateway-side spending restrictions.

Publisher validation does not make a model's findings correct. It suppresses duplicate
bot comments, limits/scans report content and rechecks both commits, but GitHub offers
no atomic issue-comment/head compare. Comments name the reviewed commit so a later
push is visible. All GitHub interactions have only been tested with controlled HTTP
transports here; no external workflow or comment was executed.

The operator, configured Docker image/daemon and host filesystem are trusted. Local
path/no-follow checks do not constitute OS isolation against a hostile process
concurrently replacing parent directories. Containers reduce repository-code exposure
but are not a guarantee against kernel/container-runtime exploits. Use an isolated
worker host for untrusted submissions. Docker deployment was not available to test here.

Sanitized snapshots exclude protected/hidden files, links and dependency directories.
They support bounded UTF-8 repositories; unsupported accessible files fail closed.
A passing outcome covers only configured checks in that snapshot, not universal
correctness or a complete reproduction of every repository environment.

Abrupt process/host death can leave a container pending operator cleanup; automatic
orphan reconciliation and durable multi-worker leases are still pending. One core
process owns a database. Application restart never automatically replays a write.
SQL triggers do not protect against a privileged database administrator.

Secret scanning is heuristic plus configured-secret matching, not complete DLP.
A local model name does not prove physical locality: validate gateway routing,
virtual keys, cache/embedding policy and deployment egress before confidential use.
Core detection of a wrong returned model cannot undo the gateway's prior request.

Price ceilings and configured attempt counts are estimates, not a hard billing cap.
Gateway retries must obey the same attempt limit. Reasoning tokens, failed attempts,
cache billing and actual logs require reconciliation before real-money/savings claims.
Research has a separate call/credit ledger and is not included in the LLM dollar budget.

## Verification

See `tests/` and `evidence/verification.json` for approval/ownership, stale writes,
links/traversal, late confidentiality, fallback budgets, sanitizer/cache isolation,
snapshot integrity, cancellation/timeout, schema validation and persisted evidence.
Policy coverage is reported for `policy.py` specifically; it is not a claim that every
security boundary is fully covered. CLI subprocess coverage is not in the in-process
coverage report, although CLI integration tests execute and assert its behavior.
