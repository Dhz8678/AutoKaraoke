"""Main editor window."""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from autokaraoke import config, steps
from autokaraoke.gui.dialogs import ProposalDialog, SettingsDialog
from autokaraoke.gui.jobs import Job
from autokaraoke.gui.lyrics_panel import LyricsPanel, fmt_time
from autokaraoke.gui.player import Player
from autokaraoke.gui.preview import KaraokePreview, current_line_index
from autokaraoke.gui.timeline import Timeline, compute_peaks
from autokaraoke.project import Project
from autokaraoke.text.edit import retext_line, to_notation

UNDO_LIMIT = 100
MIN_DUR = 0.04


class NewProjectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New karaoke project")
        form = QFormLayout(self)
        row = QHBoxLayout()
        self.source = QLineEdit()
        placeholder = "Audio file"
        if config.download_enabled():
            placeholder += " or YouTube / YT Music URL"
        self.source.setPlaceholderText(placeholder)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.source)
        row.addWidget(browse)
        form.addRow("Source", row)
        self.artist = QLineEdit()
        self.title = QLineEdit()
        self.lang = QLineEdit()
        self.artist.setPlaceholderText("guessed from file name / metadata")
        self.title.setPlaceholderText("guessed from file name / metadata")
        self.lang.setPlaceholderText("auto (e.g. it, en, es)")
        form.addRow("Artist", self.artist)
        form.addRow("Title", self.title)
        form.addRow("Language", self.lang)
        self.out = QLineEdit(str(config.PROJECTS_DIR.resolve()))
        form.addRow("Projects folder", self.out)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(560, 0)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose audio", "", "Audio (*.mp3 *.flac *.wav *.m4a *.ogg *.opus *.aac *.mp4 *.mkv)"
        )
        if path:
            self.source.setText(path)
            stem = Path(path).stem
            if " - " in stem and not self.artist.text() and not self.title.text():
                a, t = stem.split(" - ", 1)
                self.artist.setText(a.strip())
                self.title.setText(t.strip())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project: Project | None = None
        self.dirty = False
        self.undo_stack: list[list] = []
        self.redo_stack: list[list] = []
        self.job: Job | None = None
        self.tap_index = -1
        self.tap_prev = -1  # index of the last tapped syllable (Tap mode)
        self.tap_pressed = False
        self.peaks_cache: dict[str, np.ndarray] = {}

        self.player = Player(self)
        self.timeline = Timeline()
        self.canvas = self.timeline.canvas
        self.preview = KaraokePreview()
        self.lyrics = LyricsPanel()
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)

        self._build_actions()
        self._build_layout()
        self._connect()

        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        QApplication.instance().installEventFilter(self)
        self._update_title()
        self._set_enabled()

    # ---- UI construction --------------------------------------------------

    def _action(self, text, slot, shortcut=None, tip=None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        return a

    def _build_actions(self) -> None:
        self.act_new = self._action("New…", self.new_project, "Ctrl+N", "Create a project from an audio file")
        self.act_open = self._action("Open…", self.open_dialog, "Ctrl+O")
        self.act_save = self._action("Save", self.save, "Ctrl+S")
        self.act_undo = self._action("Undo", self.undo, "Ctrl+Z")
        self.act_redo = self._action("Redo", self.redo, "Ctrl+Shift+Z")
        self.act_lyrics = self._action("Lyrics", self.fetch_lyrics,
                                       tip="Fetch lyrics from LRCLIB or load a .lrc/.txt file")
        self.act_fix = self._action("Fix lyrics…", self.fix_lyrics,
                                    tip="Compare the lyrics with what is sung (speech recognition + LLM): "
                                        "expand repeated choruses, remove labels, fix wrong words")
        self.act_settings = self._action("Settings…", self.show_settings)
        self.act_separate = self._action("Separate", self.separate, tip="Split vocals / instrumental")
        self.act_align = self._action("Auto-sync", self.align, tip="Automatic syllable alignment")
        self.act_render = self._action("Render video…", self.render, "Ctrl+R")
        self.act_export = self._action("Export…", self.export, "Ctrl+E", "Export ASS or enhanced LRC")

        tb = self.addToolBar("Main")
        tb.setMovable(False)
        for a in (self.act_new, self.act_open, self.act_save):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.act_lyrics, self.act_separate, self.act_fix, self.act_align):
            tb.addAction(a)
        self.engine = QComboBox()
        self.engine.addItem("Qwen + MMS check", "qwen")
        self.engine.addItem("MMS (any language)", "mms")
        self.engine.addItem("Even spread (LRC lines)", "naive")
        self.engine.setToolTip("Alignment engine for Auto-sync")
        tb.addWidget(self.engine)
        tb.addSeparator()
        for a in (self.act_render, self.act_export):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)

        file_menu = self.menuBar().addMenu("&File")
        for a in (self.act_new, self.act_open, self.act_save, self.act_export, self.act_render):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addAction(self.act_settings)
        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._action("Keyboard shortcuts", self.show_help, "F1"))

    def _build_layout(self) -> None:
        transport = QWidget()
        h = QHBoxLayout(transport)
        h.setContentsMargins(6, 2, 6, 2)
        self.btn_play = QToolButton()
        self.btn_play.setText("▶")
        self.btn_play.setToolTip("Play / pause (Space)")
        self.time_label = QLabel("00:00.00")
        self.time_label.setMinimumWidth(80)
        self.speed = QComboBox()
        for r in (1.0, 0.75, 0.5):
            self.speed.addItem(f"{r:g}×", r)
        self.track = QComboBox()
        self.tap_mode = QComboBox()
        self.tap_mode.addItem("Off", "")
        self.tap_mode.addItem("Hold: press=start, release=end", "hold")
        self.tap_mode.addItem("Tap: each tap starts a syllable", "tap")
        self.tap_mode.setToolTip("Tap sync with the Space bar, starting from the selected syllable")
        self.latency = QSpinBox()
        self.latency.setRange(-300, 500)
        self.latency.setSuffix(" ms")
        self.latency.setValue(60)
        self.latency.setToolTip("Reaction-time compensation subtracted from taps")
        zoom_in, zoom_out = QToolButton(), QToolButton()
        zoom_in.setText("+")
        zoom_out.setText("−")
        zoom_in.clicked.connect(lambda: self.canvas.zoom(1.4))
        zoom_out.clicked.connect(lambda: self.canvas.zoom(1 / 1.4))
        for wdg in (self.btn_play, self.time_label, QLabel("Speed"), self.speed, QLabel("Audio"),
                    self.track, QLabel("Tap sync"), self.tap_mode, self.latency):
            h.addWidget(wdg)
        h.addStretch()
        h.addWidget(QLabel("Zoom"))
        h.addWidget(zoom_out)
        h.addWidget(zoom_in)

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self.lyrics)
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.preview)
        right.addWidget(self.log_view)
        right.setSizes([260, 100])
        top.addWidget(right)
        top.setSizes([420, 700])

        main = QSplitter(Qt.Orientation.Vertical)
        main.addWidget(top)
        bottom = QWidget()
        v = QVBoxLayout(bottom)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(transport)
        v.addWidget(self.timeline)
        main.addWidget(bottom)
        main.setStretchFactor(0, 1)
        main.setStretchFactor(1, 0)
        self.setCentralWidget(main)
        self.resize(1280, 800)

    def _connect(self) -> None:
        self.btn_play.clicked.connect(self.player.toggle)
        self.player.stateChanged.connect(lambda playing: self.btn_play.setText("⏸" if playing else "▶"))
        self.speed.currentIndexChanged.connect(lambda: self.player.set_rate(self.speed.currentData()))
        self.track.currentIndexChanged.connect(self._load_track)
        self.tap_mode.currentIndexChanged.connect(self._tap_mode_changed)
        self.canvas.seekRequested.connect(self.player.seek)
        self.canvas.playFromRequested.connect(self._play_from)
        self.canvas.aboutToEdit.connect(self.push_undo)
        self.canvas.edited.connect(self._after_edit)
        self.canvas.selectionChanged.connect(self._selection_changed)
        self.lyrics.lineActivated.connect(self._goto_line)
        self.lyrics.editRequested.connect(self.edit_line)

    # ---- project ----------------------------------------------------------

    def open_dialog(self) -> None:
        if not self._maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", str(config.PROJECTS_DIR), "AutoKaraoke project (*.akp.json)"
        )
        if path:
            self.open_project(Path(path))

    def open_project(self, path: Path) -> None:
        try:
            proj = Project.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return
        self._set_project(proj)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.log(f"Opened {proj.path}")

    def _set_project(self, proj: Project | None) -> None:
        self.project = proj
        self.dirty = False
        self.tap_mode.setCurrentIndex(0)
        self.peaks_cache.clear()
        self.canvas.set_project(proj, None)
        self.preview.set_project(proj)
        self.lyrics.set_project(proj)
        self.timeline._sync_scrollbar()
        self.track.blockSignals(True)
        self.track.clear()
        if proj:
            for label, rel in (("Original", proj.audio_full), ("Vocals", proj.audio_vocals),
                               ("Instrumental", proj.audio_instrumental)):
                if rel and (proj.path / rel).exists():
                    self.track.addItem(label, str(proj.path / rel))
        self.track.blockSignals(False)
        self._load_track()
        self._update_title()
        self._set_enabled()
        self._update_status()

    def _load_peaks(self, path: str) -> np.ndarray | None:
        import soundfile as sf

        if path not in self.peaks_cache:
            if not Path(path).exists():
                return None
            audio, sr = sf.read(path, dtype="float32", always_2d=True)
            self.peaks_cache[path] = compute_peaks(audio.mean(axis=1), sr)
        return self.peaks_cache[path]

    def _load_track(self) -> None:
        data = self.track.currentData()
        self.player.load(Path(data) if data else None)
        self.canvas.set_peaks(self._load_peaks(data) if data else None)

    def save(self) -> bool:
        if not self.project:
            return False
        self.project.save()
        self.dirty = False
        self._update_title()
        self.log("Saved")
        return True

    def _maybe_save(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(
            self, "Unsaved changes", "Save changes to the current project?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Save:
            self.save()
        return True

    def closeEvent(self, e) -> None:
        if self.job and self.job.isRunning():
            QMessageBox.information(self, "Busy", "Wait for the current task to finish.")
            e.ignore()
            return
        e.accept() if self._maybe_save() else e.ignore()

    def _update_title(self) -> None:
        name = f"{self.project.artist} - {self.project.title}" if self.project else "No project"
        self.setWindowTitle(f"{'*' if self.dirty else ''}{name} — AutoKaraoke")

    def _set_enabled(self) -> None:
        busy = self.job is not None and self.job.isRunning()
        has = self.project is not None
        for a in (self.act_lyrics, self.act_separate, self.act_fix, self.act_align, self.act_render,
                  self.act_export):
            a.setEnabled(has and not busy)
        for a in (self.act_new, self.act_open):
            a.setEnabled(not busy)
        self.act_save.setEnabled(has)

    def log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)
        self.statusBar().showMessage(msg, 5000)

    # ---- undo -------------------------------------------------------------

    def _snapshot(self) -> list:
        return copy.deepcopy(self.project.lines) if self.project else []

    def push_undo(self) -> None:
        if not self.project:
            return
        self.undo_stack.append(self._snapshot())
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()

    def _restore(self, lines: list) -> None:
        self.project.lines = lines
        self.canvas.refresh()
        self.lyrics.refresh()
        self._mark_dirty()

    def undo(self) -> None:
        if self.project and self.undo_stack:
            self.redo_stack.append(self._snapshot())
            self._restore(self.undo_stack.pop())

    def redo(self) -> None:
        if self.project and self.redo_stack:
            self.undo_stack.append(self._snapshot())
            self._restore(self.redo_stack.pop())

    def _mark_dirty(self) -> None:
        self.dirty = True
        self._update_title()
        self._update_status()

    def _after_edit(self) -> None:
        self.lyrics.refresh()
        self._mark_dirty()

    # ---- navigation -------------------------------------------------------

    def _tick(self) -> None:
        t = self.player.position()
        self.time_label.setText(fmt_time(t))
        self.canvas.set_playhead(t, follow=self.player.is_playing())
        self.preview.set_time(t)
        if self.project and self.player.is_playing():
            self.lyrics.highlight(current_line_index(self.project, t))

    def _play_from(self, t: float) -> None:
        self.player.seek(t)
        self.player.play()

    def _goto_line(self, row: int) -> None:
        if not self.project:
            return
        first = sum(len(ln.syllables()) for ln in self.project.lines[:row])
        line = self.project.lines[row]
        if line.syllables():
            self.canvas.select(first)
            start = line.syllables()[0].start
            if start is not None:
                self.player.seek(max(0.0, start - 0.5))

    def _selection_changed(self, index: int) -> None:
        if index >= 0 and self.project:
            self.lyrics.highlight(self.canvas.syl_line[index])
        self._update_status()

    def _update_status(self) -> None:
        if not self.project:
            return
        flagged = sum(1 for s in self.canvas.sylls if s.confidence < 0.5)
        msg = f"{len(self.project.lines)} lines · {len(self.canvas.sylls)} syllables · {flagged} to review"
        if self.tap_mode.currentData() and 0 <= self.tap_index < len(self.canvas.sylls):
            msg += f" · TAP next: “{self.canvas.sylls[self.tap_index].text}” (Space)"
        self.statusBar().showMessage(msg)

    # ---- keyboard ---------------------------------------------------------

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if e.type() not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if QApplication.activeModalWidget() is not None or not self.isActiveWindow():
            return False
        if isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit)) and \
                QApplication.focusWidget() is not self.log_view:
            return False
        key = e.key()
        press = e.type() == QEvent.Type.KeyPress
        if e.isAutoRepeat():
            return key == Qt.Key.Key_Space
        if key == Qt.Key.Key_Space:
            if self.tap_mode.currentData():
                self._tap(press)
            elif press:
                self.player.toggle()
            return True
        if not press or not self.project:
            return False
        if key == Qt.Key.Key_Escape and self.tap_mode.currentData():
            self.tap_mode.setCurrentIndex(0)
            return True
        mods = e.modifiers()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not (mods & Qt.KeyboardModifier.ControlModifier) \
                and not isinstance(QApplication.focusWidget(), LyricsPanel):
            step = -1 if key == Qt.Key.Key_Left else 1
            n = len(self.canvas.sylls)
            if n:
                self.canvas.select(min(max(self.canvas.selected + step, 0), n - 1))
            return True
        if key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight) and self.canvas.selected >= 0:
            self._set_boundary_at_playhead(start=key == Qt.Key.Key_BracketLeft)
            return True
        return False

    def _set_boundary_at_playhead(self, start: bool) -> None:
        """'[' / ']' set the selected syllable's start / end to the playhead."""
        i = self.canvas.selected
        s = self.canvas.sylls[i]
        t = self.player.position()
        self.push_undo()
        if start:
            s.start = min(t, s.end - MIN_DUR) if s.end is not None else t
            if s.end is None:
                s.end = s.start + 0.3
            prev = self.canvas.sylls[i - 1] if i > 0 else None
            if prev is not None and prev.start is not None and prev.end is not None \
                    and prev.end > s.start:
                prev.end = max(s.start, prev.start + MIN_DUR)
        else:
            s.end = max(t, (s.start if s.start is not None else t - 0.3) + MIN_DUR)
            if s.start is None:
                s.start = s.end - 0.3
        s.confidence = 1.0
        self._fix_lines()
        self.canvas.update()
        self._after_edit()

    def _fix_lines(self) -> None:
        for line in self.project.lines:
            if span := line.span():
                line.start, line.end = span

    # ---- tap sync ---------------------------------------------------------

    def _tap_mode_changed(self) -> None:
        if not self.tap_mode.currentData():
            if self.tap_index >= 0:
                self.log("Tap sync stopped")
            self.tap_index = -1
            self.tap_prev = -1
            self.tap_pressed = False
            self._update_status()
            return
        if not self.project or not self.canvas.sylls:
            self.tap_mode.setCurrentIndex(0)
            return
        self.tap_index = max(self.canvas.selected, 0)
        self.tap_prev = -1
        self.tap_pressed = False
        self.push_undo()
        s = self.canvas.sylls[self.tap_index]
        if not self.player.is_playing():
            if s.start is not None:
                self.player.seek(max(0.0, s.start - 2.0))
            self.player.play()
        self.canvas.setFocus()
        self.log("Tap sync: press Space on each syllable, Esc to stop")
        self._update_status()

    def _tap(self, press: bool) -> None:
        sylls = self.canvas.sylls
        if not (0 <= self.tap_index < len(sylls)) and press:
            self.tap_mode.setCurrentIndex(0)
            return
        t = max(0.0, self.player.position() - self.latency.value() / 1000)
        mode = self.tap_mode.currentData()
        if mode == "hold":
            if press:
                s = sylls[self.tap_index]
                # Keep the old end while the key is held so the block stays visible.
                s.start = t
                s.end = max(s.end or 0.0, t + MIN_DUR)
                s.confidence = 1.0
                self.tap_pressed = True
            elif self.tap_pressed:
                s = sylls[self.tap_index]
                s.end = max(t, s.start + MIN_DUR)
                self.tap_pressed = False
                self._tap_advance()
        elif press:  # tap mode
            s = sylls[self.tap_index]
            if 0 <= self.tap_prev < len(sylls):
                prev = sylls[self.tap_prev]
                same_line = self.canvas.syl_line[self.tap_prev] == self.canvas.syl_line[self.tap_index]
                old = max((prev.end or prev.start) - prev.start, 0.2)
                prev.end = t if same_line else min(prev.start + old, t)
                prev.end = max(prev.end, prev.start + MIN_DUR)
            old_dur = (s.end - s.start) if s.end is not None and s.start is not None else 0.3
            s.start, s.end, s.confidence = t, t + max(old_dur, MIN_DUR), 1.0
            self.tap_prev = self.tap_index
            self._tap_advance()
        self._fix_lines()
        self.canvas.update()

    def _tap_advance(self) -> None:
        self.tap_index += 1
        self.dirty = True
        self._update_title()
        if self.tap_index < len(self.canvas.sylls):
            self.canvas.select(self.tap_index, scroll=False)
        else:
            self.log("Tap sync: reached the last syllable")
            self.tap_mode.setCurrentIndex(0)
        self.lyrics.refresh()
        self._update_status()

    # ---- line editing -----------------------------------------------------

    def edit_line(self, row: int) -> None:
        if not self.project:
            return
        line = self.project.lines[row]
        text, ok = QInputDialog.getText(
            self, "Edit line",
            "Words separated by spaces, syllables by '|'.\n"
            "Without any '|' the line is split automatically.\n"
            "Timings are kept if the syllable count does not change."
            + (f"\n\n⚠ {line.note}" if line.note else ""),
            QLineEdit.EchoMode.Normal, to_notation(line),
        )
        if ok and text == to_notation(line) and line.note:
            self.push_undo()
            line.note = ""  # confirmed as is: dismiss the review hint
            self._after_edit()
            return
        if not ok or not text.strip() or text == to_notation(line):
            return
        self.push_undo()
        retext_line(line, text.strip(), self.project.language)
        line.note = ""
        self.canvas.refresh()
        self._after_edit()

    # ---- pipeline jobs ----------------------------------------------------

    def _run(self, title: str, fn, on_success=None) -> None:
        self.job = Job(title, fn, self)
        self.job.message.connect(self.log)
        self.job.failed.connect(lambda err: QMessageBox.critical(self, f"{title} failed", err))
        if on_success:
            self.job.succeeded.connect(on_success)
        self.job.finished.connect(self._job_finished)
        self.log(f"{title}…")
        self.job.start()
        self._set_enabled()

    def _job_finished(self) -> None:
        self.job = None
        self._set_enabled()

    def _reload(self, _result=None) -> None:
        if self.project and self.project.path:
            self.push_undo()
            undo = self.undo_stack[:]
            pos = self.player.position()
            self._set_project(Project.load(self.project.path))
            self.undo_stack = undo
            self.player.seek(pos)

    def _prepare_job(self) -> Path | None:
        if not self.project or not self.project.path:
            return None
        self.player.pause()
        if self.dirty:
            self.save()
        return self.project.path

    def new_project(self) -> None:
        if not self._maybe_save():
            return
        dlg = NewProjectDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.source.text().strip():
            return
        source = dlg.source.text().strip()
        if steps.is_url(source) and not config.download_enabled():
            QMessageBox.warning(self, "Download disabled",
                                "Downloading is disabled in this build. Choose an audio file you own.")
            return
        out = Path(dlg.out.text() or config.PROJECTS_DIR)
        artist, title, lang = dlg.artist.text() or None, dlg.title.text() or None, dlg.lang.text() or None

        def work(log):
            proj = steps.create_project(source, out, title, artist, None, lang, log=log)
            try:
                steps.fetch_lyrics(proj, log=log)
            except steps.LyricsNotFound as e:
                log(f"{e}. Use 'Lyrics' to load a file or to transcribe them from the vocals.")
            return proj.path

        self._run("Creating project", work, lambda path: self.open_project(path))

    def fetch_lyrics(self) -> None:
        path = self._prepare_job()
        if path is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Lyrics")
        box.setText("Where should the lyrics come from?\n\nThis replaces the current lyrics and timings.")
        b_lrclib = box.addButton("LRCLIB", QMessageBox.ButtonRole.AcceptRole)
        b_file = box.addButton("File (.lrc / .txt)…", QMessageBox.ButtonRole.AcceptRole)
        b_audio = box.addButton("Transcribe from vocals", QMessageBox.ButtonRole.AcceptRole)
        b_audio.setToolTip("Speech recognition of the vocal stem (better after Separate); "
                           "cleaned up by the LLM when an OpenRouter key is set")
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked == b_audio:
            self._run("Lyrics from audio",
                      lambda log: steps.lyrics_from_audio(Project.load(path), log=log), self._reload)
            return
        if clicked not in (b_lrclib, b_file):
            return
        lrc_file = None
        if clicked == b_file:
            f, _ = QFileDialog.getOpenFileName(self, "Lyrics file", "", "Lyrics (*.lrc *.txt)")
            if not f:
                return
            lrc_file = Path(f)
        self._run("Lyrics", lambda log: steps.fetch_lyrics(Project.load(path), lrc_file, log=log),
                  self._reload)

    def fix_lyrics(self) -> None:
        if not config.openrouter_key():
            QMessageBox.information(self, "Fix lyrics",
                                    "This needs an OpenRouter API key (a song costs a fraction of a cent).")
            if not self.show_settings() or not config.openrouter_key():
                return
        path = self._prepare_job()
        if path is None or not self.project.lines:
            return
        self._run("Fix lyrics", lambda log: steps.reconcile(Project.load(path), log=log),
                  self._review_proposal)

    def _review_proposal(self, proposal) -> None:
        if not proposal.changes:
            QMessageBox.information(self, "Fix lyrics", "The lyrics already match the recording.")
            return
        if ProposalDialog(proposal, self).exec() != QDialog.DialogCode.Accepted:
            return
        self.push_undo()
        steps.apply_proposal(self.project, proposal)
        self.dirty = False
        self._set_project_lines()
        self.log("Lyrics updated: run Auto-sync to time the changed lines")

    def _set_project_lines(self) -> None:
        self.canvas.refresh()
        self.lyrics.refresh()
        self.preview.update()
        self._update_title()
        self._update_status()

    def show_settings(self) -> bool:
        return SettingsDialog(self).exec() == QDialog.DialogCode.Accepted

    def separate(self) -> None:
        path = self._prepare_job()
        if path is not None:
            self._run("Vocal separation", lambda log: steps.separate(Project.load(path), log=log),
                      self._reload)

    def align(self) -> None:
        path = self._prepare_job()
        if path is None:
            return
        if any(s.confidence >= 0.95 for s in self.canvas.sylls):
            r = QMessageBox.question(self, "Auto-sync",
                                     "Auto-sync replaces all timings, including manual edits. Continue?")
            if r != QMessageBox.StandardButton.Yes:
                return
        engine = self.engine.currentData()
        self._run("Auto-sync", lambda log: steps.align(Project.load(path), engine, log=log), self._reload)

    def render(self) -> None:
        path = self._prepare_job()
        if path is None:
            return
        out, _ = QFileDialog.getSaveFileName(
            self, "Render video", str(path / f"{self.project.slug}.mkv"), "Video (*.mkv *.mp4)"
        )
        if not out:
            return
        bg = None
        if QMessageBox.question(self, "Background", "Use a background image?") == \
                QMessageBox.StandardButton.Yes:
            f, _ = QFileDialog.getOpenFileName(self, "Background image", "", "Images (*.png *.jpg *.jpeg *.webp)")
            bg = Path(f) if f else None
        self._run("Rendering",
                  lambda log: steps.render(Project.load(path), Path(out), bg, log=log),
                  lambda result: self.log(f"Video ready: {result}"))

    def export(self) -> None:
        if not self.project:
            return
        from autokaraoke.export.ass import write_ass
        from autokaraoke.export.lrc import write_lrc

        out, filt = QFileDialog.getSaveFileName(
            self, "Export", str(self.project.path / f"{self.project.slug}.ass"),
            "ASS subtitles (*.ass);;Enhanced LRC (*.lrc)",
        )
        if not out:
            return
        (write_lrc if out.endswith(".lrc") else write_ass)(self.project, Path(out))
        self.log(f"Exported {out}")

    def show_help(self) -> None:
        QMessageBox.information(self, "Keyboard shortcuts", (
            "Space — play / pause (tap key when Tap sync is on)\n"
            "Esc — stop tap sync\n"
            "← / → — select previous / next syllable\n"
            "[ / ] — set selected syllable start / end at the playhead\n"
            "Ctrl+Z / Ctrl+Shift+Z — undo / redo\n"
            "Ctrl+S — save\n\n"
            "Timeline: drag a block to move it, drag its edges to resize\n"
            "(touching neighbours follow; hold Alt to detach).\n"
            "Click empty area to seek, double-click a block to play from it.\n"
            "Wheel scrolls, Ctrl/Shift+wheel zooms.\n\n"
            "Colors: green = confident, yellow/red = check, blue = edited by you."
        ))
