import asyncio
import json
from collections import Counter

import httpx
import pytest
from pydantic import ValidationError

from token_agent.config import ResearchPackage, ResearchSettings
from token_agent.models import WebResearch
from token_agent.repo import PolicyError, Repository
from token_agent.research import Research
from token_agent.store import Store


@pytest.fixture
def research_env(environment, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-research-key-not-real")
    settings = ResearchSettings(
        enabled=True,
        packages={
            "python": ResearchPackage(name="Python", version="3.12", domains=["docs.python.org"])
        },
    )
    store = Store(environment["settings"].database)
    store.register_accounts(environment["settings"].users)
    session = store.create_session("first", str(environment["repo"]), False)
    run = store.start_run(session["id"])
    repo = Repository(session["repo"], ".agent-confidential", [], 1000)
    return settings, store, session, run, repo


def search(env, handler, action=None, allowed=True, failures=None):
    settings, store, session, run, repo = env

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            return await Research(settings, store, client).search(
                action or WebResearch(tool="web_research", package="python", topic="usage"),
                session,
                run,
                allowed,
                failures or Counter(),
                repo,
            )

    return asyncio.run(exercise())


def response(request):
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "url": "https://docs.python.org/3/?token=bad#x",
                    "title": "Python docs",
                    "content": "Bounded official guidance",
                }
            ],
            "usage": {"credits": 1},
        },
    )


def test_research_uses_only_catalog_and_enums_then_caches_per_session(research_env):
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert str(request.url) == "https://api.tavily.com/search"
        assert body["query"] == "Python 3.12 usage official documentation"
        assert body["include_domains"] == ["docs.python.org"]
        assert body["search_depth"] == "basic" and not body["auto_parameters"]
        return response(request)

    first = search(research_env, handler)
    second = search(research_env, handler)
    assert len(calls) == 1 and not first["cache_hit"] and second["cache_hit"]
    assert first["credits"] == 1 and second["credits"] == 0
    assert first["sources"][0]["url"] == "https://docs.python.org/3/"
    assert first["content_token_upper_bound"] == first["content_utf8_bytes"]
    settings, store, _, _, repo = research_env
    other = store.create_session("second", str(repo.root), False)
    other_run = store.start_run(other["id"])
    search((settings, store, other, other_run, repo), handler)
    assert len(calls) == 2


@pytest.mark.parametrize("case", ["disabled", "confidential", "no_permission", "unknown_package"])
def test_research_denials_happen_before_network(research_env, case):
    settings, store, session, run, repo = research_env
    if case == "disabled":
        settings = settings.model_copy(update={"enabled": False})
    if case == "confidential":
        session = {**session, "confidential": 1}
    action = WebResearch(
        tool="web_research",
        package="private-company" if case == "unknown_package" else "python",
        topic="usage",
    )
    calls = []
    with pytest.raises(PolicyError):
        search(
            (settings, store, session, run, repo),
            lambda req: calls.append(req),
            action,
            allowed=case != "no_permission",
        )
    assert calls == [] and store.research_usage(run) == []


def test_two_observed_errors_enable_generic_query(research_env):
    action = WebResearch(
        tool="web_research", package="python", topic="error", error_type="TypeError"
    )
    with pytest.raises(PolicyError, match="two observed"):
        search(research_env, response, action, allowed=False, failures=Counter(TypeError=1))
    result = search(research_env, response, action, allowed=False, failures=Counter(TypeError=2))
    assert "TypeError" in result["query"]


def test_model_cannot_inject_arbitrary_query_or_error_text():
    for extra in [
        {"query": "private source code"},
        {"error_type": "/private/token=secret"},
        {"allow_research": True},
    ]:
        with pytest.raises(ValidationError):
            WebResearch(tool="web_research", package="python", topic="usage", **extra)


def test_research_limit_counts_uncertain_requests(research_env):
    settings, store, session, run, repo = research_env
    env = (settings.model_copy(update={"max_calls_per_run": 1}), store, session, run, repo)
    with pytest.raises(PolicyError, match="HTTP 429"):
        search(env, lambda req: httpx.Response(429, text="sensitive response body"))
    with pytest.raises(PolicyError, match="call limit"):
        search(env, response)
    assert store.research_usage(run)[0]["status"] == "uncertain"


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.python.org",
        "https://docs.python.org.evil.test/x",
        "https://user:pass@docs.python.org",
        "https://docs.python.org:8443/x",
        "https://evil.test/docs.python.org",
        "file:///etc/passwd",
    ],
)
def test_untrusted_research_urls_are_not_returned(research_env, url):
    result = search(
        research_env,
        lambda req: httpx.Response(200, json={"results": [{"url": url, "content": "untrusted"}]}),
    )
    assert result["sources"] == []


def test_research_redirect_never_forwards_key(research_env):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(302, headers={"location": "https://evil.test"})

    with pytest.raises(PolicyError, match="HTTP 302"):
        search(research_env, handler)
    assert len(calls) == 1


def test_missing_key_does_not_consume_quota(research_env, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY")
    with pytest.raises(PolicyError, match="key is not configured"):
        search(research_env, response)
    assert research_env[1].research_usage(research_env[3]) == []


def test_research_response_size_and_secrets_are_blocked(research_env):
    with pytest.raises(PolicyError, match="size limit"):
        search(research_env, lambda req: httpx.Response(200, content=b"x" * 1000001))
    with pytest.raises(PolicyError, match="secret"):
        search(
            research_env,
            lambda req: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://docs.python.org/3",
                            "content": "password=secretpassword123456",
                        }
                    ]
                },
            ),
        )
