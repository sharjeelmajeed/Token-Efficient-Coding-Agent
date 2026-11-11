# Frozen benchmark task suite

**Suite name:** `token-efficient-coding-agent-benchmarks`
**Status:** frozen by PR #33. Record its merge commit in the repository README.
**Task count:** 18 tasks.
**Repositories (3):**
- `pallets/click`: 8 tasks;
- `pallets/jinja`: 5 tasks;
- `expressjs/express`: 5 tasks.

**Languages:** Python (13), JavaScript (5).
**Categories (FSD 10.3 mix):** bug fix (6), small feature (6), refactor (3), test addition (3).

---

## 1. Timing deviation disclosure

> [!IMPORTANT]
> FSD section 10.3 says benchmark tasks should be defined and frozen before optimization
> begins, to avoid tuning to the test set. In milestones M1 to M3, the core optimization
> techniques (TO-01 to TO-09) were implemented before this suite was frozen (handoff G01).
>
> To preserve evaluation integrity, the suite consists of held-out tasks from public
> repositories (`pallets/click`, `pallets/jinja` and `expressjs/express`) that were not used
> during core development or testing. All comparisons (naive baseline, optimized and the
> nine single-switch ablations) run against these frozen tasks only.

---

## 2. How the tasks were chosen and checked

Every task is a **real change from the repository's own history**:

1. **Candidates:** commits that made a small source change (40 lines or fewer, in at most two
   source files) together with a test change. Documentation, release, dependency and
   platform-specific commits were excluded.
2. **Start state:**
   - **Bug fixes, small features and refactors:** the task starts at the commit *before* the
     fix. The agent must make the change.
   - **Test additions:** the task starts at the fix commit with the fix's tests removed. The
     agent must write the tests.
3. **Automated check** (`benchmarks/checks.py`). The repository's own tests run directly
   through pytest or mocha, never through a shell:
   - `fail_to_pass`: after the agent finishes, the task's test files are replaced by the
     reference commit's versions. Every `fail_to_pass` test (failing before the fix) and every
     `pass_to_pass` test (existing behaviour) must pass. The agent cannot pass by editing
     these tests.
   - `test_addition`: the task's test files must pass on the fixed code, and at least one of
     them must fail when the source files are reverted to the commit before the fix. So the
     new tests must really test the change.
4. **Validation:** `benchmarks/validate_tasks.py` proves, in fresh clones from GitHub, that
   every task's check **fails with no agent work** and **passes with the reference change**.
   The result is in [`tasks/validation.json`](tasks/validation.json): 18 of 18 valid. Any
   change to the suite must be revalidated before use.

Each task file records:
- the start commit (`commit`) and the reference commit (`reference_commit`);
- a link to the real change (`source`);
- the prompt, its environment and its check.

Prompts describe the required behaviour, not the solution. Agents cannot read `.git`, so the
reference commit stays hidden.

## 3. Test environments

