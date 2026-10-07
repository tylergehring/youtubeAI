"""Stage contract, the idempotent stage runner and the shared command-line entry point.

Every stage follows the same lifecycle (see run_stage):

  1. verify upstream stages succeeded
  2. fingerprint inputs; if a previous success has the same fingerprint, reuse it (no work,
     no API calls) unless --force
  3. mark running, execute, then record success (outputs + fingerprint) or failure
  4. mark downstream stages stale when this stage actually re-ran

A failure leaves every earlier artifact untouched, so rerunning the failed stage never
repeats completed work.
"""

from __future__ import annotations

import argparse
import logging
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol, TypeVar

from pydantic import BaseModel

from core.config import Settings, load_settings
from core.costs import CostTracker
from core.exceptions import StageError, UpstreamNotReadyError, VidgenError
from core.hashing import fingerprint
from core.jobs import JobPaths, JobStore
from core.logging import configure_logging, get_logger, job_log_file, redact
from core.models.manifest import (
    STAGE_DEPENDENCIES,
    STAGE_STATUS,
    JobManifest,
    JobStatus,
    StageName,
    StageState,
    downstream_of,
    utcnow,
)
from core.providers import Usage
from core.storage import read_model, write_model
from providers.registry import ProviderSet, build_providers

M = TypeVar("M", bound=BaseModel)

EXIT_OK = 0
EXIT_STAGE_FAILED = 1
EXIT_USAGE = 2


class StageMain(Protocol):
    def __call__(self, argv: list[str] | None = None) -> int: ...


class Outcome(StrEnum):
    EXECUTED = "executed"
    REUSED = "reused"
    SKIPPED = "skipped"


@dataclass
class StageResult:
    outputs: list[Path] = field(default_factory=list)
    skipped: bool = False
    note: str | None = None


@dataclass
class StageContext:
    job_id: str
    stage: StageName
    paths: JobPaths
    store: JobStore
    manifest: JobManifest
    settings: Settings
    providers: ProviderSet
    cost: CostTracker
    log: logging.LoggerAdapter[logging.Logger]
    force: bool = False

    def load(self, path: Path, model_type: type[M]) -> M:
        return read_model(path, model_type)

    def save(self, path: Path, model: BaseModel) -> Path:
        write_model(path, model)
        return path

    def record_usage(self, provider: str, model: str, usage: Usage, note: str = "") -> None:
        self.cost.record(
            stage=self.stage.value, provider=provider, model=model, usage=usage, note=note
        )

    def prune(self, directory: Path, keep: set[Path]) -> None:
        """Delete files left over from a previous run that this run did not produce."""
        if not directory.is_dir():
            return
        kept = {p.resolve() for p in keep}
        for child in directory.iterdir():
            if child.is_file() and child.resolve() not in kept:
                child.unlink()


class Stage(ABC):
    name: ClassVar[StageName]
    version: ClassVar[int] = 1  # bump when the stage's logic changes output semantics
    final_status: ClassVar[JobStatus | None] = None

    @abstractmethod
    def run(self, ctx: StageContext) -> StageResult: ...

    def config_parts(self, ctx: StageContext) -> Any:
        """Settings that affect this stage's output (part of the idempotency fingerprint)."""
        return None

    def provider_parts(self, ctx: StageContext) -> Any:
        """Providers/models this stage uses (part of the idempotency fingerprint)."""
        return None


def compute_fingerprint(stage: Stage, ctx: StageContext) -> str:
    manifest = ctx.manifest
    upstream = {
        dep.value: manifest.stage(dep).input_fingerprint for dep in STAGE_DEPENDENCIES[stage.name]
    }
    return fingerprint(
        stage.name.value,
        stage.version,
        manifest.input.model_dump(mode="json"),
        manifest.style,
        manifest.target_duration_minutes,
        manifest.audience.value,
        upstream,
        stage.config_parts(ctx),
        stage.provider_parts(ctx),
    )


def _check_upstream(manifest: JobManifest, stage: Stage) -> None:
    for dep in STAGE_DEPENDENCIES[stage.name]:
        state = manifest.stage(dep).state
        if state not in (StageState.SUCCEEDED, StageState.SKIPPED):
            raise UpstreamNotReadyError(
                f"Stage '{stage.name.value}' needs '{dep.value}' to succeed first "
                f"(currently {state.value}). Run: python -m pipeline.{dep.value} "
                f"--job {manifest.job_id}"
            )


def _reusable(ctx: StageContext, stage: Stage, fp: str) -> bool:
    record = ctx.manifest.stage(stage.name)
    if record.state not in (StageState.SUCCEEDED, StageState.SKIPPED):
        return False
    if record.input_fingerprint != fp:
        return False
    return all(ctx.paths.absolute(rel).exists() for rel in record.outputs)


