"""Export to enhanced LRC (line tags plus <mm:ss.xx> word tags)."""

from __future__ import annotations

from pathlib import Path

from autokaraoke.project import Project


def lrc_time(t: float) -> str:
    cs = max(0, round(t * 100))
    m, cs = divmod(cs, 6000)
    return f"{m:02d}:{cs // 100:02d}.{cs % 100:02d}"


def build_lrc(project: Project) -> str:
    out = []
    if project.artist:
        out.append(f"[ar:{project.artist}]")
    if project.title:
        out.append(f"[ti:{project.title}]")
    for line in project.lines:
        start = line.words[0].start if line.words and line.words[0].start is not None else line.start
        if start is None:
            continue
        words = [
            f"<{lrc_time(w.start)}>{w.text}" if w.start is not None else w.text for w in line.words
        ]
        out.append(f"[{lrc_time(start)}]" + " ".join(words))
    return "\n".join(out) + "\n"


def write_lrc(project: Project, path: Path) -> Path:
    path.write_text(build_lrc(project), encoding="utf-8")
    return path
