import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autokaraoke.export.ass import AssStyle, build_ass, project_style, syllable_starts
from autokaraoke.fonts import TextMeasure, font_file, font_index
from autokaraoke.project import Project
from autokaraoke.text.lyrics import make_line


class TypographyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtWidgets import QApplication
        from autokaraoke.gui.app import install_chinese_translation

        cls.app = QApplication.instance() or QApplication([])
        install_chinese_translation(cls.app)

    @unittest.skipUnless(os.name == 'nt', 'Windows font resolver')
    def test_windows_font_measure_uses_real_chinese_font(self):
        from PySide6.QtGui import QFont, QFontMetricsF

        path = font_file('Microsoft YaHei')
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).is_file())
        measure = TextMeasure('Microsoft YaHei', 72, False)
        self.assertIsNotNone(measure._font)
        font = QFont('Microsoft YaHei')
        font.setPixelSize(round(measure.em))
        actual = QFontMetricsF(font).horizontalAdvance('中文歌词测试')
        self.assertLess(abs(measure.width('中文歌词测试') - actual), 5)
        self.assertGreater(measure.width('中'), 50)

    @unittest.skipUnless(os.name == 'nt', 'Windows TTC font collection')
    def test_ui_font_selects_its_own_collection_face(self):
        path = font_file('Microsoft YaHei UI')
        self.assertNotEqual(font_index(path, 'Microsoft YaHei'), font_index(path, 'Microsoft YaHei UI'))

    def test_spacing_changes_width_and_syllable_ball_positions_together(self):
        zero = TextMeasure('Microsoft YaHei', 72, False, 0)
        spaced = TextMeasure('Microsoft YaHei', 72, False, 4)
        text = '中文歌词'
        self.assertAlmostEqual(spaced.width(text) - zero.width(text), 12)
        line = make_line(text, 'zh')
        xs, _, _ = syllable_starts(line, spaced)
        self.assertAlmostEqual(xs[1] - xs[0], spaced.width('中') + 4)

    def test_ass_and_layout_preserve_spacing_and_baseline_distance(self):
        style = AssStyle(font='Microsoft YaHei', spacing=5.5, line_spacing=2.0, ball=False)
        line = make_line('中文歌词测试', 'zh', 1, 5)
        for index, syllable in enumerate(line.syllables()):
            syllable.start, syllable.end = 1 + index * .5, 1.5 + index * .5
        project = Project(language='zh', lines=[line])
        ass = build_ass(project, style)
        for name in ('Karaoke', 'Backing', 'Next', 'Title'):
            record = next(row for row in ass.splitlines() if row.startswith(f'Style: {name},'))
            columns = next(row for row in ass.splitlines() if row.startswith('Format: Name,')).removeprefix('Format: ').split(', ')
            values = dict(zip(columns, record.removeprefix('Style: ').split(',')))
            self.assertEqual(float(values['Spacing']), 5.5)
        self.assertEqual(style.lower_y - style.upper_y, style.size * 2)

    def test_chinese_profile_respects_saved_user_overrides(self):
        from autokaraoke import config

        project = Project(language='zh')
        with tempfile.TemporaryDirectory() as directory, patch.object(config, 'CONFIG_DIR', Path(directory)):
            style = project_style(project)
            self.assertFalse(style.bold)
            self.assertGreater(style.spacing, 0)
            self.assertLessEqual(style.outline, 2)
            project.render = {'font': 'custom-font', 'spacing': 8.0, 'line_spacing': 2.2}
            style = project_style(project)
            self.assertEqual((style.font, style.spacing, style.line_spacing), ('custom-font', 8, 2.2))

    def test_render_dialog_preview_and_saved_style_share_controls(self):
        from autokaraoke.gui.render_dialog import RenderDialog

        dialog = RenderDialog(Project(language='zh'), AssStyle(), Path('test.mkv'))
        dialog.btn_chinese.click()
        dialog.spacing.setValue(6.5)
        dialog.line_spacing.setValue(2.1)
        self.assertEqual(dialog.style().spacing, 6.5)
        self.assertEqual(dialog.preview._measure.spacing, 6.5)
        self.assertEqual(dialog.preview.style_.line_spacing, 2.1)
        self.assertEqual(AssStyle.from_dict(dialog.style().to_dict()).spacing, 6.5)
        dialog.close()


if __name__ == '__main__':
    unittest.main()
