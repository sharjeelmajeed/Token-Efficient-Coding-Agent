# Release checklist

Release candidate: `main` at `fe20a70` (application version 0.3.0), 2026-11-17. Each line says what
is done, with its evidence, or what is still open and who can close it.

## 1. Try it in 10 minutes

On Windows, with Python 3.12 and Node 24 installed:

1. Set up the project as in the [README](../README.md). The clean-machine run took 10.3 minutes
   ([clean-machine-setup.json](evidence/clean-machine-setup.json)).
2. Run `python scripts/make_demo.py`, then `agent doctor --config agent.toml`. Every required
   check passes; no Docker image or API key is needed.
3. Follow the CLI part of [DEMO_WS3.md](DEMO_WS3.md): run, deny with feedback, run again,
   approve, status. It works offline with the explicit fixture
   ([demo-rehearsal-cli.txt](evidence/demo-rehearsal-cli.txt)).

`agent.toml` keeps the Docker sandbox off. `agent.sandbox.toml` enables it with the
digest-pinned benchmark image, which needs Docker and a one-time pull of the private GHCR
package.

## 2. Done

| Area | Evidence |
|---|---|
| 605 tests, 0 failures; policy coverage 100% (overall 85.4%) | [verification.json](evidence/verification.json), [junit.xml](evidence/junit.xml) |
| 0 known vulnerabilities (53 Python, 193 npm dependencies) | [dependency-audit.json](evidence/dependency-audit.json), [npm-audit.json](evidence/npm-audit.json) |
| Live gateway, fallback, confidential routing, budget cap; costs reconciled | [HANDOFF_WS1_WS2.md](HANDOFF_WS1_WS2.md), `evidence/live/` |
| Live research and its cost (research on vs off, all 18 tasks) | [RESEARCH_COMPARISON.md](RESEARCH_COMPARISON.md) |
| Docker sandbox: 18/18 live checks; pinned benchmark image with 18/18 tasks valid | [docker-sandbox.json](evidence/live/docker-sandbox.json), [benchmark-image.md](evidence/live/benchmark-image.md) |
| A live run ending `completed_verified` (scratch repository) | [completed-verified-run.json](evidence/live/completed-verified-run.json) |
| Live git approvals in the CLI: commit, branch, push denied | [live-git-cli.json](evidence/live/live-git-cli.json) |
| VS Code: host test and installed-editor acceptance 14/14 on the fixture | [ws3-editor-acceptance.md](evidence/ws3-editor-acceptance.md) |
| Benchmark report with its limitations | [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md) |

## 3. Still open

| Item | Who | Notes |
|---|---|---|
| VS Code part of the live git check | A team member in VS Code (about 10 minutes) | Steps in [DEMO_WS3.md](DEMO_WS3.md), "Live git approval check". Then set `live_git_verified` |
| Demo recording | Team | Follow [DEMO_WS3.md](DEMO_WS3.md) |
| Branch protection on `main` | Repository owner (admin rights) | Required checks: `core (3.11)`, `core (3.12)`, `extension` |
| Submission | Team | The deadline and portal are not yet recorded in [STATUS.md](STATUS.md). Submit this commit and save the confirmation |
| Benchmark image access | Repository owner | The GHCR package is private; make it public or document the token step for judges |

## 4. Known limitations (not blockers for submission)

- **No full benchmark result and no claimed saving.** The 594-run campaign needs paid model
  credits ([BENCHMARK_REPORT.md](BENCHMARK_REPORT.md)).
- **Sandboxed runs on the frozen tasks are blocked.** `Repository.snapshot()` refuses every
  task repository: each contains a binary file, a file over 128,000 bytes, or a file matching
  the secret pattern. Lifting this needs a reviewed WS1 design (a byte-faithful snapshot or a
  benchmark execution profile), not a weaker policy ([STATUS.md](STATUS.md)).
- **Policy overhead is over 50 ms on a 4,000-file repository** (109 ms policy work, median); it
  is met on small and medium repositories.
- **The live PR-review workflow trial has not run.** It posts to GitHub and needs a team test
  repository.
