"""Mock image provider: writes a valid 1x1 PNG."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from core.providers import ImageProvider, ImageResult, Usage


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def tiny_png(rgb: tuple[int, int, int] = (15, 23, 42)) -> bytes:
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00" + bytes(rgb))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", pixel)
        + _chunk(b"IEND", b"")
    )


class MockImageProvider(ImageProvider):
    name = "mock"

    def __init__(self, model: str = "mock-image-1") -> None:
        self.model = model
        self.calls: list[str] = []

    def generate(self, prompt: str, out_path: Path, *, width: int, height: int) -> ImageResult:
        self.calls.append(prompt)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(tiny_png())
        return ImageResult(out_path, Usage())