def run_stage(
    stage: Stage,
    store: JobStore,
    job_id: str,
    settings: Settings,
    providers: ProviderSet,
    *,
    force: bool = False,
) -> Outcome:
    name = stage.name
    paths = store.paths(job_id)
    log = get_logger(job_id, name.value)

    with job_log_file(paths.log_file):
        manifest = store.load(job_id)
        _check_upstream(manifest, stage)
        ctx = StageContext(
            job_id=job_id,
            stage=name,
            paths=paths,
            store=store,
            manifest=manifest,
            settings=settings,
            providers=providers,
            cost=CostTracker(job_id, paths, settings.providers.pricing),
            log=log,
            force=force,
        )
        fp = compute_fingerprint(stage, ctx)

        if not force and _reusable(ctx, stage, fp):
            log.info("Already complete with identical inputs; reusing existing result.")
            return Outcome.REUSED

        # Validates the state-machine transition before any state is modified.
        store.set_status(job_id, STAGE_STATUS[name])

        def mark_running(m: JobManifest) -> None:
            record = m.stage(name)
            record.state = StageState.RUNNING
            record.attempts += 1
            record.started_at = utcnow()
            record.finished_at = None
            record.error = None
            record.note = None

        ctx.manifest = store.update(job_id, mark_running)
        log.info("Starting (attempt %d)%s", ctx.manifest.stage(name).attempts,
                 " [forced]" if force else "")  # fmt: skip

        try:
            result = stage.run(ctx)
        except Exception as exc:
            message = redact(f"{type(exc).__name__}: {exc}")
            log.error("Stage failed: %s", message)
            log.debug("Traceback", exc_info=True)

            def mark_failed(m: JobManifest) -> None:
                record = m.stage(name)
                record.state = StageState.FAILED
                record.finished_at = utcnow()
                record.error = message

            store.update(job_id, mark_failed)
            store.set_status(job_id, JobStatus.FAILED, failed_stage=name, error=message)
            if isinstance(exc, StageError):
                raise
            raise StageError(f"Stage '{name.value}' failed: {message}") from exc

        outputs = [paths.relative(p) for p in result.outputs]

        def mark_done(m: JobManifest) -> None:
            record = m.stage(name)
            record.state = StageState.SKIPPED if result.skipped else StageState.SUCCEEDED
            record.finished_at = utcnow()
            record.input_fingerprint = fp
            record.outputs = outputs
            record.note = result.note
            record.error = None
            for downstream in downstream_of(name):
                dep_record = m.stage(downstream)
                if dep_record.state in (StageState.SUCCEEDED, StageState.SKIPPED):
                    dep_record.state = StageState.STALE

        store.update(job_id, mark_done)
        if stage.final_status is not None:
            store.set_status(job_id, stage.final_status)
        if result.skipped:
            log.info("Skipped: %s", result.note or "nothing to do")
            return Outcome.SKIPPED
        log.info("Completed: %d output file(s)", len(outputs))
        return Outcome.EXECUTED


# ---------------------------------------------------------------- command line


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--job", required=True, help="Job ID (directory name under workspace/jobs)")
    parser.add_argument("--force", action="store_true", help="Re-run even if already complete")
    parser.add_argument("--workspace", type=Path, help="Override the workspace directory")
    parser.add_argument("--config-dir", type=Path, help="Override the config directory")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING or ERROR")


def bootstrap(args: argparse.Namespace) -> tuple[Settings, JobStore, ProviderSet]:
    settings = load_settings(args.config_dir)
    if args.workspace:
        settings = settings.model_copy(update={"workspace_dir": args.workspace.resolve()})
    configure_logging(args.log_level or settings.log_level)
    return settings, JobStore(settings.workspace_dir), build_providers(settings)


def handle_errors(action: Callable[[], int]) -> int:
    """Map exceptions to exit codes: 0 ok, 1 stage failed, 2 usage/config/not-ready."""
    try:
        return action()
    except StageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_STAGE_FAILED
    except VidgenError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - last-resort guard for the CLI
        print(f"ERROR: unexpected {type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
        return EXIT_STAGE_FAILED


def make_main(stage_type: type[Stage]) -> StageMain:
    """Build the `python -m pipeline.<stage>` entry point for a stage class."""

    def main(argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(
            prog=f"python -m pipeline.{stage_type.name.value}",
            description=(stage_type.__doc__ or "").strip().splitlines()[0]
            if stage_type.__doc__
            else None,
        )
        add_common_args(parser)
        args = parser.parse_args(argv)

        def action() -> int:
            settings, store, providers = bootstrap(args)
            outcome = run_stage(
                stage_type(), store, args.job, settings, providers, force=args.force
            )
            print(f"[{args.job}] {stage_type.name.value}: {outcome.value}")
            return EXIT_OK

        return handle_errors(action)

    return main
