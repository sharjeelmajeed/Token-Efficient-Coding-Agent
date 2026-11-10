# Frozen Benchmark Task Suite

**Suite Name:** `token-efficient-coding-agent-benchmarks`  
**Status:** Frozen  
**Task Count:** 18 tasks  
**Repositories:** 3  
**Languages:** Python (12 tasks), JavaScript (6 tasks)  
**Categories:** Bug fixes (6), Small features (5), Refactors (3), Test additions (4)  

---

## 1. Timing Deviation Disclosure

> [!IMPORTANT]
> **Handoff Disclosure (FSD Section 10.3 & WS1/WS2 Handoff G01):**  
> FSD section 10.3 specifies that benchmark tasks should be defined and frozen before optimization begins to avoid tuning to the test set. In milestones M1 through M3, core optimization mechanisms (TO-01 through TO-09) and initial test doubles were implemented prior to formal benchmark suite freezing.  
> 
> To preserve evaluation integrity, this suite consists of independent, held-out tasks sourced from public open-source repositories (`pallets/click`, `pallets/jinja`, and `expressjs/express`) that were not used during core feature development or unit testing. All comparative evaluation across systems (naive baseline, fully optimized, and nine single-switch ablations) is conducted strictly against these frozen tasks.

---

## 2. Repositories and Pinned Commits

| Repository | Language | Pinned Commit SHA | Task IDs | Automated Test Runner |
|---|---|---|---|---|
| [`pallets/click`](https://github.com/pallets/click.git) | Python | `d84bb0ee307d0d0f7a61d154425bc01287c8d929` (v8.1.7) | `TASK-01` – `TASK-06` | `pytest` |
| [`pallets/jinja`](https://github.com/pallets/jinja.git) | Python | `c87b640e74b3f886f380126ffc59d9f5ea6c4d7e` (v3.1.3) | `TASK-07` – `TASK-12` | `pytest` |
| [`expressjs/express`](https://github.com/expressjs/express.git) | JavaScript | `0436d934e2a149b1a03f4448574be07198a0026e` (v4.19.2) | `TASK-13` – `TASK-18` | `npm test` |

---

## 3. Tasks Matrix

| Task ID | Repository | Language | Category | Title | Automated Check Command |
|---|---|---|---|---|---|
| `TASK-01` | click | Python | `bug_fix` | Boolean flag toggle with custom prefix | `pytest tests/test_options.py -k test_boolean_flags` |
| `TASK-02` | click | Python | `small_feature` | Case-insensitive choice matching | `pytest tests/test_types.py -k test_choice_case_insensitive` |
| `TASK-03` | click | Python | `refactor` | Help option parameter resolution cleanup | `pytest tests/test_help.py` |
| `TASK-04` | click | Python | `test_addition` | Bash completion edge cases with spaces | `pytest tests/test_shell_completion.py -k test_completion_with_spaces` |
| `TASK-05` | click | Python | `bug_fix` | Prompt default resolution on empty input | `pytest tests/test_termui.py -k test_prompt_empty_default` |
| `TASK-06` | click | Python | `small_feature` | Hidden command support in group listing | `pytest tests/test_commands.py -k test_hidden_command_in_group` |
| `TASK-07` | jinja | Python | `bug_fix` | DebugUndefined chained filter attribute access | `pytest tests/test_runtime.py -k test_chained_debug_undefined` |
| `TASK-08` | jinja | Python | `small_feature` | Unique filter attribute parameter support | `pytest tests/test_filters.py -k test_unique_by_attribute` |
| `TASK-09` | jinja | Python | `refactor` | CodeGenerator AST visitor method caching | `pytest tests/test_compiler.py` |
| `TASK-10` | jinja | Python | `test_addition` | Async loop control test suite | `pytest tests/test_async.py -k test_async_loop_controls` |
| `TASK-11` | jinja | Python | `bug_fix` | Markup subclass XML auto-escaping | `pytest tests/test_filters.py -k test_markup_subclass_escaping` |
| `TASK-12` | jinja | Python | `small_feature` | Slice filter negative indexing support | `pytest tests/test_filters.py -k test_slice_negative_indices` |
| `TASK-13` | express | JavaScript | `bug_fix` | Query encoding preservation in res.redirect | `npm test -- test/res.redirect.js` |
| `TASK-14` | express | JavaScript | `small_feature` | Header appending via res.appendHeader | `npm test -- test/res.set.js` |
| `TASK-15` | express | JavaScript | `refactor` | Route parameter regex compilation caching | `npm test -- test/app.router.js` |
| `TASK-16` | express | JavaScript | `test_addition` | Empty request body handling test coverage | `npm test -- test/req.body.js` |
| `TASK-17` | express | JavaScript | `bug_fix` | Trailing slash router mount path resolution | `npm test -- test/app.use.js` |
| `TASK-18` | express | JavaScript | `small_feature` | Custom status message fallback in res.sendStatus | `npm test -- test/res.sendStatus.js` |

---

## 4. Evaluation Protocol

Each task is evaluated across 11 system configurations defined in `docs/OPTIMIZATIONS.md`:
1. **Naive Baseline:** all nine optimization switches set to `false`.
2. **Optimized Agent:** full default configuration.
3. **9 Single-Switch Ablations:** exactly one technique turned `false` per ablation (`repo_map`, `targeted_reads`, `output_truncation`, `diff_edits`, `history_compaction`, `tool_pruning`, `gateway_direct_cache`, `tiered_routing`, `early_termination`).

### Execution Controls
- Minimum 3 repeats per task per system (18 tasks $\times$ 11 systems $\times$ 3 repeats = 594 runs).
- Pinned models, temperatures, prices, budgets, `output_chars` (8,000 default), and `context_chars` (100,000 maximum).
- Costs reconciled against Bifrost gateway logs (`logs.db`) using `token_agent.reconcile`.
- Metrics recorded per run: pass/fail, input/output/cached tokens, token attribution breakdown, wall time, tool steps, and research calls.
