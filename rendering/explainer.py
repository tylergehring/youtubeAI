"""Original, data-driven portrait graphics. No source video, screenshots or stock footage."""

from __future__ import annotations

import math
import subprocess
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw

from core.exceptions import RenderError
from core.explainer import BondScenario, EditorialScene, bond_price, calculation_context
from rendering.ffmpeg import FFmpeg
from rendering.reel_layout import FontSet, wrap_text

BG = "#0D1724"
PANEL = "#162638"
WHITE = "#F3F5F7"
MUTED = "#A5B8CC"
GOLD = "#F1C66D"
TEAL = "#67DCC5"
CORAL = "#F1A299"
SCENE_HOLD_SECONDS = 0.65


class ExplainerGraphics:
    """Draw in reference pixels, checking each text box before encoding."""

    def __init__(self, scenario: BondScenario, width: int = 1080, height: int = 1920) -> None:
        self.scenario = scenario
        self.context = calculation_context(scenario)
        self.fonts = FontSet()
        self.size = (width, height)

    def text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        x: int,
        y: int,
        width: int,
        height: int,
        size: int,
        color: str = WHITE,
        bold: bool = False,
    ) -> None:
        font = self.fonts.get(bold, size)
        lines = wrap_text(text, font, width)
        step = round(size * 1.3)
        if len(lines) * step > height or any(font.getlength(line) > width for line in lines):
            raise RenderError(f"Explainer text does not fit its safe-area box: {text!r}")
        for i, line in enumerate(lines):
            draw.text((x, y + i * step), line, font=font, fill=color)

    def frame(
        self,
        scene: EditorialScene,
        index: int,
        count: int,
        progress: float,
        *,
        background_video: bool = False,
    ) -> Image.Image:
        panel_fill: str | tuple[int, int, int, int]
        if background_video:
            image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
            panel_fill = (22, 38, 56, 220)
        else:
            image = Image.new("RGB", (1080, 1920), BG)
            panel_fill = PANEL
        d = ImageDraw.Draw(image)
        if background_video:
            d.rectangle((0, 0, 1080, 1920), fill=(13, 23, 36, 185))
        d.ellipse((760, -180, 1270, 330), outline="#1B2D3F", width=4)
        d.ellipse((830, -110, 1200, 260), outline="#17283A", width=3)
        self.text(d, "THE 9-2-5", 90, 145, 820, 45, 26, TEAL, True)
        self.text(d, scene.heading, 90, 245, 890, 220, 76, bold=True)
        d.rounded_rectangle((70, 545, 1010, 1260), radius=46, fill=panel_fill)
        progress = min(1.0, max(0.0, progress))
        self._visual(d, scene.visual, 1 - (1 - progress) ** 3)
        self.text(d, scene.supporting_text, 95, 1320, 890, 140, 44, TEAL, True)
        d.rounded_rectangle((90, 1625, 990, 1632), radius=4, fill=PANEL)
        end = 90 + round(900 * ((index + progress) / count))
        d.rounded_rectangle((90, 1625, max(92, end), 1632), radius=4, fill=TEAL)
        self.text(d, "HYPOTHETICAL EXAMPLE  •  NOT FINANCIAL ADVICE", 90, 1690, 900, 45, 23, MUTED)
        return (
            image if self.size == image.size else image.resize(self.size, Image.Resampling.LANCZOS)
        )

    def _visual(self, d: ImageDraw.ImageDraw, kind: str, p: float) -> None:
        s, c = self.scenario, self.context
        if kind == "hook":
            self.text(d, f"{s.long_years}-YEAR EXAMPLE", 135, 660, 800, 48, 26, GOLD, True)
            self.text(d, "PROMISED", 135, 765, 330, 48, 26, MUTED, True)
            self.text(d, c["face"], 135, 820, 340, 125, 92, WHITE, True)
            self.text(d, "IF SOLD TODAY", 620, 765, 330, 48, 26, MUTED, True)
            self.text(d, c["long_price"], 620, 820, 340, 125, 92, CORAL, True)
            arrow_end = 500 + round(75 * p)
            d.line((470, 880, arrow_end, 880), fill=GOLD, width=7)
            d.polygon(
                [(arrow_end, 880), (arrow_end - 20, 865), (arrow_end - 20, 895)],
                fill=GOLD,
            )
        elif kind == "yield":
            for x, label, value, detail, color in [
                (135, "YOUR BOND", c["coupon_pct"], f"{c['coupon_payment']} each check", TEAL),
                (575, "MARKET RATE", c["changed_yield_pct"], "higher rates available", GOLD),
            ]:
                self.text(d, label, x, 720, 360, 48, 27, MUTED, True)
                self.text(d, value, x, 790, 360, 135, 104, color, True)
                self.text(d, detail, x, 960, 360, 80, 29, WHITE)
            d.line((540, 705, 540, 1080), fill="#31485C", width=3)
        elif kind == "comparison":
            maximum = float(s.face_value)
            self.text(d, f"START: {c['face']}", 135, 670, 800, 45, 26, MUTED, True)
            for y, label, years, color in [
                (805, "short", s.short_years, TEAL),
                (1000, "long", s.long_years, CORAL),
            ]:
                self.text(d, f"{years} YEARS", 135, y - 55, 210, 45, 26, MUTED, True)
                self.text(d, c[f"{label}_price"], 780, y - 60, 190, 70, 43, color, True)
                length = 570 * float(
                    bond_price(s, years, s.changed_yield_pct) / Decimal(str(maximum))
                )
                d.rounded_rectangle(
                    (135, y, 135 + max(2, length * p), y + 42), radius=18, fill=color
                )
        elif kind == "curve":
            low = max(Decimal(0), s.initial_yield_pct - 2)
            high = s.changed_yield_pct + 2
            yields = [low + (high - low) * Decimal(i) / 60 for i in range(61)]
            values = [
                [float(bond_price(s, years, rate)) for rate in yields]
                for years in (s.short_years, s.long_years)
            ]
            bottom, top = min(map(min, values)), max(map(max, values))
            d.line((175, 700, 175, 1065, 895, 1065), fill="#476074", width=2)
            for series, color in zip(values, (TEAL, CORAL), strict=True):
                points = [
                    (180 + i * 690 / 60, 1045 - (value - bottom) / (top - bottom) * 300)
                    for i, value in enumerate(series)
                ]
                d.line(points[: max(2, round(p * 60) + 1)], fill=color, width=6)
            self.text(d, f"{s.short_years} YEARS", 200, 665, 240, 48, 26, TEAL, True)
            self.text(d, f"{s.long_years} YEARS", 610, 665, 250, 48, 26, CORAL, True)
        elif kind == "cashflows":
            d.line((160, 865, 900, 865), fill="#476074", width=4)
            for i in range(6):
                x = 175 + i * 125
                if x <= 175 + 725 * p:
                    d.ellipse((x - 12, 853, x + 12, 877), fill=TEAL)
            self.text(d, "SMALL CHECKS", 135, 720, 500, 48, 27, TEAL, True)
            self.text(d, "EVERY SIX MONTHS", 135, 920, 500, 48, 26, MUTED)
            self.text(d, f"{c['face']} AT THE END", 135, 1010, 790, 90, 53, GOLD, True)
        else:
            self.text(d, "PRICE CAN FALL", 135, 740, 730, 90, 58, CORAL, True)
            d.line((500, 880, 580, 880), fill=GOLD, width=7)
            d.line((500, 910, 580, 910), fill=GOLD, width=7)
            d.line((550, 860, 530, 930), fill=GOLD, width=7)
            self.text(d, "PAYMENTS ARRIVE", 135, 975, 790, 85, 56, TEAL, True)
            for x in (825, 875, 925):
                d.ellipse((x, 1065, x + 22, 1087), fill=TEAL)


