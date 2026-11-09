# Client contract v1 / application 0.3.0

Generated schemas: `openapi.json`, `event.schema.json`. Regenerate with
`python scripts/export_contracts.py`. No server or provider credentials are embedded.

Every endpoint except `/health` requires `Authorization: Bearer <environment token>`.
No cookies, frontend role flags or user-supplied IDs confer authority.

| Endpoint | Implemented behavior |
| --- | --- |
| `POST /sessions` | Accept `repo_path` and optional `mode` (`interactive` or `read_only`); resolve roots, owner, classification and effective permission in core |
| `POST /sessions/{id}/messages` | Accept `text` and optional boolean `allow_research`; start one run; return `run_id` |
| `GET /sessions/{id}/events` | SSE; numeric `Last-Event-ID` or `after` cursor; owner check |
| `POST /sessions/{id}/approvals/{approval_id}` | `approve_once`, `deny`, `deny_with_feedback`; optional feedback |
| `GET /sessions/{id}/approvals/{approval_id}/preview` | Pending owner-only file approval: exact `path`, `before`, `after`, `expected_sha256`, `approval_id`; no write |
| `POST /sessions/{id}/cancel` | Cancel active run and invalidate pending approval |
| `GET /sessions/{id}/status` | Run, mode, privacy, remaining budget, pending diffs and usage |
| `GET /metrics/runs/{run_id}` | Owner-only accounting with explicit evidence/cost labels |

SSE records contain `schema_version`, `id`, `session_id`, nullable `run_id`, `type`,
`timestamp` (UTC ISO-8601) and `payload`. Unknown event types may be ignored by clients.
The numeric event ID is the cursor, not a step number. Heartbeats start with `:`.

| Event | Payload |
| --- | --- |
| `session_created` | `mode`, `confidential` |
| `run_started` | `model`, `mode`, `confidential`, `budget_remaining_micro_usd` |
| `context_prepared` | `before_chars`, `after_chars`, `compacted`, `available_tools` |
| `research_used` | Sanitized `query`, `cache_hit`, `credits`, context byte/token upper bounds |
| `tool_call` | `action` (validated tagged tool input), `class` |
| `tool_result` | `tool`, `result` (bounded text and truncation flag when applicable) |
| `approval_request` | `approval_id`, exact `action`, full bounded `diff`, `deadline`, `tool_class` |
| `approval_decision` | `approval_id`, `decision`, authenticated `user`, `feedback` |
| `approval_consumed`, `approval_expired` | `approval_id` |
| `model_switched` | `from`, `to`, `reason` (no unverified outage cause invented) |
| `budget_warning` | `remaining_micro_usd`, `limit_micro_usd`, `warning_percent`, `options` |
| `budget_hit` | `reason`, `remaining_micro_usd`, `limit_micro_usd`, `warning_percent`, `options` |
| `policy_block` | `reason` |
| `session_paused` | `reason`, optional `resume` instructions |
| `message_delta` | `text` (emits a final message, not token streaming) |
| `run_complete` | `outcome`, optional verification `note` |

On reconnect, request status first. A pending approval may exist even if the client
already stored its event cursor. Submit a decision once; a 409 means it is no longer
valid. Never fabricate local approval state or re-submit an old approval automatically.

Status also includes `budget_limit_micro_usd` and `budget_warning`, which the core works out
from the user's budget on every request. Show the warning from these fields rather than
storing it in the client: the budget belongs to the user, not the session, so the value stays
right across new sessions and when an operator changes the budget. `budget_warning` is emitted
at most once per run. Amounts include held and uncertain spend, so they are not final billing.
Current `options` values are `continue` and `cancel` on a warning, and
`raise_budget_then_resubmit` on a budget hit. Automatic downgrade to a local model is not
implemented, because the confirmed default at the limit is to pause.

Session creation and status include effective `read_only`. An operator's read-only
identity cannot be upgraded by a request. The backend blocks every gated proposal
and approval decision for read-only identities/sessions regardless of tool pruning.
Existing databases migrate interactive sessions with `read_only=false`; promotion is
sticky. Preview revalidates the persisted action and current file hash; stale/expired
or decided approvals return 409. A different owner gets 404. It is not an editable
action template; only the original approval ID can authorize the exact stored change.

Supported model actions: `list_tree`, `read_file`, `grep` (literal, capped), `repo_map`,
`apply_patch` (single exact replacement + original hash), `write_file` (hash or `absent`),
`syntax_check` (Python parser), `run_shell` (argv/purpose, when sandbox enabled),
`web_research` (catalog package/topic/error, when research enabled), and `finish`.
Writes are withheld until an actual file inspection when tool pruning is enabled.
Other unimplemented FSD tools are not advertised or accepted.

Command approvals include an `action.execution` object with the backend-selected
snapshot hash, pinned image, timeout, memory, network and writability settings.
This object is display-only; clients approve the existing ID and cannot edit it.
`diff` is empty for commands. File approvals still contain a full bounded diff.

Status/metrics include `research_usage` separately from LLM `usage`. Usage `details`
is an object containing cache tokens/hit, latency, request ID, source and attempt ceiling.
Amounts in `held_for_reconciliation`/`uncertain` are conservative holds, not final costs.
`run.options` is the persisted JSON string of typed run options, not model permissions.

`completed_verified` requires every operator-configured verification command to pass
on the same unchanged sanitized snapshot. `completed_unverified` records no such
complete check set. A model-written summary never overrides the authoritative outcome.

The Bifrost adapter uses JSON action replies. All replies are revalidated in core;
the prompt is guidance, not security enforcement. WS2 can add native tool-call support
later without moving policy into a client.
