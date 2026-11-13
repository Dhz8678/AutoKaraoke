"""Exercise unsaved API tests, waiting/cancel states, model progress and Chinese text."""

import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from autokaraoke import components, config, llm, steps
from autokaraoke.component_progress import InstallProgress
from autokaraoke.project import Project
from autokaraoke.task_progress import TaskCancelled, model_download_progress
from autokaraoke.text.chinese import script_language, simplify_project, to_simplified
from autokaraoke.text.lyrics import make_line
from autokaraoke.text.syllabify import detect_language


class ConnectionTests(unittest.TestCase):
    def test_unsaved_configuration_used_without_mutating_environment(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={'model': 'unsaved-model', 'choices': [
                {'message': {'content': '{"ok":true}'}}]})

        saved = {'AUTOKARAOKE_LLM_BASE_URL': 'https://openrouter.ai/api/v1',
                 'AUTOKARAOKE_LLM_API_KEY': 'saved-secret'}
        with patch.dict(os.environ, saved), httpx.Client(transport=httpx.MockTransport(respond)) as client, \
                patch.object(llm.httpx, 'post', side_effect=client.post):
            result = llm.test_connection('http://127.0.0.1:8000/v1', 'unsaved-secret', 'unsaved-model')
            self.assertEqual(os.environ['AUTOKARAOKE_LLM_API_KEY'], 'saved-secret')
        self.assertTrue(result.success)
        self.assertEqual(str(requests[0].url), 'http://127.0.0.1:8000/v1/chat/completions')
        self.assertEqual(requests[0].headers['Authorization'], 'Bearer unsaved-secret')
        self.assertEqual(json.loads(requests[0].content)['model'], 'unsaved-model')
        self.assertNotIn('reasoning', json.loads(requests[0].content))

    def test_failure_redacts_credentials_and_does_not_retry(self):
        response = httpx.Response(401, text='invalid key example-secret')
        with patch.object(llm.httpx, 'post', return_value=response) as post, patch.object(llm.time, 'sleep') as sleep:
            result = llm.test_connection('http://localhost:8000/v1', 'example-secret', 'test-model')
        self.assertFalse(result.success)
        self.assertIn('401', result.message)
        self.assertNotIn('example-secret', result.message)
        post.assert_called_once()
        sleep.assert_not_called()

    def test_timeout_explains_failure(self):
        with patch.object(llm.httpx, 'post', side_effect=httpx.ReadTimeout('')):
            result = llm.test_connection('http://localhost:8000/v1', '', 'test-model')
        self.assertFalse(result.success)
        self.assertIn('超时', result.message)

    def test_non_json_response_is_not_reported_as_usable(self):
        response = httpx.Response(200, json={'choices': [{'message': {'content': 'hello'}}]})
        with patch.object(llm.httpx, 'post', return_value=response):
            result = llm.test_connection('http://localhost:8000/v1', '', 'test-model')
        self.assertFalse(result.success)
        self.assertIn('JSON', result.message)


