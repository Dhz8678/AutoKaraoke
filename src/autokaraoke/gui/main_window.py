"""Main editor window."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from autokaraoke import config, steps
from autokaraoke.gui.dialogs import LineEditDialog, ProposalDialog, SettingsDialog
from autokaraoke.gui.jobs import Job
from autokaraoke.gui.lyrics_panel import LyricsPanel, fmt_time
from autokaraoke.gui.player import Player
from autokaraoke.gui.preview import KaraokePreview, current_line_index
from autokaraoke.gui.timeline import HINTS, Timeline, spectrogram_image
from autokaraoke.project import Project
from autokaraoke.text import edit as text_edit
from autokaraoke.text.edit import to_notation

UNDO_LIMIT = 100
MIN_DUR = 0.04
MIX_TRACK = "mix"  # track combo data for the generated karaoke mix
NUDGE = 0.01


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
        # (start, end, loop): stop or loop playback at ``end``.
        self.play_range: tuple[float, float, bool] | None = None
        # Audio analysis per file path: (peaks, spectrogram image, onsets).
        self.analysis: dict[str, tuple] = {}
        self.bg_jobs: set[Job] = set()
        self.settings = QSettings("AutoKaraoke", "AutoKaraoke")

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
        self._restore_settings()

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
        self.act_fix = self._action("Fix lyrics (experimental)…", self.fix_lyrics,
                                    tip="Experimental: compare the lyrics with what is sung (speech "
                                        "recognition + LLM). Enable it in File → Settings")
        self.act_fix.setVisible(config.experimental_llm())
        self.act_settings = self._action("Settings…", self.show_settings)
        self.act_separate = self._action("Separate", self.separate, tip="Split vocals / instrumental")
        self.act_align = self._action("Auto-sync", self.align, tip="Automatic syllable alignment")
        self.act_render = self._action("Render video…", self.render, "Ctrl+R")
        self.act_export = self._action("Export…", self.export, "Ctrl+E", "Export ASS or enhanced LRC")

        # Line actions work on the lines selected in the lyrics list (shortcuts active there).
        self.act_line_edit = self._action("Edit line…", lambda: self.edit_line(self.lyrics.currentRow()),
                                          "Return", "Edit text and syllables; new lines split the line")
        self.act_line_insert = self._action("Insert line below…", self.insert_line, "Insert")
        self.act_line_delete = self._action("Delete lines", self.delete_lines, "Delete")
        self.act_line_merge = self._action("Merge with next line", self.merge_lines, "Ctrl+J",
                                           "Join the selected lines (or the line and the next one)")
        self.act_line_backing = self._action(
            "Backing vocals on/off", self.toggle_backing, "B",
            "Backing lines (choir, yodel…) stay in the karaoke audio and are shown as backing")
        self.line_actions = [self.act_line_edit, self.act_line_insert, self.act_line_delete,
                             self.act_line_merge, self.act_line_backing]
        for a in self.line_actions:
            a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.lyrics.addAction(a)

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
        lines_menu = self.menuBar().addMenu("&Lines")
        for a in self.line_actions:
            lines_menu.addAction(a)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._action("Keyboard shortcuts", self.show_help, "F1"))

    def _build_layout(self) -> None:
        # Row 1: playback.
        transport = QWidget()
        h = QHBoxLayout(transport)
        h.setContentsMargins(6, 2, 6, 0)
        self.btn_play = QToolButton()
        self.btn_play.setText("▶")
        self.btn_play.setToolTip("Play / pause (Space)")
        self.time_label = QLabel("00:00.00")
        self.time_label.setMinimumWidth(80)
        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(5, 30)  # × 5 %
        self.speed.setValue(20)
        self.speed.setPageStep(1)
        self.speed.setFixedWidth(140)
        self.speed.setToolTip("Playback speed, 25–150 % (pitch is preserved)")
        self.speed_label = QToolButton()
        self.speed_label.setText("100%")
        self.speed_label.setToolTip("Reset to 100 %")
        self.speed_label.setMinimumWidth(48)
        self.track = QComboBox()
        self.track.setToolTip("Audio to play and to show in the timeline")
        self.view = QComboBox()
        self.view.addItem("Waveform", "wave")
        self.view.addItem("Spectrogram", "spec")
        self.view.addItem("Spectrogram + wave", "both")
        self.view.setToolTip("Timeline display. In the spectrogram, consonants (t, k, s, p…) show as "
                             "bright vertical streaks: good syllable starts")
        self.snap = QToolButton()
        self.snap.setText("Snap")
        self.snap.setCheckable(True)
        self.snap.setToolTip("Snap syllable starts to detected attacks (cyan ticks) while dragging")
        zoom_in, zoom_out = QToolButton(), QToolButton()
        zoom_in.setText("+")
        zoom_out.setText("−")
        zoom_in.clicked.connect(lambda: self.canvas.zoom(1.4))
        zoom_out.clicked.connect(lambda: self.canvas.zoom(1 / 1.4))
        for wdg in (self.btn_play, self.time_label, QLabel("Speed"), self.speed, self.speed_label,
                    QLabel("Audio"), self.track, QLabel("View"), self.view, self.snap):
            h.addWidget(wdg)
        h.addStretch()
        h.addWidget(QLabel("Zoom"))
        h.addWidget(zoom_out)
        h.addWidget(zoom_in)

        # Row 2: selected syllable and tap sync.
        inspector = QWidget()
        h = QHBoxLayout(inspector)
        h.setContentsMargins(6, 0, 6, 2)
        self.syl_label = QLabel("No syllable selected")
        self.syl_label.setMinimumWidth(200)
        self.syl_start = QDoubleSpinBox()
        self.syl_end = QDoubleSpinBox()
        for spin, tip in ((self.syl_start, "Start of the selected syllable (s)"),
                          (self.syl_end, "End of the selected syllable (s)")):
            spin.setDecimals(2)
            spin.setSingleStep(0.01)
            spin.setRange(0, 24 * 3600)
            spin.setKeyboardTracking(False)
            spin.setToolTip(tip + "; arrows step 10 ms")
        self.btn_set_start = QToolButton()
        self.btn_set_start.setText("[ Start here")
        self.btn_set_start.setToolTip("Set the start at the playhead ([)")
        self.btn_set_end = QToolButton()
        self.btn_set_end.setText("End here ]")
        self.btn_set_end.setToolTip("Set the end at the playhead (])")
        self.btn_play_syl = QToolButton()
        self.btn_play_syl.setText("▶ Syllable")
        self.btn_play_syl.setToolTip("Play the selected syllable (P)")
        self.btn_play_line = QToolButton()
        self.btn_play_line.setText("▶ Line")
        self.btn_play_line.setToolTip("Play the line of the selected syllable")
        self.btn_loop = QToolButton()
        self.btn_loop.setText("⟲ Loop line")
        self.btn_loop.setCheckable(True)
        self.btn_loop.setToolTip("Repeat the line of the selected syllable (L)")
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
        for wdg in (self.syl_label, QLabel("Start"), self.syl_start, QLabel("End"), self.syl_end,
                    self.btn_set_start, self.btn_set_end, self.btn_play_syl, self.btn_play_line,
                    self.btn_loop):
            h.addWidget(wdg)
        h.addStretch()
        for wdg in (QLabel("Tap sync"), self.tap_mode, self.latency):
            h.addWidget(wdg)

        self.hint = QLabel()
        self.hint.setStyleSheet("color: #9a9aa8; padding: 0 6px")

        lyrics_box = QWidget()
        v = QVBoxLayout(lyrics_box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        lines_bar = QToolBar()
        lines_bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for a in self.line_actions:
            lines_bar.addAction(a)
        v.addWidget(lines_bar)
        v.addWidget(self.lyrics, 1)

        self.top_split = QSplitter(Qt.Orientation.Horizontal)
        self.top_split.addWidget(lyrics_box)
        self.right_split = QSplitter(Qt.Orientation.Vertical)
        self.right_split.addWidget(self.preview)
        self.right_split.addWidget(self.log_view)
        self.right_split.setSizes([260, 100])
        self.top_split.addWidget(self.right_split)
        self.top_split.setSizes([460, 700])

        self.main_split = QSplitter(Qt.Orientation.Vertical)
        self.main_split.addWidget(self.top_split)
        bottom = QWidget()
        v = QVBoxLayout(bottom)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(transport)
        v.addWidget(inspector)
        v.addWidget(self.timeline, 1)
        v.addWidget(self.hint)
        self.main_split.addWidget(bottom)
        self.main_split.setStretchFactor(0, 1)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([420, 380])
        self.setCentralWidget(self.main_split)
        self.resize(1280, 860)

    def _connect(self) -> None:
        self.btn_play.clicked.connect(self.player.toggle)
        self.player.stateChanged.connect(lambda playing: self.btn_play.setText("⏸" if playing else "▶"))
        self.speed.valueChanged.connect(self._speed_changed)
        self.speed_label.clicked.connect(lambda: self.speed.setValue(20))
        self.track.currentIndexChanged.connect(self._load_track)
        self.view.currentIndexChanged.connect(lambda: self.canvas.set_view_mode(self.view.currentData()))
        self.snap.toggled.connect(self._snap_toggled)
        self.tap_mode.currentIndexChanged.connect(self._tap_mode_changed)
        self.canvas.seekRequested.connect(self._seek)
        self.canvas.playFromRequested.connect(self._play_from)
        self.canvas.aboutToEdit.connect(self.push_undo)
        self.canvas.edited.connect(self._after_edit)
        self.canvas.selectionChanged.connect(self._selection_changed)
        self.canvas.hintChanged.connect(self.hint.setText)
        self.canvas.contextRequested.connect(self._syllable_menu)
        self.syl_start.valueChanged.connect(lambda v: self._edge_edited("start", v))
        self.syl_end.valueChanged.connect(lambda v: self._edge_edited("end", v))
        self.btn_set_start.clicked.connect(lambda: self._set_boundary_at_playhead(True))
        self.btn_set_end.clicked.connect(lambda: self._set_boundary_at_playhead(False))
        self.btn_play_syl.clicked.connect(self.play_syllable)
        self.btn_play_line.clicked.connect(lambda: self.play_line(loop=False))
        self.btn_loop.toggled.connect(self._loop_toggled)
        self.lyrics.lineActivated.connect(self._goto_line)
        self.lyrics.editRequested.connect(self.edit_line)

    def _restore_settings(self) -> None:
        s = self.settings
        for key, split in (("split/main", self.main_split), ("split/top", self.top_split),
                           ("split/right", self.right_split)):
            if s.value(key) is not None:
                split.restoreState(s.value(key))
        self.canvas.syl_h = int(s.value("timeline/syl_h", self.canvas.syl_h))
        self.view.setCurrentIndex(max(0, self.view.findData(s.value("timeline/view", "wave"))))
        self.snap.setChecked(s.value("timeline/snap", "false") == "true")
        self.latency.setValue(int(s.value("tap/latency", 60)))
        self.hint.setText(HINTS[""])

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("split/main", self.main_split.saveState())
        s.setValue("split/top", self.top_split.saveState())
        s.setValue("split/right", self.right_split.saveState())
        s.setValue("timeline/syl_h", self.canvas.syl_h)
        s.setValue("timeline/view", self.view.currentData())
        s.setValue("timeline/snap", "true" if self.snap.isChecked() else "false")
        s.setValue("tap/latency", self.latency.value())

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
        self.player.reset()  # another song: start from the beginning, paused
        self._set_project(proj)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.log(f"Opened {proj.path}")

    def _set_project(self, proj: Project | None) -> None:
        self.project = proj
        self.dirty = False
        self.tap_mode.setCurrentIndex(0)
        self.play_range = None
        self.btn_loop.setChecked(False)
        self.analysis.clear()
        self.canvas.set_project(proj, None)
        self.preview.set_project(proj)
        self.lyrics.set_project(proj)
        self.timeline._sync_scrollbar()
        self._refresh_tracks(keep=False)
        if proj and proj.audio_vocals and proj.abspath(proj.audio_vocals).exists():
            self._analyse(str(proj.abspath(proj.audio_vocals)))  # its attacks are always shown
        self._update_title()
        self._set_enabled()
        self._update_status()
        self._update_inspector()

    def _refresh_tracks(self, keep: bool = True) -> None:
        """Fill the audio combo; the karaoke mix is offered once some line is marked as backing."""
        proj = self.project
        current = self.track.currentData() if keep else None
        self.track.blockSignals(True)
        self.track.clear()
        if proj:
            for label, rel in (("Original", proj.audio_full), ("Vocals", proj.audio_vocals),
                               ("Instrumental", proj.audio_instrumental)):
                if rel and (proj.path / rel).exists():
                    self.track.addItem(label, str(proj.path / rel))
            if proj.audio_vocals and proj.audio_instrumental and any(ln.backing for ln in proj.lines):
                self.track.addItem("Karaoke (instrumental + backing)", MIX_TRACK)
        idx = self.track.findData(current) if current is not None else -1
        self.track.setCurrentIndex(max(idx, 0))
        self.track.blockSignals(False)
        if not keep or idx < 0:
            self._load_track()

    def _analyse(self, path: str) -> None:
        """Waveform, spectrogram and attacks of an audio file, computed in the background."""
        if path in self.analysis or any(j.title == path for j in self.bg_jobs):
            self._apply_analysis()
            return
        from autokaraoke.spectrum import load_analysis

        job = Job(path, lambda log: load_analysis(path), self)
        job.succeeded.connect(lambda a, p=path: self._analysis_ready(p, a))
        job.failed.connect(lambda err: self.log(f"Audio analysis failed: {err}"))
        job.finished.connect(lambda j=job: self.bg_jobs.discard(j))
        self.bg_jobs.add(job)
        job.start()

    def _analysis_ready(self, path: str, a) -> None:
        if self.project is None or not path.startswith(str(self.project.path)):
            return  # finished after another project was opened
        self.analysis[path] = (a.peaks, spectrogram_image(a.spec), a.onsets)
        self._apply_analysis()

    def _current_audio(self) -> str | None:
        data = self.track.currentData()
        if data == MIX_TRACK and self.project:
            return str(self.project.path / "karaoke_mix.flac")
        return data

    def _apply_analysis(self) -> None:
        proj = self.project
        cur = self.analysis.get(self._current_audio() or "")
        voc = self.analysis.get(str(proj.abspath(proj.audio_vocals))) if proj and proj.audio_vocals else None
        onsets = (voc or cur or (None, None, None))[2]
        if cur:
            self.canvas.set_signal(cur[0], cur[1], onsets)
        else:
            self.canvas.set_signal(None, None, onsets)

    def _load_track(self) -> None:
        data = self.track.currentData()
        if data == MIX_TRACK:
            self._build_mix()
            return
        self.player.load(Path(data) if data else None)
        if data:
            self._analyse(data)
        else:
            self._apply_analysis()

    def _build_mix(self) -> None:
        """(Re)write the karaoke mix with the current backing lines, then play it."""
        if not self.project:
            return
        from autokaraoke.mix import karaoke_mix

        proj = copy.copy(self.project)
        proj.lines = copy.deepcopy(self.project.lines)

        def done(path) -> None:
            if path is None or self.track.currentData() != MIX_TRACK:
                return
            self.analysis.pop(str(path), None)
            self.player.load(Path(path), reload=True)
            self._analyse(str(path))

        job = Job("mix", lambda log: karaoke_mix(proj), self)
        job.succeeded.connect(done)
        job.failed.connect(lambda err: self.log(f"Karaoke mix failed: {err}"))
        job.finished.connect(lambda j=job: self.bg_jobs.discard(j))
        self.bg_jobs.add(job)
        job.start()

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
        if not self._maybe_save():
            e.ignore()
            return
        self._save_settings()
        for job in list(self.bg_jobs):
            job.wait()
        e.accept()

    def _update_title(self) -> None:
        name = f"{self.project.artist} - {self.project.title}" if self.project else "No project"
        self.setWindowTitle(f"{'*' if self.dirty else ''}{name} — AutoKaraoke")

    def _set_enabled(self) -> None:
        busy = self.job is not None and self.job.isRunning()
        has = self.project is not None
        for a in (self.act_lyrics, self.act_separate, self.act_fix, self.act_align, self.act_render,
                  self.act_export, *self.line_actions):
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
        backing = [ln.backing for ln in self.project.lines]
        self.project.lines = lines
        self._lines_changed(backing_changed=backing != [ln.backing for ln in lines])

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
        self.preview.update()
        self._update_inspector()
        self._mark_dirty()

    def _lines_changed(self, backing_changed: bool = False) -> None:
        """After lines were added, removed, split or re-flagged."""
        self.canvas.refresh()
        self._after_edit()
        if backing_changed:
            self._refresh_tracks()
            if self.track.currentData() == MIX_TRACK:
                self._build_mix()

    # ---- navigation -------------------------------------------------------

    def _tick(self) -> None:
        t = self.player.position()
        if self.play_range and self.player.is_playing():
            start, end, loop = self.play_range
            if t >= end:
                if loop:
                    self.player.seek(start)
                    t = start
                else:
                    self.player.pause()
                    self.play_range = None
        self.time_label.setText(fmt_time(t))
        self.canvas.set_playhead(t, follow=self.player.is_playing())
        self.preview.set_time(t)
        if self.project and self.player.is_playing():
            self.lyrics.highlight(current_line_index(self.project, t))

    def _seek(self, t: float) -> None:
        if self.play_range and not self.play_range[2]:
            self.play_range = None
        self.player.seek(t)

    def _play_from(self, t: float) -> None:
        self.player.seek(t)
        self.player.play()

    def _speed_changed(self, value: int) -> None:
        self.speed_label.setText(f"{value * 5}%")
        self.player.set_rate(value * 5 / 100)

    def _snap_toggled(self, on: bool) -> None:
        self.canvas.snap = on
        self.canvas.update()

    def _goto_line(self, row: int) -> None:
        if not self.project:
            return
        first = sum(len(ln.syllables()) for ln in self.project.lines[:row])
        line = self.project.lines[row]
        if line.syllables():
            self.canvas.select(first)
            span = line.span()
            if span is not None:
                self.player.seek(max(0.0, span[0] - 0.5))

    def _selection_changed(self, index: int) -> None:
        if index >= 0 and self.project:
            self.lyrics.highlight(self.canvas.syl_line[index])
        self._update_status()
        self._update_inspector()

    def _update_inspector(self) -> None:
        i = self.canvas.selected
        ok = self.project is not None and 0 <= i < len(self.canvas.sylls)
        for w in (self.syl_start, self.syl_end, self.btn_set_start, self.btn_set_end,
                  self.btn_play_syl, self.btn_play_line, self.btn_loop):
            w.setEnabled(ok)
        if not ok:
            self.syl_label.setText("No syllable selected")
            return
        s = self.canvas.sylls[i]
        line = self.canvas.syl_line[i]
        self.syl_label.setText(f"Syllable “{s.text}” · line {line + 1}")
        for spin, value in ((self.syl_start, s.start), (self.syl_end, s.end)):
            spin.blockSignals(True)
            spin.setValue(value if value is not None else 0.0)
            spin.setSpecialValueText("—" if value is None else "")
            spin.blockSignals(False)

    def _edge_edited(self, mode: str, value: float) -> None:
        i = self.canvas.selected
        if not self.project or not 0 <= i < len(self.canvas.sylls):
            return
        self.push_undo()
        self.canvas.set_edge(i, mode, value)
        self._after_edit()

    def _syllable_range(self, i: int) -> tuple[float, float] | None:
        s = self.canvas.sylls[i]
        return (s.start, s.end) if s.start is not None and s.end is not None else None

    def play_syllable(self) -> None:
        i = self.canvas.selected
        if not 0 <= i < len(self.canvas.sylls) or not (r := self._syllable_range(i)):
            return
        self.btn_loop.setChecked(False)
        self.play_range = (max(0.0, r[0] - 0.3), r[1] + 0.15, False)
        self._play_from(self.play_range[0])

    def play_line(self, loop: bool) -> None:
        i = self.canvas.selected
        if not self.project or not 0 <= i < len(self.canvas.sylls):
            return
        span = self.project.lines[self.canvas.syl_line[i]].span()
        if span is None:
            return
        self.play_range = (max(0.0, span[0] - 0.3), span[1] + 0.2, loop)
        self._play_from(self.play_range[0])

    def _loop_toggled(self, on: bool) -> None:
        if on:
            self.play_line(loop=True)
            if self.play_range is None:
                self.btn_loop.setChecked(False)
        elif self.play_range and self.play_range[2]:
            self.play_range = None
            self.player.pause()

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
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        arrows = (Qt.Key.Key_Left, Qt.Key.Key_Right)
        if e.isAutoRepeat() and not (ctrl and key in arrows):
            return key == Qt.Key.Key_Space
        if key == Qt.Key.Key_Space:
            if self.tap_mode.currentData():
                self._tap(press)
            elif press:
                self.player.toggle()
            return True
        if not press or not self.project:
            return False
        if key == Qt.Key.Key_Escape:
            if self.tap_mode.currentData():
                self.tap_mode.setCurrentIndex(0)
                return True
            if self.play_range:
                self.btn_loop.setChecked(False)
                self.play_range = None
                return True
            return False
        if isinstance(QApplication.focusWidget(), LyricsPanel):
            return False  # arrows, Enter, Delete... belong to the list there
        n = len(self.canvas.sylls)
        sel = self.canvas.selected
        if key in arrows and ctrl:
            if 0 <= sel < n:
                step = NUDGE * (5 if mods & Qt.KeyboardModifier.ShiftModifier else 1)
                self.canvas.nudge(sel, -step if key == Qt.Key.Key_Left else step)
            return True
        if key in arrows:
            if n:
                self.canvas.select(min(max(sel + (-1 if key == Qt.Key.Key_Left else 1), 0), n - 1))
            return True
        if key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight) and sel >= 0:
            self._set_boundary_at_playhead(start=key == Qt.Key.Key_BracketLeft)
            return True
        if key == Qt.Key.Key_P and not ctrl:
            self.play_syllable()
            return True
        if key == Qt.Key.Key_L and not ctrl:
            self.btn_loop.toggle()
            return True
        return False

    def _set_boundary_at_playhead(self, start: bool) -> None:
        """'[' / ']' set the selected syllable's start / end to the playhead."""
        i = self.canvas.selected
        if not 0 <= i < len(self.canvas.sylls):
            return
        self.push_undo()
        self.canvas.set_edge(i, "start" if start else "end", self.player.position())
        self._after_edit()

    def _fix_lines(self) -> None:
        for line in self.project.lines:
            if span := line.span():
                line.start, line.end = span

    def _locate(self, i: int) -> tuple[int, int, int]:
        """Flat syllable index -> (line, word, syllable) indices."""
        li = self.canvas.syl_line[i]
        k = i - self.canvas.syl_line.index(li)
        for wi, word in enumerate(self.project.lines[li].words):
            if k < len(word.syllables):
                return li, wi, k
            k -= len(word.syllables)
        raise IndexError(i)

    def _syllable_menu(self, i: int, pos) -> None:
        li, wi, si = self._locate(i)
        word = self.project.lines[li].words[wi]
        s = self.canvas.sylls[i]
        menu = QMenu(self)
        menu.addAction("Start at playhead  [", lambda: self._set_boundary_at_playhead(True))
        menu.addAction("End at playhead  ]", lambda: self._set_boundary_at_playhead(False))
        menu.addAction("Play syllable  P", self.play_syllable)
        menu.addSeparator()
        split = menu.addAction("Split syllable…", lambda: self.split_syllable(i))
        split.setEnabled(len(s.text) > 1)
        merge = menu.addAction("Merge with next syllable", lambda: self.merge_syllable(i))
        merge.setEnabled(si + 1 < len(word.syllables))
        menu.addSeparator()
        menu.addAction("Edit line…", lambda: self.edit_line(li))
        backing = menu.addAction("Backing vocals", lambda: self._set_backing([li], not self.project.lines[li].backing))
        backing.setCheckable(True)
        backing.setChecked(self.project.lines[li].backing)
        menu.exec(pos)

    def split_syllable(self, i: int) -> None:
        li, wi, si = self._locate(i)
        word = self.project.lines[li].words[wi]
        text = word.syllables[si].text
        mid = (len(text) + 1) // 2
        notation, ok = QInputDialog.getText(
            self, "Split syllable", "Put a '|' where the syllable should be split:",
            QLineEdit.EchoMode.Normal, f"{text[:mid]}|{text[mid:]}")
        if not ok or notation.count("|") != 1 or notation.replace("|", "") != text:
            return
        pos = notation.index("|")
        if not 0 < pos < len(text):
            return
        self.push_undo()
        text_edit.split_syllable(word, si, pos)
        self._lines_changed()
        self.canvas.select(i)

    def merge_syllable(self, i: int) -> None:
        li, wi, si = self._locate(i)
        word = self.project.lines[li].words[wi]
        if si + 1 >= len(word.syllables):
            return
        self.push_undo()
        text_edit.merge_syllable_with_next(word, si)
        self._lines_changed()
        self.canvas.select(i)

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
        self.play_range = None
        self.btn_loop.setChecked(False)
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
        if not self.project or not 0 <= row < len(self.project.lines):
            return
        line = self.project.lines[row]
        dlg = LineEditDialog("Edit line", to_notation(line), line.note, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = dlg.text()
        if text == to_notation(line):
            if line.note:
                self.push_undo()
                line.note = ""  # confirmed as is: dismiss the review hint
                self._after_edit()
            return
        self.push_undo()
        if not text:
            del self.project.lines[row]
        else:
            new = text_edit.split_into_lines(line, text, self.project.language)
            for ln in new:
                ln.note = ""
            self.project.lines[row : row + 1] = new
        self._lines_changed(backing_changed=line.backing)
        self.lyrics.setCurrentRow(min(row, len(self.project.lines) - 1))

    def insert_line(self) -> None:
        if not self.project:
            return
        rows = self.lyrics.selected_rows()
        row = rows[-1] if rows else len(self.project.lines) - 1
        dlg = LineEditDialog("Insert line below", "", parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.text():
            return
        lines = self.project.lines
        prev = lines[row] if 0 <= row < len(lines) else None
        nxt = lines[row + 1] if row + 1 < len(lines) else None
        # Time all the text as one line in the gap, then split it: several lines share the gap.
        joined = text_edit.new_line(" ".join(dlg.text().split()), self.project.language, prev, nxt)
        new = text_edit.split_into_lines(joined, dlg.text(), self.project.language)
        self.push_undo()
        lines[row + 1 : row + 1] = new
        self._lines_changed()
        self.lyrics.setCurrentRow(row + 1)
        if not new[0].span():
            self.log("The new line has no timing yet: select it and use Tap sync, [ / ], or Auto-sync")

    def delete_lines(self) -> None:
        rows = self.lyrics.selected_rows()
        if not self.project or not rows:
            return
        self.push_undo()
        backing = any(self.project.lines[r].backing for r in rows)
        for r in reversed(rows):
            del self.project.lines[r]
        self.canvas.selected = -1
        self._lines_changed(backing_changed=backing)
        self.lyrics.setCurrentRow(min(rows[0], len(self.project.lines) - 1))

    def merge_lines(self) -> None:
        rows = self.lyrics.selected_rows()
        if not self.project or not rows:
            return
        first, last = rows[0], rows[-1] if len(rows) > 1 else rows[0] + 1
        if last >= len(self.project.lines):
            return
        self.push_undo()
        merged = self.project.lines[first]
        for ln in self.project.lines[first + 1 : last + 1]:
            merged = text_edit.merge_lines(merged, ln)
        self.project.lines[first : last + 1] = [merged]
        self._lines_changed(backing_changed=True)
        self.lyrics.setCurrentRow(first)

    def toggle_backing(self) -> None:
        rows = self.lyrics.selected_rows()
        if self.project and rows:
            self._set_backing(rows, not all(self.project.lines[r].backing for r in rows))

    def _set_backing(self, rows: list[int], on: bool) -> None:
        self.push_undo()
        for r in rows:
            self.project.lines[r].backing = on
        self._lines_changed(backing_changed=True)
        if on:
            self.log("Backing lines stay in the karaoke audio: choose “Karaoke (instrumental + backing)” "
                     "under Audio to hear it")

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
            # The stems may have been rewritten (Separate): re-read the file, keeping
            # position and play state.
            data = self.track.currentData()
            if data and data != MIX_TRACK:
                self.player.load(Path(data), reload=True)
            self.player.seek(pos)

    def _prepare_job(self) -> Path | None:
        if not self.project or not self.project.path:
            return None
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
                           "check the result line by line")
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
        if not config.experimental_llm():
            return
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
        self._refresh_tracks()

    def show_settings(self) -> bool:
        ok = SettingsDialog(self).exec() == QDialog.DialogCode.Accepted
        self.act_fix.setVisible(config.experimental_llm())
        return ok

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
        if not self.project or not self.project.path:
            return
        from autokaraoke.export.ass import project_style
        from autokaraoke.gui.render_dialog import RenderDialog

        dlg = RenderDialog(self.project, project_style(self.project),
                           self.project.path / f"{self.project.slug}.mkv", self)
        result = dlg.exec()
        if result == QDialog.DialogCode.Rejected:
            return
        style = dlg.style()
        if style.to_dict() != project_style(self.project).to_dict():
            self.project.render = style.to_dict()
            self._mark_dirty()
        self.preview.set_style(style)
        if result != QDialog.DialogCode.Accepted:  # "Save style" only
            return
        out = dlg.output_path()
        path = self._prepare_job()
        if path is None or not str(out):
            return
        self._run("Rendering",
                  lambda log: steps.render(Project.load(path), out, log=log, style=style),
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
            "Fixing a syllable by hand\n"
            "  1. Click it in the timeline (or go to it with ← / →).\n"
            "  2. P plays it, L loops its line (slow down with Speed if needed).\n"
            "  3. Drag the block or its edges, or press [ / ] at the right moment while it\n"
            "     plays, or type the times in Start / End. Ctrl+← / → nudges it by 10 ms\n"
            "     (Shift: 50 ms).\n"
            "  The Spectrogram view shows consonants as bright vertical streaks; Snap makes\n"
            "  dragged starts stick to the detected attacks (cyan ticks).\n\n"
            "Keys\n"
            "  Space — play / pause (tap key when Tap sync is on)\n"
            "  Esc — stop tap sync / loop\n"
            "  ← / → — select previous / next syllable\n"
            "  [ / ] — set selected syllable start / end at the playhead\n"
            "  P / L — play syllable / loop line\n"
            "  Ctrl+← / → — nudge syllable (Shift: more)\n"
            "  Ctrl+Z / Ctrl+Shift+Z — undo / redo, Ctrl+S — save\n\n"
            "Lyrics list (multiple selection with Ctrl / Shift)\n"
            "  Enter — edit (new lines split it) · Insert — add a line below\n"
            "  Delete — delete · Ctrl+J — merge · B — backing vocals on / off\n\n"
            "Timeline: drag a block to move it, drag its edges to resize\n"
            "(touching neighbours follow; hold Alt to detach). Drag the line between the\n"
            "audio and the syllables to resize the syllable lane. Right-click a block for more.\n"
            "Click empty area to seek, double-click a block to play from it.\n"
            "Wheel scrolls, Ctrl/Shift+wheel zooms.\n\n"
            "Colors: green = confident, yellow/red = check, blue = edited by you.\n"
            "Blue lines in the list are backing vocals (kept in the karaoke audio)."
        ))
