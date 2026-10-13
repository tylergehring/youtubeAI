# Pipeline

Stage order and dependencies (a stage needs its dependencies `succeeded` or `skipped`):

| # | Stage | Depends on | Output | Fake (M1) behaviour |
|---|---|---|---|---|
| 1 | `acquire` | - | `source/source.json`, `transcript/source_NN.json` | verifies each uploaded file's hash, parses `.txt/.srt/.vtt`, strips `[music]`/`>>`-style noise (counted in provenance), splits long blocks into sentence-based chunks; no network |
| 2 | `knowledge` | acquire | `knowledge/knowledge.json` | mock LLM, strict `KnowledgeBase` schema; sends every transcript in full and **fails** (calling nothing) if they exceed `knowledge.max_source_chars` |
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

## Mock content

The mock providers return placeholder content templated on your topic, labelled `[mock]`, with
the transcript labels as `source_refs` and obviously fake evidence (`example.com/mock-evidence`).
They never read the transcript, so `source_overlap` is only meaningful with a real LLM.

## Cost tracking

Every provider call is recorded in `job_cost.json` (provider, model, tokens/characters,
estimated cost, stage, job). Prices come from `config/providers.yaml`; mock prices are zero.
Reused stages make no calls and add no records.

## Finance reels (`--format finance_facts`)

A separate content path, not a variant of the transcript/scene model. A job has a `format`
(`explainer`, the default and unchanged, or `finance_facts`) and each format runs its own stages:

| Stage | Output | What it does |
|---|---|---|
| `finance` | `finance/reel_plan.json` | validates the spec, computes figures, re-verifies asset hashes, probes the background/voiceover, schedules the reveal |
| `render` | `render/video.mp4`, `layout.json`, `render.json` | Pillow lays out the text, FFmpeg composites background + scrim + staged layers |
| `validate` | `validation/report.json` | technical checks plus figure/provenance/safe-area integrity checks |
| `package` | `final/video.mp4`, `provenance.json`, `review_summary.json` | -> `awaiting_review` |

`acquire`..`captions` do not apply: no transcripts, LLM, scenes, TTS or captions are used, and
running one of them on a reel job exits `2` without touching the job. The usual fingerprint/reuse/
`--force` behaviour applies to the four stages above. A reel job's manifest tracks only these
stages; explainer manifests and fingerprints are unchanged.

### Creating a reel

```powershell
python -m pipeline.jobs create --format finance_facts --reel-spec spec.yaml   # topic defaults to the title
python -m pipeline.run --job ID
python -m pipeline.preview --job ID      # render/preview/frame_*.png and contact_sheet.png
```

`params.json` accepts `"format": "finance_facts", "reel_spec": "path"`. The spec and its files are
validated before the job exists and copied (with SHA-256) into `source/reel/`; a missing or
invalid file fails creation clearly and leaves no job behind. `ingest.allowed_dirs` applies to
them too.

### Spec fields (JSON or YAML, `examples/finance/`)

| Field | Notes |
|---|---|
| `title`, `cta` | the question at the top and the closing question; promise/advice wording ("guaranteed", "risk-free", "you should invest"...) is rejected |
| `methodology` | the assumptions line. Required for `manual` figures; optional for calculated ones, where it is generated (`Using a 4.7% withdrawal rate`). If you write your own it must still show every assumption |
| `figures` | `kind: manual` (rows of `label`/`value` display text), `withdrawal_rate`, `age_target` or `compound_growth` (below). 1-12 rows; label <= 32 and value <= 16 characters |
| `disclosure` | always visible, bottom of the frame |
| `background` | `path` (local file, relative to the spec), `origin` (`user_supplied`, `licensed_project_asset`, `generated_placeholder`), `license` (required text), optional `attribution` |
| `voiceover` | optional `path` to narration audio you recorded or licensed (`.wav/.mp3/.m4a/.aac`) |
| `provenance` | `verification: illustrative` (default) or `sourced`; `sources[]` with `title`, `url`, `as_of` date, optional `publisher`; `notes` |
| `accent_color` | optional `#RRGGBB` |

**Financial correctness.** Figures are never produced by an LLM. Unsourced figures stay
`illustrative` and then the disclosure must say they are hypothetical/illustrative; `sourced`
requires at least one citation (title, http(s) URL, date). URLs are recorded, never fetched.
Calculated figures use `Decimal` arithmetic (60 digits) and the full record (inputs, formula,
assumptions, rounding, unrounded results) is written to `reel_plan.json` and `final/provenance.json`;
`validate` recomputes it from the stored spec and fails on any difference.

