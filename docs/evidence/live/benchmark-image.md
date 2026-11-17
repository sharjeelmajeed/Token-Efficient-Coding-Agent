# Benchmark image evidence

This records the build, in-image validation and live Docker acceptance of the
benchmark task image. It is infrastructure evidence, not a benchmark result. No
model run or token/cost measurement is claimed here.

## Published image

| Field | Value |
|---|---|
| Reference | `ghcr.io/sharjeelmajeed/token-efficient-coding-agent-bench@sha256:4c9cc21dcc726f8611dc1b326fec05192fda6baf5345261aa3ee72420b142039` |
| Registry | GHCR, private package; pull needs a token with `read:packages` |
| Platform | `linux/amd64` |
| Base | `python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` (approved as a base only) |
| Python | CPython 3.12.16 with `benchmarks/environments/requirements-python.txt` at `/opt/bench/python` |
| Node | 24.15.0 LTS at `/opt/bench/node`, tarball checked against its published SHA-256 |
| Node modules | `npm ci` trees at `/opt/bench/node-express4/node_modules` and `/opt/bench/node-express5/node_modules` |
| Build | `.github/workflows/benchmark-image.yml`, manual workflow |
| Workflow run | [43409541457](https://github.com/sharjeelmajeed/Token-Efficient-Coding-Agent/actions/runs/43409541457), success |
| Head commit | `44dca10e9faf9827fe05253b8667bb76311bb33a` |
| Evidence artifact | `benchmark-image-evidence` (id `14029595644`), containing `docker-sandbox.json` and `validation-in-image.json` |

The workflow builds the image, validates all 18 frozen tasks inside it, and only
then pushes the validated image. The live Docker acceptance runs against the
pushed digest on the same clean GitHub runner.

## In-image task validation

`benchmarks/tasks/validation.json` records the report from that run:

- 18/18 tasks valid.
- Linux, CPython 3.12.16; Node `v24.15.0`.
- Environments: `node-express4`, `node-express5`, `python`.
- TASK-03 was fixed for portability: 21 Windows-only `msvcrt` tests were removed
  from `pass_to_pass`, leaving 58; the prompt and reference change are unchanged.

## Live Docker acceptance

`docs/evidence/live/docker-sandbox.json` records 18/18 checks passed against the
same digest, with:

- `image_selection.approved_by = "Muhammad Usman Khan"` and status
  `approved for the benchmark task image`.
- Docker 28.0.4, `linux/amd64`, on the GitHub runner.
- No network, read-only root and repository, non-root uid `65534:65534`, dropped
  capabilities, `no-new-privileges`, bounded CPU/memory/PIDs, and a `noexec`
  `/tmp`.
- Changed-snapshot refusal, timeout/cancellation cleanup, missing-image refusal
  without pulling, and no container left behind.

## Pull on a benchmark machine

The package is private, so authenticate once with a token that has
`read:packages` and pull the pinned digest before running `agent doctor` or
`agent bench`:

```
docker login ghcr.io --username <github-user>
docker pull ghcr.io/sharjeelmajeed/token-efficient-coding-agent-bench@sha256:4c9cc21dcc726f8611dc1b326fec05192fda6baf5345261aa3ee72420b142039
```

Do not put the token in a command line, file or evidence. The sandbox runner uses
`--pull never`, so a stale tag or a local rebuild cannot silently replace the
pinned image.

## Not covered

- The benchmark campaign itself. It is 0/594 runs; gateway credits and the
  sandbox snapshot decision are still needed.
- Verified early termination. `agent.sandbox.toml` leaves `verification_commands` empty
  because the frozen check is applied after the run and a generic command can
  falsely verify the start commit.
- The strict snapshot blocker. Every frozen task repository contains an accessible
  binary file, a file over 128,000 bytes, or a source/test file that matches the
  heuristic secret pattern, so `Repository.snapshot()` rejects it. The image can
  run pytest, mocha and both Express trees, but a sandboxed benchmark run needs a
  WS1-owned byte-faithful snapshot or an explicit benchmark execution profile.
  This is not an image defect and must not be worked around by deleting task
  files or disabling isolation.
