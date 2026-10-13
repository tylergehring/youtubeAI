"""Text-first portrait layout for finance reels, drawn with Pillow.

Each on-screen element (title, methodology, every figure row, CTA, disclosure) becomes its own
transparent PNG so the FFmpeg renderer can reveal them one at a time. Layout is computed here, in
pixels, from the plan and settings: wrapping, sizing and the safe-area fit are all deterministic
and independent of FFmpeg. Nothing is hard-coded to a particular video's content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from core.config import FinanceSettings
from core.exceptions import RenderError
from core.models.finance import LayoutBox, ReelLayout, ReelPlan

# Sizes are in pixels for a 1080-px-wide frame and scale with the real width.
_REFERENCE_WIDTH = 1080
_TITLE_PX, _METHOD_PX, _ROW_PX, _CTA_PX, _DISCLOSURE_PX = 80, 40, 46, 50, 28
_SCALE_STEP = 0.02
_LINE_SPACING = 0.96  # of the font's ascent + descent, which is generous in most UI fonts

_REGULAR_NAMES = (
    "segoeui.ttf",
    "arial.ttf",
    "calibri.ttf",
    "Arial.ttf",
    "Helvetica.ttc",
    "DejaVuSans.ttf",
    "LiberationSans-Regular.ttf",
)
_BOLD_NAMES = (
    "segoeuib.ttf",
    "arialbd.ttf",
    "calibrib.ttf",
    "Arial Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
)

RGBA = tuple[int, int, int, int]


def hex_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


class FontSet:
    """Regular and bold faces, from configured files or common system fonts.

    If no suitable system font exists, Pillow's bundled font is used and bold is simulated with
    a thin stroke, so rendering still works (and the fallback is recorded in layout.json).
    """

    def __init__(self, regular: Path | None = None, bold: Path | None = None) -> None:
        self.regular_name = self._probe(regular, _REGULAR_NAMES, "regular")
        self.bold_name = self._probe(bold, _BOLD_NAMES, "bold")
        self._cache: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}

    @staticmethod
    def _probe(configured: Path | None, names: tuple[str, ...], kind: str) -> str:
        if configured is not None:
            try:
                ImageFont.truetype(str(configured), 12)
            except OSError as exc:
                raise RenderError(
                    f"Cannot load configured {kind} font {configured}: {exc}"
                ) from exc
            return str(configured)
        for name in names:
            try:
                ImageFont.truetype(name, 12)
            except OSError:
                continue
            return name
        return "pillow-default"

    @property
    def synthetic_bold(self) -> bool:
        return self.bold_name == "pillow-default"

    def get(self, bold: bool, size: int) -> ImageFont.FreeTypeFont:
        key = (bold, size)
        if key not in self._cache:
            name = self.bold_name if bold else self.regular_name
            if name == "pillow-default":
                # With FreeType available (always, in Pillow wheels) this is a FreeTypeFont.
                self._cache[key] = cast(ImageFont.FreeTypeFont, ImageFont.load_default(size))
            else:
                self._cache[key] = ImageFont.truetype(name, size)
        return self._cache[key]


@dataclass
class Block:
    """One revealable element: a transparent image plus where and when it appears."""

    name: str
    image: Image.Image
    x: int  # where the image is pasted (includes its shadow margin)
    y: int
    box: LayoutBox  # the visible text/panel rectangle, used for the safe-area check
    start_s: float
    slide: bool = True


@dataclass
class ReelLayoutResult:
    blocks: list[Block]
    scrim: Image.Image
    report: ReelLayout
    warnings: list[str] = field(default_factory=list)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """Greedy word wrap. A single word wider than the line is left on its own line (the caller's
    fit check then rejects it)."""
    if "\n" in text:
        return [
            line
            for paragraph in text.split("\n")
            for line in (wrap_text(paragraph, font, max_width) if paragraph.strip() else [""])
        ]
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and font.getlength(candidate) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _line_height(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return round((ascent + descent) * _LINE_SPACING)


def _draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: RGBA,
    stroke: int = 0,
) -> None:
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke, stroke_fill=fill)


def _text_block(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    color: RGBA,
    width: int,
    stroke: int,
    margin: int,
    size: int,
    align: str = "center",
) -> tuple[Image.Image, int]:
    """Centered lines on a transparent canvas with a soft drop shadow; returns (image, text_h)."""
    line_h = _line_height(font)
    text_h = line_h * len(lines)
    canvas_size = (width + 2 * margin, text_h + 2 * margin)
    shadow = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    text = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    shadow_draw, text_draw = ImageDraw.Draw(shadow), ImageDraw.Draw(text)
    offset = max(1, round(size * 0.04))
    for index, line in enumerate(lines):
        x = margin if align == "left" else margin + (width - font.getlength(line)) / 2
        y = margin + index * line_h
        _draw_text(shadow_draw, (x, y + offset), line, font, (0, 0, 0, 215), stroke)
        _draw_text(text_draw, (x, y), line, font, color, stroke)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1.0, size * 0.07)))
    return Image.alpha_composite(shadow, text), text_h


def _scrim(width: int, height: int, finance: FinanceSettings) -> Image.Image:
    """Dark vertical gradient: strongest at the top and bottom edges, light mid-frame."""
    low, high = finance.scrim_min_opacity, finance.scrim_max_opacity
    red, green, blue = hex_rgb(finance.colors.scrim)
    column = Image.new("L", (1, height))
    for y in range(height):
        position = y / max(1, height - 1)
        edge = abs(position - 0.5) * 2  # 0 at the middle, 1 at the edges
        opacity = low + (high - low) * edge**1.6
        column.putpixel((0, y), round(255 * opacity))
    alpha = column.resize((width, height), Image.Resampling.NEAREST)
    image = Image.new("RGBA", (width, height), (red, green, blue, 0))
    image.putalpha(alpha)
    return image


class _DoesNotFit(Exception):
    pass


def _compose(plan: ReelPlan, finance: FinanceSettings, fonts: FontSet, scale: float) -> list[Block]:
    """Lay everything out at one text scale; raises _DoesNotFit if it leaves the safe area."""
    width, height = finance.width, finance.height
    margins = finance.safe_margins
    x0, x1 = round(width * margins.left), width - round(width * margins.right)
    y0, y1 = round(height * margins.top), height - round(height * margins.bottom)
    inner = x1 - x0

    def px(base: int) -> int:
        return max(1, round(base * scale))

    colors = finance.colors
    text_rgb = (*hex_rgb(colors.text), 255)
    muted_rgb = (*hex_rgb(colors.muted), 255)
    accent_rgb = (*hex_rgb(plan.accent_color or colors.accent), 255)
    panel_rgb = hex_rgb(colors.panel)
    panel_fill = (*panel_rgb, round(255 * finance.panel_opacity))
    timing = plan.timing
    editorial = plan.style.preset == "editorial"
    alignment = plan.style.title_align or ("left" if editorial else "center")
    row_format = plan.style.row_format or ("rules" if editorial else "panels")
    margin = px(14)
    blocks: list[Block] = []

    def add(name: str, image: Image.Image, x: int, y: int, box_h: int, box_w: int, at: float,
            slide: bool = True) -> None:  # fmt: skip
        if x < x0 or x + box_w > x1 or y < y0 or y + box_h > y1:
            raise _DoesNotFit(name)
        blocks.append(
            Block(
                name=name,
                image=image,
                x=x - margin,
                y=y - margin,
                box=LayoutBox(name=name, x=x, y=y, width=box_w, height=box_h, start_s=at),
                start_s=at,
                slide=slide,
            )
        )

    def text_element(
        name: str, text: str, base_px: int, bold: bool, color: RGBA, at: float, y: int,
        slide: bool = True, align: str = "center",
    ) -> int:  # fmt: skip
        size = px(base_px)
        font = fonts.get(bold, size)
        lines = wrap_text(text, font, inner)
        if any(font.getlength(line) > inner for line in lines):
            raise _DoesNotFit(name)
        stroke = max(1, size // 30) if bold and fonts.synthetic_bold else 0
        image, text_h = _text_block(lines, font, color, inner, stroke, margin, size, align)
        add(name, image, x0, y, text_h, inner, at, slide)
        return text_h

    # Bottom-anchored disclosure first: it fixes how much room the rest has.
    disclosure_size = px(_DISCLOSURE_PX)
    disclosure_font = fonts.get(False, disclosure_size)
    disclosure_lines = wrap_text(plan.disclosure, disclosure_font, inner)
    disclosure_h = _line_height(disclosure_font) * len(disclosure_lines)
    disclosure_top = y1 - disclosure_h

    cursor = y0
    if plan.style.eyebrow:
        cursor += text_element(
            "eyebrow",
            plan.style.eyebrow,
            28,
            True,
            accent_rgb,
            timing.title_at_s,
            cursor,
            align=alignment,
        )
        cursor += px(22)
    cursor += text_element(
        "title",
        plan.title,
        plan.style.title_size,
        True,
        text_rgb,
        timing.title_at_s,
        cursor,
        align=alignment,
    )
    cursor += px(26)
    cursor += text_element(
        "methodology",
        plan.methodology,
        _METHOD_PX,
        False,
        muted_rgb,
        timing.methodology_at_s,
        cursor,
        align=alignment,
    )
    cursor += px(38)

    # One row font size for every row: the largest that fits the widest label + value.
    pad, bar = px(18), px(8)
    row_size = px(plan.style.row_size)
    floor = max(1, round(row_size * 0.7))
    while True:
        label_font, value_font = fonts.get(False, row_size), fonts.get(True, row_size)
        widest = max(
            label_font.getlength(r.label) + value_font.getlength(r.value) for r in plan.rows
        )
        if widest + px(40) <= inner - 2 * pad - bar or row_size <= floor:
            break
        row_size -= 1
    if widest + px(40) > inner - 2 * pad - bar:
        raise _DoesNotFit("rows")
    row_h = round(row_size * (1.25 if editorial else 1.0)) + 2 * pad
    row_gap = px(12)
    stroke = max(1, row_size // 30) if fonts.synthetic_bold else 0
    for index, row in enumerate(plan.rows):
        panel = Image.new("RGBA", (inner + 2 * margin, row_h + 2 * margin), (0, 0, 0, 0))
        draw = ImageDraw.Draw(panel)
        if row_format == "panels":
            draw.rounded_rectangle(
                (margin, margin, margin + inner - 1, margin + row_h - 1),
                radius=row_h // 4,
                fill=panel_fill,
            )
            inset = round(row_h * 0.22)
            draw.rounded_rectangle(
                (margin + px(14), margin + inset, margin + px(14) + bar, margin + row_h - inset),
                radius=bar // 2,
                fill=accent_rgb,
            )
        else:
            draw.rectangle(
                (margin, margin, margin + inner - 1, margin + row_h - 1),
                fill=panel_fill,
            )
            draw.line(
                (margin, margin + row_h - 1, margin + inner - 1, margin + row_h - 1),
                fill=(*hex_rgb(colors.muted), 85),
                width=px(1),
            )
        text_y = margin + (row_h - _line_height(label_font)) / 2
        label_x = margin + (pad if row_format == "rules" else px(14) + bar + pad)
        _draw_text(draw, (label_x, text_y), row.label, label_font, text_rgb)
        value_x = margin + inner - pad - value_font.getlength(row.value)
        _draw_text(draw, (value_x, text_y), row.value, value_font, accent_rgb, stroke)
        add(f"row_{index + 1}", panel, x0, cursor, row_h, inner, timing.row_at_s[index])
        cursor += row_h + row_gap
    cursor += px(44) - row_gap

    # Closing question in a bordered panel, then the persistent disclosure at the bottom.
    cta_size = px(_CTA_PX)
    cta_font = fonts.get(True, cta_size)
    cta_pad = px(28)
    cta_lines = wrap_text(plan.cta, cta_font, inner - 2 * cta_pad)
    if any(cta_font.getlength(line) > inner - 2 * cta_pad for line in cta_lines):
        raise _DoesNotFit("cta")
    cta_stroke = max(1, cta_size // 30) if fonts.synthetic_bold else 0
    text_img, text_h = _text_block(
        cta_lines, cta_font, text_rgb, inner - 2 * cta_pad, cta_stroke, 0, cta_size
    )
    cta_h = text_h + 2 * cta_pad
    cta_panel = Image.new("RGBA", (inner + 2 * margin, cta_h + 2 * margin), (0, 0, 0, 0))
    draw = ImageDraw.Draw(cta_panel)
    draw.rounded_rectangle(
        (margin, margin, margin + inner - 1, margin + cta_h - 1),
        radius=px(10 if editorial else 26),
        fill=panel_fill,
        outline=accent_rgb,
        width=max(1, px(2 if editorial else 4)),
    )
    cta_panel.alpha_composite(text_img, (margin + cta_pad, margin + cta_pad))
    add("cta", cta_panel, x0, cursor, cta_h, inner, timing.cta_at_s)
    cursor += cta_h + px(24)
    if cursor > disclosure_top:
        raise _DoesNotFit("disclosure")

    text, _ = _text_block(
        disclosure_lines, disclosure_font, muted_rgb, inner, 0, margin, disclosure_size
    )
    add("disclosure", text, x0, disclosure_top, disclosure_h, inner, timing.disclosure_at_s, False)
    return blocks


def build_layout(plan: ReelPlan, finance: FinanceSettings, fonts: FontSet) -> ReelLayoutResult:
    """Fit the plan's text inside the safe area, shrinking type (never below `min_text_scale`)
    until it fits. Raises RenderError when the content is simply too long."""
    base = finance.width / _REFERENCE_WIDTH
    minimum = (
        max(finance.min_text_scale, 0.85)
        if plan.style.preset == "editorial"
        else (finance.min_text_scale)
    )
    scale = 1.0
    blocks: list[Block] | None = None
    failed = ""
    while scale >= minimum - 1e-9:
        try:
            blocks = _compose(plan, finance, fonts, base * scale)
            break
        except _DoesNotFit as exc:
            failed = str(exc)
            scale = round(scale - _SCALE_STEP, 4)
    if blocks is None:
        raise RenderError(
            f"Reel text does not fit the safe area even at {minimum:.0%} size "
            f"(stopped at {failed!r}). Shorten the title, labels or CTA, or reduce the row count."
        )
    margins = finance.safe_margins
    report = ReelLayout(
        width=finance.width,
        height=finance.height,
        safe_left=round(finance.width * margins.left),
        safe_top=round(finance.height * margins.top),
        safe_right=finance.width - round(finance.width * margins.right),
        safe_bottom=finance.height - round(finance.height * margins.bottom),
        scale=round(scale, 4),
        font_regular=fonts.regular_name,
        font_bold=fonts.bold_name,
        boxes=[b.box for b in blocks],
    )
    warnings = []
    if fonts.regular_name == "pillow-default" or fonts.synthetic_bold:
        warnings.append(
            "No system font found; used Pillow's bundled font. Set finance.font_regular and "
            "finance.font_bold in config/settings.yaml for a consistent look."
        )
    return ReelLayoutResult(
        blocks=blocks, scrim=_scrim(finance.width, finance.height, finance), report=report,
        warnings=warnings,
    )  # fmt: skip


def prepare_background_image(
    source: Path,
    width: int,
    height: int,
    out: Path,
    *,
    centering: tuple[float, float] = (0.5, 0.5),
) -> None:
    """Scale-and-crop ("cover") an image to the frame, honouring EXIF orientation."""
    try:
        with Image.open(source) as opened:
            fitted = ImageOps.fit(
                ImageOps.exif_transpose(opened).convert("RGB"),
                (width, height),
                Image.Resampling.LANCZOS,
                centering=centering,
            )
    except Exception as exc:  # noqa: BLE001 - corrupt files raise many Pillow types
        raise RenderError(f"Cannot read background image {source.name}: {exc}") from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    fitted.save(out, "PNG")
