"""Text measurement matching libass, to place things (the bouncing ball) over syllables.

libass sizes fonts like VSFilter: the ASS font size is the height usWinAscent +
usWinDescent of the OS/2 table, not the em size, so the em size is derived from those.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


def _font_name(name: str) -> str:
    aliases = {"微软雅黑": "Microsoft YaHei", "宋体": "SimSun", "黑体": "SimHei"}
    return re.sub(r"[\s_-]+", "", aliases.get(name, name)).casefold()


def _windows_font(family: str, bold: bool) -> str | None:
    folders = [Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts",
               Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    known = {
        "microsoftyahei": ("msyh.ttc", "msyhbd.ttc"),
        "microsoftyaheiui": ("msyh.ttc", "msyhbd.ttc"),
        "simsun": ("simsun.ttc", "simsun.ttc"),
        "simhei": ("simhei.ttf", "simhei.ttf"),
        "arial": ("arial.ttf", "arialbd.ttf"),
        "segoeui": ("segoeui.ttf", "segoeuib.ttf"),
    }
    wanted = _font_name(family)
    if wanted in known:
        for folder in folders:
            path = folder / known[wanted][int(bold)]
            if path.is_file():
                return str(path)
    try:
        import winreg

        candidates = []
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        name, filename, _ = winreg.EnumValue(key, index)
                        if not isinstance(filename, str):
                            continue
                        is_bold = "bold" in name.casefold()
                        names = re.sub(r"\([^)]*\)|\b(Bold|Regular)\b", "", name, flags=re.I).split("&")
                        if wanted not in {_font_name(item) for item in names}:
                            continue
                        for path in ([Path(filename)] if Path(filename).is_absolute() else [folder / filename for folder in folders]):
                            if path.is_file():
                                candidates.append((is_bold != bold, str(path)))
            except OSError:
                continue
        if candidates:
            return min(candidates)[1]
    except ImportError:
        pass
    return None


@lru_cache(maxsize=32)
def font_file(family: str, bold: bool = False) -> str | None:
    """Font file fontconfig picks for ``family`` (the same one libass will use)."""
    if os.name == "nt":
        found = _windows_font(family, bold)
        if found:
            return found
    if shutil.which("fc-match") is None:
        return None
    pattern = f"{family}:weight={'bold' if bold else 'regular'}"
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True,
                             timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out and Path(out).exists() else None


def _tables(data: bytes, index: int = 0) -> dict[bytes, int]:
    offset = 0
    if data[:4] == b"ttcf":  # collection: use the first font
        count = struct.unpack_from(">I", data, 8)[0]
        offset = struct.unpack_from(">I", data, 12 + 4 * (index if index < count else 0))[0]
    n = struct.unpack_from(">H", data, offset + 4)[0]
    tables = {}
    for i in range(n):
        tag, _checksum, start, _length = struct.unpack_from(">4sIII", data, offset + 12 + 16 * i)
        tables[tag] = start
    return tables


@lru_cache(maxsize=64)
def font_index(path: str, family: str) -> int:
    """Select the requested family inside TTC collections, including YaHei UI."""
    try:
        data = Path(path).read_bytes()
        if data[:4] != b"ttcf":
            return 0
        wanted = _font_name(family)
        for index in range(struct.unpack_from(">I", data, 8)[0]):
            start = _tables(data, index).get(b"name")
            if start is None:
                continue
            _, count, strings = struct.unpack_from(">HHH", data, start)
            for entry in range(count):
                platform, encoding, _, name_id, length, offset = struct.unpack_from(">HHHHHH", data, start + 6 + entry * 12)
                if name_id not in (1, 16):
                    continue
                raw = data[start + strings + offset:start + strings + offset + length]
                name = raw.decode("utf-16-be" if platform in (0, 3) else "mac_roman", errors="ignore")
                if _font_name(name) == wanted:
                    return index
    except (OSError, KeyError, struct.error):
        pass
    return 0


@lru_cache(maxsize=32)
def size_metrics(path: str, index: int = 0) -> tuple[float, float]:
    """(em size, descent) per unit of ASS font size.

    The em is unitsPerEm / (winAscent + winDescent); the descent is where the baseline
    sits above the bottom of the line box.
    """
    try:
        data = Path(path).read_bytes()
        t = _tables(data, index)
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

    def __init__(self, family: str, size: float, bold: bool = True, spacing: float = 0.0):
        self.size = size
        self.spacing = spacing
        self._font = None
        path = font_file(family, bold)
        self.em, self.descent = size * 0.75, size * 0.22  # em size and baseline offset (px)
        if path is None:
            return
        index = font_index(path, family)
        em, descent = size_metrics(path, index)
        self.em, self.descent = size * em, size * descent
        try:
            from PIL import ImageFont

            # Measure at 10x for sub-pixel precision.
            self._font = ImageFont.truetype(path, max(1, round(size * em * 10)), index=index)
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
            width = sum(self.em * (1.0 if unicodedata.east_asian_width(char) in ("W", "F") else .55)
                        for char in text)
        else:
            width = self._font.getlength(text) / 10
        return width + max(0, len(text) - 1) * self.spacing

    def advance(self, text: str) -> float:
        """Pen position after text, including the gap before the next glyph."""
        return self.width(text) + (self.spacing if text else 0.0)