class ChineseTests(unittest.TestCase):
    def test_han_only_lyrics_are_not_misidentified_as_korean(self):
        self.assertEqual(detect_language('繁體中文歌詞測試 這是中文文字'), 'zh')
        self.assertEqual(detect_language('简体中文歌词测试 这是中文文字'), 'zh')
        self.assertEqual(script_language('さくらの花びら'), 'ja')
        self.assertEqual(script_language('사랑하는 노래'), 'ko')

    @unittest.skipUnless(os.name == 'nt', 'Windows-native Chinese conversion')
    def test_conversion_preserves_lrc_tags_numbers_and_emoji(self):
        self.assertEqual(to_simplified('[00:12.30]繁體中文 歌詞 測試 123% 🎵'),
                         '[00:12.30]繁体中文 歌词 测试 123% 🎵')

    @unittest.skipUnless(os.name == 'nt', 'Windows-native Chinese conversion')
    def test_project_conversion_preserves_every_timing_and_backing_flag(self):
        project = Project(language='ko', lines=[make_line('繁體中文 歌詞測試', 'ko', 1, 4)])
        project.lines[0].backing = True
        for index, syllable in enumerate(project.lines[0].syllables()):
            syllable.start, syllable.end, syllable.confidence = index + 1, index + 1.5, .8
        before = [(s.start, s.end, s.confidence) for s in project.lines[0].syllables()]
        self.assertEqual(simplify_project(project), 1)
        self.assertEqual(project.language, 'zh')
        self.assertEqual(project.lines[0].text, '繁体中文 歌词测试')
        self.assertEqual(before, [(s.start, s.end, s.confidence) for s in project.lines[0].syllables()])
        self.assertTrue(project.lines[0].backing)

    @unittest.skipUnless(os.name == 'nt', 'Windows-native Chinese conversion')
    def test_local_lrc_fetch_uses_simplified_and_can_preserve_original(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            lrc = folder / 'test.lrc'
            lrc.write_text('[00:01.00]繁體中文 歌詞測試\n[00:03.00]這是中文文字', encoding='utf-8')
            project = Project(language='ko', duration=5, path=folder)
            with patch.dict(os.environ, {'AUTOKARAOKE_SIMPLIFIED_CHINESE': '1'}):
                steps.fetch_lyrics(project, lrc)
            self.assertEqual(project.language, 'zh')
            self.assertEqual(project.lines[0].text, '繁体中文 歌词测试')
            self.assertEqual([line.start for line in project.lines], [1, 3])
            with patch.dict(os.environ, {'AUTOKARAOKE_SIMPLIFIED_CHINESE': '0'}):
                steps.fetch_lyrics(project, lrc)
            self.assertEqual(project.lines[0].text, '繁體中文 歌詞測試')


class DownloadTests(unittest.TestCase):
    def test_cancelled_model_file_is_not_left_as_a_complete_cached_model(self):
        from autokaraoke.separation import _download_model_file

        def partial_download(url, path):
            Path(path).write_bytes(b'incomplete model')
            raise TaskCancelled()

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'model.ckpt'
            with self.assertRaises(TaskCancelled):
                _download_model_file('https://example.com/model.ckpt', target, partial_download)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_environment_lock_wait_has_reason_and_resumes(self):
        states, messages = [], []
        with patch.object(components, '_environment_busy', side_effect=[True, True, False]), \
                patch.object(components.time, 'sleep'):
            components._wait_for_environment(messages.append, states.append)
        self.assertEqual(states[0].stage, 'waiting')
        self.assertIn('其他安装任务', states[0].detail)
        self.assertEqual(states[-1].stage, 'preparing')
        self.assertIn('锁已释放', messages[-1])

    def test_environment_lock_timeout_does_not_launch_another_installer(self):
        with patch.object(components, '_environment_busy', return_value=True), \
                self.assertRaises(RuntimeError):
            components._wait_for_environment(lambda _: None, lambda _: None, timeout=0)

    def test_hf_and_separator_byte_counters_and_processing_iterations(self):
        from tqdm import tqdm
        from huggingface_hub.utils.tqdm import tqdm as hf_tqdm

        original = tqdm.update
        states = []
        with model_download_progress(states.append, lambda: False):
            with hf_tqdm(total=100, unit='B', desc='weights.safetensors', disable=True) as bar:
                bar.update(40)
                time.sleep(.11)
                bar.update(20)
            with tqdm(total=200, unit='iB', disable=True) as bar:
                bar.update(100)
            for _ in tqdm(range(4), disable=True):
                time.sleep(.04)
        self.assertIs(tqdm.update, original)
        self.assertTrue(any(s.stage == 'model' and s.downloaded == 60 and s.size == 100 for s in states))
        self.assertTrue(any(s.stage == 'model' and s.downloaded == 100 and s.size == 200 for s in states))
        self.assertTrue(any(s.stage == 'processing' and s.completed == 4 and s.total == 4 for s in states))

    def test_model_download_cancellation_restores_library_methods(self):
        from tqdm import tqdm

        original = tqdm.update
        cancelled = False
        with self.assertRaises(TaskCancelled):
            with model_download_progress(lambda _: None, lambda: cancelled):
                with tqdm(total=100, unit='B') as bar:
                    cancelled = True
                    bar.update(1)
        self.assertIs(tqdm.update, original)

    def test_silent_child_emits_heartbeat_before_output(self):
        from autokaraoke.terminal import TerminalProcess

        with TerminalProcess([sys.executable, '-u', '-c', "import time; time.sleep(.4); print('DONE')"]) as process:
            output = list(process.output(heartbeat=.1))
            self.assertEqual(process.wait(), 0)
        self.assertIn(b'', output)
        self.assertIn(b'DONE', b''.join(output))


class FollowupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtWidgets import QApplication
        from autokaraoke.gui.app import install_chinese_translation

        cls.app = QApplication.instance() or QApplication([])
        install_chinese_translation(cls.app)

    def pump(self, condition, timeout=5):
        end = time.monotonic() + timeout
        while not condition() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition())

    def test_test_button_uses_unsaved_fields_does_not_save_and_keeps_ui_responsive(self):
        from autokaraoke.gui.dialogs import SettingsDialog

        def verify(base, key, model):
            self.assertEqual((base, key, model), ('http://localhost:8000/v1', 'test-secret', 'unsaved-model'))
            time.sleep(.05)
            return llm.ConnectionTest(True, .05, '测试成功')

        with patch('autokaraoke.llm.test_connection', side_effect=verify), \
                patch('autokaraoke.gui.dialogs.save_settings') as save:
            dialog = SettingsDialog()
            dialog.experimental.setChecked(True)
            dialog.api_url.setText('http://localhost:8000/v1')
            dialog.key.setText('test-secret')
            dialog.model.setText('unsaved-model')
            dialog.test_button.click()
            self.assertIsNotNone(dialog.test_job)
            self.assertFalse(dialog.test_button.isEnabled())
            self.pump(lambda: dialog.test_job is None)
            self.assertIn('测试成功', dialog.test_status.text())
            self.assertIn('用时', dialog.test_status.text())
            self.assertTrue(dialog.test_button.isEnabled())
            save.assert_not_called()
            dialog.close()

    def test_model_progress_and_cancel_restore_action_buttons(self):
        from autokaraoke.gui.main_window import MainWindow

        def work(log):
            from tqdm import tqdm
            with tqdm(total=1000, unit='B', desc='test-model.bin') as bar:
                for _ in range(100):
                    bar.update(10)
                    time.sleep(.01)

        with patch.object(MainWindow, '_restore_settings'), patch.object(MainWindow, '_save_settings'):
            window = MainWindow()
            window._set_project(Project(lines=[make_line('test lyric', 'en')]))
            window._run('人声分离', work)
            self.assertFalse(window.act_separate.isEnabled())
            self.pump(lambda: window.download_progress.file.value() > 0)
            self.assertIn('test-model.bin', window.download_progress.file_label.text())
            window.download_progress.cancel_button.click()
            self.pump(lambda: window.job is None)
            self.assertTrue(window.act_separate.isEnabled())
            self.assertIn('已取消', window.download_progress.summary.text())
            window.close()


if __name__ == '__main__':
    unittest.main()
