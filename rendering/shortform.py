"""Render declarative, topic-specific scene plans into portrait short-form videos."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw

from core.exceptions import RenderError
from core.models.editorial import ShortScript, VisualPlan, VisualPlanScene
from core.models.scenes import (
    ArrowObject,
    CircleObject,
    CodeBlockObject,
    ComputerObject,
    DatabaseObject,
    Draw,
    EquationObject,
    FadeIn,
    FadeOut,
    GraphObject,
    Highlight,
    IconObject,
    LineObject,
    Move,
    PacketObject,
    PersonObject,
    Pulse,
    RectangleObject,
    RouterObject,
    Scale,
    SceneObject,
    ServerObject,
    SupportedAnimation,
    TextObject,
    TimelineObject,
    TypeText,
)
from rendering.ffmpeg import FFmpeg
from rendering.reel_layout import FontSet, wrap_text

BG = (13, 23, 36, 255)
WHITE = "#F3F5F7"
MUTED = "#A5B8CC"
GOLD = "#F1C66D"
TEAL = "#67DCC5"
CORAL = "#F1A299"
LAVENDER = "#B7AEFF"
ACCENTS = (TEAL, GOLD, CORAL, LAVENDER)
SCENE_HOLD_SECONDS = 0.5


def _ease(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3 - 2 * value)


def _point(point: tuple[float, float], width: int, height: int) -> tuple[int, int]:
    return round(point[0] * width), round(point[1] * height)


def _rgba(color: str, opacity: float) -> tuple[int, int, int, int]:
    try:
        red, green, blue = ImageColor.getrgb(color)[:3]
    except ValueError as exc:
        raise RenderError(f"Visual plan contains invalid color {color!r}") from exc
    return red, green, blue, round(255 * min(1.0, max(0.0, opacity)))


def _interval_progress(animation: SupportedAnimation, progress: float) -> float:
    return _ease((progress - animation.start) / (animation.end - animation.start))


class ShortFormGraphics:
    """Draw scene-plan objects with safe-area text checks and frame-based animation."""

    def __init__(
        self,
        width: int = 1080,
        height: int = 1920,
        *,
        fonts: FontSet | None = None,
    ) -> None:
        if width <= 0 or height <= 0 or width % 2 or height % 2:
            raise RenderError("Short-form rendering requires positive, even dimensions.")
        self.width = width
        self.height = height
        self.fonts = fonts or FontSet()

    def _draw_text(
        self,
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
        if not text.strip():
            return
        font = self.fonts.get(bold, size)
        lines = wrap_text(text, font, max_width)
        line_height = round(size * 1.25)
        if (
            len(lines) > max_lines
            or any(font.getlength(line) > max_width for line in lines)
            or y < 0
            or y + len(lines) * line_height > self.height * 0.94
        ):
            raise RenderError(f"Visual plan text does not fit its safe area: {text!r}")
        fill = _rgba(color, opacity)
        for line_index, line in enumerate(lines):
            draw_x = x
            if center:
                draw_x -= round(font.getlength(line) / 2)
            if draw_x < self.width * 0.06 or draw_x + font.getlength(line) > self.width * 0.94:
                raise RenderError(f"Visual plan text is outside its horizontal safe area: {text!r}")
            draw.text((draw_x, y + line_index * line_height), line, font=font, fill=fill)

    @staticmethod
    def _animation_state(
        obj: SceneObject,
        animations: list[SupportedAnimation],
        progress: float,
    ) -> tuple[tuple[float, float] | None, float, float, str | None, str | None]:
        object_id = obj.id
        position = getattr(obj, "position", None)
        opacity = 1.0
        scale = 1.0
        color = None
        text = None
        for animation in animations:
            if animation.target != object_id:
                continue
            t = _interval_progress(animation, progress)
            if isinstance(animation, FadeIn):
                opacity *= t if progress >= animation.start else 0.0
            elif isinstance(animation, FadeOut):
                opacity *= 1.0 - t if progress >= animation.start else 1.0
            elif isinstance(animation, Move) and position is not None:
                position = (
                    position[0] + (animation.to[0] - position[0]) * t,
                    position[1] + (animation.to[1] - position[1]) * t,
                )
            elif isinstance(animation, Scale):
                scale *= 1.0 + (animation.factor - 1.0) * t
            elif isinstance(animation, Pulse):
                scale *= 1.0 + 0.08 * math.sin(math.pi * t)
            elif isinstance(animation, Highlight) and animation.color:
                color = animation.color if animation.start <= progress <= animation.end else color
            elif isinstance(animation, TypeText):
                original = getattr(obj, "text", "")
                text = animation.text if animation.text is not None else original
                text = text[: round(len(text) * t)]
        return position, opacity, scale, color, text

    def _object(
        self,
        draw: ImageDraw.ImageDraw,
        scene: VisualPlanScene,
        obj: SceneObject,
        progress: float,
        color: str,
    ) -> None:
        position, opacity, scale, changed_color, changed_text = self._animation_state(
            obj, scene.animations, progress
        )
        color = changed_color or color
        if opacity <= 0:
            return
        width, height = self.width, self.height
        fill = _rgba(color, opacity)
        line = _rgba(color, opacity)
        label = getattr(obj, "label", None)
        if isinstance(obj, IconObject):
            label = label or obj.icon_name.replace("_", " ")
        elif isinstance(
            obj,
            (
                PersonObject, ComputerObject, ServerObject, DatabaseObject, RouterObject,
                PacketObject,
            ),
        ):
            label = label or obj.type.replace("_", " ")

        if isinstance(obj, (TextObject, CodeBlockObject, EquationObject)):
            if position is None:
                return
            x, y = _point(position, width, height)
            if isinstance(obj, TextObject):
                text = changed_text if changed_text is not None else obj.text
                size = max(20, round(obj.font_size * width * scale))
                self._draw_text(
                    draw, text, x, y, round(width * 0.82), size, color, opacity, bold=True
                )
            elif isinstance(obj, CodeBlockObject):
                text = obj.code
                size = max(18, round(width * 0.035 * scale))
                draw.rounded_rectangle(
                    (x - 18, y - 18, width * 0.9, y + min(height * 0.28, size * 7)),
                    radius=24,
                    fill=(22, 38, 56, round(235 * opacity)),
                    outline=fill,
                    width=2,
                )
                self._draw_text(
                    draw, text, x, y, round(width * 0.75), size, WHITE, opacity,
                    max_lines=7,
                )
            else:
                size = max(24, round(width * 0.06 * scale))
                self._draw_text(
                    draw, obj.latex, x, y, round(width * 0.82), size, color, opacity,
                    bold=True,
                )
            return

        if isinstance(obj, (LineObject, ArrowObject)):
            start = _point(obj.start, width, height)
            end = _point(obj.end, width, height)
            draw_animation = next(
                (a for a in scene.animations if isinstance(a, Draw) and a.target == obj.id),
                None,
            )
            if draw_animation:
                t = _interval_progress(draw_animation, progress)
                end = (
                    round(start[0] + (end[0] - start[0]) * t),
                    round(start[1] + (end[1] - start[1]) * t),
                )
            draw.line((start, end), fill=line, width=max(3, round(width * 0.006 * scale)))
            if isinstance(obj, ArrowObject):
                dx, dy = end[0] - start[0], end[1] - start[1]
                length = max(1.0, math.hypot(dx, dy))
                ux, uy = dx / length, dy / length
                side = max(10, round(width * 0.022 * scale))
                draw.polygon(
                    [
                        end,
                        (round(end[0] - ux * side - uy * side * 0.65),
                         round(end[1] - uy * side + ux * side * 0.65)),
                        (round(end[0] - ux * side + uy * side * 0.65),
                         round(end[1] - uy * side - ux * side * 0.65)),
                    ],
                    fill=fill,
                )
            if label:
                self._draw_text(
                    draw,
                    label,
                    round((start[0] + end[0]) / 2),
                    round((start[1] + end[1]) / 2),
                    round(width * 0.4),
                    max(18, round(width * 0.03)),
                    WHITE,
                    opacity,
                    center=True,
                    max_lines=2,
                )
            return

        if position is None:
            return
        x, y = _point(position, width, height)

        if isinstance(obj, CircleObject):
            radius = max(8, round(obj.radius * min(width, height) * scale))
            draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                         fill=(fill[0], fill[1], fill[2], round(65 * opacity)),
                         outline=line, width=max(3, round(width * 0.006)))
        elif isinstance(obj, RectangleObject):
            box_width = round(obj.width * width * scale)
            box_height = round(obj.height * height * scale)
            draw.rounded_rectangle(
                (x, y, x + box_width, y + box_height),
                radius=max(8, round(width * 0.018)),
                fill=(fill[0], fill[1], fill[2], round(55 * opacity)),
                outline=line,
                width=max(3, round(width * 0.006)),
            )
            if label and label.strip():
                horizontal_padding = max(4, round(box_width * 0.06))
                vertical_padding = max(4, round(box_height * 0.08))
                label_width = max(1, box_width - 2 * horizontal_padding)
                font_size = max(14, min(32, round(min(box_width, box_height) * 0.14)))
                while True:
                    font = self.fonts.get(True, font_size)
                    label_lines = wrap_text(label, font, label_width)
                    line_height = round(font_size * 1.25)
                    label_height = len(label_lines) * line_height
                    if (
                        len(label_lines) <= 2
                        and label_height <= box_height - 2 * vertical_padding
                        and all(font.getlength(line) <= label_width for line in label_lines)
                    ):
                        break
                    if font_size <= 14:
                        raise RenderError(
                            f"Visual plan label does not fit its rectangle: {label!r}"
                        )
                    font_size = max(14, font_size - 2)
                self._draw_text(
                    draw,
                    label,
                    x + box_width // 2,
                    y + (box_height - label_height) // 2,
                    label_width,
                    font_size,
                    WHITE,
                    opacity,
                    bold=True,
                    center=True,
                    max_lines=2,
                )
        elif isinstance(obj, GraphObject):
            self._graph(draw, obj, x, y, color, opacity, scale)
        elif isinstance(obj, TimelineObject):
            self._timeline(draw, obj, x, y, color, opacity, scale)
        elif isinstance(obj, IconObject):
            radius = round(width * 0.055 * scale)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                         outline=line, width=max(3, round(width * 0.006)))
        elif isinstance(
            obj,
            (PersonObject, ComputerObject, ServerObject, DatabaseObject, RouterObject,
             PacketObject),
        ):
            self._symbol(draw, obj, x, y, color, opacity, scale)
        if label and not isinstance(obj, RectangleObject):
            self._draw_text(
                draw, label, x, y + round(height * 0.09), round(width * 0.55),
                max(18, round(width * 0.03)), WHITE, opacity, center=True, max_lines=2,
            )

    def _graph(
        self,
        draw: ImageDraw.ImageDraw,
        obj: GraphObject,
        x: int,
        y: int,
        color: str,
        opacity: float,
        scale: float,
    ) -> None:
        width, height = round(self.width * 0.66 * scale), round(self.height * 0.28 * scale)
        left, bottom = x, y + height
        draw.line((left, y, left, bottom, left + width, bottom),
                  fill=_rgba(MUTED, opacity), width=3)
        points = [
            (left + round(px * width), bottom - round(py * height))
            for px, py in obj.points
        ]
        if len(points) > 1:
            draw.line(points, fill=_rgba(color, opacity), width=max(4, round(self.width * 0.007)))
            for px, py in points:
                radius = round(self.width * 0.009)
                draw.ellipse((px - radius, py - radius, px + radius, py + radius),
                             fill=_rgba(color, opacity))
        self._draw_text(
            draw, obj.x_label, left + width // 2, bottom + 14, width,
            max(17, round(self.width * 0.025)), MUTED, opacity, center=True, max_lines=1,
        )
        self._draw_text(
            draw, obj.y_label, left, y - round(self.height * 0.035), width,
            max(17, round(self.width * 0.025)), MUTED, opacity, max_lines=1,
        )

    def _timeline(
        self,
        draw: ImageDraw.ImageDraw,
        obj: TimelineObject,
        x: int,
        y: int,
        color: str,
        opacity: float,
        scale: float,
    ) -> None:
        span = round(self.width * 0.68 * scale)
        left, right = x - span // 2, x + span // 2
        draw.line((left, y, right, y), fill=_rgba(MUTED, opacity), width=4)
        for event in obj.events:
            px = left + round((right - left) * event.t)
            radius = round(self.width * 0.012)
            draw.ellipse((px - radius, y - radius, px + radius, y + radius),
                         fill=_rgba(color, opacity))
            self._draw_text(
                draw, event.label, px, y + round(self.height * 0.03),
                round(self.width * 0.24), max(17, round(self.width * 0.027)),
                WHITE, opacity, center=True, max_lines=2,
            )

    def _symbol(
        self,
        draw: ImageDraw.ImageDraw,
        obj: object,
        x: int,
        y: int,
        color: str,
        opacity: float,
        scale: float,
    ) -> None:
        size = round(self.width * 0.11 * scale)
        line = _rgba(color, opacity)
        if isinstance(obj, PersonObject):
            head = round(size * 0.22)
            draw.ellipse((x - head, y - size, x + head, y - size + 2 * head),
                         outline=line, width=5)
            draw.line((x, y - size + 2 * head, x, y + size // 2), fill=line, width=6)
            draw.line((x - size // 2, y, x + size // 2, y), fill=line, width=6)
            draw.line((x, y + size // 2, x - size // 3, y + size), fill=line, width=6)
            draw.line((x, y + size // 2, x + size // 3, y + size), fill=line, width=6)
        elif isinstance(obj, PacketObject):
            draw.ellipse((x - size // 2, y - size // 2, x + size // 2, y + size // 2),
                         fill=_rgba(color, 0.3 * opacity), outline=line, width=5)
        else:
            height = round(size * 0.72)
            box = (x - size, y - height // 2, x + size, y + height // 2)
            draw.rounded_rectangle(box, radius=18, outline=line, width=5,
                                   fill=(line[0], line[1], line[2], round(35 * opacity)))
            if isinstance(obj, DatabaseObject):
                for offset in (-height // 4, height // 4):
                    draw.arc((box[0], y + offset - 18, box[2], y + offset + 18),
                             0, 180, fill=line, width=3)
    def frame(
        self,
        scene: VisualPlanScene,
        progress: float,
        index: int,
        count: int,
        *,
        background_video: bool = False,
    ) -> Image.Image:
        alpha = 165 if background_video else 255
        image = Image.new("RGBA", (self.width, self.height), (*BG[:3], alpha))
        draw = ImageDraw.Draw(image)
        draw.ellipse(
            (round(self.width * 0.79), -round(self.height * 0.08),
             round(self.width * 1.18), round(self.height * 0.18)),
            outline=(27, 45, 63, alpha), width=4,
        )
        self._draw_text(
            draw, "THE 9-2-5", round(self.width * 0.08), round(self.height * 0.075),
            round(self.width * 0.84), max(20, round(self.width * 0.024)),
            TEAL, 1, bold=True,
        )
        for object_index, obj in enumerate(scene.objects):
            self._object(
                draw,
                scene,
                obj,
                progress,
                ACCENTS[object_index % len(ACCENTS)],
            )
        if scene.text:
            footer_y = round(self.height * 0.76)
            for text_index, text in enumerate(scene.text):
                self._draw_text(
                    draw, text, round(self.width * 0.09),
                    footer_y + text_index * round(self.height * 0.045),
                    round(self.width * 0.82), max(20, round(self.width * 0.035)),
                    TEAL if text_index == 0 else WHITE, 1, bold=text_index == 0,
                    max_lines=1,
                )
        self._draw_text(
            draw, "VERIFY SOURCES", round(self.width * 0.08),
            round(self.height * 0.89), round(self.width * 0.84),
            max(16, round(self.width * 0.018)), MUTED, 1, max_lines=1,
        )
        y = round(self.height * 0.94)
        draw.rounded_rectangle(
            (round(self.width * 0.08), y, round(self.width * 0.92), y + 7),
            radius=4, fill=(49, 72, 92, 255),
        )
        right = round(self.width * (0.08 + 0.84 * ((index + progress) / count)))
        draw.rounded_rectangle(
            (round(self.width * 0.08), y, max(round(self.width * 0.08) + 2, right), y + 7),
            radius=4, fill=TEAL,
        )
        return image


def _audio_filter(index: int, duration: float, output: str) -> str:
    return (
        f"[{index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"apad=whole_dur={duration:.6f},atrim=duration={duration:.6f},"
        f"asetpts=PTS-STARTPTS[{output}]"
    )


def render_short_video(
    ffmpeg: FFmpeg,
    graphics: ShortFormGraphics,
    script: ShortScript,
    visual_plan: VisualPlan,
    audio_paths: list[Path],
    destination: Path,
    *,
    fps: int = 30,
    background_video: Path | None = None,
    music: Path | None = None,
) -> tuple[float, list[float]]:
    if len(script.scenes) != len(visual_plan.scenes) or len(audio_paths) != len(script.scenes):
        raise RenderError(
            "Script, visual plan and narration audio must have matching scene counts."
        )
    if not 1 <= fps <= 60:
        raise RenderError("Short-form frame rate must be between 1 and 60.")
    visual_ids = [scene.script_scene_id for scene in visual_plan.scenes]
    script_ids = [scene.id for scene in script.scenes]
    if visual_ids != script_ids:
        raise RenderError("Visual plan scene order does not match the script.")
    audio_info = [ffmpeg.analyze(path) for path in audio_paths]
    if any(not info.has_audio or info.duration_s <= 0 for info in audio_info):
        raise RenderError("Every narration asset must contain nonempty audio.")
    scene_durations = [
        math.ceil((info.duration_s + SCENE_HOLD_SECONDS) * fps) / fps
        for info in audio_info
    ]
    total_duration = sum(scene_durations)
    if not 30 <= total_duration <= 65:
        raise RenderError(
            f"Rendered narration would be {total_duration:.1f}s; Shorts drafts must be 30-65s."
        )
    if background_video is not None:
        info = ffmpeg.analyze(background_video)
        if not info.has_video or info.duration_s <= 0:
            raise RenderError("Background asset must contain nonempty video.")
    if music is not None:
        info = ffmpeg.analyze(music)
        if not info.has_audio or info.duration_s <= 0:
            raise RenderError("Music asset must contain nonempty audio.")

    width, height = graphics.width, graphics.height
    args = [
        "-y", "-hide_banner", "-nostats", "-loglevel", "error",
        "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{width}x{height}",
        "-framerate", str(fps), "-i", "pipe:0",
    ]
    if background_video is not None:
        args.extend(["-stream_loop", "-1", "-i", str(background_video)])
    for path in audio_paths:
        args.extend(["-i", str(path)])
    if music is not None:
        args.extend(["-stream_loop", "-1", "-i", str(music)])

    audio_start = 2 if background_video is not None else 1
    audio_filters = [
        _audio_filter(audio_start + index, duration, f"voice{index}")
        for index, duration in enumerate(scene_durations)
    ]
    audio_filters.append(
        f"{''.join(f'[voice{i}]' for i in range(len(scene_durations)))}"
        f"concat=n={len(scene_durations)}:v=0:a=1[voice]"
    )
    if music is not None:
        audio_filters.append("[voice]asplit=2[voice_out][sidechain]")
        music_index = audio_start + len(audio_paths)
        fade = min(1.0, total_duration / 3)
        audio_filters.extend(
            [
                f"[{music_index}:a]atrim=duration={total_duration:.6f},"
                "asetpts=PTS-STARTPTS,loudnorm=I=-18:TP=-2:LRA=7,volume=0.25,"
                f"afade=t=in:d={fade:.3f},"
                f"afade=t=out:st={total_duration - fade:.6f}:d={fade:.3f}[music]",
                "[music][sidechain]sidechaincompress=threshold=0.02:ratio=8:"
                "attack=20:release=250[ducked]",
                "[voice_out][ducked]amix=inputs=2:duration=first:normalize=0,"
                "alimiter=limit=0.89:level=0:latency=1[a]",
            ]
        )
    else:
        audio_filters.append(
            "[voice]loudnorm=I=-16:TP=-1.5:LRA=11,"
            "alimiter=limit=0.89:level=0:latency=1[a]"
        )

    video_filters: list[str] = []
    if background_video is not None:
        video_filters.extend(
            [
                f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,fps={fps},"
                "eq=brightness=-0.02:saturation=0.82,gblur=sigma=4:steps=2,"
                f"trim=duration={total_duration:.6f},setpts=PTS-STARTPTS[bg]",
                "[bg][0:v]overlay=0:0:format=auto,format=yuv420p[v]",
            ]
        )
    else:
        video_filters.append("[0:v]format=yuv420p[v]")
    filter_script = destination.with_suffix(".filter.txt")
    filter_script.parent.mkdir(parents=True, exist_ok=True)
    filter_script.write_text(
        ";\n".join([*video_filters, *audio_filters]) + "\n",
        encoding="utf-8",
    )
    temp = destination.with_name(destination.stem + ".partial.mp4")
    log_path = destination.with_suffix(".ffmpeg.log")
    encode_args = [
        *args,
        "-filter_complex_script", str(filter_script),
        "-map", "[v]", "-map", "[a]",
        "-t", f"{total_duration:.6f}",
        "-r", str(fps),
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-threads", "4",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-f", "mp4", str(temp),
    ]
    frames_by_scene = [round(duration * fps) for duration in scene_durations]
    try:
        with log_path.open("wb") as errors:
            try:
                process = subprocess.Popen(
                    [str(ffmpeg.executable), *encode_args],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=errors,
                    shell=False,
                )
            except OSError as exc:
                raise RenderError(f"Could not start FFmpeg; inspect {log_path}") from exc
            if process.stdin is None:
                process.kill()
                process.wait()
                raise RenderError("FFmpeg input stream could not be opened.")
            try:
                for index, (scene, frame_count) in enumerate(
                    zip(visual_plan.scenes, frames_by_scene, strict=True)
                ):
                    for frame_index in range(frame_count):
                        progress = frame_index / max(1, frame_count - 1)
                        image = graphics.frame(
                            scene,
                            progress,
                            index,
                            len(visual_plan.scenes),
                            background_video=background_video is not None,
                        )
                        process.stdin.write(image.tobytes())
                process.stdin.close()
                code = process.wait(timeout=900)
            except (OSError, subprocess.TimeoutExpired) as exc:
                process.kill()
                process.wait()
                raise RenderError(f"Short-form FFmpeg encoding failed; inspect {log_path}") from exc
            finally:
                if not process.stdin.closed:
                    process.stdin.close()
                if process.poll() is None:
                    process.kill()
                    process.wait()
            if code:
                raise RenderError(f"FFmpeg exited {code}; inspect {log_path}")
        temp.replace(destination)
        log_path.unlink(missing_ok=True)
    finally:
        temp.unlink(missing_ok=True)
        filter_script.unlink(missing_ok=True)
    return total_duration, scene_durations
