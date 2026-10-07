# Pipeline

Stage order and dependencies (a stage needs its dependencies `succeeded` or `skipped`):

| # | Stage | Depends on | Output | Fake (M1) behaviour |
|---|---|---|---|---|
| 1 | `acquire` | - | `source/source.json`, `transcript/source_NN.json` | verifies each uploaded file's hash, parses `.txt/.srt/.vtt`, records provenance; no network |
| 2 | `knowledge` | acquire | `knowledge/knowledge.json` | mock LLM, strict `KnowledgeBase` schema; prompt contains the topic and every transcript |
| 3 | `research` | knowledge | `research/research.json` | mock research provider verifies SOURCE_CLAIMs |
| 4 | `lesson` | knowledge, research | `lesson/lesson.json` | mock LLM |
| 5 | `script` | knowledge, research, lesson | `script/script.json` | mock LLM; rejects unknown claim IDs |
| 6 | `scenes` | script | `scenes/scenes.json` | mock LLM; every script section must have a scene |
| 7 | `visuals` | scenes | `visuals/visuals.json` | palette per scene (no image generation yet) |
| 8 | `voice` | scenes | `audio/scene_NNN.wav`, `audio/audio.json` | mock TTS tone; real durations recorded |
| 9 | `captions` | scenes, voice | `captions/captions.srt`, `captions.json` | real SRT from real durations |
| 10 | `render` | scenes, visuals, voice, captions | `render/video.mp4`, `render.json` | real FFmpeg encode of solid-colour scenes + audio |
| 11 | `validate` | all above | `validation/report.json` | real deterministic checks via FFmpeg |
| 12 | `package` | captions, render, validate | `final/video.mp4`, `captions.srt`, `review_summary.json` | -> `awaiting_review` |

## Commands

```powershell
python -m pipeline.jobs create --topic "How TCP works"                    # prints the job ID
python -m pipeline.jobs create --topic "How TCP works" --transcript a.txt --transcript b.srt
python -m pipeline.jobs create --params-file params.json              # used by Jenkins
python -m pipeline.jobs status --job ID | show --job ID | list | reindex

python -m pipeline.<stage> --job ID [--force] [--workspace DIR] [--config-dir DIR] [--log-level L]
python -m pipeline.run --job ID [--from STAGE] [--to STAGE] [--force]
```

Exit codes: `0` success/reused/skipped; `1` stage failed (job marked `failed`); `2`
usage/config/job-not-found/upstream-not-ready/invalid transition (job state unchanged).

## params.json (used by Jenkins)

```json
{"topic": "How TCP works", "audience": "beginner", "duration": 3,
 "transcripts": ["C:/t/a.txt", {"path": "C:/t/b.srt", "label": "Creator B", "url": "https://example.com/b"}]}
```

Labels and reference URLs are only recorded as provenance. Nothing is ever fetched from them.

## Validation checks

`video_exists`, `file_size`, `video_opens`, `duration_reasonable` (range and match with timeline),
`resolution`, `frame_rate`, `audio_present`, `audio_not_silent`, `all_scenes_rendered`,
`assets_exist`, `captions_exist`, `narration_present`, `scene_durations_positive`,
`visuals_cover_scenes`, `source_overlap` (share of the script's 5-grams found in your uploaded
transcripts; skipped when none were supplied).

A failing check fails the `validate` stage but the report is kept. Semantic (LLM) QA is a later
milestone (`qa.semantic_enabled` currently only logs a warning).

## Cost tracking

Every provider call is recorded in `job_cost.json` (provider, model, tokens/characters,
estimated cost, stage, job). Prices come from `config/providers.yaml`; mock prices are zero.
Reused stages make no calls and add no records.
