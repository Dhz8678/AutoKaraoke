"""Audio player with a smooth, interpolated clock and switchable stems."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class Player(QObject):
    stateChanged = Signal(bool)  # playing
    durationChanged = Signal(float)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._player.setAudioOutput(self._output)
        self._player.positionChanged.connect(self._on_position)
        self._player.playbackStateChanged.connect(
            lambda s: self.stateChanged.emit(s == QMediaPlayer.PlaybackState.PlayingState)
        )
        self._player.durationChanged.connect(lambda ms: self.durationChanged.emit(ms / 1000))
        # QMediaPlayer reports position coarsely; interpolate between reports.
        self._last_pos = 0.0
        self._last_wall = time.monotonic()
        self._source: Path | None = None

    def load(self, path: Path | None, keep_position: bool = True) -> None:
        if path == self._source:
            return
        pos, playing = self.position(), self.is_playing()
        self._source = path
        self._player.setSource(QUrl.fromLocalFile(str(path)) if path else QUrl())
        if keep_position and path:
            self.seek(pos)
            if playing:
                self._player.play()

    def _on_position(self, ms: int) -> None:
        self._last_pos = ms / 1000
        self._last_wall = time.monotonic()

    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def position(self) -> float:
        if not self.is_playing():
            return self._last_pos
        elapsed = (time.monotonic() - self._last_wall) * self._player.playbackRate()
        # Do not extrapolate far if position updates stall.
        return self._last_pos + min(elapsed, 0.5)

    def seek(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        self._player.setPosition(int(seconds * 1000))
        self._last_pos = seconds
        self._last_wall = time.monotonic()

    def play(self) -> None:
        self._last_wall = time.monotonic()
        self._player.play()

    def pause(self) -> None:
        self._last_pos = self.position()
        self._player.pause()

    def toggle(self) -> None:
        self.pause() if self.is_playing() else self.play()

    def set_rate(self, rate: float) -> None:
        self._last_pos = self.position()
        self._last_wall = time.monotonic()
        self._player.setPlaybackRate(rate)
        if hasattr(self._player, "setPitchCompensation"):
            self._player.setPitchCompensation(True)

    def set_volume(self, volume: float) -> None:
        self._output.setVolume(volume)
