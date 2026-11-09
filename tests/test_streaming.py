"""Incremental finish text. Transports are controlled, so live Bifrost streaming stays unproven."""

import asyncio
import json
import threading

import httpx
import pytest

from token_agent.api import create_app
from token_agent.providers import BifrostProvider, ProviderError, ProviderResult
from token_agent.repo import PolicyError
from token_agent.streaming import SummaryExtractor, TextStream, complete_prefix

from .conftest import live_client, session, start, wait_status
from .test_providers import live_settings

SUMMARY = "Fixed the calculator. " * 12


def reply(action):
    return json.dumps({"action": action})


FINISH = reply({"tool": "finish", "summary": SUMMARY})


def pieces_of(text, size=7):
    return [text[index : index + size] for index in range(0, len(text), size)]


class StreamingProvider:
    def __init__(self, replies, model=None, hook=None):
        self.replies, self.model, self.hook = list(replies), model, hook
        self.calls = 0

    async def complete(
        self, messages, model, user, confidential, request_context=None, on_text=None
    ):
        self.calls += 1
        content = self.replies.pop(0) if self.replies else FINISH
        for index, piece in enumerate(pieces_of(content)):
            on_text(piece)
            if self.hook:
                await self.hook(index)
        return ProviderResult(content, self.model or model, 20, 10, "test_transport")


def streaming_settings(environment, **updates):
    return live_settings(environment, streaming=True, **updates)


def deltas(app, sid):
    return [e["payload"] for e in app.state.store.events(sid) if e["type"] == "message_delta"]


# --- Reading the summary out of partial JSON -------------------------------------------


@pytest.mark.parametrize(
    "summary",
    ["Plain text", 'Quote " and \\ slash', "Line\nbreak\ttab", "Café 🙂 done", "}]{[,:"],
)
@pytest.mark.parametrize("ascii_only", [True, False])
def test_summary_decodes_correctly_at_every_split(summary, ascii_only):
    raw = json.dumps({"action": {"tool": "finish", "summary": summary}}, ensure_ascii=ascii_only)
    for split in range(len(raw) + 1):
        extractor = SummaryExtractor()
        extractor.feed(raw[:split])
        assert extractor.feed(raw[split:]) == summary, f"split at {split}"


def test_summary_grows_one_character_at_a_time_without_garbage():
    summary = 'A "quoted" 🙂 end'
    raw = json.dumps({"action": {"tool": "finish", "summary": summary}})
    extractor = SummaryExtractor()
    previous = ""
    for char in raw:
        current = extractor.feed(char)
        assert current.startswith(previous) and summary.startswith(current)
        previous = current
    assert previous == summary


@pytest.mark.parametrize(
    "raw",
    [
        reply({"tool": "read_file", "path": "summary", "summary": "not finish"}),
        reply({"summary": "tool comes later", "tool": "finish"}),
        reply({"tool": "apply_patch", "new": '"summary":"fake","tool":"finish"', "old": "x"}),
        json.dumps({"summary": "top level", "action": {"tool": "finish"}}),
        json.dumps({"tool": "finish", "summary": "not inside action"}),
        json.dumps({"action": {"tool": "finish", "extra": {"summary": "too deep"}}}),
        json.dumps({"other": {"tool": "finish", "summary": "wrong parent"}}),
    ],
)
def test_only_a_finish_summary_is_ever_extracted(raw):
    assert SummaryExtractor().feed(raw) == ""


def test_first_summary_is_kept_when_a_key_is_repeated():
    raw = '{"action":{"tool":"finish","summary":"first","summary":"second"}}'
    assert SummaryExtractor().feed(raw) == "first"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("abc", 3), ("ab\\", 2), ("ab\\n", 4), ("ab\\u00", 2), ("\\u00e9", 6)]
    + [("\\ud83d", 0), ("\\ud83d\\ude4", 0), ("x\\ud83d\\ude42", 13)],
)
def test_complete_prefix_never_splits_an_escape(raw, expected):
    assert complete_prefix(raw) == expected


