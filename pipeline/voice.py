"""Generate narration audio for every scene and record its real duration."""

from __future__ import annotations

from core.models.manifest import StageName
from core.models.media import AudioManifest, AudioRecord
from core.models.scenes import ScenePlan
from pipeline import artifacts
from pipeline.stage import Stage, StageContext, StageResult, make_main


class VoiceStage(Stage):
    """Synthesise one audio file per scene."""

    name = StageName.VOICE

    def provider_parts(self, ctx: StageContext) -> tuple[str, str, str]:
        tts = ctx.providers.tts
        return tts.name, tts.model, tts.default_voice

    def run(self, ctx: StageContext) -> StageResult:
        plan = ctx.load(artifacts.scenes_json(ctx.paths), ScenePlan)
        tts = ctx.providers.tts
        records: list[AudioRecord] = []
        files = []

        for scene in plan.scenes:
            target = artifacts.scene_audio(ctx.paths, scene.scene_id)
            ctx.log.info("Synthesising scene %d with '%s'", scene.scene_id, tts.name)
            result = tts.synthesize(scene.narration, target)
            ctx.record_usage(tts.name, tts.model, result.usage, note=f"scene {scene.scene_id}")
            files.append(result.path)
            records.append(
                AudioRecord(
                    scene_id=scene.scene_id,
                    path=ctx.paths.relative(result.path),
                    duration_s=result.duration_s,
                    provider=tts.name,
                    voice=result.voice,
                    metadata=result.metadata,
                )
            )

        manifest_path = ctx.save(artifacts.audio_json(ctx.paths), AudioManifest(records=records))
        ctx.prune(ctx.paths.subdir("audio"), {manifest_path, *files})
        return StageResult([manifest_path, *files])


main = make_main(VoiceStage)

if __name__ == "__main__":
    raise SystemExit(main())