- `withdrawal_rate`: `required_capital = annual_income / (withdrawal_rate_pct / 100)`, one row per
  `annual_incomes` entry, labelled `$30K/year` (labels are exact, never rounded). Display is
  half-up: `$638K` below $1M (to the nearest $1K), `$1.06M` from $1M (two decimals), `$1.20B` from $1B.
- `compound_growth`: what a lump sum grows to after each entry of `years`:
  `value = initial_amount * (1 + r/m)^(m * years)`. **Required:** `initial_amount`,
  `annual_return_pct`, `compounding`, `years`. No contributions; the methodology line states this.
  Displayed in whole dollars (half-up), labelled `After 10 years`.
- `age_target`: the level contribution per period from each `starting_ages` entry to reach
  `target_amount` by `target_age`. **Required, no defaults:** `target_amount`, `target_age`,
  `starting_ages`, `annual_return_pct` (nominal annual), `contribution_frequency` and
  `compounding` (`annual|semiannual|quarterly|monthly`); `contribution_timing` (`end` default |
  `start`). With `m` compounding and `f` contribution periods a year, the per-period growth is
  `i = (1 + r/m)^(m/f) - 1`, `n = years * f`, `contribution = target / (((1+i)^n - 1) / i)` (times
  `1+i` for `start`; `n` for a 0% return). The balance starts at $0. The contribution is rounded
  **up** to a whole dollar so the target is actually met. All assumptions are shown on screen. The
  output is "contribution needed at each starting age"; it never presents the withdrawal-rate table
  as amounts to invest.

### Layout, timing, assets

- Frame, safe margins, colours, timing and fonts live in the `finance:` block of
  `config/settings.yaml` (`VIDGEN__finance__width=...` works too). Default 1080x1920, 30 fps;
  top 10% / bottom 20% / sides 7% are kept free of text for platform UI. Type sizes are designed
  at 1080 px wide and scale with the frame; if the text does not fit it shrinks (down to
  `min_text_scale`, default 60%) and otherwise rendering fails instead of overflowing.
  `validate` checks every box in `layout.json` against the safe area.
- Legibility: a dark vertical gradient scrim (strongest at the edges, `scrim_min_opacity` in the
  middle so the footage stays visible), translucent rounded row panels and soft text shadows.
- Reveal: title, methodology, then **one row at a time** (0.9 s apart by default), then the CTA;
  everything stays for a 3 s hold. Disclosure is visible from the start. Fade + short slide only.
- Fonts: configured `font_regular`/`font_bold`, else common system fonts (Segoe UI, Arial, DejaVu,
  Liberation...), else Pillow's bundled font with simulated bold (recorded in `layout.json`).
- Background: image (`.jpg/.jpeg/.png/.webp`, cover-cropped, EXIF-rotated) or video (`.mp4/.mov/
  .m4v/.webm`, scaled/cropped to the frame and looped; its own audio is dropped). Up to
  `finance.max_asset_mb`. Backgrounds under 480 px short side log a warning. Missing, wrongly
  typed, empty, corrupt or tampered files fail with a clear error (at creation, and again in the
  `finance` stage).
- Voiceover and music are optional. With neither, there is no audio track and audio checks
  are skipped. Voiceover can only lengthen the reel; music never changes its duration.

### Single-video editorial workflow

```powershell
python -m pipeline.reel --spec examples\finance\editorial_compound.yaml
```

This creates a new job, runs finance -> render -> validate -> package, and writes key frames,
a contact sheet and `final/cover.png`. It never overwrites an existing job. To resume after
a stage failure, use `python -m pipeline.run --job JOB_ID`, followed by
`python -m pipeline.preview --job JOB_ID` for preview frames.

The optional `style` block selects `preset: editorial` (classic remains the default).
Editorial defaults to left-aligned headlines and ruled rows, with a minimum 85% text scale.
Use roughly 4-6 rows; shorten the text if it cannot fit rather than making it unreadably small.
Both presets accept `title_align: left | center`, `row_format: panels | rules`,
`title_size: 48..110` and `row_size: 36..64` (reference pixels at 1080 wide).
`eyebrow` adds a short label above the headline. Explicit YAML multiline text preserves line
breaks; this is plain text, not HTML or Markdown rich-text rendering.

