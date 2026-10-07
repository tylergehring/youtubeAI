# Development

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Requires Python 3.11+. FFmpeg is needed by `render`, `validate` and the ffmpeg-marked tests. Use a
system install on PATH, set `FFMPEG_PATH` (or `ffmpeg_path` in `config/settings.yaml`), or rely on
the bundled binary from the `dev` extra:

```powershell
$env:FFMPEG_PATH = (python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
```

The tests discover FFmpeg automatically (PATH, then the bundled binary) and skip ffmpeg tests
if neither exists.

## Checks

```powershell
pytest                         # everything (~45s)
pytest tests/unit              # fast, no ffmpeg
pytest -m ffmpeg               # only tests that encode video
ruff check . ; ruff format --check .
mypy
```

## Configuration

* `config/settings.yaml`, `styles.yaml`, `providers.yaml`; secrets only in the environment/`.env`
  (copy `.env.example`).
* Override any setting: `VIDGEN__<section>__<key>=value` (case-insensitive), e.g.
  `VIDGEN__video__width=1920`. Also `VIDGEN_WORKSPACE_DIR`, `VIDGEN_CONFIG_DIR`, `FFMPEG_PATH`.

## Debugging a failed job

```powershell
python -m pipeline.jobs status --job ID        # which stage failed and why
Get-Content workspace\jobs\ID\logs\pipeline.log
python -m pipeline.<stage> --job ID --log-level DEBUG   # retry just that stage
```

## Adding a stage

1. Add the name to `StageName` (order matters), its dependencies to `STAGE_DEPENDENCIES`, and its
   running status to `STAGE_STATUS`/`JobStatus` in `core/models/manifest.py`.
2. Create `pipeline/<stage>.py` with a `Stage` subclass, `main = make_main(...)` and the
   `__main__` guard; list any settings/providers that affect output in `config_parts` /
   `provider_parts`.
3. Register it in `pipeline/run.py:default_stages` and add tests.
