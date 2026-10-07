# Architecture

## Principles

1. **Jenkins orchestrates, it does not contain logic.** Jenkins runs
   `python -m pipeline.<stage> --job JOB_ID`. Every stage also runs locally.
2. **Everything belongs to a Job.** `workspace/jobs/<job_id>/` holds all artifacts. There are no
   global temp files.
3. **`manifest.json` is the single source of truth.** SQLite (`workspace/index.db`) is a derived
   index and can be rebuilt with `python -m pipeline.jobs reindex`.
4. **Providers hide external services.** Stages depend only on the interfaces in
   `core/providers.py`; concrete providers are chosen by name in `config/providers.yaml`.
5. **The scene spec separates content from rendering.** `core/models/scenes.py` is the contract.
6. **Audio duration is the timing authority.** `rendering/timeline.py` computes scene timing from
   real audio lengths; captions and the renderer both use it.

## Layers

```
pipeline/*  ->  core/ (models, jobs, config, logging, costs, provider interfaces)
            ->  providers/* (implement core.providers)
            ->  rendering/* (Renderer abstraction + FFmpeg)
```

## Job directory

```
workspace/jobs/<job_id>/
  input.json  manifest.json  job_cost.json  .manifest.lock
  source/originals/ (your uploaded transcripts)  source/source.json (provenance)  transcript/ (parsed)
  knowledge/ research/ lesson/ script/ scenes/
  visuals/ audio/ captions/ render/ validation/ final/ logs/pipeline.log
```

## Idempotency and retries

Each stage record in the manifest stores `state`, `attempts`, `outputs` and an
`input_fingerprint`. The fingerprint hashes: stage name and version, job input and parameters,
the fingerprints of the stages it depends on, relevant settings, and the providers/models used.

* Same fingerprint + `succeeded`/`skipped` + outputs on disk -> **reused**, no work, no API call.
* `--force`, changed inputs/config/providers, a missing output, or a `stale` state -> re-run.
* A stage that actually re-runs marks its transitive dependents `stale` (dependency graph in
  `core/models/manifest.py`).
* A failure records `failed` on that stage and `failed` on the job (with `failed_stage`), and
  leaves all earlier artifacts untouched. Re-running the same command resumes there.

## Job status

Overall `status` is a state machine (`ALLOWED_TRANSITIONS`). While a stage runs the job reports
that stage's status (e.g. `rendering`); it keeps it after the stage succeeds until the next stage
starts. `package` ends in `awaiting_review`. `failed`, `approved`, `rejected`, `uploaded` follow
the transitions in code. Per-stage detail lives in `manifest.stages`.

Two statuses were added to your list: `generating_captions` and `packaging`.

## Deviations from the original proposal

* `timeline.py` lives in `rendering/` (not `pipeline/`) because both captions and render use it.
* There is no YouTube provider, transcription provider or `transcribe` stage: you supply
  transcripts. (YouTube upload in Milestone 10 is out of scope for now.)
* `lesson.py` stage added; `notify.py`, real providers, web approval and upload are later
  milestones.
* `docker-compose.yml` and `LICENSE` are not created yet.
* `scene_padding_s` silence is appended to every scene; timings snap to whole frames.

## Security notes

* No network access for sources: input is a topic plus local transcript files. Files are validated
  (extension, size, UTF-8, parseable, no duplicates), copied into the job and hashed
  (`core/ingest.py`); `acquire` re-checks the hash. `ingest.allowed_dirs` can restrict where files
  may be read from (the Jenkinsfile sets it). Reference URLs are only recorded, never fetched.
* Job IDs are validated and manifest-relative paths cannot escape the job directory.
* `subprocess` is only used with argument lists and `shell=False` (`rendering/ffmpeg.py`).
* Logs are passed through a redaction filter (env-var secrets and common key patterns).
* The Jenkinsfile passes user text through `params.json` (`pipeline.jobs create --params-file`),
  never through a shell command line.
