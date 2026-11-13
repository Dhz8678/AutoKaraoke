"""Offline acceptance: a real uv wheel download from a throttled loopback server.

Run with the installed Python and pass --uv <bundled-uv.exe>. No AI downloads,
external service requests, or changes to the user's application environment.
"""

import argparse
import codecs
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from autokaraoke.component_progress import UvProgress
from autokaraoke.terminal import TerminalProcess


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--uv', required=True)
    options = args.parse_args()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as wheel:
        wheel.writestr('progress_fixture/__init__.py', '')
        wheel.writestr('progress_fixture/payload.dat', b'x' * (3 * 1024 * 1024))
        wheel.writestr('progress_fixture-1.0.dist-info/METADATA',
                       'Metadata-Version: 2.1\nName: progress-fixture\nVersion: 1.0\n')
        wheel.writestr('progress_fixture-1.0.dist-info/WHEEL',
                       'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        wheel.writestr('progress_fixture-1.0.dist-info/RECORD', '')
    data = buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, body):
            start, end = 0, len(data) - 1
            requested = self.headers.get('Range', '')
            if requested.startswith('bytes='):
                first, last = requested[6:].split('-', 1)
                if first:
                    start = int(first)
                    end = int(last) if last else end
                else:
                    start = len(data) - int(last)
            self.send_response(206 if requested else 200)
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            if requested:
                self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
            self.end_headers()
            if body:
                try:
                    for position in range(start, end + 1, 16384):
                        self.wfile.write(data[position:min(end + 1, position + 16384)])
                        self.wfile.flush()
                        time.sleep(.02)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_HEAD(self):
            self.reply(False)

        def do_GET(self):
            self.reply(True)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    output = Path(tempfile.mkdtemp(prefix='autokaraoke-progress-acceptance-'))
    environment = output / 'venv'
    subprocess.run([options.uv, 'venv', '--python', sys.executable, str(environment)],
                   check=True, capture_output=True)
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    command = [options.uv, 'pip', 'install', '--python', str(python), '--no-cache', '--no-deps',
               f'http://127.0.0.1:{server.server_port}/progress_fixture-1.0-py3-none-any.whl']
    reporter, states, raw = UvProgress(), [], []
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    try:
        with TerminalProcess(command) as process:
            for chunk in process.output():
                raw.append(chunk)
                state, _ = reporter.feed(decoder.decode(chunk))
                states.append(state)
            code = process.wait()
    finally:
        server.shutdown()
        server.server_close()
    partial = [state for state in states if 0 < state.downloaded < state.size]
    summary = {'exit_code': code, 'frames': len(states), 'partial_frames': len(partial),
               'samples': [asdict(state) for state in partial[::max(1, len(partial)//5)]],
               'final_log': reporter.log_lines()[-8:]}
    (output / 'capture.bin').write_bytes(b''.join(raw))
    (output / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('Artifacts:', output)
    assert code == 0 and partial, 'No real partial-download progress captured'


if __name__ == '__main__':
    main()
