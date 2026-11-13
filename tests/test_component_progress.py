"""Progress must reflect real bytes/counts and survive terminal redraws/errors."""

import codecs
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from autokaraoke.component_progress import InstallProgress, TerminalScreen, UvProgress
from autokaraoke import components
from autokaraoke.terminal import TerminalProcess


class ProgressTests(unittest.TestCase):
    def test_redraw_changes_only_digits_and_split_escape_sequences(self):
        reporter = UvProgress()
        state, _ = reporter.feed("Preparing packages... (1/3)\r\ntorch ---- 100.0 MiB/2.0 GiB", now=1)
        self.assertEqual((state.completed, state.total), (1, 3))
        self.assertEqual(state.downloaded, 100 * 1024**2)
        # ConPTY sends cursor positioning + changed characters, not a fresh line.
        reporter.feed("\x1b[2;12", now=2)
        state, _ = reporter.feed("H200.0", now=3)
        self.assertEqual(state.package, "torch")
        self.assertEqual(state.downloaded, 200 * 1024**2)
        self.assertGreater(state.speed, 0)

    def test_install_phase_uses_its_own_package_counter(self):
        reporter = UvProgress()
        reporter.feed("Preparing packages... (4/5)")
        state, _ = reporter.feed("\r\x1b[2K--- [2/8] Installing wheels...")
        self.assertEqual((state.stage, state.completed, state.total), ("installing", 2, 8))

    def test_largest_active_download_gets_the_file_bar(self):
        reporter = UvProgress()
        state, messages = reporter.feed("torch ---- 1.0 GiB/2.0 GiB\r\nscipy ---- 2.0 MiB/35.0 MiB")
        self.assertEqual(state.package, "torch")
        self.assertEqual(state.downloaded / state.size, 0.5)
        self.assertEqual(len(messages), 2)
        self.assertEqual(reporter.feed("\r", now=2)[1], [])

    def test_unknown_size_does_not_invent_a_percentage(self):
        state, _ = UvProgress().feed("Resolving dependencies...")
        self.assertEqual((state.total, state.size), (0, 0))

    def test_console_title_and_colour_are_not_visible_text(self):
        screen = TerminalScreen()
        screen.feed("\x1b]0;window title\x07\x1b[31mhello\x1b[0m")
        self.assertEqual(screen.rows[0], "hello")

    def test_failure_does_not_create_ready_marker_or_report_complete(self):
        states = []
        with patch.object(components, 'packaged', return_value=True), \
                patch.object(components, 'torch_variant', return_value='cpu'), \
                patch.object(components, 'install_command', return_value=['uv']), \
                patch.object(components, '_run_installer', return_value=7), \
                patch.object(components, '_marker') as marker:
            with self.assertRaises(RuntimeError):
                components.install(lambda _: None, states.append)
        marker.assert_not_called()
        self.assertEqual(states[-1].stage, 'failed')
        self.assertFalse(any(s.stage == 'complete' for s in states))

    def test_success_records_marker_then_reports_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'ready'
            states = []
            with patch.object(components, 'packaged', return_value=True), \
                    patch.object(components, 'torch_variant', return_value='cpu'), \
                    patch.object(components, 'install_command', return_value=['uv']), \
                    patch.object(components, '_run_installer', return_value=0), \
                    patch.object(components, '_marker', return_value=marker), \
                    patch.dict(os.environ, {'AUTOKARAOKE_LOCK_HASH': 'test-lock'}):
                components.install(lambda _: None, states.append)
            self.assertEqual(marker.read_text(), 'test-lock')
            self.assertEqual(states[-1].stage, 'complete')

    def test_fallback_preserves_error_output_and_exit_code(self):
        messages, states = [], []
        with patch('autokaraoke.terminal.TerminalProcess', side_effect=OSError('no console')):
            code = components._run_installer(
                [sys.executable, '-c', "import sys; print('download error'); sys.exit(6)"],
                messages.append, states.append)
        self.assertEqual(code, 6)
        self.assertIn('download error', messages)
        self.assertTrue(all(s.size == 0 for s in states))

    def test_hidden_terminal_captures_real_tty_and_large_output(self):
        command = [sys.executable, '-u', '-c',
                   "import sys; print('TTY='+str(sys.stderr.isatty())); "
                   "print('X'*100000); print('END-OF-OUTPUT')"]
        with TerminalProcess(command) as process:
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            output = ''.join(decoder.decode(data) for data in process.output())
            code = process.wait()
        self.assertEqual(code, 0)
        self.assertIn('TTY=True', output)
        self.assertIn('END-OF-OUTPUT', output)
        # ConPTY coalesces rapid screen updates; the final marker proves that
        # writing beyond the pipe buffer completed without a shutdown deadlock.


class ChineseUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtWidgets import QApplication
        from autokaraoke.gui.app import install_chinese_translation

        cls.app = QApplication.instance() or QApplication([])
        install_chinese_translation(cls.app)

    def test_native_standard_buttons_are_chinese(self):
        from PySide6.QtWidgets import QDialogButtonBox

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        self.assertIn('保存', buttons.button(QDialogButtonBox.StandardButton.Save).text())
        self.assertIn('取消', buttons.button(QDialogButtonBox.StandardButton.Cancel).text())

    def test_chinese_text_has_real_font_glyphs(self):
        from PySide6.QtGui import QRawFont

        font = QRawFont.fromFont(self.app.font())
        self.assertTrue(font.isValid())
        for character in '设置歌词下载进度123%':
            self.assertTrue(font.supportsCharacter(ord(character)), character)

    def test_file_progress_and_failure_are_visible_and_distinct(self):
        from autokaraoke.gui.download_progress import DownloadProgress

        widget = DownloadProgress()
        widget.update_progress(InstallProgress('preparing', 3, 5, 'torch', 1024**3, 2*1024**3))
        self.assertEqual(widget.overall.value(), 3)
        self.assertEqual(widget.file.value(), 500)
        self.assertIn('torch', widget.file_label.text())
        self.assertIn('组件', widget.summary.text())
        widget.update_progress(InstallProgress(stage='failed', detail='测试下载失败'))
        self.assertEqual(widget.overall.value(), 0)
        self.assertIn('失败', widget.summary.text())
        self.assertTrue(widget.file.isHidden())
        widget.close()

    def test_playback_controls_use_real_icons_instead_of_font_symbols(self):
        from autokaraoke.gui.main_window import MainWindow

        with patch.object(MainWindow, '_restore_settings'), patch.object(MainWindow, '_save_settings'):
            window = MainWindow()
            for button in (window.btn_play, window.btn_play_syl, window.btn_play_line, window.btn_loop):
                self.assertFalse(button.icon().isNull())
                self.assertFalse(any(char in button.text() for char in '▶⏸⟲'))
            window.player.stateChanged.emit(True)
            self.assertFalse(window.btn_play.icon().isNull())
            window.close()

    def test_settings_explain_cloud_model_and_preserve_custom_input(self):
        from PySide6.QtWidgets import QLabel
        from autokaraoke.gui.dialogs import SettingsDialog

        dialog = SettingsDialog()
        self.assertEqual(dialog.windowTitle(), '设置')
        labels = ' '.join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn('API 基础地址', labels)
        self.assertIn('本地音频 AI 模型', labels)
        dialog.model.setText('provider/custom-model')
        self.assertEqual(dialog.model.text(), 'provider/custom-model')
        dialog.close()


if __name__ == '__main__':
    unittest.main()
