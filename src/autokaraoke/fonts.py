"""Text measurement matching libass, to place things (the bouncing ball) over syllables.

libass sizes fonts like VSFilter: the ASS font size is the height usWinAscent +
usWinDescent of the OS/2 table, not the em size, so the em size is derived from those.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=32)
def font_file(family: str, bold: bool = False) -> str | None:
    """Font file fontconfig picks for ``family`` (the same one libass will use)."""
    if shutil.which("fc-match") is None:
        return None
    pattern = f"{family}:weight={'bold' if bold else 'regular'}"
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True,
                             timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out and Path(out).exists() else None


def _tables(data: bytes) -> dict[bytes, int]:
    offset = 0
    if data[:4] == b"ttcf":  # collection: use the first font
        offset = struct.unpack_from(">I", data, 12)[0]
    n = struct.unpack_from(">H", data, offset + 4)[0]
    tables = {}
    for i in range(n):
        tag, _checksum, start, _length = struct.unpack_from(">4sIII", data, offset + 12 + 16 * i)
        tables[tag] = start
    return tables


@lru_cache(maxsize=32)
def size_metrics(path: str) -> tuple[float, float]:
    """(em size, descent) per unit of ASS font size.

    The em is unitsPerEm / (winAscent + winDescent); the descent is where the baseline
    sits above the bottom of the line box.
    """
    try:
        data = Path(path).read_bytes()
        t = _tables(data)
        upem = struct.unpack_from(">H", data, t[b"head"] + 18)[0]
        win_ascent, win_descent = struct.unpack_from(">HH", data, t[b"OS/2"] + 74)
        if win_ascent + win_descent:
            return upem / (win_ascent + win_descent), win_descent / (win_ascent + win_descent)
    except (OSError, KeyError, struct.error):
        pass
    return 0.75, 0.22


def em_per_size(path: str) -> float:
    return size_metrics(path)[0]


class TextMeasure:
    """Widths of text in ASS pixels for a font family, size and weight."""

    def __init__(self, family: str, size: float, bold: bool = True):
        self.size = size
        self._font = None
        path = font_file(family, bold)
        self.em, self.descent = size * 0.75, size * 0.22  # em size and baseline offset (px)
        if path is None:
            return
        em, descent = size_metrics(path)
        self.em, self.descent = size * em, size * descent
        try:
            from PIL import ImageFont

            # Measure at 10x for sub-pixel precision.
            self._font = ImageFont.truetype(path, max(1, round(size * em_per_size(path) * 10)))
            if bold:
                self._set_bold()
        except (ImportError, OSError):
            self._font = None

    def _set_bold(self) -> None:
        try:
            axes = self._font.get_variation_axes()
        except Exception:
            return  # not a variable font: fontconfig already picked the bold file
        values = []
        for axis in axes:
            name = axis.get("name", b"")
            name = name.decode() if isinstance(name, bytes) else str(name)
            default = axis.get("default", axis["minimum"])
            values.append(min(max(700, axis["minimum"]), axis["maximum"]) if name.lower() == "weight"
                          else default)
        try:
            self._font.set_variation_by_axes(values)
        except Exception:
            pass

    def width(self, text: str) -> float:
        if self._font is None:
            return len(text) * self.size * 0.55  # rough average for Latin text
        return self._font.getlength(text) / 10
