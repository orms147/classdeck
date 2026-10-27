# LLM provider

All model calls go through one small interface in `src/classdeck/llm/`. The rest of the code never imports an SDK directly. That gives one place to switch model or endpoint, to cache, to mock in tests, and to meter cost.

## Any OpenAI-compatible endpoint

The provider speaks the Chat Completions format through the `openai` Python SDK with `base_url` pointed wherever `MODEL_API_BASE_URL` says. Nothing in the pipeline knows which vendor is behind it. Endpoints used so far:

| endpoint | `MODEL_API_BASE_URL` | model ids | notes |
|---|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-6-luna` (daily default), `deepseek/deepseek-v4.1-flash-1017`; for the judge role, any model from a different family than the generator | one key, many model families; the configuration used for day-to-day runs and for the judge role |
| Meta Model API | `https://api.meta.ai/v1` | `muse-spark-1.3` | used during the hackathon; details below |
| DeepSeek (direct) | `https://api.deepseek.com` | see the provider's model list | the same models without the aggregator |

### Measured on 2026-10-28 (OpenRouter list prices, `llm/pricing.py`)

| run | generator | tokens in / out | cost |
|---|---|---|---|
| one period: `plan` (refined) + `draft` + grounding judge | `openai/gpt-6-luna` ($0.10 / $0.50 per M) | ≈ 11k / 2k | ≈ $0.002 |
| one `edit --apply` | `openai/gpt-6-luna` | ≈ 3k / 0.5k | < $0.001 |
| `eval` of one public case with scripted edits | `openai/gpt-6-luna` + an independent judge model ($2 / $10 per M class) | ≈ 13k / 3.5k + judge | ≈ $0.003 + judge ≈ $0.015 |

