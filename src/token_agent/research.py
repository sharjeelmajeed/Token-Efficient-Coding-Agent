import hashlib
import json
import os
import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from .config import ResearchPackage, ResearchSettings
from .models import WebResearch
from .repo import PolicyError, Repository
from .store import Store

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
# Only plain release numbers from the repository may ever reach a search query.
PLAIN_VERSION = re.compile(r"^\d+(?:\.\d+){0,3}$")
PY_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", re.MULTILINE)
JS_IMPORT = re.compile(r"""(?:\brequire\(\s*|\bfrom\s+|\bimport\s+)['"]((?:@[\w.-]+/)?[\w.-]+)""")
REQUIREMENT = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:===|==|~=|>=)\s*([0-9][0-9.]*)",
    re.MULTILINE,
)
DOC_SUFFIXES = (".md", ".rst", ".txt")


@dataclass(frozen=True)
class PackageUse:
    """What the repository's own files say about one catalog package."""

    imported: bool = False
    documented: bool = False
    pinned: str = ""


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def release(version: str) -> tuple[int, ...]:
    parts = tuple(int(p) for p in version.split("."))
    return parts + (0,) * (4 - len(parts))


def pinned_versions(path: str, text: str) -> dict[str, str]:
    """Package name -> pinned release from requirements, pyproject or package.json."""
    found: dict[str, str] = {}
    name = path.rsplit("/", 1)[-1]
    try:
        if name.startswith("requirements") and name.endswith(".txt"):
            for package, version in REQUIREMENT.findall(text):
                found[normalize(package)] = version
        elif name == "pyproject.toml":
            project = tomllib.loads(text).get("project", {})
            specs = list(project.get("dependencies", []))
            for group in project.get("optional-dependencies", {}).values():
                specs.extend(group)
            for spec in specs:
                for package, version in REQUIREMENT.findall(str(spec)):
                    found[normalize(package)] = version
        elif name == "package.json":
            data = json.loads(text)
            for key in ("dependencies", "devDependencies"):
                for package, spec in (data.get(key) or {}).items():
                    version = re.sub(r"^[\^~>=v\s]+", "", str(spec))
                    found[normalize(package)] = version
    except (ValueError, TypeError, AttributeError, tomllib.TOMLDecodeError):
        return {}
    return {k: v for k, v in found.items() if PLAIN_VERSION.match(v)}


def package_use(repo: Repository, package: ResearchPackage, key: str) -> PackageUse:
    """Scan readable repository files; protected, secret or binary files are skipped."""
    names = {normalize(package.name), normalize(key)}
    mention = re.compile(
        r"(?<![\w-])(?:" + "|".join(re.escape(n) for n in sorted(names)) + r")(?![\w-])",
        re.IGNORECASE,
    )
    imported = documented = False
    pinned = ""
    for path in repo.files():
        try:
            text = repo.read(path)
        except PolicyError:
            continue
        lower = path.lower()
        if lower.endswith(".py"):
            imported |= any(normalize(m) in names for m in PY_IMPORT.findall(text))
        elif lower.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
            imported |= any(normalize(m) in names for m in JS_IMPORT.findall(text))
        versions = pinned_versions(path, text)
        if versions.keys() & names:
            imported = True
            pinned = next(versions[n] for n in sorted(names) if n in versions)
        elif (lower.startswith("docs/") or lower.split("/")[-1].startswith("readme")) and (
            lower.endswith(DOC_SUFFIXES) and mention.search(text)
        ):
            documented = True
    return PackageUse(imported, documented, pinned)


class Research:
    def __init__(
        self, settings: ResearchSettings, store: Store, client: httpx.AsyncClient | None = None
    ):
        self.settings, self.store, self.client = settings, store, client

    def trigger(
        self,
        action: WebResearch,
        package: ResearchPackage,
        allowed_by_user: bool,
        failures: Counter,
        repo: Repository,
    ) -> tuple[str | None, str]:
        """Which FSD 7.2 condition allows this call, and the version to search for.

        Decided by the core from the trusted catalog and the repository's own files, never
        from the model's request or the task text.
        """
        if allowed_by_user:
            return "user_permission", package.version
        if action.topic == "error" and action.error_type and failures[action.error_type] >= 2:
            return "repeated_error", package.version
        if action.topic == "error":
            return None, package.version
        use = package_use(repo, package, action.package)
        if (
            use.pinned
            and package.known_version
            and release(use.pinned) > release(package.known_version)
        ):
            return "newer_version", use.pinned
        if use.imported and not use.documented and action.topic == "usage":
            return "unknown_dependency", package.version
        return None, package.version

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
        if action.topic == "error" and not action.error_type:
            raise PolicyError("Error research requires a recognized generic exception type")
        trigger, version = self.trigger(action, package, allowed_by_user, failures, repo)
        if trigger is None:
            raise PolicyError(
                "Research needs explicit user permission, two observed same errors, an "
                "undocumented catalog dependency or a version newer than the model knows"
            )
        # No arbitrary model/file/task string is ever used as a search query.
        query = " ".join(
            filter(
                None,
                [
                    package.name,
                    version,
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
            return {**cached, "cache_hit": True, "credits": 0, "trigger": trigger}
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
            "trigger": trigger,
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
