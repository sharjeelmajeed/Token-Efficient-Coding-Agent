import asyncio
import base64
import copy
import json

import httpx
import pytest

from token_agent.github_client import GitHubClient
from token_agent.pr_review import (
    comment_body,
    fully_read_paths,
    prepare_bundle,
    publish_report,
    review_bundle,
    safe_path,
)
from token_agent.providers import ProviderResult
from token_agent.repo import PolicyError

HEAD, BASE, BLOB = "a" * 40, "b" * 40, "c" * 40
TOKEN = "test-github-credential-" + "x" * 32
PATCH = "@@ -1 +1 @@\n-return a - b\n+return a + b"


class GitHubFixture:
    def __init__(self):
        self.pull = {
            "number": 7,
            "state": "open",
            "base": {"sha": BASE, "repo": {"full_name": "team/agent", "private": False}},
            "head": {"sha": HEAD, "repo": {"full_name": "contributor/agent"}},
            "changed_files": 1,
        }
        self.files = [{"filename": "app.py", "status": "modified", "sha": BLOB, "patch": PATCH}]
        self.content = "return a + b\n"
        self.markers = set()
        self.comments = []
        self.requests = []
        self.head_changes = False
        self.pull_calls = 0

    def respond(self, request):
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/pulls/7"):
            self.pull_calls += 1
            result = copy.deepcopy(self.pull)
            if self.head_changes and self.pull_calls > 1:
                result["head"]["sha"] = "d" * 40
            return httpx.Response(200, json=result)
        if path.endswith("/pulls/7/files"):
            return httpx.Response(200, json=self.files)
        if "/contents/" in path:
            return httpx.Response(
                200 if request.url.params["ref"] in self.markers else 404,
                json={"type": "file"},
            )
        if "/git/blobs/" in path:
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "size": len(self.content.encode()),
                    "content": base64.b64encode(self.content.encode()).decode(),
                },
            )
        if path.endswith("/issues/7/comments"):
            if request.method == "POST":
                self.comments.append(
                    {
                        "body": json.loads(request.content)["body"],
                        "user": {"login": "github-actions[bot]", "type": "Bot"},
                    }
                )
                return httpx.Response(201, json={"id": 1})
            return httpx.Response(200, json=self.comments)
        raise AssertionError(f"Unexpected API path {path}")


async def bundle(fixture, root):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.respond)) as http:
        return await prepare_bundle(
            GitHubClient(TOKEN, http), "team/agent", 7, root, ".confidential", [TOKEN]
        )


@pytest.mark.parametrize("classification", ["public", "private", "base_marker", "head_marker"])
def test_snapshot_is_data_only_and_confidentiality_is_conservative(tmp_path, classification):
    fixture = GitHubFixture()
    fixture.pull["base"]["repo"]["private"] = classification == "private"
    fixture.markers = (
        {BASE}
        if classification == "base_marker"
        else ({HEAD} if classification == "head_marker" else set())
    )
    fixture.content = "return a + b\n" + "irrelevant context\n" * 300
    root = tmp_path / "snapshot"
    manifest = asyncio.run(bundle(fixture, root))
    assert manifest["confidential"] == (classification != "public")
    assert (root / ".confidential").exists() == manifest["confidential"]
    assert not (root / "app.py").exists()
    assert "return a + b" in (root / "context/001.txt").read_text()
    assert (root / "context/001.txt").read_text().count("irrelevant context") < 30
    assert manifest["head_sha"] == HEAD
    assert all(r.method == "GET" for r in fixture.requests)


@pytest.mark.parametrize("path", ["../escape", "/etc/passwd", "a\\b", "a//b", "a/./b", "C:x"])
def test_pr_paths_never_escape(path):
    with pytest.raises(PolicyError, match="unsafe"):
        safe_path(path)


@pytest.mark.parametrize("case", ["protected", "renamed_secret", "secret", "oversized", "changed"])
def test_unsafe_snapshot_is_withheld(tmp_path, case):
    fixture = GitHubFixture()
    if case == "protected":
        fixture.files[0]["filename"] = ".env.production"
    elif case == "renamed_secret":
        fixture.files[0]["previous_filename"] = ".env"
    elif case == "secret":
        fixture.content = f"api_key = '{TOKEN}'"
    elif case == "oversized":
        fixture.files[0]["patch"] = "x" * 60001
    else:
        fixture.head_changes = True
    with pytest.raises(PolicyError):
        asyncio.run(bundle(fixture, tmp_path / "snapshot"))
    assert all(r.method == "GET" for r in fixture.requests)


def test_missing_patch_has_explicit_coverage_and_no_fake_file(tmp_path):
    fixture = GitHubFixture()
    fixture.pull["changed_files"] = 2
    fixture.files.append({"filename": "logo.png", "status": "added", "sha": BLOB})
    manifest = asyncio.run(bundle(fixture, tmp_path / "snapshot"))
    assert len(manifest["changes"]) == 1
    assert "logo.png" in manifest["coverage_notes"][0]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://attacker.invalid"}),
        httpx.Response(200, content=b"x" * 2000001),
        httpx.Response(200, content=b"invalid-json"),
    ],
)
def test_transport_does_not_follow_redirects_or_echo_response(response):
    requests = []

    async def exercise():
        def respond(request):
            requests.append(request)
            return response

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            with pytest.raises(PolicyError) as failure:
                await GitHubClient(TOKEN, http).pull("team/agent", 7)
            assert TOKEN not in str(failure.value)
        assert len(requests) == 1

    asyncio.run(exercise())


