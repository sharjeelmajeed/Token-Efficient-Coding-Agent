# M2 implementation scope — 2026-11-02

Continue the confirmed FSD and M1 core; preserve working API/session behavior.

This increment adds selective Tavily research with a trusted package catalog,
sanitized generic queries, private-repository default-off policy, bounded results,
per-session cache and per-run call limits. Explicit research permission is a typed
client option; repository/model text cannot grant it.

Approved commands run in an ephemeral, read-only repository snapshot using Docker,
with no network, no inherited secrets, dropped capabilities, resource limits and
timeout/cancellation cleanup. Only a configured exact verification command on the
unchanged snapshot can establish a verified run outcome. No host-shell fallback.

Add guarded file creation, deterministic history compaction, tool schema pruning,
configured fast/strong routing, explicit local/standard fallback chains, direct
gateway cache controls, request/usage metadata and a diagnostic command. Preserve
budget reservations for potentially billed fallback attempts until reconciliation.

Validation includes transport tests, approval integration tests, concurrency and
negative-policy cases, CLI demonstration, lint, dependency audit and measured policy
coverage. No new runtime package is planned; coverage is a development-only tool.

The current host lacks Docker and provider/research credentials. Live gateway,
Tavily, local-model and Docker execution acceptance remain external prerequisites.
No service will be claimed installed or verified on the strength of a mock test.
Full git tooling, gateway log reconciliation, VS Code, PR workflow and benchmark
measurement remain in scope for subsequent work; they are not fake endpoints here.