def render_scene(
    ffmpeg: FFmpeg,
    graphics: ExplainerGraphics,
    scene: EditorialScene,
    index: int,
    count: int,
    audio: Path,
    duration_s: float,
    destination: Path,
    fps: int = 30,
    *,
    background_video: Path | None = None,
    background_offset_s: float = 0.0,
) -> float:
    frames = math.ceil((duration_s + SCENE_HOLD_SECONDS) * fps)
    duration = frames / fps
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".partial.mp4")
    log = destination.with_suffix(".ffmpeg.log")
    args = [
        str(ffmpeg.executable),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgba" if background_video is not None else "rgb24",
        "-video_size",
        f"{graphics.size[0]}x{graphics.size[1]}",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
    ]
    if background_video is not None:
        args.extend(
            [
                "-stream_loop",
                "-1",
                "-ss",
                f"{background_offset_s:.6f}",
                "-i",
                str(background_video),
            ]
        )
    args.extend(["-i", str(audio)])
    if background_video is None:
        args.extend(["-map", "0:v:0", "-map", "1:a:0"])
    else:
        width, height = graphics.size
        args.extend(
            [
                "-filter_complex",
                (
                    f"[1:v:0]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},setsar=1,fps={fps},"
                    "eq=brightness=-0.10:saturation=0.72,gblur=sigma=8:steps=2,"
                    "setpts=PTS-STARTPTS[bg];"
                    "[bg][0:v:0]overlay=0:0:format=auto,format=yuv420p[v]"
                ),
                "-map",
                "[v]",
                "-map",
                "2:a:0",
            ]
        )
    args.extend(
        [
            "-t",
            str(duration),
            "-af",
            "apad",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "fast",
            "-threads",
            "4",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(temp),
        ]
    )
    try:
        with log.open("wb") as errors:
            process = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors
            )
            assert process.stdin is not None
            try:
                for frame in range(frames):
                    image = graphics.frame(
                        scene,
                        index,
                        count,
                        frame / max(1, frames - 1),
                        background_video=background_video is not None,
                    )
                    process.stdin.write(image.tobytes())
                process.stdin.close()
                code = process.wait(timeout=180)
            except (OSError, subprocess.TimeoutExpired) as exc:
                process.kill()
                process.wait()
                raise RenderError(f"Explainer encoding failed; inspect {log}") from exc
            finally:
                process.stdin.close()
                if process.poll() is None:
                    process.kill()
                    process.wait()
            if code:
                raise RenderError(f"Explainer FFmpeg exited {code}; inspect {log}")
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)
    return duration
