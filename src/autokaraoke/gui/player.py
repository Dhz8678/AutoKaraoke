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
        self._player.mediaStatusChanged.connect(self._on_status)
        # QMediaPlayer reports position coarsely; interpolate between reports.
        self._last_pos = 0.0
        self._last_wall = time.monotonic()
        self._source: Path | None = None
        # (position, playing) to restore once a newly set source has loaded: seeking
        # before that is ignored by QMediaPlayer and playback would restart from 0.
        self._pending: tuple[float, bool] | None = None

    def load(self, path: Path | None, keep_position: bool = True, reload: bool = False) -> None:
        """``reload`` re-reads the same file (it was rewritten)."""
        if path == self._source and not reload:
            return
        pos, playing = self.position(), self.is_playing()
        if self._pending is not None:
            pos, playing = self._pending
        self._source = path
        self._pending = None
        if reload:
            self._player.setSource(QUrl())
        self._player.setSource(QUrl.fromLocalFile(str(path)) if path else QUrl())
        if keep_position and path:
            self._pending = (pos, playing)
            self._last_pos = pos
            self._last_wall = time.monotonic()
            self.stateChanged.emit(playing)

    def _on_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._pending = None
            self.stateChanged.emit(False)
            return
        loaded = (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia)
        if self._pending is None or status not in loaded:
            return
        pos, playing = self._pending
        self._pending = None
        self.seek(pos)
        if playing:
            self.play()

    def _on_position(self, ms: int) -> None:
        if self._pending is not None:
            return
        self._last_pos = ms / 1000
        self._last_wall = time.monotonic()

    def is_playing(self) -> bool:
        if self._pending is not None:
            return self._pending[1]
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def position(self) -> float:
        if self._pending is not None or not self.is_playing():
            return self._last_pos
        elapsed = (time.monotonic() - self._last_wall) * self._player.playbackRate()
        # Do not extrapolate far if position updates stall.
        return self._last_pos + min(elapsed, 0.5)

    def seek(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        if self._pending is not None:
            self._pending = (seconds, self._pending[1])
        else:
            self._player.setPosition(int(seconds * 1000))
        self._last_pos = seconds
        self._last_wall = time.monotonic()

    def play(self) -> None:
        if self._pending is not None:
            self._pending = (self._pending[0], True)
            self.stateChanged.emit(True)
            return
        self._last_wall = time.monotonic()
        self._player.play()

    def pause(self) -> None:
        self._last_pos = self.position()
        if self._pending is not None:
            self._pending = (self._pending[0], False)
            self.stateChanged.emit(False)
            return
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
