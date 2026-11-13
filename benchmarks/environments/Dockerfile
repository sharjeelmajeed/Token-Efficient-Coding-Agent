# syntax=docker/dockerfile:1

# Benchmark test environments for the frozen task suite.
#
# The base image is approved as a base only. The sandbox runs with
# `--network none` and never installs packages, so pytest, Node and the Express
# test modules are baked in here and pinned by version or lockfile.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ARG NODE_VERSION=24.15.0
ARG NODE_SHA256=472655581fb851559730c48763e0c9d3bc25975c59d518003fc0849d3e4ba0f6

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# git is only needed by validate_tasks.py, which clones each task repository.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-python.txt /tmp/requirements-python.txt
RUN python -m venv /opt/bench/python \
    && /opt/bench/python/bin/python -m pip install -r /tmp/requirements-python.txt \
    && rm /tmp/requirements-python.txt

# Node LTS from the official release, checked against the published SHA-256.
RUN NODE_VERSION="$NODE_VERSION" NODE_SHA256="$NODE_SHA256" python - <<'PY'
import hashlib
import os
import tarfile
import urllib.request

version = os.environ["NODE_VERSION"]
expected = os.environ["NODE_SHA256"]
url = f"https://nodejs.org/dist/v{version}/node-v{version}-linux-x64.tar.xz"
archive = "/tmp/node.tar.xz"
urllib.request.urlretrieve(url, archive)
with open(archive, "rb") as handle:
    actual = hashlib.sha256(handle.read()).hexdigest()
if actual != expected:
    raise SystemExit(f"Node checksum mismatch: {actual}")
with tarfile.open(archive, "r:xz") as tar:
    tar.extractall("/opt/bench", filter="data")
os.rename(f"/opt/bench/node-v{version}-linux-x64", "/opt/bench/node")
os.remove(archive)
PY

ENV PATH="/opt/bench/node/bin:/opt/bench/python/bin:${PATH}" \
    BENCH_PYTHON="/opt/bench/python/bin/python" \
    BENCH_NODE_EXPRESS4="/opt/bench/node-express4/node_modules" \
    BENCH_NODE_EXPRESS5="/opt/bench/node-express5/node_modules"

COPY node-express4/package.json node-express4/package-lock.json /opt/bench/node-express4/
COPY node-express5/package.json node-express5/package-lock.json /opt/bench/node-express5/
RUN cd /opt/bench/node-express4 \
    && npm ci --no-audit --no-fund \
    && cd /opt/bench/node-express5 \
    && npm ci --no-audit --no-fund \
    && npm cache clean --force

# Fail the build if the interpreters or both mocha versions are not usable.
RUN python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version" \
    && python -m pytest --version \
    && node --version \
    && /opt/bench/node-express4/node_modules/.bin/mocha --version \
    && /opt/bench/node-express5/node_modules/.bin/mocha --version

# The sandbox mounts the repository here and runs as 65534:65534.
WORKDIR /workspace
