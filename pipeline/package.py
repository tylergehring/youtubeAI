"""Assemble the final reviewable package and move the job to awaiting_review."""

from __future__ import annotations

import shutil

from core.models.content import LessonPlan
from core.models.manifest import JobStatus, StageName
from core.models.media import RenderResult, ValidationReport
from core.storage import write_json
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main


def format_duration(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 60}:{total % 60:02d}"


class PackageStage(Stage):
    """Copy final artifacts to final/ and write the review summary and cost report."""

    name = StageName.PACKAGE
    final_status = JobStatus.AWAITING_REVIEW

    def run(self, ctx: StageContext) -> StageResult:
        paths = ctx.paths
        render = ctx.load(artifacts.render_json(paths), RenderResult)
        report = ctx.load(artifacts.validation_json(paths), ValidationReport)
        lesson = ctx.load(artifacts.lesson_json(paths), LessonPlan)

        video = artifacts.final_video(paths)
        captions = artifacts.final_captions(paths)
        shutil.copyfile(paths.absolute(render.video_path), video)
        shutil.copyfile(artifacts.captions_srt(paths), captions)

        cost = ctx.cost.load()
        if not paths.cost_file.is_file():
            ctx.save(paths.cost_file, cost)

        summary = {
            "job_id": ctx.job_id,
            "title": lesson.title,
            "duration": format_duration(render.duration_s),
            "duration_s": round(render.duration_s, 2),
            "resolution": f"{render.width}x{render.height}",
            "technical_checks_passed": report.technical_passed,
            "quality_score": report.semantic.score if report.semantic else None,
            "warnings": len(report.semantic.warnings) if report.semantic else 0,
            "estimated_cost_usd": cost.total_estimated_cost_usd,
            "video": paths.relative(video),
            "captions": paths.relative(captions),
            "status": "awaiting_review",
        }
        summary_path = artifacts.final_summary(paths)
        write_json(summary_path, summary)
        return StageResult([video, captions, summary_path])


main = make_main(PackageStage)

if __name__ == "__main__":
    raise SystemExit(main())
