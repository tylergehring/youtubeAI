"""Render an animated portrait comparison with optional local background music."""

from __future__ import annotations

import math
import os
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw

from core.exceptions import RenderError
from core.models.stock_comparison import StockComparison
from rendering.ffmpeg import FFmpeg
from rendering.reel_layout import FontSet

ChartBackground = Literal["white", "black"]


@dataclass(frozen=True)
class _ChartPalette:
    background: str
    text: str
    muted: str
    grid: str
    red: str
    blue: str


_PALETTES: dict[ChartBackground, _ChartPalette] = {
    "white": _ChartPalette(
        background="#FFFFFF",
        text="#202124",
        muted="#555B61",
        grid="#D9DDE1",
        red="#C52222",
        blue="#2878B8",
    ),
    "black": _ChartPalette(
        background="#000000",
        text="#F5F5F5",
        muted="#B0B6BF",
        grid="#383D42",
        red="#FF6B6B",
        blue="#60A5FA",
    ),
}


def _palette_for(background: ChartBackground) -> _ChartPalette:
    try:
        return _PALETTES[background]
    except KeyError as exc:
        raise RenderError("Stock-comparison background must be 'white' or 'black'.") from exc


def _compact_money(value: Decimal) -> str:
    """Format a readable approximate display value; exact values remain in the data files."""
    if value >= Decimal("1000000"):
        divisor = Decimal("1000000")
        suffix = "M"
        places = 2 if value < Decimal("10000000") else 1
    elif value >= Decimal("100000"):
        divisor = Decimal("1000")
        suffix = "K"
        places = 1
    elif value >= Decimal("10000"):
        divisor = Decimal("1000")
        suffix = "K"
        places = 0
    else:
        return f"${value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"
    quantum = Decimal(1).scaleb(-places)
    amount = (value / divisor).quantize(quantum, rounding=ROUND_HALF_UP)
    return f"${amount:,.{places}f}{suffix}"


