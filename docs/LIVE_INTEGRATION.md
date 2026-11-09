# WS2 live-integration handoff

The adapter uses one trusted Maxim AI Bifrost chat-completion endpoint and the
user's virtual key in `x-bf-vk`. Endpoint, credentials, model allowlists and fallbacks
come from operator configuration, never from model output.

Use [M2_USAGE](M2_USAGE.md) for configuration, exact commands, optional research and
sandbox prerequisites. Required live evidence remains tracked in [STATUS](STATUS.md).

## Request and accounting behavior

- `standard_fallbacks` and `local_fallbacks` are explicit provider/model strings.
  They must be in the corresponding allowlist. Private requests use only the local chain.
- The core sends `x-request-id` for correlation and reads the gateway's actual
  `x-bifrost-routing-info-provider`, `model` and `is-fallback` headers. Legacy
  provider/body metadata is used only when newer fields are absent.
- `usage.prompt_tokens`, `completion_tokens` and optional cached-token counts are
  validated. Invalid/missing usage, forbidden actual models and failed requests pause.
- The reservation covers configured input/output ceilings multiplied by the maximum
  gateway attempt count. Multi-attempt requests retain the complete reservation even
  after a successful final response, because failed attempts may also have costs.
- Gateway billing-log reconciliation is pending. Held/uncertain amounts cannot yet
  be safely released by an automatic reconciliation command. Do not edit the database
  to manufacture spend or benchmark numbers.
- Optional direct-cache partitioning includes session identity, repository snapshot
  hash and request payload. Private/default-disabled caching uses a fresh partition,
  direct-cache mode and `x-bf-cache-no-store`. Enforce equivalent gateway policy;
  response headers and local settings cannot undo an incorrectly routed request.
- No core retry after a possibly billed error; configured gateway fallback owns retry
  behavior. Failure causes are not guessed when the gateway supplies only an outcome.

## Streaming

With `streaming = true` the adapter sends `stream: true` and asks for usage in the final
chunk. Text stays hidden until the serving model is confirmed as allowlisted. A stream with
no usage pauses and keeps the reservation as uncertain, and the core never retries it. A
non-event-stream response is read as a normal JSON reply. Whether Bifrost sends routing
headers, usage and fallbacks this way on a stream is untested against a live gateway.

## Service status

The current environment has no configured Bifrost/Tavily keys, Ollama or Docker.
No real model, search or container call is presented as verified. Python transport,
approval and accounting tests use controlled responses. Fixture usage is synthetic.

Primary implementation references checked on 2026-11-02:

- [Maxim AI Bifrost](https://github.com/maximhq/bifrost)
- [Bifrost response headers](https://docs.getbifrost.ai/providers/response-headers)
- [Bifrost retries and fallbacks](https://docs.getbifrost.ai/features/retries-and-fallbacks)
- [Bifrost caching controls](https://docs.getbifrost.ai/features/semantic-caching)
- [Bifrost virtual keys](https://docs.getbifrost.ai/features/governance/virtual-keys)
- [Tavily search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Docker run reference](https://docs.docker.com/engine/containers/run/)
- [Docker network isolation](https://docs.docker.com/engine/network/drivers/none/)
- [HTTPX asynchronous client](https://www.python-httpx.org/async/)
