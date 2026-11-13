"""Read uv's terminal progress without confusing package counts with bytes."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class InstallProgress:
    stage: str = "preparing"
    completed: int = 0
    total: int = 0
    package: str = ""
    downloaded: int = 0
    size: int = 0
    speed: float = 0
    detail: str = ""


def format_bytes(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


class TerminalScreen:
    """Minimal VT screen for uv (ConPTY can redraw only the changed digits)."""

    def __init__(self, width: int = 180, height: int = 120):
        self.width, self.height = width, height
        self.rows = [""] * height
        self.row = self.col = 0
        self.escape = ""

    def feed(self, text: str) -> None:
        for char in text:
            if self.escape:
                self.escape += char
                if self.escape.startswith("\x1b]"):
                    if char == "\x07" or self.escape.endswith("\x1b\\") or len(self.escape) > 4096:
                        self.escape = ""
                    continue
                if self.escape == "\x1b[":
                    continue
                if self.escape.startswith("\x1b[") and "@" <= char <= "~":
                    self._control(self.escape[2:-1], char)
                    self.escape = ""
                elif not self.escape.startswith("\x1b["):
                    self.escape = ""
                continue
            if char == "\x1b":
                self.escape = char
            elif char == "\r":
                self.col = 0
            elif char == "\n":
                self._newline()
            elif char == "\b":
                self.col = max(0, self.col - 1)
            elif char >= " ":
                if self.col >= self.width:
                    self.col = 0
                    self._newline()
                line = self.rows[self.row].ljust(self.col)
                self.rows[self.row] = line[:self.col] + char + line[self.col + 1:]
                self.col += 1

    def _newline(self) -> None:
        self.row += 1
        if self.row == self.height:
            self.rows.pop(0)
            self.rows.append("")
            self.row -= 1

    def _control(self, params: str, command: str) -> None:
        if params.startswith("?") or command == "m":
            return
        values = [int(v) if v.isdigit() else 0 for v in params.split(";")]
        n = values[0] or 1
        if command in ("H", "f"):
            self.row = min(self.height - 1, max(0, n - 1))
            self.col = min(self.width - 1, max(0, (values[1] if len(values) > 1 else 1) - 1))
        elif command in "ABCD":
            if command == "A":
                self.row = max(0, self.row - n)
            elif command == "B":
                self.row = min(self.height - 1, self.row + n)
            elif command == "C":
                self.col = min(self.width - 1, self.col + n)
            else:
                self.col = max(0, self.col - n)
        elif command == "G":
            self.col = min(self.width - 1, max(0, n - 1))
        elif command == "K":
            if values[0] == 2:
                self.rows[self.row] = ""
            elif values[0] == 1:
                self.rows[self.row] = " " * (self.col + 1) + self.rows[self.row][self.col + 1:]
            else:
                self.rows[self.row] = self.rows[self.row][:self.col]
        elif command == "J":
            if values[0] == 2:
                self.rows = [""] * self.height
            elif values[0] == 0:
                self.rows[self.row] = self.rows[self.row][:self.col]
                self.rows[self.row + 1:] = [""] * (self.height - self.row - 1)


_BYTES = r"(\d+(?:\.\d+)?)\s*([KMGT]?i?B)"
_PACKAGE = re.compile(r"^\s*([\w][\w.-]*)\s+.*?" + _BYTES + r"\s*/\s*" + _BYTES + r"\s*$")
_COUNT = re.compile(r"[\[(](\d+)\s*/\s*(\d+)[\])]")


def _bytes(number: str, unit: str) -> int:
    exponent = {"B": 0, "K": 1, "M": 2, "G": 3, "T": 4}[unit[0]]
    return int(float(number) * (1024 if "i" in unit else 1000) ** exponent)


class UvProgress:
    def __init__(self):
        self.screen = TerminalScreen()
        self.current = InstallProgress()
        self.samples: dict[str, tuple[float, int]] = {}
        self.names: set[str] = set()

    def log_lines(self) -> list[str]:
        return [line.strip() for line in self.screen.rows if line.strip()
                and not _PACKAGE.match(line) and "Preparing packages" not in line
                and "Installing wheels" not in line]

    def feed(self, text: str, now: float | None = None) -> tuple[InstallProgress, list[str]]:
        now = time.monotonic() if now is None else now
        self.screen.feed(text)
        stage = self.current.stage
        completed, total = self.current.completed, self.current.total
        active = []
        messages = []
        # The bottom-most matching line is the most recent phase/count.
        for line in self.screen.rows:
            if "Preparing packages" in line or "Installing wheels" in line:
                new_stage = "installing" if "Installing wheels" in line else "preparing"
                count = _COUNT.search(line)
                if count:
                    stage = new_stage
                    completed, total = map(int, count.groups())
            match = _PACKAGE.match(line)
            if not match:
                continue
            name, number, unit, size_number, size_unit = match.groups()
            downloaded, size = _bytes(number, unit), _bytes(size_number, size_unit)
            if size <= 0:
                continue
            downloaded = min(downloaded, size)
            start, initial = self.samples.setdefault(name, (now, downloaded))
            speed = max(0, downloaded - initial) / max(0.1, now - start)
            active.append((size, name, downloaded, speed))
            if name not in self.names:
                self.names.add(name)
                messages.append(f'正在下载 {name} ({format_bytes(size)})')
        size, package, downloaded, speed = max(active, default=(0, "", 0, 0))
        self.current = InstallProgress(stage, completed, total, package, downloaded, size, speed)
        return self.current, messages
