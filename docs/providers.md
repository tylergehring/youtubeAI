# Providers

Interfaces live in `core/providers.py`; implementations in `providers/<kind>/`.

Source material is never fetched by a provider: transcripts are supplied by the user (see
`core/ingest.py`), so there are no YouTube or transcription providers.

| Interface | Methods | Mock |
|---|---|---|
| `LLMProvider` | `generate`, `generate_structured(prompt, schema)` | `providers/llm/mock.py` |
| `TTSProvider` | `synthesize(text, out_path)` | `providers/tts/mock.py` |
| `ImageProvider` | `generate(prompt, out_path, width, height)` | `providers/image/mock.py` |
| `ResearchProvider` | `verify_claim(claim, topic)` | `providers/research/mock.py` |

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
