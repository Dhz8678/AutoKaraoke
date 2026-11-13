"""Chinese script detection and Windows-native traditional-to-simplified mapping."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

_HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")


def script_language(text: str) -> str:
    """Use explicit scripts before statistical detection (Han-only text isn't Korean)."""
    han, kana, hangul = (len(pattern.findall(text)) for pattern in (_HAN, _KANA, _HANGUL))
    letters = sum(char.isalpha() for char in text)
    if hangul >= 2 and hangul > kana:
        return "ko"
    if kana >= 2:
        return "ja"
    if han >= 2 and not kana and not hangul and han >= letters * .4:
        return "zh"
    return ""


def to_simplified(text: str) -> str:
    if not text or not _HAN.search(text):
        return text
    if os.name != "nt":
        try:
            from opencc import OpenCC
        except ImportError:
            return text
        return OpenCC("t2s").convert(text)
    import ctypes as c
    from ctypes import wintypes as w

    mapper = c.WinDLL("kernel32", use_last_error=True).LCMapStringEx
    mapper.argtypes = [w.LPCWSTR, w.DWORD, w.LPCWSTR, c.c_int, w.LPWSTR,
                       c.c_int, c.c_void_p, c.c_void_p, c.c_ssize_t]
    mapper.restype = c.c_int
    size = mapper("zh-CN", 0x02000000, text, -1, None, 0, None, None, 0)
    if not size:
        raise c.WinError(c.get_last_error())
    output = c.create_unicode_buffer(size)
    if not mapper("zh-CN", 0x02000000, text, -1, output, size, None, None, 0):
        raise c.WinError(c.get_last_error())
    return output.value


def simplify_project(project) -> int:
    """Keep all timing, confidence, backing flags and syllable boundaries intact."""
    text = "\n".join(line.text for line in project.lines)
    if script_language(text) != "zh":
        return 0
    project.language = "zh"
    changed = 0
    for line in project.lines:
        converted = to_simplified(line.text)
        changed += converted != line.text
        line.text = converted
        for word in line.words:
            word.text = to_simplified(word.text)
            for syllable in word.syllables:
                syllable.text = to_simplified(syllable.text)
    return changed


def backup_lyrics_project(project) -> Path | None:
    if not project.path:
        return None
    source = project.path / "project.akp.json"
    if not source.is_file():
        return None
    target = source.with_name(f"project.before-simplified-{time.time_ns()}.json")
    shutil.copy2(source, target)
    return target
