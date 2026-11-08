# Dependency review — 2026-11-03

Installed and exercised in an isolated Python 3.12.14 environment. Runtime constraints
are in `pyproject.toml`; exact transitive versions and hashes are in the lock files.
These packages are project-relevant framework, validation, transport, server, CLI
and verification dependencies. Releases, Python support and licenses were checked
against project documentation/PyPI and installed distribution metadata.

| Package | Installed version | Declared license |
| --- | --- | --- |
| fastapi | 0.162.0 | MIT |
| pydantic | 2.14.2 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| typer | 0.27.3 | MIT |
| rich | 14.3.4 | MIT |
| pytest | 9.2.0 | MIT |
| ruff | 0.17.3 | MIT |
| pip-audit | 2.10.1 | Apache Software License |
| coverage | 7.17.0 | Apache-2.0 |

The dependency audit returned no known vulnerabilities for the scanned environment.
This is a point-in-time advisory result, not a guarantee that packages have no bugs.
The full JSON and package list are in `evidence/dependency-audit.json`.

FastAPI/Pydantic, Uvicorn and Typer release information was checked on official project
sources. HTTPX 0.28.1 is the official stable package version used for controlled HTTP
transport; no speculative replacement package was installed. Tests use a real Uvicorn
server rather than Starlette's deprecated HTTPX TestClient integration.

Primary references:

- https://fastapi.tiangolo.com/release-notes/
- https://github.com/fastapi/fastapi/blob/master/pyproject.toml
- https://github.com/pydantic/pydantic/blob/main/pyproject.toml
- https://pypi.org/project/httpx/
- https://www.python-httpx.org/api/
- https://pypi.org/project/uvicorn/
- https://pypi.org/project/typer/
- https://github.com/actions/checkout
- https://github.com/actions/setup-python
- https://pypi.org/project/coverage/

Coverage is development-only. Its official metadata declares Python >=3.11 and
Apache-2.0; it was installed, imported and used to measure the current test suite.
M2 added no new runtime dependency. Tavily and Bifrost use the existing HTTPX adapter.

CI uses GitHub-owned actions pinned to exact commits verified against official
repositories/releases. CI has not run remotely. Optional provider SDKs, Docker,
Bifrost, Ollama and Tavily services were not installed. M3 adds no Python runtime dependency.

## VS Code development dependencies

Installed and exercised with Node 24.22.1/npm 11.9.0. The extension ships no runtime
npm dependency; compiled JavaScript uses the native VS Code and Node APIs.

| Package | Locked version | License | Purpose |
| --- | --- | --- | --- |
| TypeScript | 7.0.2 | Apache-2.0 | Strict compilation |
| @types/node | 22.20.5 | MIT | Node API compatibility |
| @types/vscode | 1.144.0 | MIT | Declared VS Code engine API |
| @vscode/vsce | 4.1.0 | MIT | Local VSIX packaging |
| @vscode/test-electron | 3.1.0 | MIT | Official editor host smoke runner |
| minimatch | 11.0.1 | BlueOak-1.0.0 | Transitive packaging glob matcher |

Official npm registry metadata supplied engine/version/license information. The two
Microsoft tools require Node >=22; the client declares the same floor. VS Code API
version and extension engine both target 1.144.0. TypeScript compilation, Node tests
and actual VSIX packaging passed. Editor-host execution is pending; a downloaded
test runner package is not proof that an editor was installed or exercised.

`@vscode/vsce` 4.1.0 requests minimatch ^10.2.6. The current advisory affects versions
below 11.0.1, so a scoped npm override pins its matcher to official 11.0.1. Its named
`minimatch` API and supported Node engine are compatible with the packaging call;
actual packaging and file inclusion were verified. Revisit the override when vsce
updates its supported range. `npm audit` now reports zero known vulnerabilities,
including development dependencies. Exact dependency integrity hashes are locked.
The advisory result is a point-in-time check, not a complete security proof.

Primary M3 references:

- https://code.visualstudio.com/api/extension-guides/webview
- https://code.visualstudio.com/api/extension-guides/virtual-documents
- https://code.visualstudio.com/api/extension-guides/workspace-trust
- https://code.visualstudio.com/api/working-with-extensions/testing-extension
- https://code.visualstudio.com/api/working-with-extensions/continuous-integration
- https://github.com/microsoft/vscode-vsce
- https://github.com/microsoft/vscode-test
- https://github.com/isaacs/minimatch/releases/tag/v11.0.1
- https://github.com/advisories/GHSA-g3hv-75wg-m249
- https://docs.github.com/en/actions/reference/security/secure-use
- https://docs.github.com/en/rest/pulls/pulls
- https://docs.github.com/en/rest/issues/comments
- https://github.com/actions/setup-node/commit/2028fbc5c25fe9cf00d9f06a71cc4710d4507903
- https://github.com/actions/upload-artifact/commit/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
- https://github.com/actions/download-artifact/commit/3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c