def no_check(_text):
    return None


def test_text_stream_holds_back_and_can_finish_from_the_validated_summary():
    shown = []
    stream = TextStream(no_check, shown.append, [])
    for piece in pieces_of(FINISH):
        stream.feed(piece)
    released = "".join(shown)
    assert released and SUMMARY.startswith(released)
    assert len(SUMMARY) - len(released) >= 64
    assert released + stream.remainder(SUMMARY) == SUMMARY


def test_text_stream_rejects_a_summary_that_differs_from_what_was_shown():
    stream = TextStream(no_check, lambda _text: None, [])
    stream.feed(FINISH)
    with pytest.raises(PolicyError, match="did not match"):
        stream.remainder("Something else entirely")


def test_holdback_covers_the_longest_configured_secret():
    secret = "z" * 200
    shown = []

    def check(text):
        if secret in text:
            raise PolicyError("secret")

    stream = TextStream(check, shown.append, [secret])
    raw = reply({"tool": "finish", "summary": "x" * 150 + secret + "y" * 50})
    with pytest.raises(PolicyError):
        for char in raw:
            stream.feed(char)
    assert "z" not in "".join(shown)


# --- Engine behavior --------------------------------------------------------------------


def test_setting_is_off_by_default_and_the_message_is_unchanged(environment):
    assert environment["settings"].streaming is False
    app = create_app(environment["settings"])
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        aid = wait_status(client, sid, {"waiting_approval"})["pending_approvals"][0]["id"]
        client.post(f"/sessions/{sid}/approvals/{aid}", json={"decision": "approve_once"})
        wait_status(client, sid, {"completed"})
        payloads = deltas(app, sid)
    assert len(payloads) == 1 and set(payloads[0]) == {"text"}


def test_pieces_join_to_the_summary_and_end_exactly_once(environment):
    app = create_app(streaming_settings(environment), StreamingProvider([FINISH]))
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        wait_status(client, sid, {"completed"})
        payloads = deltas(app, sid)
    assert len(payloads) >= 3
    assert [p["partial"] for p in payloads] == [True] * (len(payloads) - 1) + [False]
    assert "".join(p["text"] for p in payloads) == SUMMARY


def test_text_is_visible_while_the_model_is_still_answering(environment):
    release = threading.Event()
    seen_partial = threading.Event()

    async def hold(index):
        if index == len(pieces_of(FINISH)) - 1:
            seen_partial.set()
            while not release.is_set():
                await asyncio.sleep(0.01)

    app = create_app(streaming_settings(environment), StreamingProvider([FINISH], hook=hold))
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        assert seen_partial.wait(5)
        status = client.get(f"/sessions/{sid}/status").json()
        early = deltas(app, sid)
        release.set()
        wait_status(client, sid, {"completed"})
    assert status["run"]["status"] == "running"
    assert early and all(p["partial"] for p in early)


def test_tool_actions_are_never_shown_as_text(environment):
    provider = StreamingProvider(
        [reply({"tool": "read_file", "path": "calculator.py"}), FINISH],
    )
    app = create_app(streaming_settings(environment), provider)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        wait_status(client, sid, {"completed"})
        payloads = deltas(app, sid)
    assert "calculator.py" not in "".join(p["text"] for p in payloads)
    assert "".join(p["text"] for p in payloads) == SUMMARY


def test_secret_in_the_summary_pauses_and_is_never_stored(environment):
    token = environment["first"]["Authorization"].split()[1]
    leaking = reply({"tool": "finish", "summary": "x" * 200 + token + "y" * 200})
    app = create_app(streaming_settings(environment), StreamingProvider([leaking]))
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"paused"})
        events = json.dumps(app.state.store.events(sid))
    assert "secret" in data["run"]["outcome"]
    assert token not in events and token[:12] not in events


