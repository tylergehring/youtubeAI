import pytest
from pydantic import ValidationError

from core.models.content import Claim, KnowledgeBase, Script, Transcript
from core.models.manifest import (
    STAGE_DEPENDENCIES,
    STAGE_ORDER,
    JobInput,
    JobManifest,
    JobStatus,
    StageName,
    StageState,
    downstream_of,
)
from core.models.scenes import PacketAnimation, Scene, ScenePlan
from providers import mock_data


class TestManifest:
    def make(self, **kw: object) -> JobManifest:
        base = {
            "job_id": "j1",
            "input": JobInput(topic="x"),
            "style": "technical_explainer",
            "target_duration_minutes": 3,
            "audience": "beginner",
        }
        return JobManifest(**{**base, **kw})  # type: ignore[arg-type]

    def test_defaults(self) -> None:
        m = self.make()
        assert m.status is JobStatus.CREATED
        assert set(m.stages) == set(STAGE_ORDER)
        assert all(r.state is StageState.PENDING for r in m.stages.values())

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            self.make(surprise=1)

    def test_rejects_bad_values(self) -> None:
        with pytest.raises(ValidationError):
            self.make(target_duration_minutes=0)
        with pytest.raises(ValidationError):
            self.make(audience="expert")

    def test_roundtrip_json(self) -> None:
        m = self.make()
        assert JobManifest.model_validate_json(m.model_dump_json()) == m

    def test_required_statuses_exist(self) -> None:
        required = {
            "created", "acquiring_source", "extracting_knowledge", "researching",
            "planning_lesson", "generating_script", "planning_scenes", "generating_visuals",
            "generating_audio", "rendering", "validating", "awaiting_review", "approved",
            "rejected", "uploaded", "failed",
        }  # fmt: skip
        assert required <= {s.value for s in JobStatus}


class TestStageGraph:
    def test_dependencies_only_reference_earlier_stages(self) -> None:
        for stage, deps in STAGE_DEPENDENCIES.items():
            for dep in deps:
                assert STAGE_ORDER.index(dep) < STAGE_ORDER.index(stage)

    def test_downstream_is_transitive_and_ordered(self) -> None:
        down = downstream_of(StageName.SCENES)
        assert down == [
            StageName.VISUALS, StageName.VOICE, StageName.CAPTIONS, StageName.RENDER,
            StageName.VALIDATE, StageName.PACKAGE,
        ]  # fmt: skip
        assert downstream_of(StageName.PACKAGE) == []
        assert StageName.SCRIPT in downstream_of(StageName.KNOWLEDGE)


class TestKnowledgeAndScript:
    def test_canned_content_validates(self) -> None:
        assert KnowledgeBase.model_validate(mock_data.knowledge("TCP")).claims
        assert Script.model_validate(mock_data.script()).sections

    def test_malformed_knowledge_rejected(self) -> None:
        bad = mock_data.knowledge("TCP")
        bad["concepts"][0]["importance"] = 1.7
        with pytest.raises(ValidationError):
            KnowledgeBase.model_validate(bad)

    def test_unknown_field_rejected(self) -> None:
        bad = mock_data.knowledge("TCP")
        bad["extra"] = True
        with pytest.raises(ValidationError):
            KnowledgeBase.model_validate(bad)

    def test_duplicate_claim_ids_rejected(self) -> None:
        data = mock_data.knowledge("TCP")
        data["claims"].append(dict(data["claims"][0]))
        with pytest.raises(ValidationError, match="unique"):
            KnowledgeBase.model_validate(data)

    def test_claim_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Claim(id="c", text="t", confidence=1.2)

    def test_blank_script_narration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Script.model_validate(
                {"sections": [{"id": "a", "purpose": "hook", "narration": "   "}]}
            )

    def test_transcript_needs_segments_and_order(self) -> None:
        with pytest.raises(ValidationError):
            Transcript(format="txt", segments=[])
        with pytest.raises(ValidationError):
            Transcript(format="srt", segments=[{"start_s": 5, "end_s": 1, "text": "a"}])


def scene(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "scene_id": 1,
        "narration": "The client sends a SYN packet.",
        "estimated_duration_s": 5,
        "visual_type": "network_diagram",
        "objects": [
            {"type": "computer", "id": "client", "position": [0.2, 0.5]},
            {"type": "server", "id": "server", "position": [0.8, 0.5]},
        ],
        "animations": [{"type": "packet", "from": "client", "to": "server", "label": "SYN"}],
    }
    return {**base, **overrides}


class TestSceneValidation:
    def test_valid_scene_with_from_alias(self) -> None:
        s = Scene.model_validate(scene())
        anim = s.animations[0]
        assert isinstance(anim, PacketAnimation)
        assert anim.from_id == "client"
        assert '"from"' in s.model_dump_json(by_alias=True)

    def test_canned_scene_plan_valid(self) -> None:
        assert len(ScenePlan.model_validate(mock_data.scenes()).scenes) == 5

    def test_animation_unknown_target(self) -> None:
        bad = scene(animations=[{"type": "fade_in", "target": "ghost"}])
        with pytest.raises(ValidationError, match="unknown"):
            Scene.model_validate(bad)

    def test_packet_unknown_endpoint(self) -> None:
        bad = scene(animations=[{"type": "packet", "from": "client", "to": "nowhere"}])
        with pytest.raises(ValidationError, match="unknown"):
            Scene.model_validate(bad)

    def test_duplicate_object_ids(self) -> None:
        dup = [{"type": "server", "id": "a", "position": [0.1, 0.1]}] * 2
        with pytest.raises(ValidationError, match="unique"):
            Scene.model_validate(scene(objects=dup, animations=[]))

    @pytest.mark.parametrize("position", [[1.2, 0.5], [-0.1, 0.5]])
    def test_position_out_of_range(self, position: list[float]) -> None:
        bad = scene(objects=[{"type": "server", "id": "s", "position": position}], animations=[])
        with pytest.raises(ValidationError, match="normalised"):
            Scene.model_validate(bad)

    def test_unknown_object_type(self) -> None:
        bad = scene(objects=[{"type": "toaster", "id": "t", "position": [0.5, 0.5]}], animations=[])
        with pytest.raises(ValidationError):
            Scene.model_validate(bad)

    def test_blank_narration_and_zero_duration(self) -> None:
        with pytest.raises(ValidationError):
            Scene.model_validate(scene(narration=" "))
        with pytest.raises(ValidationError):
            Scene.model_validate(scene(estimated_duration_s=0))

    def test_animation_timing_must_be_ordered(self) -> None:
        bad = scene(animations=[{"type": "pulse", "target": "client", "start": 0.8, "end": 0.2}])
        with pytest.raises(ValidationError, match="greater than start"):
            Scene.model_validate(bad)

    def test_duplicate_scene_ids(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            ScenePlan.model_validate({"scenes": [scene(), scene()]})

    def test_empty_plan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScenePlan.model_validate({"scenes": []})
