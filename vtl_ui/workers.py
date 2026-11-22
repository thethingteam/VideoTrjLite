"""Background workers. They never touch Tk widgets; results go through queues."""
from __future__ import annotations

import queue
import threading
from typing import Optional

from vtl.video import open_video
from vtl.video.base import VideoSource


class PreviewWorker:
    """Decodes preview frames off the UI thread, always serving the latest request."""

    def __init__(self, out: "queue.Queue"):
        self._out = out
        self._cond = threading.Condition()
        self._decode_lock = threading.Lock()
        self._req: Optional[tuple[int, float]] = None
        self._src: Optional[VideoSource] = None
        self._gen = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="preview", daemon=True)
        self._thread.start()

    def open(self, path: str) -> None:
        """Open synchronously (so errors surface to the caller)."""
        src = open_video(path)
        with self._cond:
            old, self._src = self._src, src
            self._gen += 1
            self._req = None
        if old is not None:
            with self._decode_lock:
                old.close()

    @property
    def generation(self) -> int:
        return self._gen

    @property
    def info(self):
        return self._src.info if self._src else None

    def request(self, pts: float) -> None:
        with self._cond:
            self._req = (self._gen, pts)
            self._cond.notify()

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._req is None and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                gen, pts = self._req  # type: ignore[misc]
                self._req = None
                src = self._src
            if src is None:
                continue
            with self._decode_lock:
                if gen != self._gen:
                    continue  # source replaced (and possibly closed) meanwhile
                try:
                    actual, img = src.frame_at(pts)
                    self._out.put(("preview", gen, pts, actual, img))
                except Exception as exc:  # noqa: BLE001
                    self._out.put(("preview_error", gen, pts, str(exc)))

    def close(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()
            src, self._src = self._src, None
            self._gen += 1
        if src is not None:
            with self._decode_lock:
                src.close()