def test_repeated_summary_key_cannot_swap_the_text_after_it_was_shown(environment):
    swapped = (
        '{"action":{"tool":"finish","summary":"'
        + SUMMARY
        + '","summary":"Completely different result"}}'
    )
    app = create_app(streaming_settings(environment), StreamingProvider([swapped]))
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        data = wait_status(client, sid, {"paused"})
    assert "did not match" in data["run"]["outcome"]


def test_invalid_reply_after_partial_text_pauses_with_a_resume_option(environment):
    broken = FINISH[:-2] + ',"approved":true}}'
    app = create_app(streaming_settings(environment), StreamingProvider([broken]))
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        wait_status(client, sid, {"paused"})
        events = app.state.store.events(sid)
    paused = next(e for e in events if e["type"] == "session_paused")
    assert paused["payload"]["resume"]
    assert not any(e["payload"].get("partial") is False for e in events)


def test_cancel_while_text_is_streaming_stops_and_keeps_the_uncertain_cost(environment):
    started = threading.Event()

    async def hang(index):
        if index == len(pieces_of(FINISH)) // 2:
            started.set()
            await asyncio.sleep(60)

    app = create_app(streaming_settings(environment), StreamingProvider([FINISH], hook=hang))
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        run = start(client, sid)
        assert started.wait(5)
        assert client.post(f"/sessions/{sid}/cancel").status_code == 200
        data = wait_status(client, sid, {"cancelled"})
        usage = app.state.store.usage(run)
        payloads = deltas(app, sid)
    assert data["run"]["outcome"] == "user_cancelled"
    assert usage[0]["state"] == "uncertain"
    assert not any(p["partial"] is False for p in payloads)


def test_fixture_streams_so_clients_can_be_built_without_keys(environment):
    settings = environment["settings"].model_copy(update={"streaming": True})
    app = create_app(settings)
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        start(client, sid)
        aid = wait_status(client, sid, {"waiting_approval"})["pending_approvals"][0]["id"]
        client.post(f"/sessions/{sid}/approvals/{aid}", json={"decision": "approve_once"})
        wait_status(client, sid, {"completed"})
        payloads = deltas(app, sid)
    assert payloads[-1]["partial"] is False
    assert "Fixture completed" in "".join(p["text"] for p in payloads)


# --- Gateway adapter --------------------------------------------------------------------


def sse(*chunks, done=True):
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def content_chunks(text, model="cloud/primary", usage=True):
    chunks = [
        {"model": model, "choices": [{"delta": {"content": piece}}]} for piece in pieces_of(text)
    ]
    if usage:
        chunks.append(
            {"model": model, "choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 6}}
        )
    return chunks


def stream_response(body, headers=None):
    return httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream", **(headers or {})}
    )


def exercise(environment, monkeypatch, handler, model="cloud/primary", confidential=False):
    monkeypatch.setenv("BIFROST_VIRTUAL_KEY", "virtual-key-for-tests")
    monkeypatch.setenv("BIFROST_LOCAL_VIRTUAL_KEY", "local-key-for-tests")
    shown = []

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = BifrostProvider(streaming_settings(environment), client)
            user = environment["settings"].users[0]
            return await provider.complete([], model, user, confidential, on_text=shown.append)

    return asyncio.run(run()), shown


