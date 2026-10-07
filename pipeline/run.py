"""Run the whole pipeline (or a slice of it) locally: python -m pipeline.run --job JOB_ID."""

from __future__ import annotations

import argparse

from core.config import Settings
from core.jobs import JobStore
from core.logging import get_logger
from core.models.manifest import STAGE_ORDER, StageName
from pipeline.acquire import AcquireStage
from pipeline.captions import CaptionsStage
from pipeline.knowledge import KnowledgeStage
from pipeline.lesson import LessonStage
from pipeline.package import PackageStage
from pipeline.render import RenderStage
from pipeline.research import ResearchStage
from pipeline.scenes import ScenesStage
from pipeline.script import ScriptStage
from pipeline.stage import (
    EXIT_OK,
    Outcome,
    Stage,
    add_common_args,
    bootstrap,
    handle_errors,
    run_stage,
)
from pipeline.validate import ValidateStage
from pipeline.visuals import VisualsStage
from pipeline.voice import VoiceStage
from providers.registry import ProviderSet


def default_stages() -> dict[StageName, Stage]:
    stages: list[Stage] = [
        AcquireStage(),
        KnowledgeStage(),
        ResearchStage(),
        LessonStage(),
        ScriptStage(),
        ScenesStage(),
        VisualsStage(),
        VoiceStage(),
        CaptionsStage(),
        RenderStage(),
        ValidateStage(),
        PackageStage(),
    ]
    return {s.name: s for s in stages}


def run_pipeline(
    store: JobStore,
    job_id: str,
    settings: Settings,
    providers: ProviderSet,
    *,
    start: StageName | None = None,
    end: StageName | None = None,
    force: bool = False,
    stages: dict[StageName, Stage] | None = None,
) -> dict[StageName, Outcome]:
    """Run stages in order, stopping at the first failure (the exception propagates)."""
    stages = stages or default_stages()
    first = STAGE_ORDER.index(start) if start else 0
    last = STAGE_ORDER.index(end) if end else len(STAGE_ORDER) - 1
    log = get_logger(job_id, "RUN")
    outcomes: dict[StageName, Outcome] = {}
    for name in STAGE_ORDER[first : last + 1]:
        outcomes[name] = run_stage(stages[name], store, job_id, settings, providers, force=force)
        log.info("%s -> %s", name.value, outcomes[name].value)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.run", description="Run pipeline stages in order."
    )
    add_common_args(parser)
    names = [s.value for s in STAGE_ORDER]
    parser.add_argument("--from", dest="start", choices=names, help="First stage to run")
    parser.add_argument("--to", dest="end", choices=names, help="Last stage to run")
    args = parser.parse_args(argv)

    def action() -> int:
        settings, store, providers = bootstrap(args)
        outcomes = run_pipeline(
            store,
            args.job,
            settings,
            providers,
            start=StageName(args.start) if args.start else None,
            end=StageName(args.end) if args.end else None,
            force=args.force,
        )
        for name, outcome in outcomes.items():
            print(f"[{args.job}] {name.value}: {outcome.value}")
        return EXIT_OK

    return handle_errors(action)


if __name__ == "__main__":
    raise SystemExit(main())