| Environment | Used by | Pinned in |
|---|---|---|
| `python` | click and jinja tasks (pytest, run from each repository's `src`) | `environments/requirements-python.txt` |
| `node-express5` | express 5.x tasks (mocha) | `environments/node-express5/package-lock.json` |
| `node-express4` | express 4.x tasks (mocha) | `environments/node-express4/package-lock.json` |

```
python benchmarks/setup_environments.py <directory>     # prints the arguments below
python benchmarks/validate_tasks.py --python <python> \
    --node-modules node-express5=<path> --node-modules node-express4=<path> \
    --out benchmarks/tasks/validation.json
```

Node.js 18 or later and git are required. Validation was run on Windows 11 with Python 3.12
and Node.js 24. Before the benchmark, rerun it on the benchmark machine or in its Docker
image (G05).

## 4. Tasks

| Task | Repository | Category | Title | Real change | Check |
|---|---|---|---|---|---|
| `TASK-01` | click | bug_fix | show_envvar with a sequence or empty envvar | [`c53d3732b9`](https://github.com/pallets/click/commit/c53d3732b98c764932e146c007146ad402acb229) | pytest: 9 fail-to-pass, 667 pass-to-pass |
| `TASK-02` | click | bug_fix | Zsh completion items containing colons | [`a1235aacb1`](https://github.com/pallets/click/commit/a1235aacb1be55dc66ddcfefbf64dec44b6ab54d) | pytest: 3 fail-to-pass, 47 pass-to-pass |
| `TASK-03` | click | small_feature | Option to hide the progress bar | [`fcd85032cf`](https://github.com/pallets/click/commit/fcd85032cff78aa536a6d2b455fb83bfcc02b228) | pytest: 1 fail-to-pass, 79 pass-to-pass |
| `TASK-04` | click | small_feature | multiple=True for flag options | [`b36e3ede78`](https://github.com/pallets/click/commit/b36e3ede781b388ae8135af61e83269e211acdf1) | pytest: 1 fail-to-pass, 115 pass-to-pass |
| `TASK-05` | click | refactor | Return value and signature of add_completion_class | [`e002dbc40c`](https://github.com/pallets/click/commit/e002dbc40ceb6a7e78dbe5e5868744690ba3120f) | pytest: 3 fail-to-pass, 37 pass-to-pass |
| `TASK-06` | click | refactor | Hide the internal UNSET sentinel in lookup_default | [`aaf99a3ee6`](https://github.com/pallets/click/commit/aaf99a3ee6935f321f755bc7c5717a91d321cd39) | pytest: 4 fail-to-pass, 18 pass-to-pass |
| `TASK-07` | click | refactor | Copy, deepcopy and pickle of Sentinel members | [`f58ca3e814`](https://github.com/pallets/click/commit/f58ca3e81424a35626c8a475eb59ab95589008ce) | pytest: 3 fail-to-pass, 7 pass-to-pass |
| `TASK-08` | click | test_addition | Tests for show_default with non-string defaults | [`d340b0c120`](https://github.com/pallets/click/commit/d340b0c1202284a1b9d0ca892549208527d586ce) | pytest: agent tests must catch the reverted fix |
| `TASK-09` | jinja | bug_fix | pass_context tests with the select filter | [`d05bd3858c`](https://github.com/pallets/jinja/commit/d05bd3858c3f4990e91201dae058147caf317462) | pytest: 1 fail-to-pass, 52 pass-to-pass |
| `TASK-10` | jinja | bug_fix | Reject invalid characters in xmlattr keys | [`d655030770`](https://github.com/pallets/jinja/commit/d655030770081e2dfe46f90e27620472a502289d) | pytest: 7 fail-to-pass, 124 pass-to-pass |
| `TASK-11` | jinja | bug_fix | Subscripts after filters and calls in async mode | [`a150ff749a`](https://github.com/pallets/jinja/commit/a150ff749a0347c41e8e353c2ce2063e8f84ab42) | pytest: 2 fail-to-pass, 56 pass-to-pass |
| `TASK-12` | jinja | small_feature | PackageLoader with a single-module package | [`eec0db8642`](https://github.com/pallets/jinja/commit/eec0db8642b25691867890f14fc6c9ca04c8a8c9) | pytest: 3 fail-to-pass, 37 pass-to-pass |
| `TASK-13` | jinja | test_addition | Tests for sandbox protection of clear and pop | [`b512058270`](https://github.com/pallets/jinja/commit/b5120582706b9e997cfd3277b02a32fd35b1c9d4) | pytest: agent tests must catch the reverted fix |
| `TASK-14` | express | bug_fix | Settings must ignore Object.prototype values | [`c17fe05861`](https://github.com/expressjs/express/commit/c17fe058613dc7dfb7779fbe68a9738a108fe408) | mocha: 4 fail-to-pass, 22 pass-to-pass |
| `TASK-15` | express | small_feature | Conditional revalidation for QUERY requests | [`ae6dd37680`](https://github.com/expressjs/express/commit/ae6dd37680e3a00618d6c8a3e522f0ee4eeba1a4) | mocha: 1 fail-to-pass, 5 pass-to-pass |
| `TASK-16` | express | small_feature | Multiple links with the same rel in res.links | [`caa4f68ee8`](https://github.com/expressjs/express/commit/caa4f68ee8d32474676fa29cc2086dcc1d62208b) | mocha: 1 fail-to-pass, 2 pass-to-pass |
| `TASK-17` | express | small_feature | Allow null or undefined options in app.render | [`c9ecf7b658`](https://github.com/expressjs/express/commit/c9ecf7b658388ccaa2b8470aabad92aabde458a2) | mocha: 1 fail-to-pass, 20 pass-to-pass |
| `TASK-18` | express | test_addition | Tests for res.location with non-string values | [`a003cfab03`](https://github.com/expressjs/express/commit/a003cfab034fbadb1c78ae337ee8ab389adda217) | mocha: agent tests must catch the reverted fix |

## 5. Evaluation protocol

Each task is evaluated across 11 system configurations defined in `docs/OPTIMIZATIONS.md`:
1. **Naive Baseline:** all nine optimization switches set to `false`.
2. **Optimized Agent:** full default configuration.
3. **9 Single-Switch Ablations:** exactly one technique turned `false` per ablation (`repo_map`, `targeted_reads`, `output_truncation`, `diff_edits`, `history_compaction`, `tool_pruning`, `gateway_direct_cache`, `tiered_routing`, `early_termination`).

### Execution Controls
- Minimum 3 repeats per task per system (18 tasks $\times$ 11 systems $\times$ 3 repeats = 594 runs).
- Pinned models, temperatures, prices, budgets, `output_chars` (8,000 default), and `context_chars` (100,000 maximum).
- Costs reconciled against Bifrost gateway logs (`logs.db`) using `token_agent.reconcile`.
- Metrics recorded per run: pass/fail, input/output/cached tokens, token attribution breakdown, wall time, tool steps, and research calls.

### How the harness runs (`benchmarks/harness.py`)
- **Engine and sessions:** each run uses the same core engine and session creation as the API
  server, including the confidentiality check. Each run gets its own database and a fresh copy
  of the repository at the pinned commit. Each repository is cloned once, then copied locally
  for every run.
- **Approvals (disclosed benchmark setting):** runs are unattended, so every exact pending
  action is approved automatically. Each approval is recorded as an `approval_decision` event
  by `benchmark-auto-approver`, and each run records `auto_approvals`. Approval still binds the
  exact action, and every policy check is unchanged.
- **Wall time** covers the agent's work only, not cloning or the check command. Each run has a
  time limit (default 900 s), after which it is cancelled and recorded as unfinished.
- **Cost:** with `--gateway-log`, each run's own database is reconciled against the Bifrost log
  before its cost is read. `cost_reconciled` is true only when every usage row was reconciled.
- **Evidence:** the report's `benchmark_evidence` is true only for `mode = "bifrost"` with every
  run reconciled. Fixture or scripted runs are never benchmark evidence.
- **Checks:** a task's `check_command` runs through a shell in the task workspace. It comes only
  from this frozen, reviewed task suite, never from model output or the repository.

## 6. Research on versus off (handoff G08)

`benchmarks/research_comparison.py` runs the same frozen tasks, model and configuration in two
modes:
- **research off:** `[research]` is disabled, so the `web_research` tool is not offered and no
  automatic trigger can fire;
- **research on:** `[research]` is enabled, with explicit user permission for public research
  (FSD 7.2).

It reports, per mode and per task:
- pass rate;
- total tokens, and the tokens research text added (`research_input_tokens`);
- research calls and Tavily credits;
- gateway-reconciled cost.

It reports numbers and spread only; no saving is claimed from a small sample.
`benchmarks/research.example.toml` restricts research to each library's official
documentation site. The default sample is two tasks per repository (TASK-01, 03, 09, 12, 14
and 15) with three repeats per mode.
