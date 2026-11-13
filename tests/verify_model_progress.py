"""Exercise the real separator downloader and Qt worker through loopback only."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['NO_PROXY'] = '127.0.0.1,localhost'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from autokaraoke.gui.app import install_chinese_translation
from autokaraoke.gui.dialogs import SettingsDialog
from autokaraoke.gui.main_window import MainWindow
from autokaraoke.project import Project
from autokaraoke.separation import _download_model_file


def main():
    from audio_separator.separator import Separator
    from tqdm import tqdm

    payload = b'x' * (4 * 1024 * 1024)
    api_calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                for position in range(0, len(payload), 16384):
                    self.wfile.write(payload[position:position + 16384])
                    self.wfile.flush()
                    time.sleep(.005)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            api_calls.append({'path': self.path, 'model': body['model']})
            response = json.dumps({'model': body['model'], 'choices': [
                {'message': {'content': '{"ok":true}'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    output = Path(tempfile.mkdtemp(prefix='autokaraoke-model-gui-acceptance-'))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output / 'settings'))
    app = QApplication([])
    app.setStyle('Fusion')
    install_chinese_translation(app)
    images = Path(__file__).resolve().parents[1] / 'docs/images'

    def wait_for(condition, timeout=35):
        until = time.monotonic() + timeout
        while not condition() and time.monotonic() < until:
            app.processEvents()
            time.sleep(.005)
        assert condition(), 'GUI task timed out'

    settings = SettingsDialog()
    settings.experimental.setChecked(True)
    settings.api_url.setText(f'http://127.0.0.1:{server.server_port}/v1')
    settings.key.setText('local-test-key')
    settings.model.setText('local-test-model')
    settings.show()
    settings.test_button.click()
    wait_for(lambda: settings.test_job is None)
    assert '测试成功' in settings.test_status.text()
    settings.grab().save(str(images / 'zh-settings-test-success.png'))
    settings.close()

    window = MainWindow()
    window._set_project(Project(title='本机进度验证'))
    window.show()

    def work(log):
        downloader = Separator.__new__(Separator)
        downloader.logger = logging.getLogger('autokaraoke-model-progress-test')
        log('本机真实下载验证：使用测试文件，不是当前歌曲模型。')
        target = output / 'model-progress-test.ckpt'
        _download_model_file(f'http://127.0.0.1:{server.server_port}/test.ckpt', target,
                             downloader.download_file_if_not_exists)
        assert target.stat().st_size == len(payload)
        log('下载结束，验证与人声分离器相同的处理步骤计数。')
        for _ in tqdm(range(12), desc='音频处理步骤验证'):
            time.sleep(.06)

    download_frames = processing_frames = 0
    captured_download = captured_processing = False
    window._run('人声分离（本机验证）', work)
    end = time.monotonic() + 35
    try:
        while window.job is not None and time.monotonic() < end:
            app.processEvents()
            progress = window.download_progress
            if progress.file.isVisible() and 0 < progress.file.value() < 1000:
                download_frames += 1
                if not captured_download and progress.file.value() > 200:
                    window.grab().save(str(images / 'zh-model-download-progress.png'))
                    captured_download = True
            if progress.overall.maximum() == 12 and 0 < progress.overall.value() < 12:
                processing_frames += 1
                if not captured_processing:
                    window.grab().save(str(images / 'zh-separation-progress.png'))
                    captured_processing = True
            time.sleep(.005)
        assert window.job is None and download_frames and processing_frames
        assert window.act_separate.isEnabled()
        summary = {'api_test_button': 'passed', 'api_requests': api_calls,
                   'real_separator_download_partial_frames': download_frames,
                   'processing_partial_frames': processing_frames,
                   'action_buttons_restored': True, 'external_provider_called': False,
                   'real_song_inference': False}
        (output / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print('Acceptance artifacts:', output)
    finally:
        window.close()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
