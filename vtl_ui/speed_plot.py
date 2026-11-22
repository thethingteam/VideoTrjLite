"""Track speed curve with a cursor for the current video frame (plain Tk Canvas)."""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from vtl.timefmt import to_local

from .compat import ui_family

PAD_L, PAD_R, PAD_T, PAD_B = 44, 8, 8, 18


class SpeedPlot(tk.Canvas):
    def __init__(self, master, height: int = 120, on_click: Optional[Callable[[float], None]] = None):
        super().__init__(master, height=height, background="#fafafa", highlightthickness=1,
                         highlightbackground="#ccc")
        self._series: list[tuple[float, float]] = []
        self._tz = 0
        self._cursor: Optional[float] = None
        self._span: Optional[tuple[float, float]] = None
        self._on_click = on_click
        self._font = (ui_family(self), 8)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)

    def set_series(self, series: list[tuple[float, float]], tz_minutes: int) -> None:
        self._series = series
        self._tz = tz_minutes
        self._draw()

    def set_cursor(self, utc: Optional[float], span: Optional[tuple[float, float]], tz: int) -> None:
        self._cursor, self._span, self._tz = utc, span, tz
        self._draw()

    # ------------------------------------------------------------------
    def _geom(self):
        w, h = max(10, self.winfo_width()), max(10, self.winfo_height())
        t0, t1 = self._series[0][0], self._series[-1][0]
        if t1 <= t0:
            t1 = t0 + 1
        vmax = max((v for _, v in self._series), default=1.0) or 1.0
        vmax *= 1.1
        return w, h, t0, t1, vmax

    def _x(self, t, w, t0, t1):
        return PAD_L + (t - t0) / (t1 - t0) * (w - PAD_L - PAD_R)

    def _draw(self) -> None:
        self.delete("all")
        if not self._series:
            self.create_text(8, 8, anchor="nw", fill="#888",
                             text="Speed curve appears here after loading a track. Click it to jump "
                                  "(needs alignment).")
            return
        w, h, t0, t1, vmax = self._geom()
        y0, y1 = h - PAD_B, PAD_T

        def y(v):
            return y0 - v / vmax * (y0 - y1)

        if self._span:
            a = max(t0, min(t1, self._span[0]))
            b = max(t0, min(t1, self._span[1]))
            if b > a:
                self.create_rectangle(self._x(a, w, t0, t1), y1, self._x(b, w, t0, t1), y0,
                                      fill="#dbe9ff", outline="")
        # axes
        self.create_line(PAD_L, y0, w - PAD_R, y0, fill="#999")
        self.create_line(PAD_L, y0, PAD_L, y1, fill="#999")
        kmh_max = vmax * 3.6
        self.create_text(PAD_L - 4, y1, anchor="ne", text=f"{kmh_max:.0f}", fill="#666",
                         font=self._font)
        self.create_text(PAD_L - 4, y0, anchor="se", text="km/h", fill="#666", font=self._font)
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            t = t0 + (t1 - t0) * frac
            x = self._x(t, w, t0, t1)
            anchor = "nw" if frac == 0 else ("ne" if frac == 1 else "n")
            self.create_text(x, y0 + 2, anchor=anchor, fill="#666", font=self._font,
                             text=to_local(t, self._tz).strftime("%H:%M:%S"))
        # curve, downsampled to ~2 points per pixel
        n = len(self._series)
        step = max(1, n // max(1, (w - PAD_L - PAD_R) * 2))
        pts = []
        for i in range(0, n, step):
            t, v = self._series[i]
            pts += [self._x(t, w, t0, t1), y(v)]
        if len(pts) >= 4:
            self.create_line(*pts, fill="#2a6fdb", width=1)
        if self._cursor is not None and t0 <= self._cursor <= t1:
            x = self._x(self._cursor, w, t0, t1)
            self.create_line(x, y1, x, y0, fill="#d22", width=2)

    def _click(self, event) -> None:
        if not self._series or self._on_click is None:
            return
        w, h, t0, t1, _ = self._geom()
        frac = (event.x - PAD_L) / max(1, (w - PAD_L - PAD_R))
        frac = min(1.0, max(0.0, frac))
        self._on_click(t0 + frac * (t1 - t0))