| `eval` of one public case, generator `muse-spark-1.3` (Meta Model API, $1.25 / $4.25 per M) | same judge | ≈ 30k / 50k (the output includes the model's reasoning tokens) | ≈ $0.25 + judge |
| one flashcard picture, `muse-image-1.0` | — | — | $0.01 |

A teacher drafting one period a day on the daily default spends well under one US cent per period; the judge is the only non-trivial cost and is used only in evaluation. Muse Spark 1.3 scores about the same as the daily default on the judge rubric (3.5 vs 3.6 mean on the two public cases) but costs roughly thirty times more per period because its output tokens include reasoning; `muse-spark-1.3-contributor` is priced like the daily default but lets the provider train on the prompts, which teachers' material should not be used for.

Reasoning-style models (DeepSeek v4.1 flash among them) may return an empty `content` when `max_tokens` is tiny because the budget is spent on reasoning; `ping` uses a small but sufficient budget for that reason.

Three profiles are built in (`config.ENDPOINTS`): `meta` (Meta Model API, key `META_API_KEY`, default `muse-spark-1.3`), `openrouter` (`OPENROUTER_API_KEY`, default `openai/gpt-6-luna`) and `deepseek` (`DEEPSEEK_API_KEY`, default `deepseek-chat`). Select one with `CLASSDECK_ENDPOINT` or per command with `--endpoint meta --model muse-spark-1.2`; without a profile, `MODEL_API_*` apply and a missing `MODEL_API_KEY` is taken from the profile whose base URL matches.

The pipeline is designed so that a cheap model is enough — few calls, small prompts, deterministic fallbacks (see [output-formats.md → Token economy](output-formats.md#token-economy-on-api-runs)). With no API key at all, the prompt packs in `handoff/` move the generative step to a free chat model in the browser.

## Meta Model API (hackathon endpoint)

Facts checked against the [developer docs](https://dev.meta.ai/docs/) on 2026-10-27:

| | |
|---|---|
| Base URL | `https://api.meta.ai/v1` |
| Auth | `Authorization: Bearer $MODEL_API_KEY` |
| Request formats | Chat Completions (OpenAI-compatible), Responses, and a Messages format. Drop-in with the OpenAI SDK and OpenAI-compatible agent CLIs. |
| Text model used here | `muse-spark-1.3` — latest Spark, default in the docs' examples. Text, image, video, audio and PDF input; 1,048,576-token context; tool calling and structured output. |
| Not used | `muse-spark-1.3-contributor` is cheaper but lets Meta train on prompts and completions. Teachers' material goes through Standard mode only. |
| Phase 3 | `muse-image-1.0` (`/v1/images/generations`, `/v1/images/edits`), `muse-voice-transcribe-1.0` (`/v1/asr/transcribe`), `/v1/files` for upload-once file references. |

Chat Completions is the most portable of the three formats and the one every mock and cache tool understands, so it is the only one the provider uses. Model ID and base URL are settings (`CLASSDECK_MODEL`, `MODEL_API_BASE_URL`), never literals in pipeline code.

## Two roles: generator and judge

| role | what it does | configured by | default |
|---|---|---|---|
| **generator** | Everything in the product pipeline: outlining, drafting, the borderline citation judge in grounding, edits. | `MODEL_API_*`, `CLASSDECK_MODEL` | whatever the endpoint settings point at |
| **judge** | Evaluation only: scores generated lessons against the rubric in [evaluation.md](evaluation.md#llm-judge). Never called by the product pipeline. | `JUDGE_API_KEY`, `JUDGE_API_BASE_URL`, `JUDGE_MODEL` | unset → falls back to the generator, and the eval report says so |

The judge should be a **different model family** from the generator — a model grading its own output is biased towards it. Any OpenAI-compatible endpoint works for the judge role (an aggregator such as OpenRouter gives access to most families through one key). The product stays Meta-only; the judge is an evaluation instrument.

## Interface

```python
class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str | list[TextPart | ImagePart]      # parts for multimodal input (phase 3)

class Usage(BaseModel):
    input_tokens: int | None
    output_tokens: int | None

class LLMResponse(BaseModel):
    text: str
    model: str
    usage: Usage
    cached: bool = False
    latency_s: float | None = None

class LLMProvider(Protocol):
    name: str
    def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
    ) -> LLMResponse: ...
```

Implementations:

| class | file | purpose |
|---|---|---|
| `OpenAICompatibleProvider` | `llm/openai_compat.py` | Real calls to any Chat-Completions-compatible endpoint. Wraps `openai.OpenAI(base_url=…, api_key=…)`; constructed with a `name` (`"api:<host>"` for the generator, e.g. `api:api.deepseek.com`; `"judge"` for the judge), `base_url`, `api_key`, `model`. Maps `json_schema` to `response_format={"type": "json_schema", …}`; if the server rejects that shape (HTTP 400), retries once without it and relies on the prompt's JSON instruction. Raises `LLMError` with the HTTP status and body on other failures. Retries 429/5xx twice with backoff. |
| `MockProvider` | `llm/mock.py` | Tests and `--provider mock`. Either a queue of canned responses or a `handler(messages) -> str`. Records every call in `.calls` so tests can assert on prompts. |
| `CachingProvider` | `llm/cache.py` | Wraps any provider. Key = SHA-256 of `(provider.name, model, messages, temperature, max_tokens, json_schema)`. One JSON file per key under `CLASSDECK_CACHE_DIR`. Hits return `cached=True` and cost nothing. |

`llm.factory.build_provider(settings, role="generator" | "judge", *, use_cache=True)`:

- `generator` → `OpenAICompatibleProvider` on the `MODEL_API_*` settings when `CLASSDECK_PROVIDER=api` (the legacy value `meta` means the same), or `MockProvider` when `CLASSDECK_PROVIDER=mock`.
- `judge` → `OpenAICompatibleProvider` on the `JUDGE_*` settings when all three are set; otherwise the generator provider, with a logged warning.
- Either is wrapped in `CachingProvider` when `cache_dir` is set and `use_cache` is true.

## Structured output

Every pipeline stage that needs a typed result calls:

```python
def complete_json(provider, messages, model_cls: type[T], *, retries: int = 1) -> T
```

1. Appends the JSON schema of `model_cls` to the last user message as an instruction *and* passes it as `json_schema` to the provider (belt and braces: works whether or not the server enforces it).
2. Strips code fences, parses JSON, validates with Pydantic.
3. On a parse or validation error, re-prompts once with the error text and the offending output, then raises `StructuredOutputError` carrying both attempts.

The models passed here are the pipeline's own (`Outline`, `DraftSection`, `EditProposalDraft`, `GroundingVerdict`), not the lesson schema directly — the model proposes, the engine validates and assembles.

## Prompts

Prompt text lives in `src/classdeck/llm/prompts/*.md`, one file per task, loaded by name with `{{placeholder}}` substitution. The English rules fragment (`english/rules.py`) is inserted into every prompt by the stage that builds it. Prompts are versioned with the code; a change to a prompt is a code change with a diff.

## Cost and credit discipline

- Development runs with `CLASSDECK_PROVIDER=mock`. Real calls are for eval runs and manual checks.
- The cache is on by default; `--no-cache` on the CLI bypasses it for one run.
- `UsageMeter` (in `llm/base.py`) sums `Usage` over a run; the CLI prints tokens at the end of every command that called the model, and eval writes them per case.
- `cost_usd` is computed from a per-model price table in `llm/pricing.py` that starts empty — the docs do not list prices on the model page — and is filled in from the billing page as it becomes known. Unknown price → `None`, never a guess.
- Every real request is logged at `INFO` with model, token counts and latency.

## `classdeck ping`

Sends one short message (`"Reply with the single word: pong"`, `max_tokens=5`) to the configured provider and prints the model, the reply and the token count. It is the first thing to run after setting `MODEL_API_KEY`, and the cheapest way to confirm that credits are applied.
