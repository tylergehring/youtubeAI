# Automated Video Generator

A job-based pipeline that turns a **topic plus transcripts you supply** into an original
educational explainer video, with a human review step before anything is published.

The tool never talks to YouTube (or any site) to get source material. You paste or save the
transcripts of videos you want to learn from as `.txt`, `.srt` or `.vtt` files and pass them in;
the pipeline treats them as research input, builds its own lesson and writes an original script.

**Status: Milestone 1 - fake end-to-end pipeline.** Every stage runs with mock providers
(no AI APIs, no network, no cost). This proves the architecture before real providers are added.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"          # also installs a bundled ffmpeg for tests

# FFmpeg is required for the render/validate stages. Either have it on PATH or:
$env:FFMPEG_PATH = (python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")

$job = python -m pipeline.jobs create --topic "How TCP works" `
    --transcript C:\transcripts\creator_a.txt --transcript C:\transcripts\creator_b.srt
python -m pipeline.run --job $job
python -m pipeline.jobs status --job $job
```

Output lands in `workspace/jobs/<job_id>/final/` (`video.mp4`, `captions.srt`,
`review_summary.json`); `job_cost.json` is in the job root.

Transcripts are optional (a topic alone works) and are copied into the job, so the originals can
move afterwards. Allowed: `.txt` (blank-line paragraphs; lines like `0:05` are read as timestamps),
`.srt`, `.vtt`; UTF-8, up to 5 MB each, max 10 files (`ingest` in `config/settings.yaml`).

Run a single stage (this is exactly what Jenkins does):

```powershell
python -m pipeline.script --job $job            # reuses result if already complete
python -m pipeline.script --job $job --force    # explicitly regenerate
python -m pipeline.run --job $job --from render # re-run a slice
```

Exit codes: `0` success/reused/skipped, `1` stage failed, `2` usage/config/not-ready.

## Development

```powershell
pytest                 # tests (ffmpeg tests auto-skip if no ffmpeg is found)
ruff check . ; ruff format --check .
mypy
```

See [docs/architecture.md](docs/architecture.md), [docs/pipeline.md](docs/pipeline.md),
[docs/providers.md](docs/providers.md) and [docs/development.md](docs/development.md).

## Layout

| Path | Purpose |
|---|---|
| `core/` | Models, job store/state machine, config, logging, cost tracking, provider interfaces |
| `pipeline/` | One module per stage (`python -m pipeline.<stage> --job ID`), plus `run` and `jobs` |
| `providers/` | Concrete providers behind the interfaces (mock only for now) |
| `rendering/` | FFmpeg wrapper, timeline resolution, renderer abstraction |
| `config/` | `settings.yaml`, `styles.yaml`, `providers.yaml` |
| `workspace/` | Generated jobs (git-ignored) |
| `Jenkinsfile` | Orchestration only; calls the same commands |

No `LICENSE` file has been added yet; choose one before publishing.
