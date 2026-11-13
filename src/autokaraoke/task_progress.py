"""Forward model-library byte progress and cooperative cancellation to the GUI."""

from contextlib import contextmanager
from contextvars import ContextVar
import importlib
import io
import threading
import time

from autokaraoke.component_progress import InstallProgress, format_bytes


class TaskCancelled(Exception):
    pass


_download_name = ContextVar("autokaraoke_download_name", default="模型文件")


@contextmanager
def named_download(name: str):
    token = _download_name.set(name)
    try:
        yield
    finally:
        _download_name.reset(token)


@contextmanager
def model_download_progress(report, cancelled):
    try:
        # Instrument the base class: HF, audio-separator and its processing loops
        # inherit it, even when they imported their classes before the task began.
        bar_class = importlib.import_module("tqdm.std").tqdm
    except ImportError:
        yield
        return
    original_init, original_update, original_close = bar_class.__init__, bar_class.update, bar_class.close
    bars = {}
    lock = threading.RLock()

    def emit(info, force=False):
        if cancelled():
            raise TaskCancelled()
        now = time.monotonic()
        if not force and now - info['last'] < .1:
            return
        info['last'] = now
        active = [bar for bar in bars.values() if bar['bytes'] == info['bytes']
                  and (not bar['size'] or bar['n'] < bar['size'])]
        current = max(active, key=lambda bar: bar['size'], default=info)
        speed = max(0, current['n'] - current['initial']) / max(.1, now - current['start'])
        if current['bytes']:
            detail = "" if current['size'] else f"正在下载 {current['name']}，已接收 {format_bytes(current['n'])}（总量未知）。"
            report(InstallProgress(stage="model", package=current['name'], downloaded=current['n'],
                                   size=current['size'], speed=speed, detail=detail))
        else:
            report(InstallProgress(stage="processing", completed=current['n'], total=current['size'],
                                   detail=current['name']))

    def init(bar, *args, **kwargs):
        # Keep counters active in pythonw/disabled-console environments while
        # suppressing terminal redraw noise; progress is rendered by the GUI.
        kwargs['disable'] = False
        kwargs['file'] = io.StringIO()
        original_init(bar, *args, **kwargs)
        with lock:
            now = time.monotonic()
            is_bytes = getattr(bar, 'unit', '') in ('B', 'iB', 'byte', 'bytes')
            info = {'name': str(getattr(bar, 'desc', '') or (_download_name.get() if is_bytes else '正在处理音频')),
                    'size': int(getattr(bar, 'total', 0) or 0),
                    'n': int(getattr(bar, 'initial', 0) or 0), 'bytes': is_bytes,
                    'initial': int(getattr(bar, 'initial', 0) or 0), 'start': now, 'last': 0}
            bars[id(bar)] = info
            emit(info, True)

    def update(bar, n=1):
        if cancelled():
            raise TaskCancelled()
        value = original_update(bar, n)
        with lock:
            if info := bars.get(id(bar)):
                info['n'] = max(0, info['n'] + int(n))
                emit(info)
        return value

    def close(bar):
        try:
            with lock:
                info = bars.get(id(bar))
                if info and not cancelled():
                    info['n'] = max(info['n'], int(getattr(bar, 'n', 0) or 0))
                    emit(info, True)
                bars.pop(id(bar), None)
        finally:
            original_close(bar)

    bar_class.__init__, bar_class.update, bar_class.close = init, update, close
    try:
        yield
    finally:
        bar_class.__init__, bar_class.update, bar_class.close = original_init, original_update, original_close
