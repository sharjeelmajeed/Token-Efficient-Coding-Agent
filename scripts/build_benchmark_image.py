"""Build the benchmark image and, with --push, publish it to GHCR.

The image installs the frozen task environments: CPython 3.12 with
``requirements-python.txt``, Node LTS, and ``npm ci`` for express 4 and 5. The
sandbox runner never pulls or installs, so every machine must receive the same
digest from a registry and pin it in ``[sandbox] image``.

    python scripts/build_benchmark_image.py
    python scripts/build_benchmark_image.py --push

After a push the script prints the ``name@sha256:<digest>`` reference to pin.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = ROOT / "benchmarks" / "environments"
DEFAULT_TAG = "ghcr.io/sharjeelmajeed/token-efficient-coding-agent-bench:bench-2026-11-13"


def docker() -> str:
    found = shutil.which("docker")
    if found is None:
        raise SystemExit("docker is not on PATH")
    return found


def run(*args: str) -> str:
    completed = subprocess.run(
        [docker(), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the pinned benchmark test image")
    parser.add_argument("--tag", default=DEFAULT_TAG, help="Image tag to build")
    parser.add_argument(
        "--platform", default="linux/amd64", help="Target platform recorded by the evidence"
    )
    parser.add_argument("--push", action="store_true", help="Push the tag to its registry")
    args = parser.parse_args()

    run(
        "build",
        "--platform",
        args.platform,
        "--file",
        str(ENVIRONMENTS / "Dockerfile"),
        "--tag",
        args.tag,
        str(ENVIRONMENTS),
    )
    image_id = run("image", "inspect", "--format", "{{.Id}}", args.tag)
    print(f"Built {args.tag}")
    print(f"Local image id: {image_id}")

    if not args.push:
        print("Not pushed. A registry manifest digest only exists after a push; then run:")
        print(f"  docker push {args.tag}")
        return 0

    run("push", args.tag)
    digests = json.loads(run("image", "inspect", "--format", "{{json .RepoDigests}}", args.tag))
    if not digests:
        print("Push finished but no repository digest was recorded", file=sys.stderr)
        return 1
    reference = digests[0]
    print(f"Pushed. Pin this reference: {reference}")
    print("Then rerun:")
    print(f"  python scripts/accept_docker_sandbox.py --image {reference} \\")
    print("      --approved-by 'Muhammad Usman Khan' --out docs/evidence/live/docker-sandbox.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
