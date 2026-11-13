# Benchmark report

**Status, 2026-11-14.** The benchmark machinery is built, validated and exercised live. **The full
benchmark (baseline against optimized, plus per-technique ablations) has not been run, so no
token or cost saving is claimed.** Running it needs paid model credits (section 5). Everything below
is a measured result with its evidence file, or is stated as not measured.

## 1. Frozen task suite

| | |
|---|---|
| Tasks | 18 real fix or feature commits from 3 repositories and 2 languages: `pallets/click` (8) and `pallets/jinja` (5) in Python, `expressjs/express` (5) in JavaScript |
| Freeze | `7ac9085` ([`benchmarks/tasks/manifest.json`](../benchmarks/tasks/manifest.json)) |
| Validity | Each task's check fails with no agent work and passes with the reference change (`benchmarks/validate_tasks.py`): 18/18 on Windows with CPython 3.12 ([`validation.json`](../benchmarks/tasks/validation.json)) |
| Interpreter | The checks need CPython 3.12. On 3.13, TASK-05, 11 and 12 fail even with the reference change (#45) |
| Platform | TASK-03 listed 21 Windows-only tests, so it could not pass on Linux. PR #50 removes them, after the freeze, and records why; with that change all 18 tasks validate inside the Linux benchmark image |

## 2. Systems compared

`docs/OPTIMIZATIONS.md` defines a naive baseline, the optimized system with nine switchable
techniques (TO-01 to TO-09), and one ablation per technique: 11 systems in all. The harness
(`benchmarks/harness.py`, `agent bench`) runs them on the frozen tasks with the same models,
prices, budgets and safety settings. It reconciles every run's cost against the Bifrost log, and
it marks a report as benchmark evidence only when every run went through the live gateway and
reconciled.

**Not run:** 11 systems × 18 tasks × 3 repeats. See section 5.

## 3. What is measured

### 3.1 Research on versus off (G08)

Cohere Command A, all 18 tasks, every run reconciled
([`RESEARCH_COMPARISON.md`](RESEARCH_COMPARISON.md)):

| | Research off | Research on |
|---|---|---|
| Passed, 18 tasks | 0 / 18 | 0 / 18 |
| Median tokens per run | 3,911 | 15,374 (3.9×) |
| Passed, TASK-03/09/15 × 3 | 0 / 9 | 1 / 9 |

The cost side is measured; the benefit side is not, because the model solves almost nothing either
way.

### 3.2 End-to-end verified run

On a scratch repository (not a benchmark task), Cohere fixed a bug. The configured
`python -m unittest discover -v` then passed in the Docker sandbox on the unchanged snapshot, so the
run ended `completed_verified`: 4 steps, 7,441 input and 275 output tokens, all 4 cost rows
reconciled ([`completed-verified-run.json`](evidence/live/completed-verified-run.json)).

### 3.3 Cost accounting

| Check | Result | Evidence |
|---|---|---|
| Fallback across providers (TC-FO-01) | Reservation held, then reconciled to the gateway's cost: 4/4 rows; $0.3128 held, $0.0042 settled | [`tc-fo-01-reconciliation.json`](evidence/live/tc-fo-01-reconciliation.json) |
| Standard-model run | 4/4 rows reconciled; $0.2103 held, $0.0009 settled | [`final-main-standard-reconciliation.json`](evidence/live/final-main-standard-reconciliation.json) |
| Gateway budget cap | Requests 1–4 allowed, request 5 refused with 402 at the $0.0002 cap | [`gateway-budget-cap.json`](evidence/live/gateway-budget-cap.json) |
| Streaming through both clients | Core and Bifrost token counts match, including the fallback child rows | [`live-streaming-clients.json`](evidence/live/live-streaming-clients.json) |
| Token attribution | 4,602 real input tokens split by prompt part; research text 1,229 of them | [`token-attribution.json`](evidence/live/token-attribution.json) |

### 3.4 Safety

| Check | Result | Evidence |
|---|---|---|
| Confidential routing (TC-CF-01) | 0 requests reached a non-local model; 3/3 core requests matched local gateway rows | [`tc-cf-01-injection.json`](evidence/live/tc-cf-01-injection.json) |
| Docker sandbox | 18/18 live checks: no network, read-only repository, non-executable tmpfs, timeout and cancel cleanup, secrets withheld, uid 65534, no pulling | [`docker-sandbox.json`](evidence/live/docker-sandbox.json) |
| Git approvals (CLI) | Commit and branch approved and made; push denied, nothing pushed. The VS Code part is pending | [`live-git-cli.json`](evidence/live/live-git-cli.json) |
| Policy tests | 600 tests, 0 failures; policy line and branch coverage 100%, overall 85.4% | [`verification.json`](evidence/verification.json) |

### 3.5 Non-functional targets

| Target | Result | Evidence |
|---|---|---|
| Policy overhead < 50 ms, excluding network | Policy work median 10.4, 15.0 and 109.4 ms on 1, 126 and 4,000 files. Met on small and medium repositories; missed on the 4,000-file one, where the per-request rescan dominates. Total core work between requests: 48.3, 119.8 and 518.0 ms | [`policy-overhead-release.json`](evidence/policy-overhead-release.json) |
| Clean setup < 15 minutes | 10.3 minutes on a fresh clone (3.5 minutes without tests) | [`clean-machine-setup.json`](evidence/clean-machine-setup.json) |
| Policy coverage ≥ 80% | 100% | [`verification.json`](evidence/verification.json) |

## 4. Limitations

1. **No baseline-against-optimized result.** None of the nine techniques' savings is measured, and
   none is claimed.
2. **No benchmark task has ended `completed_verified`.** The only verified run is on a scratch
   repository (section 3.2).
3. **The models available for free could not solve the tasks.** Cohere passed 0 of 18. Most other
   free models could not follow the agent's JSON-in-text action format, rewrote or shortened the
   prompt, or were rate-limited after a few calls ([`RESEARCH_COMPARISON.md`](RESEARCH_COMPARISON.md)
   section 3).
4. **TASK-03 changed after the freeze** (PR #50). Its prompt, reference change and remaining 58
   checks are unchanged.
5. **Policy overhead exceeds the target on very large repositories** (section 3.5).
6. **Live evidence is from pilots**, each recorded with `benchmark_evidence: false`, on one
   Windows laptop.
7. **The live PR-review workflow trial is not done.** It posts to GitHub and needs a team test
   repository.

## 5. How to run the full benchmark

Requirements:
- a model that solves a meaningful share of the tasks, with credits: about $1–5 for one model,
  from our measured token use;
- Bifrost running;
- the benchmark image or the environments from `benchmarks/setup_environments.py`.

```
agent bench --task TASK-01 ... --task TASK-18 --system naive_baseline --system optimized ... \
    --repeats 3 --python <python 3.12> \
    --node-modules node-express5=<path> --node-modules node-express4=<path> \
    --gateway-log <bifrost logs.db> --output-dir benchmarks/results
```

Report only runs where `benchmark_evidence` is true. Include:
- pass rate, medians and spread;
- cost per successful task;
- every failure;
- the freeze ID and the TASK-03 change;
- that the auto-approver answered approvals.

The research comparison reruns unchanged with `benchmarks/research_comparison.py`.
