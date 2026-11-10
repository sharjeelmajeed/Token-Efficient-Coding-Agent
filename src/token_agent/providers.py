import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

import httpx

from .config import Settings, UserConfig

DEMO_TASK = "demo: fix calculator addition"
# Reason used when the gateway refuses a request because its virtual-key budget is spent.
GATEWAY_BUDGET_HIT = "gateway_budget_hit"


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class ProviderResult:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    source: str
    cached_tokens: int = 0
    cache_hit: bool = False
    latency_ms: int = 0
    request_id: str = ""
    is_fallback: bool = False


def serving_model(metadata: dict, reported, extra: dict) -> str:
    """The model that actually answered, qualified by provider when one is known."""
    actual = metadata.get("x-bifrost-routing-info-model") or reported
    if not isinstance(actual, str) or not actual:
        return ""
    provider = (
        metadata.get("x-bifrost-routing-info-provider")
        or extra.get("provider")
        or metadata.get("x-bifrost-provider")
    )
    if isinstance(provider, str) and provider and not actual.startswith(provider + "/"):
        return provider + "/" + actual
    return actual


class FixtureProvider:
    """Offline scripted integration fixture. Never used as benchmark evidence."""

    async def complete(
        self,
        messages: list[dict],
        model: str,
        user: UserConfig,
        confidential: bool,
        request_context: dict | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        if not any(m["content"] == DEMO_TASK for m in messages if m["role"] == "user"):
            raise ProviderError(f"Fixture mode only supports: {DEMO_TASK}")
        results = []
        for message in messages:
            try:
                item = json.loads(message["content"])
                if isinstance(item, dict) and "tool_result" in item:
                    results.append(item)
            except (json.JSONDecodeError, TypeError):
                continue
        if not results:
            action = {"tool": "read_file", "path": "calculator.py", "start_line": 1, "end_line": 20}
        else:
            last = results[-1]
            result = last["tool_result"]
            if result.get("denied"):
                action = {"tool": "finish", "summary": "Change was denied. No patch was applied."}
            elif last["tool"] == "read_file" and "return a - b" in result.get("text", ""):
                action = {
                    "tool": "apply_patch",
                    "path": "calculator.py",
                    "expected_sha256": result["sha256"],
                    "old": "return a - b",
                    "new": "return a + b",
                }
            elif last["tool"] == "apply_patch":
                action = {"tool": "syntax_check", "path": "calculator.py"}
            else:
                action = {
                    "tool": "finish",
                    "summary": "Fixture completed. Python syntax was checked; "
                    "run the repository tests separately to verify behavior.",
                }
        text = json.dumps({"action": action})
        if on_text:
            for start in range(0, len(text), 16):
                on_text(text[start : start + 16])
                await asyncio.sleep(0)
        return ProviderResult(
            text,
            model,
            max(1, len(json.dumps(messages)) // 4),
            max(1, len(text) // 4),
            "synthetic_fixture_not_benchmark",
        )


class BifrostProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client

    async def complete(
        self,
        messages: list[dict],
        model: str,
        user: UserConfig,
        confidential: bool,
        request_context: dict | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        key_env = user.local_key_env if confidential else user.bifrost_key_env
        virtual_key = os.environ.get(key_env, "")
        if not virtual_key:
            raise ProviderError("Required Bifrost virtual-key environment variable is missing")
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_completion_tokens": self.settings.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        fallbacks = (
            self.settings.local_fallbacks if confidential else self.settings.standard_fallbacks
        )
        allowed = (
            self.settings.local_allowlist if confidential else self.settings.standard_allowlist
        )
        if fallbacks and any(item not in allowed for item in fallbacks):
            raise ProviderError("Fallback configuration violates model policy")
        payload["fallbacks"] = list(dict.fromkeys(item for item in fallbacks if item != model))
        context = request_context or {}
        request_id = context.get("request_id") or str(uuid4())
        cache_material = json.dumps(
            [context.get("session_id", request_id), context.get("snapshot_sha256"), payload],
            sort_keys=True,
        )
        caching = self.settings.optimization.gateway_direct_cache and not confidential
        headers = {
            "x-bf-vk": virtual_key,
            "x-request-id": request_id,
            "x-bf-cache-type": "direct",
            "x-bf-cache-key": hashlib.sha256(cache_material.encode()).hexdigest()
            if caching
            else request_id,
        }
        if not caching:
            headers["x-bf-cache-no-store"] = "true"
        if on_text:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        response_metadata = {}
        started = time.perf_counter()

        async def read_stream(response: httpx.Response) -> dict:
            parts, pending, usage, reported, extra, size = [], [], None, "", {}, 0
            async for line in response.aiter_lines():
                size += len(line)
                if size > 2000000:
                    raise ProviderError("Gateway response exceeded the size limit")
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                chunk = json.loads(body)
                if not isinstance(chunk, dict) or chunk.get("error"):
                    raise ProviderError("Gateway stream reported an error; session paused")
                reported = chunk.get("model") or reported
                usage = chunk.get("usage") or usage
                if isinstance(chunk.get("extra_fields"), dict):
                    extra.update(chunk["extra_fields"])
                choices = chunk.get("choices")
                first = choices[0] if isinstance(choices, list) and choices else {}
                delta = first.get("delta") if isinstance(first, dict) else None
                text = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(text, str) and text:
                    parts.append(text)
                    pending.append(text)
                # Text from an unconfirmed model stays hidden; the engine's check is final.
                if pending and serving_model(response_metadata, reported, extra) in allowed:
                    for piece in pending:
                        on_text(piece)
                    pending.clear()
            result = {"choices": [{"message": {"content": "".join(parts)}}], "extra_fields": extra}
            if usage:
                result["usage"] = usage
            if reported:
                result["model"] = reported
            return result

        async def request(client: httpx.AsyncClient) -> dict:
            try:
                async with client.stream(
                    "POST",
                    self.settings.gateway_url,
                    headers=headers,
                    json=payload,
                    follow_redirects=False,
                ) as response:
                    if response.status_code == 402:
                        body = (await response.aread())[:4096].decode(errors="replace")
                        if "budget_exceeded" in body:
                            # The gateway's own spend limit; nothing was sent to a provider.
                            raise ProviderError(GATEWAY_BUDGET_HIT)
                    if response.status_code != 200:
                        raise ProviderError(
                            f"Gateway returned HTTP {response.status_code}; session paused"
                        )
                    response_metadata.update(
                        {
                            k: response.headers.get(k, "")
                            for k in [
                                "x-bifrost-provider",
                                "x-bifrost-request-id",
                                "x-bifrost-routing-info-provider",
                                "x-bifrost-routing-info-model",
                                "x-bifrost-routing-info-is-fallback",
                            ]
                        }
                    )
                    if on_text and "text/event-stream" in response.headers.get("content-type", ""):
                        return await read_stream(response)
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > 2000000:
                            raise ProviderError("Gateway response exceeded the size limit")
                    return json.loads(chunks)
            except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ProviderError("Gateway transport or response failed; session paused") from exc

        if self.client:
            data = await request(self.client)
        else:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60, connect=5), trust_env=False, follow_redirects=False
            ) as client:
                data = await request(client)
        try:
            usage = data["usage"]
            content = data["choices"][0]["message"]["content"]
            extra = data.get("extra_fields", {})
            if not isinstance(extra, dict):
                extra = {}
            actual_model = serving_model(response_metadata, data.get("model"), extra)
            if not actual_model:
                raise ValueError("Missing serving model")
            incoming, outgoing = usage["prompt_tokens"], usage["completion_tokens"]
            if (
                type(incoming) is not int
                or type(outgoing) is not int
                or incoming < 0
                or outgoing < 0
                or not isinstance(content, str)
                or not isinstance(actual_model, str)
            ):
                raise ValueError("Invalid response fields")
            token_details = usage.get("prompt_tokens_details", {})
            cached_tokens = (
                token_details.get("cached_tokens", 0) if isinstance(token_details, dict) else 0
            )
            if type(cached_tokens) is not int or not 0 <= cached_tokens <= incoming:
                raise ValueError("Invalid cache token accounting")
            cache = extra.get("cache_debug", {})
            cache_hit = isinstance(cache, dict) and cache.get("cache_hit") is True
            return ProviderResult(
                content,
                actual_model,
                incoming,
                outgoing,
                "bifrost_response_usage_pending_log_reconciliation",
                cached_tokens,
                cache_hit,
                int((time.perf_counter() - started) * 1000),
                str(response_metadata.get("x-bifrost-request-id") or request_id)[:200],
                response_metadata.get("x-bifrost-routing-info-is-fallback") == "true",
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(
                "Gateway response lacks valid content, model or usage metadata"
            ) from exc
