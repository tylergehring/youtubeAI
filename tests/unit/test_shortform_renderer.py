from __future__ import annotations

import math
import wave
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from core.models.editorial import (
    ShortScript,
    ShortScriptScene,
    VisualPlan,
    VisualPlanScene,
)
from core.models.scenes import GraphObject, RectangleObject, TextObject, VisualType
from rendering.ffmpeg import FFmpeg
from rendering.shortform import ShortFormGraphics, render_short_video
from tests.conftest import FFMPEG


def script_and_visual_plan() -> tuple[ShortScript, VisualPlan]:
    script = ShortScript(
        title="Why bond prices can move",
        hook="A bond can lose value without missing a payment.",
        scenes=[
            ShortScriptScene(
                id=f"scene-{index}",
                purpose="explanation",
                narration=(
                    f"Scene {index} explains how a fixed payment and changing interest rate "
                    "can move a bond's market price."
                ),
            )
            for index in range(1, 5)
        ],
        claims_to_verify=["Check bond valuation sources."],
        disclosure="This explanation is educational, not personal financial advice.",
    )
    visuals = VisualPlan(
        creative_direction="Use a simple price curve to show the mechanism in every scene.",
        scenes=[
            VisualPlanScene(
                scene_id=index,
                script_scene_id=f"scene-{index}",
                visual_goal="Show a changing price relationship with one clear label.",
                visual_type=VisualType.GRAPH,
                objects=[
                    TextObject(
                        id="heading",
                        position=(0.1, 0.23),
                        text="One rate change, two price outcomes",
                        font_size=0.05,
                    ),
                    GraphObject(
                        id="price-curve",
                        position=(0.14, 0.36),
                        points=[(0.0, 0.9), (0.5, 0.55), (1.0, 0.2)],
                        x_label="Yield",
                        y_label="Price",
                    ),
                ],
                animations=[{"type": "type", "target": "heading", "start": 0, "end": 0.5}],
                text=["Prices follow rates"],
            )
            for index in range(1, 5)
        ],
    )
    return script, visuals


def _write_tone(path: Path, seconds: float, frequency: int = 440) -> None:
    sample_rate = 48_000
    samples = (
        round(6000 * math.sin(2 * math.pi * frequency * index / sample_rate))
        for index in range(round(seconds * sample_rate))
    )
    import array

    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        output.writeframes(array.array("h", samples).tobytes())


def test_shortform_graphics_draws_scene_plan_in_safe_area() -> None:
    _, plan = script_and_visual_plan()
    graphics = ShortFormGraphics(270, 480)

    early = graphics.frame(plan.scenes[0], 0, 0, len(plan.scenes))
    complete = graphics.frame(plan.scenes[0], 1, 0, len(plan.scenes))

    assert early.size == (270, 480)
    assert early.mode == "RGBA"
    assert early.tobytes() != complete.tobytes()
    with pytest.raises(ValueError, match="normalized"):
        VisualPlanScene(
            scene_id=1,
            script_scene_id="scene-1",
            visual_goal="Keep every graph point inside the normalized chart.",
            visual_type=VisualType.GRAPH,
            objects=[
                GraphObject(
                    id="bad",
                    position=(0.1, 0.1),
                    points=[(1.2, 0.5)],
                )
            ],
        )


def test_rectangle_labels_are_drawn_inside_the_rectangle(monkeypatch: pytest.MonkeyPatch) -> None:
    graphics = ShortFormGraphics(270, 480)
    rectangle = RectangleObject(
        id="balance",
        label="Debt $100",
        position=(0.25, 0.5),
        width=0.2,
        height=0.12,
    )
    scene = VisualPlanScene(
        scene_id=1,
        script_scene_id="debt-example",
        visual_goal="Keep the debt label readable inside its rectangle.",
        visual_type=VisualType.DIAGRAM,
        objects=[rectangle],
    )
    calls: list[tuple[str, int, int, int, int, bool]] = []

    def capture_text(
        draw: ImageDraw.ImageDraw,
        text: str,
        x: int,
        y: int,
        max_width: int,
        size: int,
        color: str,
        opacity: float,
        *,
        bold: bool = False,
        center: bool = False,
        max_lines: int = 5,
    ) -> None:
        calls.append((text, x, y, max_width, size, center))

    monkeypatch.setattr(graphics, "_draw_text", capture_text)
    canvas = Image.new("RGBA", (270, 480))
    graphics._object(ImageDraw.Draw(canvas), scene, rectangle, 1.0, "#67DCC5")

    assert len(calls) == 1
    text, label_x, label_y, label_width, label_size, centered = calls[0]
    left = round(rectangle.position[0] * graphics.width)
    top = round(rectangle.position[1] * graphics.height)
    box_width = round(rectangle.width * graphics.width)
    box_height = round(rectangle.height * graphics.height)
    assert text == "Debt $100"
    assert label_x == left + box_width // 2
    assert label_width < box_width
    assert centered
    assert label_y >= top
    assert label_y + round(label_size * 1.25) * 2 <= top + box_height


def test_background_graphics_layer_is_transparent_enough_to_show_video() -> None:
    _, plan = script_and_visual_plan()
    graphics = ShortFormGraphics(270, 480)

    with_background = graphics.frame(
        plan.scenes[0], 1.0, 0, len(plan.scenes), background_video=True
    )
    without_background = graphics.frame(plan.scenes[0], 1.0, 0, len(plan.scenes))

    background_pixel = with_background.getpixel((0, 0))
    opaque_pixel = without_background.getpixel((0, 0))
    assert isinstance(background_pixel, tuple)
    assert isinstance(opaque_pixel, tuple)
    assert background_pixel[3] == 165
    assert opaque_pixel[3] == 255


@pytest.mark.ffmpeg
@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
def test_shortform_render_encodes_narration_background_and_music(tmp_path: Path) -> None:
    ffmpeg = FFmpeg.locate(FFMPEG)
    script, plan = script_and_visual_plan()
    graphics = ShortFormGraphics(270, 480)
    narration = tmp_path / "narration.wav"
    music = tmp_path / "music.wav"
    background = tmp_path / "background.mp4"
    destination = tmp_path / "video.mp4"
    _write_tone(narration, 7)
    _write_tone(music, 1, frequency=220)
    ffmpeg.run_checked(
        [
            "-y", "-hide_banner", "-nostats", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=#24404a:size=270x480:rate=2:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(background),
        ]
    )

    duration, scene_durations = render_short_video(
        ffmpeg,
        graphics,
        script,
        plan,
        [narration] * 4,
        destination,
        fps=1,
        background_video=background,
        music=music,
    )

    info = ffmpeg.analyze(destination)
    assert duration == pytest.approx(sum(scene_durations))
    assert 30 <= duration <= 65
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (270, 480)
    assert info.duration_s == pytest.approx(duration, abs=0.1)
    assert info.mean_volume_db is not None and info.mean_volume_db > -35
    assert not list(tmp_path.glob("*.partial.mp4"))
