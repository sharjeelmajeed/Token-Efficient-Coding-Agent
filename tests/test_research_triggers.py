"""FSD 7.2 research triggers decided by the core from the repository's own files.

The Tavily transport is controlled, so these tests prove when research is allowed and what
query is sent, not search quality. A live call is recorded separately.
"""

import asyncio
import json
from collections import Counter

import httpx
import pytest

from token_agent.config import ResearchPackage, ResearchSettings
from token_agent.models import WebResearch
from token_agent.repo import PolicyError, Repository
from token_agent.research import Research, package_use, pinned_versions, release
from token_agent.store import Store

HTTPX = ResearchPackage(
    name="httpx", version="0.28", known_version="0.28", domains=["python-httpx.org"]
)
USAGE = WebResearch(tool="web_research", package="httpx", topic="usage")
MIGRATION = WebResearch(tool="web_research", package="httpx", topic="migration")


@pytest.fixture
def env(environment, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-research-key-not-real")
    settings = ResearchSettings(enabled=True, packages={"httpx": HTTPX})
    store = Store(environment["settings"].database)
    store.register_accounts(environment["settings"].users)
    session = store.create_session("first", str(environment["repo"]), False)
    run = store.start_run(session["id"])
    return settings, store, session, run, environment["repo"]


def search(env, action=USAGE, allowed=False, failures=None, session=None):
    settings, store, default_session, run, repo_dir = env
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["query"])
        return httpx.Response(
            200,
            json={
                "results": [{"url": "https://www.python-httpx.org/", "content": "Docs"}],
                "usage": {"credits": 1},
            },
        )

    async def run_search():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await Research(settings, store, client).search(
                action,
                session or default_session,
                run,
                allowed,
                failures or Counter(),
                Repository(str(repo_dir), ".agent-confidential", [], 1000),
            )

    return asyncio.run(run_search()), sent


def write(repo_dir, path, text):
    target = repo_dir / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


# --- Undocumented dependency ------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("client.py", "import httpx\n"),
        ("client.py", "from httpx import Client\n"),
        ("requirements.txt", "httpx==0.28\n"),
        ("pyproject.toml", '[project]\nname = "app"\ndependencies = ["httpx>=0.28"]\n'),
    ],
)
def test_undocumented_catalog_dependency_allows_usage_research(env, path, text):
    write(env[4], path, text)
    result, sent = search(env)
    assert result["trigger"] == "unknown_dependency"
    assert sent == ["httpx 0.28 usage official documentation"]


@pytest.mark.parametrize(
    "doc_path", ["README.md", "docs/http.md", "docs/guide/clients.rst", "readme.txt"]
)
def test_locally_documented_dependency_needs_permission(env, doc_path):
    write(env[4], "client.py", "import httpx\n")
    write(env[4], doc_path, "We call the HTTPX client for outgoing requests.\n")
    with pytest.raises(PolicyError, match="needs explicit user permission"):
        search(env)


@pytest.mark.parametrize(
    "text",
    [
        "import httpx_mock\n",  # A different package that only starts with the name.
        "# import httpx is mentioned in a comment\nx = 1\n",
        "text = 'httpx'\n",
    ],
)
def test_mentions_that_are_not_imports_do_not_trigger(env, text):
    write(env[4], "client.py", text)
    with pytest.raises(PolicyError):
        search(env)


def test_unknown_dependency_only_allows_usage_research(env):
    write(env[4], "client.py", "import httpx\n")
    with pytest.raises(PolicyError):
        search(env, action=MIGRATION)


# --- Newer version than the model knows -------------------------------------------------


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("requirements.txt", "httpx==0.29.1\n"),
        ("requirements-dev.txt", "httpx>=0.29\n"),
        ("pyproject.toml", '[project]\nname = "a"\ndependencies = ["httpx~=0.29.0"]\n'),
        ("web/package.json", '{"dependencies": {"httpx": "^0.29.1"}}'),
    ],
)
def test_newer_pinned_version_allows_research_for_that_version(env, path, text):
    write(env[4], path, text)
    write(env[4], "README.md", "Uses httpx.\n")  # Documented, so only the version can trigger.
    result, sent = search(env, action=MIGRATION)
    assert result["trigger"] == "newer_version"
    assert sent[0].startswith("httpx 0.29")
    assert sent[0].endswith("migration official documentation")