`style.font_regular` and `style.font_bold` accept local TTF/OTF/TTC paths relative to the spec.
`style.font_license` is required when either is supplied. Font files are validated, copied and
hashed at job creation, checked at rendering, and recorded in provenance. Without per-spec
fonts, the existing global font settings/system fallback apply. No fonts are downloaded.

Optional `pacing: {row_interval_s: 1.2, end_hold_s: 4}` overrides the row spacing and final hold
for this video without changing global defaults. Other reveal settings remain in `finance.timing`.

```yaml
music:
  path: music.wav
  license: Your licence statement
  attribution: Required artist credit
  start_s: 0
  gain_db: -12
  fade_in_s: 0.5
  fade_out_s: 1.5
```

Music paths are local, relative to the spec, and use the configured audio extensions. Music
must contain audible audio; `start_s` must precede the source's end. The track loops as needed,
starts at that offset and is trimmed to the reel duration. Processing uses FFmpeg loudnorm
(-18 LUFS target, not a certified two-pass loudness measurement), then `gain_db`, fades and a
peak limiter. With narration, a sidechain compressor lowers music during speech. Keep voiceover
levels sensible and listen to the exported mix before publishing. The source video's own audio
is always discarded.

`background.crop_x` and `crop_y` are 0..1 focal positions in the available crop range:
0 = left/top, 0.5 = centred (default), 1 = right/bottom. They work for both images and video.
The editorial preset encodes H.264 at CRF 18 / medium; classic keeps CRF 20 / veryfast.
All audio outputs use AAC at 192 kbps, 44.1 kHz.

`final/publishing.json` is assembled from the actual resolved plan, not an LLM's recollection:
title (under 100 characters), description containing the displayed figures and assumptions,
source dates/URLs, user-provided credits, and topic-relevant hashtags. It is a draft requiring
review; no SEO performance is promised, no facts are added, and nothing is uploaded.

Publishing remains separate: `python -m pipeline.youtube upload` consumes the final video and
publishing draft, requires successful technical QA, explicit approval and confirmation of rights,
and checks the authorized channel against a required channel ID. See the README's YouTube section.
It stores a durable receipt and verifies YouTube's private future schedule before moving an
approved job to `uploaded`; ambiguous failures retain the session/video ID for recovery.

Reference review: [ShortFormGenerator](https://github.com/DrewStewart7/ShortFormGenerator)
uses split-screen clip composition; [Neural Reels](https://github.com/double-k-3033/NR-AI-short-form-video-generator)
separates scene/media assembly from script-grounded SEO output. This repo follows that separation
but retains deterministic single-background finance layouts, explicit framing and restrained
animation instead of randomized effects. No external implementation or prompt was copied.

### Rendering approach (why not Remotion)

`spikes/remotion/` (untouched) was reviewed. It was not integrated because: it is a 1920x1080
narrated-beats prototype with content hard-coded in `beats.ts`; Node was not available on the
development machine, and Remotion needs Node plus a downloaded Chromium and (as written)
fetches Google Fonts at render time, which breaks a deterministic offline demo; and the existing
`Renderer` interface is shaped around narrated scenes, audio timelines and captions. Instead the
text-first reel reuses the repo's established approach (Python orchestrates, FFmpeg encodes via
the existing wrapper, `RenderResult`, atomic partial-file writes) with Pillow for text layout and
a small `ReelRenderer` next to `Renderer` (`rendering/reel.py`). What carried over from the spike:
restrained fade/slide reveals, a theme-token approach and per-beat timing.
Content is data (the spec), not code.

### Validation checks (reels)

`video_exists`, `file_size`, `video_opens`, `duration_reasonable`, `resolution`, `frame_rate`,
`audio_present`/`audio_not_silent` (when voiceover or music exists), `background_asset` (file + hash),
`music_asset`/`font_assets` when supplied, `music_matches_spec`, `presentation_matches_spec`,
`figures_match_spec`, `provenance_recorded`, `all_text_rendered`, `text_in_safe_area`.
Licence text is recorded, not legally verified; a passing asset/hash check does not clear rights.

### Limitations

Single background, single column of up to 12 rows; no per-word captions; fonts depend on the
machine unless pinned; Jenkinsfile still targets the explainer only. Safe margins are a template,
not a guarantee against every platform's UI: review in the intended app before publication.
