# Research on versus off (G08): findings

**Status:** the cost side is measured; the benefit side is open. Research on used about
3–4× more tokens than research off. No model we could run for free solved enough tasks to
show whether research raises the pass rate. No saving is claimed.

Raw runs: [`evidence/live/research-comparison.json`](evidence/live/research-comparison.json).
Tool and method: [`benchmarks/README.md` section 6](../benchmarks/README.md).

## 1. Setup

- **Tasks:** the frozen suite at `7ac9085` (18 tasks: click, jinja, express).
- **Model:** Cohere `command-a-03-2025`, through Bifrost on the standard virtual key, with no
  fallbacks. It was used on Cohere's free tier, so nothing was billed. Costs below are what
  Bifrost recorded at Cohere list prices. **Every run reconciled** against the Bifrost log.
- **Research off:** `[research]` disabled, so `web_research` is not offered and no trigger fires.
- **Research on:** `[research]` enabled with explicit user permission, restricted to the
  official documentation of click, jinja2 and express.
- **Run settings:** Python checks on CPython 3.12, `max_steps` 20, `context_chars` 40,000,
  requests paced at 3.5 s for Cohere's 20 requests a minute.

## 2. Results

### All 18 tasks, one repeat per mode

| | Research off | Research on |
|---|---|---|
| Tasks passed | **0 / 18** | **0 / 18** |
| Median tokens per run | 3,911 | 15,374 (**3.9×**) |
| Total tokens | 114,846 | 329,163 (**2.9×**) |
| Research calls | 0 | 23 |
| Research text in the prompt | 0 | 59,293 tokens (18% of the total) |
| Cost at list price | $0.44 | $1.00 |
| Reconciled | 18 / 18 | 18 / 18 |

### TASK-03, TASK-09 and TASK-15, three repeats per mode

| | Research off | Research on |
|---|---|---|
| Tasks passed | **0 / 9** | **1 / 9** (TASK-15, third repeat) |
| Median tokens per run | 5,224 | 21,675 (**4.1×**) |
| Total tokens | 41,011 | 307,806 (**7.5×**) |
| Reconciled | 9 / 9 | 9 / 9 |

TASK-09 and TASK-15 were rerun after a Bifrost budget cap and two upstream HTTP 502 errors
invalidated runs in the first pass. The invalid runs are excluded.

### What the numbers show

1. **Research makes the model work longer.** With research off, Cohere usually stopped after
   2–3 steps, claiming completion without a fix. With research on, it explored for 2–16 steps.
2. **That costs 3–4× the tokens per run** (median), and up to 7.5× in total.
3. **No measurable gain in solved tasks:** 1 pass in 27 research-on runs against 0 in 27
   research-off runs is far too small a difference to call an improvement.
4. **Research-off runs repeat almost exactly** (temperature 0). Research-on runs vary more
   (TASK-15: 21,675 to 96,599 tokens), likely because search results vary.
5. **The model fails at editing, not at finding information.** Most research-on failures were
   oversized reads, patches that did not match the file, or malformed actions.

## 3. Other models tried

None of these is evidence. They were pilots on TASK-03 to find a model strong enough to
measure the benefit side, at no cost to the team.

| Model and route | What happened |
|---|---|
| `qwen2.5-coder:7b`, `qwen3.5:4b` (local Ollama) | 0/6 each. Context-limit pauses and very slow steps on CPU. The 7B used research and got stuck sooner; the 4B never used it. |
| Gemini Flash models (free tier) | 503 overload, timeouts, or empty replies on a real agent prompt. Older models are refused for new keys. |
| Groq `gpt-oss-120b` | `tool_use_failed`: the model insists on native tool calls. |
| OpenRouter free (Nemotron 550B, North-Mini-Code) | Empty content: a reasoning loop, or a native tool call instead of a JSON action. |
| FreeLLMAPI (`auto` router) | Valid actions, but it routes to small models and switches model between steps, so it cannot give a same-model comparison. |
| LLM7 free models | The provider shortened or replaced the agent's system prompt (about 1,000 tokens sent, 64–277 counted), so the model never saw the action schema. |
| AgentRouter | Refuses non-whitelisted clients (`unauthorized client detected`). Not pursued: passing would require disguising the client. |
| Cloudflare Workers AI, Qwen3-30B | Claimed the task was done at step 1 without reading a file. The checks marked it not passed. |
| Cloudflare Workers AI, Llama 3.3 70B | Guessed file paths, used research twice, then finished with a wrong conclusion. Not passed. |
| Cloudflare Workers AI, Qwen 3.8 27B | Found the right files, then re-read them in a loop until the step limit (section 4.2). |
| DeepSeek V4 Pro (NovAI reseller) | Seven precise steps (list, search, read the right file), then the free account's 10-call daily limit. The only strong model found; not free. |

## 4. Lessons for the handoff

### 4.1 Most free models cannot follow the JSON-in-text action format

The agent asks the model to write each action as one JSON object in the reply text. Most newer
free models are trained for the provider's native tool calling. They return empty content, a
tool call in their own format, or extra wrapping (code fences, tags). The core correctly
refuses all of these. Supporting native tool calling in the provider layer would open many
more models, but it is a contract and policy change and was not attempted here.

### 4.2 The context limit can cost more tokens with mid-strength models

`context_chars` keeps the prompt small by dropping older context. Qwen 3.8 27B read the right
files, the reads were evicted, and it read them again, three to four times, until it hit the
step limit (94,818 tokens with research off). A strong model wrote its patch before the
content was evicted. Anyone tuning `context_chars` should measure re-reads, not only prompt
size.

### 4.3 The safety checks held in every live run

- No empty, malformed or wrongly wrapped reply was accepted as an action.
- No model's claim of completion counted as a pass: the task checks decided.
- The local-only virtual key was refused by every cloud provider added for these tests.
- On TASK-11, a research request was blocked because the error type was not on the recognised
  list (the privacy rule working as designed).
- On TASK-16, research content was withheld as a possible secret. This may be a false positive
  on the Express code and is worth a look.

## 5. What remains

The benefit side needs a model that solves a meaningful share of the tasks. The same tool can be
rerun unchanged when the G10 benchmark has credits:

```
python benchmarks/research_comparison.py --config research.toml \
    --gateway-log <bifrost logs.db> --python <python 3.12> \
    --node-modules node-express5=<path> --node-modules node-express4=<path> \
    --tasks TASK-01 TASK-02 ... TASK-18 --repeats 3
```

Report it only when `benchmark_evidence` is true, with the spread, and without claiming a saving
the sample cannot support.