def test_adapter_requests_a_stream_and_rebuilds_the_reply(environment, monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return stream_response(sse(*content_chunks(FINISH)))

    result, shown = exercise(environment, monkeypatch, handler)
    assert bodies[0]["stream"] is True
    assert bodies[0]["stream_options"] == {"include_usage": True}
    assert result.content == FINISH and "".join(shown) == FINISH
    assert (result.input_tokens, result.output_tokens) == (12, 6)
    assert result.model == "cloud/primary"


def test_adapter_without_a_callback_does_not_ask_for_a_stream(environment, monkeypatch):
    monkeypatch.setenv("BIFROST_VIRTUAL_KEY", "virtual-key-for-tests")
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "cloud/primary",
                "choices": [{"message": {"content": FINISH}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = BifrostProvider(live_settings(environment), client)
            return await provider.complete(
                [], "cloud/primary", environment["settings"].users[0], False
            )

    assert asyncio.run(run()).content == FINISH
    assert "stream" not in bodies[0]


def test_gateway_that_ignores_streaming_still_works(environment, monkeypatch):
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "model": "cloud/primary",
                "choices": [{"message": {"content": FINISH}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    result, shown = exercise(environment, monkeypatch, handler)
    assert result.content == FINISH and shown == []


def test_text_from_a_forbidden_model_is_never_shown(environment, monkeypatch):
    def handler(_request):
        return stream_response(sse(*content_chunks(FINISH, model="unapproved/model")))

    result, shown = exercise(environment, monkeypatch, handler)
    assert shown == [] and result.model == "unapproved/model"


def test_confidential_stream_shows_nothing_from_a_cloud_fallback(environment, monkeypatch):
    def handler(_request):
        return stream_response(sse(*content_chunks(FINISH, model="cloud/primary")))

    _, shown = exercise(environment, monkeypatch, handler, "ollama/code", confidential=True)
    assert shown == []


def test_routing_headers_identify_the_model_and_fallback(environment, monkeypatch):
    def handler(_request):
        return stream_response(
            sse(*content_chunks(FINISH, model="fallback")),
            {
                "x-bifrost-routing-info-provider": "cloud",
                "x-bifrost-routing-info-is-fallback": "true",
            },
        )

    result, shown = exercise(environment, monkeypatch, handler)
    assert result.model == "cloud/fallback" and result.is_fallback
    assert "".join(shown) == FINISH


@pytest.mark.parametrize(
    "body",
    [
        sse(*content_chunks(FINISH, usage=False)),
        sse(*content_chunks(FINISH, usage=False), done=False),
        sse({"error": {"message": "provider down"}}),
        b"data: {not json}\n\n",
        b"data: [1,2]\n\n",
        sse({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
        sse({"model": "cloud/primary", "choices": ["bad", 1]}),
    ],
    ids=["no-usage", "cut-off", "error-chunk", "bad-json", "not-object", "no-model", "bad-choices"],
)
def test_broken_streams_fail_as_provider_errors(environment, monkeypatch, body):
    with pytest.raises(ProviderError):
        exercise(environment, monkeypatch, lambda _request: stream_response(body))


def test_stream_size_limit(environment, monkeypatch):
    body = b"data: " + b'{"pad":"' + b"x" * 2000001 + b'"}\n\n'
    with pytest.raises(ProviderError, match="size limit"):
        exercise(environment, monkeypatch, lambda _request: stream_response(body))


def test_connection_lost_midway_pauses_and_a_new_instruction_continues(environment, monkeypatch):
    monkeypatch.setenv("BIFROST_VIRTUAL_KEY", "virtual-key-for-tests")
    attempts = []

    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse(*content_chunks(FINISH)[:30], done=False)
            raise httpx.ReadError("connection lost")

    def handler(_request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(
                200, stream=Broken(), headers={"content-type": "text/event-stream"}
            )
        return stream_response(sse(*content_chunks(FINISH)))

    settings = streaming_settings(environment)
    client_holder = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(settings, BifrostProvider(settings, client_holder))
    with live_client(app, environment["first"]) as client:
        sid = session(client, environment)
        first_run = start(client, sid)
        data = wait_status(client, sid, {"paused"})
        assert "session paused" in data["run"]["outcome"]
        assert app.state.store.usage(first_run)[0]["state"] == "uncertain"
        assert len(attempts) == 1  # The core never retries a possibly billed request.

        start(client, sid, "Continue")
        wait_status(client, sid, {"completed"})
        payloads = deltas(app, sid)
    assert len(attempts) == 2
    assert payloads[-1]["partial"] is False