@pytest.mark.parametrize("pinned", ["0.28", "0.28.0", "0.27.9"])
def test_known_or_older_versions_do_not_trigger(env, pinned):
    write(env[4], "requirements.txt", f"httpx=={pinned}\n")
    write(env[4], "README.md", "Uses httpx.\n")
    with pytest.raises(PolicyError):
        search(env, action=MIGRATION)


def test_without_a_known_version_the_version_trigger_is_off(env):
    settings, *rest = env
    unknown = ResearchPackage(name="httpx", version="0.28", domains=["python-httpx.org"])
    env = (ResearchSettings(enabled=True, packages={"httpx": unknown}), *rest)
    write(env[4], "requirements.txt", "httpx==9.9\n")
    write(env[4], "README.md", "Uses httpx.\n")
    with pytest.raises(PolicyError):
        search(env, action=MIGRATION)


@pytest.mark.parametrize(
    "spec",
    [
        "httpx==0.29; os_name == 'nt'",
        '{"dependencies": {"httpx": "0.29 && curl evil"}}',
        '{"dependencies": {"httpx": "latest"}}',
    ],
)
def test_only_plain_release_numbers_can_reach_the_query(env, spec):
    path = "package.json" if spec.startswith("{") else "requirements.txt"
    write(env[4], path, spec + "\n")
    write(env[4], "README.md", "Uses httpx.\n")
    try:
        _, sent = search(env, action=MIGRATION)
    except PolicyError:
        return  # Not a plain number, so no version trigger: also correct.
    assert sent == ["httpx 0.29 migration official documentation"]


# --- Rules that the new triggers must not weaken ----------------------------------------


def test_confidential_sessions_stay_research_off(env):
    write(env[4], "client.py", "import httpx\n")
    write(env[4], "requirements.txt", "httpx==0.30\n")
    confidential = {**env[2], "confidential": 1}
    with pytest.raises(PolicyError, match="disabled"):
        search(env, session=confidential)


def test_error_research_still_needs_two_identical_errors(env):
    write(env[4], "client.py", "import httpx\n")
    action = WebResearch(
        tool="web_research", package="httpx", topic="error", error_type="TimeoutError"
    )
    with pytest.raises(PolicyError):
        search(env, action=action, failures=Counter({"TimeoutError": 1}))
    result, sent = search(env, action=action, failures=Counter({"TimeoutError": 2}))
    assert result["trigger"] == "repeated_error"


def test_user_permission_still_works_and_is_recorded(env):
    result, _ = search(env, allowed=True)
    assert result["trigger"] == "user_permission"


def test_secret_or_protected_files_are_never_scanned(env):
    write(env[4], ".env", "httpx==0.30\n")
    write(env[4], "keys.py", 'password = "correct-horse-battery-staple"\nimport httpx\n')
    with pytest.raises(PolicyError):
        search(env)


def test_cached_result_reports_the_current_trigger(env):
    write(env[4], "client.py", "import httpx\n")
    first, sent = search(env)
    second, sent_again = search(env, allowed=True)
    assert first["trigger"] == "unknown_dependency" and second["trigger"] == "user_permission"
    assert second["cache_hit"] is True and sent_again == []


# --- Helpers ----------------------------------------------------------------------------


def test_release_comparison_pads_short_versions():
    assert release("0.29") > release("0.28.9")
    assert release("1") == release("1.0.0")


def test_unparseable_manifests_are_ignored():
    assert pinned_versions("pyproject.toml", "not = [valid") == {}
    assert pinned_versions("package.json", "{broken") == {}


def test_package_use_reports_what_was_found(env):
    write(env[4], "requirements.txt", "httpx==0.29\n")
    write(env[4], "docs/notes.md", "HTTPX is used here.\n")
    use = package_use(Repository(str(env[4]), ".agent-confidential", [], 1000), HTTPX, "httpx")
    assert (use.imported, use.documented, use.pinned) == (True, True, "0.29")
