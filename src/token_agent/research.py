import hashlib
import json
import os
from collections import Counter
from urllib.parse import urlsplit, urlunsplit

import httpx

from .config import ResearchSettings
from .models import WebResearch
from .repo import PolicyError, Repository
from .store import Store

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class Research:
    def __init__(
        self, settings: ResearchSettings, store: Store, client: httpx.AsyncClient | None = None
    ):
        self.settings, self.store, self.client = settings, store, client

    async def search(
        self,
        action: WebResearch,
        session: dict,
        run_id: str,
        allowed_by_user: bool,
        failures: Counter,
        repo: Repository,
    ) -> dict:
        settings = self.settings
        if not settings.enabled or session["confidential"] and not settings.allow_confidential:
            raise PolicyError("External research is disabled for this session")
        package = settings.packages.get(action.package)
        if package is None:
            raise PolicyError("Research package is not in the trusted public package catalog")
        if not allowed_by_user and not (
            action.topic == "error" and action.error_type and failures[action.error_type] >= 2
        ):
            raise PolicyError("Research needs explicit user permission or two observed same errors")
        if action.topic == "error" and not action.error_type:
            raise PolicyError("Error research requires a recognized generic exception type")
        # No arbitrary model/file/task string is ever used as a search query.
        query = " ".join(
            filter(
                None,
                [
                    package.name,
                    package.version,
                    action.error_type,
                    action.topic,
                    "official documentation",
                ],
            )
        )
        key = hashlib.sha256(
            json.dumps(
                [query, package.domains, settings.max_sources, settings.result_chars]
            ).encode()
        ).hexdigest()
        if cached := self.store.cached_research(session["id"], key):
            return {**cached, "cache_hit": True, "credits": 0}
        api_key = os.environ.get(settings.key_env, "")
        if not api_key:
            raise PolicyError("Tavily key is not configured")
        call_id = self.store.reserve_research(
            session["id"], run_id, query, settings.max_calls_per_run
        )
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": settings.max_sources,
            "include_domains": package.domains,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "auto_parameters": False,
            "include_usage": True,
        }

        async def send(client: httpx.AsyncClient):
            try:
                async with client.stream(
                    "POST",
                    TAVILY_SEARCH_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise PolicyError(f"Research service returned HTTP {response.status_code}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1000000:
                            raise PolicyError("Research response exceeds size limit")
                    return json.loads(body)
            except (httpx.HTTPError, ValueError, UnicodeError) as exc:
                raise PolicyError("Research service failed; details withheld") from exc

        if self.client:
            data = await send(self.client)
        else:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20, connect=5), trust_env=False, follow_redirects=False
            ) as client:
                data = await send(client)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise PolicyError("Research service returned an invalid result schema")
        sources = []
        for item in data["results"][: settings.max_sources]:
            if not isinstance(item, dict):
                continue
            try:
                url = urlsplit(item.get("url", ""))
                host = url.hostname or ""
                if (
                    url.scheme != "https"
                    or url.username
                    or url.password
                    or url.port not in {None, 443}
                    or not any(
                        host == domain or host.endswith("." + domain) for domain in package.domains
                    )
                ):
                    continue
                safe_url = urlunsplit(("https", host, url.path, "", ""))
            except (ValueError, TypeError):
                continue
            if len(safe_url) > 512:
                continue
            source = {
                "url": safe_url,
                "title": str(item.get("title", ""))[:100],
                "content": str(item.get("content", ""))[: settings.result_chars // 3],
            }
            repo.check_secrets(json.dumps(source))
            sources.append(source)
        content = {"query": query, "sources": sources}
        while sources and len(json.dumps(content)) > settings.result_chars:
            sources.pop()
        measured_bytes = len(json.dumps(content, ensure_ascii=False).encode())
        usage = data.get("usage", {})
        credits = usage.get("credits") if isinstance(usage, dict) else None
        credits = credits if type(credits) is int and credits >= 0 else None
        result = {
            **content,
            "cache_hit": False,
            "credits": credits,
            "content_utf8_bytes": measured_bytes,
            "content_token_upper_bound": measured_bytes,
            "token_measurement": "UTF-8 byte upper bound; not model-tokenizer usage",
        }
        self.store.finish_research(call_id, credits)
        if settings.cache_seconds:
            self.store.cache_research(session["id"], key, result, settings.cache_seconds)
        return result
