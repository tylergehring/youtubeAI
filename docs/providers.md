# Providers

Interfaces live in `core/providers.py`; implementations in `providers/<kind>/`.

The original stage pipeline takes local transcripts through `core/ingest.py`. An explicit
`pipeline.explainer intake` command can now retrieve public YouTube captions through
`youtube-transcript-api`; it does not download or transcribe source audio.

| Interface | Methods | Mock |
|---|---|---|
| `LLMProvider` | `generate`, `generate_structured(prompt, schema)` | `providers/llm/mock.py` |
| `TTSProvider` | `synthesize(text, out_path)` | `providers/tts/mock.py` |
| `ImageProvider` | `generate(prompt, out_path, width, height)` | `providers/image/mock.py` |
| `ResearchProvider` | `verify_claim(claim, topic)` | `providers/research/mock.py` |

## OpenAI LLM provider (`providers/llm/openai_provider.py`)

Setup: put `OPENAI_API_KEY=sk-...` in `.env` (git-ignored), then choose it per run or permanently:

```powershell
$env:VIDGEN__providers__selected__llm = "openai"      # this shell only
# or edit selected.llm in config/providers.yaml
python -m pipeline.check_provider --llm openai         # FREE: key + model access, no tokens
python -m pipeline.check_provider --llm openai --live-call   # one tiny billable request (<$0.001)
```

* Model is `providers.models.llm.openai` (default `gpt-4.1-mini`); `models.<kind>.<provider>` means
  switching provider also switches model. Pricing for cost tracking is in `providers.pricing`.
* **Structured output:** JSON mode + strict Pydantic validation. If the reply is invalid the
  provider re-asks (up to `openai.repair_attempts`, default 2) showing the model the errors; if it
  still fails the stage fails. Replies cut off at the token limit are never parsed.
* **Cost:** every attempt is summed; billable-but-failed calls are recorded in `job_cost.json`
  (note `failed call`). Prices are estimates and ignore cached-input discounts.
* Errors are translated to actionable messages (bad key, no credit `insufficient_quota`, unknown
  model, rate limit, timeout, network) and never include the key. Transient errors are retried by
  the SDK (`openai.max_retries`).
* Reasoning-style models (`o*`, `gpt-5*`, `gpt-6*`) get no `temperature` parameter; override with
  `openai.send_temperature`.

## OpenAI speech

`providers/tts/openai_provider.py` implements real narration with the existing `TTSProvider`
contract. It requires `OPENAI_API_KEY`, writes WAV atomically, measures the decoded duration
and audio level with FFmpeg, and fails explicitly rather than returning mock audio.
The editorial command uses it directly. The existing stage pipeline can opt in with:

```powershell
$env:VIDGEN__providers__selected__tts = "openai"
```

`providers.models.tts.openai` selects the speech model and `providers.tts.openai_voice`
selects the voice (defaults `gpt-4o-mini-tts` and `marin`). Mock defaults remain unchanged.
Characters and logical requests are returned as usage. Token-based speech billing is not
inferred from character count; no speech price is configured by default. A cost record
marked `no pricing configured` is not evidence that speech was free. Editorial audio receipts
retain usage independently of the original pipeline's cost tracker.

## Rules for implementers

* Return a `Usage` (tokens/characters/requests) from billable calls; never compute prices -
  `core/costs.py` does that from `config/providers.yaml`.
* `generate_structured` must return a validated instance or raise `StructuredOutputError`.
  Use `core.providers.parse_structured`; do not repair or silently ignore malformed output.
* Read secrets from environment variables only; never log them.
* Expose `name` (and `model`/`default_voice` where relevant): they are part of each stage's
  idempotency fingerprint, so changing the provider or model re-runs the affected stages.

## Adding a provider

1. Implement the interface under `providers/<kind>/<name>.py`.
2. Register a factory in `providers/registry.py` (`_FACTORIES[kind][name]`).
3. Select it in `config/providers.yaml` (`selected:`) and add pricing.
4. Test with the mock-based pipeline tests plus provider-specific tests that use recorded or
   stubbed responses (no live calls in CI).

## Mock behaviour useful in tests

* `MockLLMProvider(raw_overrides={"LessonPlan": "..."}, fail_on={"Script"})` - inject malformed
  output or failures. `.calls` lists every call.
* `MockTTSProvider(fail_on_text_containing="...")` - fail on a scene.
* `MockResearchProvider` - claims containing `UNVERIFIABLE` come back `UNCERTAIN_CLAIM`.