def _exact_money(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"${value:,.0f}"
    return f"${value:,.2f}"


class StockComparisonGraphics:
    """Draw a chart with an investment-anchored floor and a visible-data ceiling."""

    def __init__(
        self,
        width: int = 1080,
        height: int = 1920,
        *,
        fonts: FontSet | None = None,
        background: ChartBackground = "white",
    ) -> None:
        if width <= 0 or height <= 0 or width % 2 or height % 2:
            raise RenderError("Stock comparison rendering requires positive, even dimensions.")
        self.width = width
        self.height = height
        self._scale = width / 1080
        self.fonts = fonts or FontSet()
        self.palette = _palette_for(background)

    def _x(self, value: float) -> int:
        return round(value * self.width / 1080)

    def _y(self, value: float) -> int:
        return round(value * self.height / 1920)

    def _text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        x: int,
        y: int,
        size: int,
        color: str,
        *,
        bold: bool = False,
        anchor: str = "la",
    ) -> None:
        draw.text(
            (x, y),
            text,
            font=self.fonts.get(bold, max(12, round(size * self._scale))),
            fill=color,
            anchor=anchor,
        )

    @staticmethod
    def _date_label(value: date) -> str:
        return value.strftime("%b %d, %Y").replace(" 0", " ")

    def frame(
        self,
        comparison: StockComparison,
        elapsed_s: float,
        *,
        chart_s: float = 36.0,
        end_hold_s: float = 3.0,
    ) -> Image.Image:
        """Return a chart-first frame; the last frame holds the fully expanded chart."""
        if elapsed_s < 0 or elapsed_s >= chart_s + end_hold_s:
            raise RenderError("Requested stock-comparison frame is outside the video timeline.")
        image = Image.new("RGB", (self.width, self.height), self.palette.background)
        draw = ImageDraw.Draw(image)
        progress = min(1.0, elapsed_s / chart_s)
        self._chart(draw, comparison, progress)
        return image

    def _chart(
        self, draw: ImageDraw.ImageDraw, comparison: StockComparison, progress: float
    ) -> None:
        points = comparison.points
        visible_index = min(
            len(points) - 1,
            max(0, round(progress * (len(points) - 1))),
        )
        visible = points[: visible_index + 1]
        current_date = visible[-1].date
        minimum_axis_end = comparison.start_date + timedelta(days=90)
        axis_end = max(current_date, minimum_axis_end)
        total_days = max(1, (axis_end - comparison.start_date).days)

        chart_left = self._x(150)
        chart_right = self._x(820)
        chart_top = self._y(340)
        chart_bottom = self._y(1640)
        min_value = comparison.initial_investment
        max_value = max(max(point.value_a, point.value_b) for point in visible)
        min_log = math.log10(float(min_value))
        max_log = math.log10(float(max_value))

        def chart_y(value: Decimal) -> int:
            if max_log == min_log:
                vertical_fraction = 0.0
            else:
                vertical_fraction = (math.log10(float(value)) - min_log) / (max_log - min_log)
            vertical_fraction = min(1.0, max(0.0, vertical_fraction))
            return chart_bottom - round((chart_bottom - chart_top) * vertical_fraction)

        def chart_position(trading_date: date, value: Decimal) -> tuple[int, int]:
            day_fraction = (trading_date - comparison.start_date).days / total_days
            x = chart_left + round((chart_right - chart_left) * day_fraction)
            return x, chart_y(value)

        self._text(
            draw,
            f"{comparison.label_a} and {comparison.label_b} since {comparison.start_date.year}",
            self._x(54),
            self._y(78),
            46,
            self.palette.text,
            bold=True,
        )
        self._text(
            draw,
            f"Hypothetical ${comparison.initial_investment:,f} each • "
            f"{self._date_label(comparison.start_date)} start",
            self._x(54),
            self._y(140),
            28,
            self.palette.muted,
        )

        for x, color, label in (
            (self._x(70), self.palette.red, comparison.label_a),
            (self._x(470), self.palette.blue, comparison.label_b),
        ):
            draw.line(
                (x, self._y(218), x + self._x(40), self._y(218)),
                fill=color,
                width=max(3, self._x(6)),
            )
            self._text(draw, label, x + self._x(55), self._y(199), 28, self.palette.text)

        axis_values = [min_value]
        axis_values.extend(
            Decimal(10) ** power
            for power in range(math.floor(min_log) + 1, math.floor(max_log) + 1)
            if min_value < Decimal(10) ** power < max_value
        )
        if max_value > min_value:
            axis_values.append(max_value)
        for index, value in enumerate(axis_values):
            y = chart_y(value)
            draw.line(
                (chart_left, y, chart_right, y),
                fill=self.palette.grid,
                width=max(1, self._x(2)),
            )
            self._text(
                draw,
                _exact_money(value) if index == 0 else _compact_money(value),
                chart_left - self._x(14),
                y,
                28,
                self.palette.text,
                anchor="rm",
            )

        draw.line(
            (chart_left, chart_bottom, chart_right, chart_bottom),
            fill=self.palette.text,
            width=max(2, self._x(3)),
        )
        displayed_years = [year for year in range(comparison.start_date.year, axis_end.year + 1)]
        year_span = max(1, axis_end.year - comparison.start_date.year)
        year_step = max(1, math.ceil(year_span / 5))
        for year in displayed_years:
            if (year - comparison.start_date.year) % year_step and year != axis_end.year:
                continue
            axis_date = max(comparison.start_date, date(year, 1, 1))
            if axis_date > axis_end:
                continue
            x = chart_left + round(
                (chart_right - chart_left) * (axis_date - comparison.start_date).days / total_days
            )
            draw.line(
                (x, chart_bottom, x, chart_bottom + self._y(12)),
                fill=self.palette.text,
                width=2,
            )
            self._text(
                draw,
                str(year),
                x,
                chart_bottom + self._y(22),
                27,
                self.palette.text,
                anchor="mt",
            )

        endpoints: list[tuple[int, int, str, str, Decimal]] = []
        for field_name, color, label in (
            ("value_a", self.palette.red, comparison.ticker_a),
            ("value_b", self.palette.blue, comparison.ticker_b),
        ):
            chart_points = [
                chart_position(point.date, getattr(point, field_name)) for point in visible
            ]
            if len(chart_points) == 1:
                x, y = chart_points[0]
                if visible_index > 0:
                    radius = self._x(9)
                    draw.ellipse(
                        (x - radius, y - radius, x + radius, y + radius),
                        fill=color,
                        outline=self.palette.background,
                        width=max(1, self._x(2)),
                    )
            else:
                draw.line(chart_points, fill=color, width=max(3, self._x(5)), joint="curve")
                x, y = chart_points[-1]
                radius = self._x(9)
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius),
                    fill=color,
                    outline=self.palette.background,
                    width=max(1, self._x(3)),
                )
            endpoints.append((x, y, label, color, getattr(visible[-1], field_name)))

        if visible_index == 0:
            (x_a, y_a), (x_b, y_b) = (
                chart_position(visible[0].date, visible[0].value_a),
                chart_position(visible[0].date, visible[0].value_b),
            )
            radius = max(7, self._x(11))
            draw.ellipse(
                (x_a - radius, y_a - radius, x_a + radius, y_a + radius),
                fill=self.palette.red,
                outline=self.palette.background,
                width=max(2, self._x(2)),
            )
            inner_radius = max(4, radius // 2)
            draw.ellipse(
                (x_b - inner_radius, y_b - inner_radius, x_b + inner_radius, y_b + inner_radius),
                fill=self.palette.blue,
                outline=self.palette.background,
                width=max(1, self._x(1)),
            )

        endpoints.sort(key=lambda item: item[1])
        label_positions = [item[1] for item in endpoints]
        min_gap = self._y(58)
        if label_positions[1] - label_positions[0] < min_gap:
            center = sum(label_positions) // 2
            label_positions[0] = max(
                chart_top,
                min(center - min_gap // 2, chart_bottom - min_gap),
            )
            label_positions[1] = label_positions[0] + min_gap
        for (point_x, point_y, ticker, color, value), label_y in zip(
            endpoints, label_positions, strict=True
        ):
            label_x = min(point_x + self._x(16), self.width - self._x(220))
            draw.line(
                (point_x + self._x(8), point_y, label_x - self._x(4), label_y),
                fill=color,
                width=max(2, self._x(2)),
            )
            self._text(
                draw,
                f"{ticker} {_compact_money(value)}",
                label_x,
                label_y,
                25,
                color,
                bold=True,
                anchor="lm",
            )


class StockComparisonRenderer:
    """Encode portrait H.264 frames with an optional looped and normalized music track."""

    def __init__(
        self,
        ffmpeg: FFmpeg,
        graphics: StockComparisonGraphics | None = None,
        *,
        fps: int = 30,
        chart_s: float = 36.0,
        end_hold_s: float = 3.0,
    ) -> None:
        if not 1 <= fps <= 60:
            raise RenderError("Stock comparison frame rate must be between 1 and 60.")
        if min(chart_s, end_hold_s) <= 0:
            raise RenderError("Chart and end-hold durations must be positive.")
        self.ffmpeg = ffmpeg
        self.graphics = graphics or StockComparisonGraphics()
        self.fps = fps
        self.chart_s = chart_s
        self.end_hold_s = end_hold_s
        self.duration_s = chart_s + end_hold_s

    def render(
        self,
        comparison: StockComparison,
        destination: Path,
        *,
        audio_path: Path | None = None,
    ) -> float:
        if audio_path is not None and not audio_path.is_file():
            raise RenderError(f"Stock-comparison audio track does not exist: {audio_path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        width, height = self.graphics.width, self.graphics.height
        frame_count = round(self.duration_s * self.fps)
        actual_duration = frame_count / self.fps
        partial = destination.with_name(destination.stem + ".partial.mp4")
        log_path = destination.with_suffix(".ffmpeg.log")
        arguments = [
            "-y",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(self.fps),
            "-i",
            "pipe:0",
        ]
        if audio_path is not None:
            arguments.extend(["-stream_loop", "-1", "-i", str(audio_path)])
        arguments.extend(["-map", "0:v:0"])
        if audio_path is None:
            arguments.append("-an")
        else:
            fade_in_s = min(1.0, actual_duration / 2)
            fade_out_s = min(2.0, actual_duration / 2)
            fade_out_start_s = actual_duration - fade_out_s
            audio_filter = (
                "loudnorm=I=-18:TP=-2:LRA=7,"
                "aresample=44100,"
                "aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"afade=t=in:d={fade_in_s:.6f},"
                f"afade=t=out:st={fade_out_start_s:.6f}:d={fade_out_s:.6f},"
                "alimiter=limit=0.89:level=0:latency=1"
            )
            arguments.extend(
                [
                    "-map",
                    "1:a:0",
                    "-af",
                    audio_filter,
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ar",
                    "44100",
                ]
            )
        arguments.extend(
            [
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
                "-r",
                str(self.fps),
                "-t",
                f"{actual_duration:.6f}",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(partial),
            ]
        )
        try:
            with log_path.open("wb") as errors:
                try:
                    process = subprocess.Popen(
                        [str(self.ffmpeg.executable), *arguments],
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
                    raise RenderError("FFmpeg raw-video input could not be opened.")
                try:
                    for frame_index in range(frame_count):
                        frame = self.graphics.frame(
                            comparison,
                            frame_index / self.fps,
                            chart_s=self.chart_s,
                            end_hold_s=self.end_hold_s,
                        )
                        process.stdin.write(frame.tobytes())
                    process.stdin.close()
                    return_code = process.wait(timeout=900)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired) as exc:
                    process.kill()
                    process.wait()
                    raise RenderError(
                        f"Stock comparison FFmpeg encoding failed; inspect {log_path}"
                    ) from exc
                finally:
                    if not process.stdin.closed:
                        process.stdin.close()
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            if return_code != 0:
                tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
                raise RenderError(
                    f"Stock comparison FFmpeg exited with {return_code}:\n" + "\n".join(tail)
                )
            os.replace(partial, destination)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise RenderError(f"Could not write rendered media to {destination}: {exc}") from exc
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return actual_duration


def write_decoded_review_assets(
    ffmpeg: FFmpeg,
    video: Path,
    output_dir: Path,
    *,
    duration_s: float,
    chart_s: float,
    background: ChartBackground = "white",
) -> tuple[Path, Path, list[Path]]:
    """Decode real MP4 frames, build a contact sheet, and choose an encoded-frame cover."""
    palette = _palette_for(background)
    output_dir.mkdir(parents=True, exist_ok=True)
    total_duration = duration_s
    sample_times = [
        0.0,
        chart_s * 0.1,
        chart_s * 0.25,
        chart_s * 0.50,
        chart_s * 0.75,
        chart_s * 0.98,
        total_duration - 0.2,
    ]
    frame_paths: list[Path] = []
    for index, timestamp in enumerate(sample_times):
        destination = output_dir / f"decoded_{index:02d}_{timestamp:05.2f}s.png"
        ffmpeg.run_checked(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "scale=360:640",
                str(destination),
            ],
            timeout=60,
        )
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RenderError(f"FFmpeg did not decode a review frame at {timestamp:.2f}s.")
        frame_paths.append(destination)

    cover = output_dir.parent / "cover.png"
    ffmpeg.run_checked(
        [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, total_duration - 2.0):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(cover),
        ],
        timeout=60,
    )
    if not cover.is_file() or cover.stat().st_size == 0:
        raise RenderError("FFmpeg did not decode the cover frame from the encoded MP4.")

    columns = 3
    thumb_width, thumb_height = 360, 640
    label_height = 42
    rows = math.ceil(len(frame_paths) / columns)
    contact = Image.new(
        "RGB",
        (columns * thumb_width, rows * (thumb_height + label_height)),
        palette.background,
    )
    contact_draw = ImageDraw.Draw(contact)
    for index, frame_path in enumerate(frame_paths):
        row, column = divmod(index, columns)
        x = column * thumb_width
        y = row * (thumb_height + label_height)
        with Image.open(frame_path) as decoded:
            frame = decoded.convert("RGB")
        contact.paste(frame, (x, y))
        contact_draw.text(
            (x + 8, y + thumb_height + 8),
            frame_path.stem.replace("decoded_", "t="),
            font=FontSet().get(True, 19),
            fill=palette.text,
        )
    contact_sheet = output_dir.parent / "contact_sheet.png"
    contact.save(contact_sheet, format="PNG", optimize=True)
    return cover, contact_sheet, frame_paths