class ReviewProvider:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = []

    async def complete(self, messages, model, user, confidential, request_context=None):
        self.calls.append({"confidential": confidential, "read_only": user.read_only})
        return ProviderResult(
            json.dumps({"action": next(self.actions)}), model, 20, 10, "test_transport"
        )


@pytest.mark.parametrize("classification", ["public", "private", "operator_root"])
def test_real_core_review_requires_reads_and_never_executes_pr_code(
    environment, tmp_path, classification
):
    root = tmp_path / "snapshot"
    fixture = GitHubFixture()
    fixture.pull["base"]["repo"]["private"] = classification == "private"
    manifest = asyncio.run(bundle(fixture, root))
    settings = environment["settings"].model_copy(
        update={
            "confidential_roots": [str(tmp_path / "original-root")]
            if classification == "operator_root"
            else []
        }
    )
    provider = ReviewProvider(
        [
            {"tool": "read_file", "path": path}
            for path in ["manifest.json", "patches/001.diff", "context/001.txt"]
        ]
        + [{"tool": "finish", "summary": "No supported defect. No repository tests ran."}]
    )
    result = asyncio.run(review_bundle(settings, root, manifest, provider))
    assert result["status"] == "completed"
    assert result["reviewed_text_files"] == result["supplied_text_files"] == 1
    assert result["context_files_read"] == 1
    assert not result["repository_tests_executed"] and not result["benchmark_evidence"]
    assert all(call["read_only"] for call in provider.calls)
    assert all(call["confidential"] == (classification != "public") for call in provider.calls)
    assert "Read-only analysis" in comment_body(result, "team/agent", 7)


@pytest.mark.parametrize(
    "action",
    [
        {"tool": "finish", "summary": "Unsubstantiated all-clear"},
        {"tool": "write_file", "path": "bad.py", "content": "x=1", "expected_sha256": "absent"},
        {"tool": "run_shell", "argv": ["echo", "injection"]},
    ],
)
def test_unread_or_gated_review_cannot_publish(environment, tmp_path, action):
    root = tmp_path / "snapshot"
    manifest = asyncio.run(bundle(GitHubFixture(), root))
    provider = ReviewProvider([action])
    result = asyncio.run(review_bundle(environment["settings"], root, manifest, provider))
    assert result["status"] in {"incomplete", "paused"}
    assert result["reviewed_text_files"] == 0
    assert not (root / "bad.py").exists()
    with pytest.raises(PolicyError, match="withheld"):
        comment_body(result, "team/agent", 7)


def test_truncated_or_partial_reads_do_not_inflate_coverage():
    events = []
    for start, end, truncated in [(1, 2, False), (3, 4, True)]:
        events.extend(
            [
                {
                    "type": "tool_call",
                    "payload": {
                        "action": {
                            "tool": "read_file",
                            "path": "p.diff",
                            "start_line": start,
                            "end_line": end,
                        }
                    },
                },
                {
                    "type": "tool_result",
                    "payload": {
                        "tool": "read_file",
                        "result": {"path": "p.diff", "total_lines": 4, "truncated": truncated},
                    },
                },
            ]
        )
    assert fully_read_paths(events) == set()
    events[-1]["payload"]["result"]["truncated"] = False
    assert fully_read_paths(events) == {"p.diff"}


def completed_report():
    return {
        "schema_version": 1,
        "repository": "team/agent",
        "number": 7,
        "head_sha": HEAD,
        "base_sha": BASE,
        "status": "completed",
        "read_only": True,
        "repository_tests_executed": False,
        "summary": "No supported finding. ```\n@everyone",
        "coverage_notes": [],
        "supplied_text_files": 1,
        "reviewed_text_files": 1,
    }


def test_publish_is_scoped_stale_safe_and_idempotent_in_mock_transport():
    fixture = GitHubFixture()
    report = completed_report()

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.respond)) as http:
            github = GitHubClient(TOKEN, http)
            assert await publish_report(github, report, "team/agent", 7) == "posted"
            assert await publish_report(github, report, "team/agent", 7) == "already_posted"
            assert len(fixture.comments) == 1
            assert "````text" in fixture.comments[0]["body"]
            fixture.head_changes = True
            with pytest.raises(PolicyError, match="changed"):
                await publish_report(github, report, "team/agent", 7)
        assert sum(r.method == "POST" for r in fixture.requests) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "mutation",
    [
        {"repository": "attacker/repo"},
        {"number": 8},
        {"status": "paused"},
        {"reviewed_text_files": 0},
        {"base_sha": "invalid"},
        {"coverage_notes": ["x"] * 32},
        {"summary": "password='long-secret-value-123456'"},
        {"summary": "ghp_" + "x" * 40},
    ],
)
def test_invalid_or_sensitive_comment_never_reaches_github(mutation):
    report = {**completed_report(), **mutation}
    with pytest.raises(PolicyError):
        comment_body(report, "team/agent", 7)
