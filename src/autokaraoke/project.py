"""Project data model: Song -> Line -> Word -> Syllable, persisted as JSON.

Times are in seconds from the start of the audio. ``None`` means "not timed yet".
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_FILE = "project.akp.json"
FORMAT_VERSION = 1


@dataclass
class Syllable:
    text: str
    start: float | None = None
    end: float | None = None
    # 0..1, how much the automatic aligner trusts this timing; 1.0 for manual edits.
    confidence: float = 0.0


@dataclass
class Word:
    text: str
    syllables: list[Syllable] = field(default_factory=list)

    @property
    def start(self) -> float | None:
        return self.syllables[0].start if self.syllables else None

    @property
    def end(self) -> float | None:
        return self.syllables[-1].end if self.syllables else None


@dataclass
class Line:
    text: str
    words: list[Word] = field(default_factory=list)
    # Line-level timing, e.g. from LRC. Syllable timings refine it.
    start: float | None = None
    end: float | None = None
    # Review hint shown in the editor, e.g. what the transcript heard instead.
    note: str = ""

    def syllables(self) -> list[Syllable]:
        return [s for w in self.words for s in w.syllables]

    def span(self) -> tuple[float, float] | None:
        """(start, end) over the syllables timed at both ends, or None if there are none.

        Lines can be partly timed, e.g. while tap-syncing them syllable by syllable.
        """
        timed = [s for s in self.syllables() if s.start is not None and s.end is not None]
        if not timed:
            return None
        return min(s.start for s in timed), max(s.end for s in timed)


@dataclass
class Project:
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    language: str = ""
    source: str = ""
    # Paths relative to the project directory.
    audio_full: str = ""
    audio_vocals: str = ""
    audio_instrumental: str = ""
    lyrics_source: str = ""  # "lrclib-synced", "lrclib-plain", "manual", ...
    lines: list[Line] = field(default_factory=list)
    version: int = FORMAT_VERSION

    # Not serialized: directory the project lives in.
    path: Path | None = field(default=None, repr=False, compare=False)

    @property
    def slug(self) -> str:
        return slugify(f"{self.artist} - {self.title}" if self.artist else self.title)

    def abspath(self, rel: str) -> Path | None:
        if not rel or self.path is None:
            return None
        return self.path / rel

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("path")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Project:
        d = dict(d)
        lines = [
            Line(
                text=ln["text"],
                start=ln.get("start"),
                end=ln.get("end"),
                note=ln.get("note", ""),
                words=[
                    Word(text=w["text"], syllables=[Syllable(**s) for s in w["syllables"]])
                    for w in ln.get("words", [])
                ],
            )
            for ln in d.pop("lines", [])
        ]
        known = {f for f in cls.__dataclass_fields__ if f not in ("lines", "path")}
        return cls(**{k: v for k, v in d.items() if k in known}, lines=lines)

    def save(self, directory: Path | None = None) -> Path:
        if directory is not None:
            self.path = Path(directory)
        if self.path is None:
            raise ValueError("project has no directory")
        self.path.mkdir(parents=True, exist_ok=True)
        out = self.path / PROJECT_FILE
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out)
        return out

    @classmethod
    def load(cls, path: Path) -> Project:
        path = Path(path)
        file = path / PROJECT_FILE if path.is_dir() else path
        proj = cls.from_dict(json.loads(file.read_text(encoding="utf-8")))
        proj.path = file.parent
        return proj


def slugify(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "", text).strip()
    return re.sub(r"\s+", " ", text) or "untitled"
