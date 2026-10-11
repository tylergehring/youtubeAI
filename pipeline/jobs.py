"""Job management CLI: create, list, show, status, reindex.

python -m pipeline.jobs create --topic "How TCP works" --transcript a.txt --transcript b.srt
python -m pipeline.jobs status --job JOB_ID
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.config import Settings, load_settings
from core.exceptions import InvalidInputError
from core.ingest import SourceSpec, prepare_sources
from core.jobs import JobStore
from core.logging import configure_logging
from core.models.manifest import Audience, JobFormat, JobManifest, stages_for
from core.reel_input import prepare_reel
from pipeline.stage import EXIT_OK, handle_errors


def _store_and_settings(args: argparse.Namespace) -> tuple[Settings, JobStore]:
    settings = load_settings(args.config_dir)
    if args.workspace:
        settings = settings.model_copy(update={"workspace_dir": args.workspace.resolve()})
    configure_logging(settings.log_level)
    return settings, JobStore(settings.workspace_dir)


def _print_status(manifest: JobManifest) -> None:
    print(f"job:     {manifest.job_id}")
    print(f"status:  {manifest.status.value}")
    if manifest.failed_stage:
        print(f"failed:  {manifest.failed_stage.value} - {manifest.error}")
    print(f"topic:   {manifest.topic}")
    print(f"{'stage':<12}{'state':<11}{'tries':<7}note")
    for name in stages_for(manifest.input.format):
        record = manifest.stage(name)
        note = record.error or record.note or ""
        print(f"{name.value:<12}{record.state.value:<11}{record.attempts:<7}{note}")


_SCALAR_KEYS = ("topic", "style", "duration", "audience", "job_id", "format", "reel_spec")
_PARAM_KEYS = {*_SCALAR_KEYS, "transcripts"}
_SOURCE_KEYS = {"path", "label", "url"}


def _specs_from(raw: object) -> list[SourceSpec]:
    """Accept transcripts as plain paths or {"path", "label", "url"} objects."""
    if not isinstance(raw, list):
        raise InvalidInputError("'transcripts' must be a list.")
    specs: list[SourceSpec] = []
    for item in raw:
        if isinstance(item, str):
            specs.append(SourceSpec(Path(item)))
        elif isinstance(item, dict) and "path" in item and set(item) <= _SOURCE_KEYS:
            specs.append(SourceSpec(Path(str(item["path"])), item.get("label"), item.get("url")))
        else:
            raise InvalidInputError(
                f"Each transcript must be a path or an object with keys in {sorted(_SOURCE_KEYS)}."
            )
    return specs


def _create_params(args: argparse.Namespace) -> tuple[dict[str, Any], list[SourceSpec]]:
    """Merge CLI flags with an optional JSON params file (CLI flags win; blanks are ignored).

    The params file lets orchestrators pass user-supplied text without building a shell command.
    """
    params: dict[str, Any] = {k: getattr(args, k, None) for k in _SCALAR_KEYS}
    specs = [SourceSpec(Path(p)) for p in args.transcript or []]
    if args.params_file:
        try:
            loaded = json.loads(Path(args.params_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidInputError(f"Cannot read params file: {exc}") from exc
        if not isinstance(loaded, dict) or not set(loaded) <= _PARAM_KEYS:
            raise InvalidInputError(
                f"Params file must be an object with keys in {sorted(_PARAM_KEYS)}"
            )
        for key in _SCALAR_KEYS:
            if params[key] is None and key in loaded:
                params[key] = loaded[key]
        specs += _specs_from(loaded.get("transcripts", []))
    params = {k: (None if v == "" else v) for k, v in params.items()}
    return params, specs


def cmd_create(args: argparse.Namespace) -> int:
    settings, store = _store_and_settings(args)
    params, specs = _create_params(args)
    try:
        job_format = JobFormat(params["format"] or JobFormat.EXPLAINER)
    except ValueError as exc:
        raise InvalidInputError(f"Invalid job format: {exc}") from exc

    reel = None
    if job_format is JobFormat.FINANCE_FACTS:
        if not params["reel_spec"]:
            raise InvalidInputError("--reel-spec (or 'reel_spec' in the params file) is required.")
        if specs:
            raise InvalidInputError("Transcripts are not used by the finance_facts format.")
        reel = prepare_reel(Path(str(params["reel_spec"])), settings)
        topic = str(params["topic"] or reel.spec.title)
    else:
        if params["reel_spec"]:
            raise InvalidInputError("--reel-spec only applies to --format finance_facts.")
        if not params["topic"]:
            raise InvalidInputError("A topic is required (--topic or params file).")
        topic = str(params["topic"])

    style = params["style"] or settings.defaults.style
    settings.style(style)  # validates the style exists
    try:
        audience = Audience(params["audience"] or settings.defaults.audience)
        duration = float(params["duration"] or settings.defaults.target_duration_minutes)
    except ValueError as exc:
        raise InvalidInputError(f"Invalid job parameter: {exc}") from exc
    manifest = store.create(
        topic,
        sources=prepare_sources(specs, settings.ingest),
        reel=reel,
        style=style,
        target_duration_minutes=duration,
        audience=audience,
        job_id=params["job_id"],
    )
    print(manifest.job_id)  # stdout carries only the ID so scripts/Jenkins can capture it
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    _, store = _store_and_settings(args)
    for manifest in store.list_jobs():
        print(f"{manifest.job_id}\t{manifest.status.value}\t{manifest.topic}")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    _, store = _store_and_settings(args)
    _print_status(store.load(args.job))
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    _, store = _store_and_settings(args)
    print(json.dumps(store.load(args.job).model_dump(mode="json"), indent=2))
    return EXIT_OK


def cmd_reindex(args: argparse.Namespace) -> int:
    _, store = _store_and_settings(args)
    print(f"Indexed {store.rebuild_index()} job(s)")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pipeline.jobs", description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--config-dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a job and print its ID")
    create.add_argument(
        "--format",
        choices=[f.value for f in JobFormat],
        help="Content format (default: explainer). finance_facts needs --reel-spec",
    )
    create.add_argument("--topic", help="What the video should teach (finance_facts: spec title)")
    create.add_argument(
        "--reel-spec",
        metavar="FILE",
        help="finance_facts: JSON/YAML reel spec; its background file is read from local disk",
    )
    create.add_argument(
        "--transcript",
        action="append",
        metavar="FILE",
        help="Transcript file (.txt/.srt/.vtt) to use as research input; repeatable",
    )
    create.add_argument("--params-file", help="JSON file supplying any of these options")
    create.add_argument("--style")
    create.add_argument("--duration", type=float, help="Target duration in minutes")
    create.add_argument("--audience", choices=[a.value for a in Audience])
    create.add_argument("--job-id", help="Explicit job ID (default: generated)")
    create.set_defaults(func=cmd_create)

    sub.add_parser("list", help="List jobs").set_defaults(func=cmd_list)
    for name, func in (("status", cmd_status), ("show", cmd_show)):
        p = sub.add_parser(name, help=f"{name.capitalize()} a job")
        p.add_argument("--job", required=True)
        p.set_defaults(func=func)
    sub.add_parser("reindex", help="Rebuild the SQLite index").set_defaults(func=cmd_reindex)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return handle_errors(lambda: args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
