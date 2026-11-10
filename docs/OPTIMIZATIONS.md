# Optimization switches

FSD section 8 lists nine token-optimization techniques and requires each to be switchable,
so an ablation study can measure each one's contribution. Every technique has one switch in
the `[optimization]` table of the trusted configuration. Each run records the full switch set
in its `run_started` event (`payload.optimization`), so results can be traced to a configuration.

## The nine switches

| FSD | Switch | Default | When `false` |
|---|---|---|---|
| TO-01 Repo map | `repo_map` | `true` | The `repo_map` tool is not offered, and a call to it pauses the run |
| TO-02 Targeted reads | `targeted_reads` | `true` | `read_file` returns the whole file whatever range is asked for, and `grep` is not offered |
| TO-03 Output truncation | `output_truncation` | `true` | Tool output is not cut to `output_chars`; only the fixed ceiling below applies |
| TO-04 Diff-style edits | `diff_edits` | `true` | `apply_patch` is not offered; edits use whole-file `write_file`, and the prompt says so |
| TO-05 History compaction | `history_compaction` | `true` | Older steps are not summarized |
| TO-06 Tool schema pruning | `tool_pruning` | `true` | Edit tools are offered before the first file read |
| TO-07 Prompt/response caching | `gateway_direct_cache` | `false` | Each request gets a unique cache key and `x-bf-cache-no-store`, so nothing is reused |
| TO-08 Tiered routing | `tiered_routing` | `false` | Every step uses `standard_model` |
| TO-09 Early termination | `early_termination` | `true` | The run does not stop as soon as all configured checks pass |

Defaults are the optimized agent. Two techniques default to off because they need live
configuration: caching needs gateway cache support, and routing needs a `fast_model`.

## What is never switched off

Switches change only how efficiently the agent works. In every configuration the core still
enforces authentication and ownership, repository boundaries and protected paths, secret
checks, exact human approval of every write and command, budgets, read-only sessions and
confidential local-only routing. `tests/test_optimization_switches.py` checks these with every
switch off.

Two resource ceilings are fixed and apply in every configuration:

- **File size:** a file larger than 128,000 bytes is never read (`MAX_FILE_BYTES`).
- **Tool output:** with `output_truncation = false`, output is still capped at 128,000
  characters (`ABSOLUTE_OUTPUT_CHARS`), with an explicit truncation marker.

`context_chars` also stays a hard limit. Whole-file reads without truncation fill the context
faster, so a naive baseline on larger repositories may need a higher `context_chars`
(maximum 100,000). Use the same value for every system in one comparison.

## Naive baseline

FSD 10.2 defines the baseline as full-file reads, no caching, a single strong model and no
truncation. This configuration turns every technique off:

```toml
[optimization]
repo_map = false
targeted_reads = false
output_truncation = false
diff_edits = false
history_compaction = false
tool_pruning = false
gateway_direct_cache = false
tiered_routing = false
early_termination = false
```

## Ablations

Start from the optimized configuration (all switches at the values the comparison uses, for
example with caching and routing on when they are configured) and set exactly one switch to
`false` per ablation. Keep models, temperature, prices, budgets, `output_chars`,
`context_chars`, the task set and the safety settings identical across all systems, as FSD
10.5 requires.

## Not yet measured

These switches make the ablation possible; they do not show any saving by themselves. No
token or cost saving is claimed until the frozen benchmark has been run.
