# Four-person execution plan

Names can replace WS1-WS4 after the team assigns owners.

| Owner | Owned files/modules | Acceptance and handoff |
| --- | --- | --- |
| WS1: core lead | `src/token_agent/api.py`, `engine.py`, `store.py`, `policy.py`, `models.py`, `repo.py`, `sandbox.py`, `context.py` | Authenticated API, persisted state, approvals, Docker acceptance; versioned contracts to WS3 |
| WS2: AI/integrations | `src/token_agent/providers.py`, `research.py`, future `infra/` | Bifrost/local live acceptance, fallback and log reconciliation, Tavily; adapter transport tests |
| WS3: clients | `src/token_agent/cli.py`, `clients/vscode/`, `github_client.py`, `pr_review.py` | CLI/editor/PR client acceptance; use contracts, never duplicate policy |
| WS4: evaluation | `tests/`, future `benchmarks/`, `docs/`, CI | Frozen task suite starts immediately; correctness, failure/security tests, repeated measurements, demo and submission |

Every member writes their module tests. WS4 owns cross-module acceptance and reports,
not the entire team's testing burden. WS1 reviews integration changes; WS2 reviews
WS1 changes. WS4 can own the PR workflow wiring to reduce WS3 overload.

## Merge rules

1. One repository; protected `main`; short branches `feature/core-*`,
   `feature/gateway-*`, `feature/clients-*`, `test/evaluation-*`.
2. Freeze `docs/openapi.json` and `docs/event.schema.json` early. Shared changes need
   agreement from affected owners and a compatible version update.
3. Small pull requests with problem/result, exact verification, risks and screenshots
   only when UI changes. One other person reviews; automated checks must pass.
4. Update from `main` before merging. Owners resolve conflicts together and rerun
   affected integration tests. Never overwrite somebody else's folder wholesale.
5. Integrate one real flow every day; reserve the final portion of the event for
   measured evaluation, fresh-clone rehearsal and submission upload.

## Parallel order

WS1 publishes schemas and core slice. WS2 validates gateway accounts/configuration.
WS3 uses the deterministic provider/core immediately. WS4 freezes benchmark tasks
and baseline before optimizations. The first joint checkpoint is a proposed patch,
human approval, actual file change and verified result on a disposable repository.

## Submission ownership

WS1: runnable source and architecture. WS2: service setup and integration evidence.
WS3: client UX and recorded demo. WS4: benchmark/test report, README verification,
submission checklist. One assigned person submits and records the confirmation.

The deadline and official portal are not provided; no invented calendar schedule
or assertion that submission has occurred is permitted.

## Concrete merge routine

After the team creates its shared Git repository, each member starts from current
`main` and works on the branch for their owned module. Example for WS2:

```bash
git switch main
git pull --ff-only
git switch -c feature/gateway-reconciliation
```

Commit only the intended files, then update the branch before opening its PR:

```bash
git add src/token_agent/providers.py tests/test_providers.py
git commit -m "Reconcile gateway usage with request IDs"
git fetch origin
git rebase origin/main
```

Resolve a conflict in the affected file with its owner, stage that file and run
`git rebase --continue`; `git rebase --abort` returns to the pre-rebase branch.
Do not force-push a shared branch. Run the documented lint, coverage and affected
client/integration checks. Push the feature branch with `git push -u origin HEAD`,
open a PR, and have a second person review the behavior and test evidence.

Merge approved PRs into `main` using the team's squash-merge rule, one integration
at a time. Everyone updates from `main` before starting the next branch. Lockfile,
shared schema and configuration changes belong in one coordinated PR to avoid
four conflicting copies. WS1 checks the working end-to-end demo after merges;
WS4 records the submission candidate only when its checks and evidence are complete.
These commands are guidance; no remote branch or PR was created by this package.
