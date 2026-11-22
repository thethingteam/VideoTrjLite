"""Tkinter desktop UI for video_trj_integration_lite.

Main screen: video + track, preview, Export from, Sync frame + its clock time,
Offset, FPS / distance, Export. Everything else lives in "Advanced".

Time model shown to the user:
    frame time = time at sync frame + (position - sync frame) + offset
The sync frame defaults to the first frame and its time is pre-filled from the
video's recorded start time (metadata) when present, otherwise from the start
of the track; the user can pick any frame and type its clock time.
Images are taken from "Export from" onwards, on a grid starting at that frame.
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from datetime import date, datetime
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional

from PIL import Image, ImageTk

from vtl import __version__
from vtl.align import from_single_anchor, recall, remember
from vtl.export import run_export
from vtl.models import OK, OUTSIDE_COVERAGE, Alignment, Anchor, ExportConfig, Track
from vtl.timefmt import (
    fmt_local,
    fmt_offset,
    fmt_pts,
    format_tz,
    local_tz_minutes,
    parse_date,
    parse_offset,
    parse_tz,
    parse_user_time,
    to_local,
)
from vtl.track import TRACK_EXTENSIONS, load_track
from vtl.track.interp import TrackInterpolator
from vtl.video import VIDEO_EXTENSIONS, open_video

from .compat import mono_family, open_folder, ui_family
from .speed_plot import SpeedPlot
from .workers import PreviewWorker

try:  # drag & drop is optional
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _BaseTk = TkinterDnD.Tk
    HAS_DND = True
except Exception:  # noqa: BLE001
    _BaseTk = tk.Tk
    HAS_DND = False

def _parse_position(text: str, name: str) -> Optional[float]:
    """Video position typed by the user (seconds or h:mm:ss); None when blank."""
    text = text.strip()
    if not text:
        return None
    try:
        return parse_offset(text)
    except ValueError as exc:
        raise ValueError(f"{name}: use seconds (12.5) or h:mm:ss ({fmt_pts(12.5)})") from exc


MODE_TIME = "time"  # images per second
MODE_DIST = "distance"  # one image every N metres
TRACK_EXTS = TRACK_EXTENSIONS + (".log", ".txt")  # .log / .txt are sniffed by load_track


class App(_BaseTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Video + GPS Track {__version__}")
        self.geometry("1100x820")
        self.minsize(820, 600)

        self.q: "queue.Queue" = queue.Queue()
        self.preview = PreviewWorker(self.q)
        self.track: Optional[Track] = None
        self.interp: Optional[TrackInterpolator] = None
        self.nmea_date: Optional[date] = None
        self.cur_pts = 0.0
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._last_img = None
        self._scale_lock = False
        self._refresh_job = None
        self._export_thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()

        # main settings
        self.v_video = tk.StringVar()
        self.v_track = tk.StringVar()
        self.v_sync = tk.StringVar()  # video position of the sync frame
        self.v_start = tk.StringVar()  # clock time at the sync frame
        self._start_auto: Optional[str] = None  # text we filled in ourselves
        self._prefer_track = False  # auto time from the track even with metadata (old "Track start")
        self.v_in = tk.StringVar()  # export from (video position), blank = video start
        self.v_offset = tk.StringVar(value="0")
        self.v_fps = tk.StringVar(value="2")
        self.v_mode = tk.StringVar(value=MODE_TIME)
        self.v_dist = tk.StringVar(value="5")
        self.v_outdir = tk.StringVar()
        # advanced settings
        self.v_adv = tk.BooleanVar(value=False)
        self.v_tz = tk.StringVar(value=format_tz(local_tz_minutes()))
        self.v_gap = tk.StringVar(value="3")
        self.v_minmove = tk.StringVar(value="0")
        self.v_out_pts = tk.StringVar()
        self.v_format = tk.StringVar(value="jpg")
        self.v_exif = tk.BooleanVar(value=True)

        self._build()
        for v in (self.v_tz, self.v_sync):  # keep an automatic time consistent
            v.trace_add("write", lambda *_: self._autofill_start())
        for v in (self.v_sync, self.v_start, self.v_in, self.v_offset, self.v_tz, self.v_gap):
            v.trace_add("write", lambda *_: self._schedule_refresh())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_job = self.after(50, self._poll)

    # ================================================================ layout
    def _build(self) -> None:
        pad = dict(padx=4, pady=3)

        files = ttk.Frame(self)
        files.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(files, text="Video").grid(row=0, column=0, sticky="w", **pad)
        e_v = ttk.Entry(files, textvariable=self.v_video)
        e_v.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(files, text="Open...", command=self._browse_video).grid(row=0, column=2, **pad)
        ttk.Label(files, text="GPS track").grid(row=1, column=0, sticky="w", **pad)
        e_t = ttk.Entry(files, textvariable=self.v_track)
        e_t.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(files, text="Open...", command=self._browse_track).grid(row=1, column=2, **pad)
        files.columnconfigure(1, weight=1)
        e_v.bind("<Return>", lambda e: self._load_video(self.v_video.get()))
        e_t.bind("<Return>", lambda e: self._load_track(self.v_track.get()))
        hint = "Drag a video and a GPX/NMEA file onto this window" if HAS_DND else \
            "Open a video and a GPX/NMEA file"
        self.lbl_info = ttk.Label(files, text=hint, foreground="#666")
        self.lbl_info.grid(row=2, column=0, columnspan=3, sticky="w", **pad)
        if HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)

        # ---- bottom bar first so it always stays visible
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", padx=8, pady=(2, 8))
        self._build_main_controls(bottom)

        # ---- preview area
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=8)
        self.canvas = tk.Canvas(mid, background="#202020", highlightthickness=0, height=300)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw_preview())
        self.scale = ttk.Scale(mid, from_=0, to=1, orient="horizontal", command=self._on_scale)
        self.scale.pack(fill="x", pady=(4, 0))
        self.lbl_cur = ttk.Label(mid, text="", font=(mono_family(self), 10))
        self.lbl_cur.pack(fill="x", pady=2)
        self.plot = SpeedPlot(mid, height=90, on_click=self._on_plot_click)
        self.plot.pack(fill="x", pady=(2, 4))

        # ←/→ one frame, Shift = 1 s, Ctrl = 10 s (ignored while typing)
        self.bind_all("<Left>", lambda e: self._key_step(e, -1))
        self.bind_all("<Right>", lambda e: self._key_step(e, 1))

    def _key_step(self, event, sign: int) -> None:
        info = self.preview.info
        if info is None or isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        if event.state & 0x4:
            step = 10.0
        elif event.state & 0x1:
            step = 1.0
        else:
            step = info.frame_duration
        self._seek(self.cur_pts + sign * step)

    def _build_main_controls(self, parent) -> None:
        pad = dict(padx=4, pady=3)
        family = ui_family(self)
        bold = (family, 10, "bold")
        big = (family, 11)

        # ---- export start frame
        row_from = ttk.Frame(parent)
        row_from.pack(fill="x")
        ttk.Label(row_from, text="Export from", font=bold, width=11).pack(side="left", **pad)
        ttk.Entry(row_from, textvariable=self.v_in, width=13, font=big).pack(side="left", padx=4)
        self.lbl_from_frame = ttk.Label(row_from, text="", foreground="#666", width=16)
        self.lbl_from_frame.pack(side="left")
        ttk.Button(row_from, text="Use current frame",
                   command=lambda: self.v_in.set(fmt_pts(self.cur_pts))).pack(side="left", padx=2)
        ttk.Button(row_from, text="Go", width=4,
                   command=lambda: self._goto(self.v_in)).pack(side="left", padx=2)
        ttk.Button(row_from, text="Clear", width=6,
                   command=lambda: self.v_in.set("")).pack(side="left", padx=2)

        # ---- sync frame + its clock time
        row0 = ttk.Frame(parent)
        row0.pack(fill="x")
        ttk.Label(row0, text="Sync frame", font=bold, width=11).pack(side="left", **pad)
        ttk.Entry(row0, textvariable=self.v_sync, width=13, font=big).pack(side="left", padx=4)
        self.lbl_sync_frame = ttk.Label(row0, text="", foreground="#666", width=16)
        self.lbl_sync_frame.pack(side="left")
        ttk.Button(row0, text="Use current frame", command=self._use_current_as_sync).pack(side="left", padx=2)
        ttk.Button(row0, text="Go", width=4,
                   command=lambda: self._goto(self.v_sync)).pack(side="left", padx=2)
        ttk.Label(row0, text="Time at sync frame", font=bold).pack(side="left", padx=(14, 4))
        ttk.Entry(row0, textvariable=self.v_start, width=24, font=big).pack(side="left", padx=4)
        self.lbl_start_tz = ttk.Label(row0, text="", foreground="#666")
        self.lbl_start_tz.pack(side="left")
        ttk.Button(row0, text="Reset", width=6,
                   command=lambda: self._autofill_start(force=True)).pack(side="left", padx=6)
        self.lbl_start_src = ttk.Label(row0, text="", foreground="#666")
        self.lbl_start_src.pack(side="left")

        row = ttk.Frame(parent)
        row.pack(fill="x")

        ttk.Label(row, text="Offset (s)", font=bold).pack(side="left", **pad)
        for d in (-1, -0.1):
            ttk.Button(row, text=f"{d:g}", width=5, command=lambda d=d: self._nudge(d)).pack(side="left")
        ttk.Entry(row, textvariable=self.v_offset, width=10, justify="center",
                  font=big).pack(side="left", padx=4)
        for d in (0.1, 1):
            ttk.Button(row, text=f"+{d:g}", width=5, command=lambda d=d: self._nudge(d)).pack(side="left")

        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Radiobutton(row, text="FPS", variable=self.v_mode, value=MODE_TIME,
                        command=self._mode_changed).pack(side="left", padx=(4, 0))
        self.cb_fps = ttk.Combobox(row, textvariable=self.v_fps, values=("0.5", "1", "2", "3", "5", "10"),
                                   width=6, font=big)
        self.cb_fps.pack(side="left")
        ttk.Label(row, text="images / s", foreground="#666").pack(side="left", padx=(4, 12))
        ttk.Radiobutton(row, text="Every", variable=self.v_mode, value=MODE_DIST,
                        command=self._mode_changed).pack(side="left")
        self.cb_dist = ttk.Combobox(row, textvariable=self.v_dist, values=("1", "2", "5", "10", "20", "50"),
                                    width=6, font=big)
        self.cb_dist.pack(side="left")
        ttk.Label(row, text="m", foreground="#666").pack(side="left", padx=4)
        self._mode_changed()

        self.btn_export = ttk.Button(row, text="Export", command=self._start_export, width=12)
        self.btn_export.pack(side="right", padx=4)
        self.btn_cancel = ttk.Button(row, text="Cancel", command=self._cancel.set, state="disabled")
        self.btn_cancel.pack(side="right")

        row2 = ttk.Frame(parent)
        row2.pack(fill="x")
        ttk.Label(row2, text="Save to").pack(side="left", **pad)
        ttk.Entry(row2, textvariable=self.v_outdir).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row2, text="...", width=3, command=self._browse_out).pack(side="left")
        ttk.Checkbutton(row2, text="Advanced", variable=self.v_adv,
                        command=self._toggle_advanced).pack(side="left", padx=(12, 0))

        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(fill="x", pady=(4, 0))
        self.lbl_status = ttk.Label(parent, text="", foreground="#333")
        self.lbl_status.pack(fill="x")

        self.adv = ttk.LabelFrame(parent, text="Advanced")
        self._build_advanced(self.adv)

    def _build_advanced(self, f) -> None:
        pad = dict(padx=4, pady=2)
        opts = ttk.Frame(f)
        opts.grid(row=0, column=0, sticky="w", **pad)
        for text, var, w in (("Timezone", self.v_tz, 11), ("Max GPS gap (s)", self.v_gap, 5),
                             ("Min movement (m)", self.v_minmove, 5), ("Export to", self.v_out_pts, 12)):
            ttk.Label(opts, text=text).pack(side="left", padx=(8, 2))
            ttk.Entry(opts, textvariable=var, width=w).pack(side="left")
        ttk.Label(opts, text="Format").pack(side="left", padx=(8, 2))
        ttk.Combobox(opts, textvariable=self.v_format, values=("jpg", "png"), width=4,
                     state="readonly").pack(side="left")
        ttk.Checkbutton(opts, text="EXIF GPS", variable=self.v_exif).pack(side="left", padx=8)

    def _mode_changed(self) -> None:
        dist = self.v_mode.get() == MODE_DIST
        self.cb_fps.configure(state="disabled" if dist else "normal")
        self.cb_dist.configure(state="normal" if dist else "disabled")

    def _toggle_advanced(self) -> None:
        if self.v_adv.get():
            self.adv.pack(fill="x", pady=(6, 0))
        else:
            self.adv.pack_forget()

    # ============================================================ file load
    def _on_drop(self, event) -> None:
        self.open_files(self.tk.splitlist(event.data))

    def open_files(self, paths) -> None:
        """Open videos / tracks by extension (drag & drop, command line)."""
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            if ext in TRACK_EXTS:
                self._load_track(p)
            elif ext in VIDEO_EXTENSIONS or os.path.isfile(p):
                self._load_video(p)

    def _browse_video(self) -> None:
        pats = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        p = filedialog.askopenfilename(filetypes=[("Video", pats), ("All files", "*.*")])
        if p:
            self._load_video(p)

    def _browse_track(self) -> None:
        p = filedialog.askopenfilename(
            filetypes=[("GPX / NMEA", " ".join(f"*{e}" for e in TRACK_EXTS)), ("All files", "*.*")])
        if p:
            self._load_track(p)

    def _browse_out(self) -> None:
        p = filedialog.askdirectory()
        if p:
            self.v_outdir.set(p)

    def _load_video(self, path: str) -> None:
        path = path.strip().strip('"')
        if not path:
            return
        try:
            self.preview.open(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Video", f"Could not open the video:\n{exc}")
            return
        self.v_video.set(path)
        info = self.preview.info
        self.scale.configure(from_=info.start_pts, to=self._last_pts())
        if not self.v_outdir.get():
            self.v_outdir.set(os.path.dirname(path))
        self.v_in.set("")
        self.v_out_pts.set("")
        self.v_offset.set("0")  # an offset belongs to one video; saved settings restore it below
        self._prefer_track = False
        self.v_sync.set(fmt_pts(info.start_pts))
        self._autofill_start(force=True)
        saved = recall(path)
        if saved:
            self._apply_saved(saved)
            self._set_status("Restored the previous offset for this video.")
        self._update_info()
        self._seek(info.start_pts)

    def _load_track(self, path: str) -> None:
        path = path.strip().strip('"')
        if not path:
            return
        try:
            # never reuse a date typed for another file: ask again for each dateless file
            tr = load_track(path, None)
        except ValueError as exc:
            if "no date" not in str(exc):
                messagebox.showerror("GPS track", f"Could not read the track:\n{exc}")
                return
            suggest = self.nmea_date or datetime.now().date()
            text = simpledialog.askstring(
                "Recording date",
                "This NMEA file has no date.\nEnter the recording date (YYYY-MM-DD):",
                initialvalue=suggest.strftime("%Y-%m-%d"), parent=self)
            if not text:
                return
            try:
                self.nmea_date = parse_date(text)
                tr = load_track(path, self.nmea_date)
            except ValueError as exc2:
                messagebox.showerror("GPS track", f"Could not read the track:\n{exc2}")
                return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("GPS track", f"Could not read the track:\n{exc}")
            return
        self.v_track.set(path)
        self.track = tr
        self._rebuild_interp()
        self._autofill_start()
        if tr.warnings:
            self._set_status("Track: " + "; ".join(tr.warnings))
        self._update_info()
        self._refresh()

    def _rebuild_interp(self) -> None:
        if self.track is None:
            self.interp = None
            return
        try:
            gap = float(self.v_gap.get())
        except ValueError:
            gap = 3.0
        self.interp = TrackInterpolator(self.track, gap)
        self.plot.set_series(self.interp.speed_series(), self._tz())

    def _update_info(self) -> None:
        parts = []
        info = self.preview.info
        tz = self._tz()
        if info:
            parts.append(f"Video {info.width}x{info.height}, {info.avg_fps:.2f} fps, {fmt_pts(info.duration)}")
            if info.creation_time:
                parts.append(f"recorded {fmt_local(info.creation_time, tz)}")
        if self.track:
            parts.append(
                f"Track {len(self.track)} points, {fmt_local(self.track.start, tz)} – "
                f"{fmt_local(self.track.end, tz, False)}"
            )
        self.lbl_info.configure(text="   ·   ".join(parts) or "--")

    # =============================================================== preview
    def _last_pts(self) -> float:
        info = self.preview.info
        return max(info.start_pts, info.end_pts - info.frame_duration)

    def _seek(self, pts: float) -> None:
        info = self.preview.info
        if info is None:
            return
        pts = min(max(pts, info.start_pts), self._last_pts())
        self.cur_pts = pts
        self._scale_lock = True
        self.scale.set(pts)
        self._scale_lock = False
        self.preview.request(pts)
        self._refresh()

    def _on_scale(self, value) -> None:
        if self._scale_lock:
            return
        self.cur_pts = float(value)
        self.preview.request(self.cur_pts)
        self._refresh()

    def _redraw_preview(self) -> None:
        if self._last_img is None:
            return
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        im = self._last_img
        scale = min(cw / im.width, ch / im.height)
        size = (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
        self._photo = ImageTk.PhotoImage(im.resize(size, Image.BILINEAR))
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)

    # ============================================================= alignment
    def _tz(self) -> int:
        try:
            return parse_tz(self.v_tz.get())
        except ValueError:
            return local_tz_minutes()

    def _start_candidate(self) -> tuple[Optional[float], str]:
        """Best guess for the video start time: metadata, else track start."""
        info = self.preview.info
        if self._prefer_track and self.track is not None:
            return self.track.start, "from GPS track start"
        if info is not None and info.creation_time is not None:
            return info.creation_time, "from video metadata"
        if self.track is not None:
            return self.track.start, "from GPS track start"
        return None, ""

    def _sync_pts(self) -> float:
        """Video position of the sync frame (raises ValueError for bad input)."""
        pts = _parse_position(self.v_sync.get(), "Sync frame")
        if pts is None:
            info = self.preview.info
            return info.start_pts if info else 0.0
        return pts

    def _autofill_start(self, force: bool = False) -> None:
        """Fill the time at the sync frame unless the user has typed their own."""
        cur = self.v_start.get().strip()
        if not force and cur and cur != self._start_auto:
            return
        utc, _src = self._start_candidate()
        info = self.preview.info
        text = ""
        if utc is not None and info is not None:
            try:
                sync = self._sync_pts()
            except ValueError:
                return  # sync position is being typed; keep the current time
            text = fmt_local(utc + (sync - info.start_pts), self._tz())
        self._start_auto = text
        self.v_start.set(text)

    def _start_source(self) -> str:
        cur = self.v_start.get().strip()
        if not cur:
            return "enter the clock time of the sync frame"
        if cur == self._start_auto:
            return self._start_candidate()[1]
        return "typed"

    def _use_current_as_sync(self) -> None:
        """Make the current frame the sync frame, keeping the current alignment.

        The time field then shows that frame's time, ready to be corrected.
        """
        if self.v_start.get().strip() in ("", self._start_auto):
            self.v_sync.set(fmt_pts(self.cur_pts))
            self._autofill_start(force=True)
        else:
            # a typed time: carry it over to the new frame, never discard it
            try:
                base = self._build_alignment(with_offset=False)
            except ValueError as exc:
                self._set_status(f"Sync frame not changed: {exc}")
                return
            if base is None:
                return
            tz = self._tz()
            self.v_sync.set(fmt_pts(self.cur_pts))
            self.v_start.set(fmt_local(base.to_utc(self.cur_pts), tz))
        self._refresh()

    def _goto(self, var: tk.StringVar) -> None:
        info = self.preview.info
        if info is None:
            return
        try:
            pts = _parse_position(var.get(), "Position")
        except ValueError as exc:
            self._set_status(str(exc))
            return
        self._seek(info.start_pts if pts is None else pts)

    def _frame_label(self, text: str, empty: str) -> str:
        info = self.preview.info
        if info is None:
            return ""
        try:
            pts = _parse_position(text, "")
        except ValueError:
            return "invalid"
        if pts is None:
            return empty
        n = round((pts - info.start_pts) * info.avg_fps)
        return f"frame ≈ {n}"

    def _nudge(self, d: float) -> None:
        try:
            cur = parse_offset(self.v_offset.get())
        except ValueError:
            cur = 0.0
        self.v_offset.set(fmt_offset(round(cur + d, 3)))

    def _build_alignment(self, with_offset: bool = True) -> Optional[Alignment]:
        """utc(pts) = time at sync frame + (pts - sync frame) + offset.

        None = no time reference (frames only). Raises ValueError for bad input.
        """
        info = self.preview.info
        if info is None:
            raise ValueError("Open a video first")
        offset = parse_offset(self.v_offset.get()) if with_offset else 0.0
        tz = parse_tz(self.v_tz.get())
        sync_pts = self._sync_pts()
        text = self.v_start.get().strip()
        if not text:
            if self.track is not None:
                raise ValueError("Enter the clock time at the sync frame")
            return None  # frames only, no time reference
        try:
            utc = parse_user_time(text, tz, self._default_date(tz))
        except ValueError as exc:
            raise ValueError(f"Time at sync frame: {exc}") from exc
        al = from_single_anchor(Anchor(sync_pts, utc))
        return al.shifted(offset) if offset else al

    def _try_alignment(self, with_offset: bool = True) -> Optional[Alignment]:
        try:
            return self._build_alignment(with_offset)
        except ValueError:
            return None

    def _default_date(self, tz: int) -> date:
        if self.track is not None:
            return to_local(self.track.start, tz).date()
        info = self.preview.info
        if info and info.creation_time:
            return to_local(info.creation_time, tz).date()
        return self.nmea_date or datetime.now().date()

    def _saved_state(self) -> dict:
        return {
            "sync": self.v_sync.get(), "start": self.v_start.get(), "from": self.v_in.get(),
            "to": self.v_out_pts.get(), "offset": self.v_offset.get(), "fps": self.v_fps.get(),
            "mode": self.v_mode.get(), "distance": self.v_dist.get(), "tz": self.v_tz.get(),
        }

    def _apply_saved(self, d: dict) -> None:
        d = dict(d)
        base = d.get("base")  # v0.1 settings
        if base == "Clock time at a frame" and "anchor_pts" in d:
            try:
                d["sync"] = fmt_pts(float(d["anchor_pts"]))
                d["start"] = d.get("anchor_time", "")
            except (TypeError, ValueError):
                pass
        elif base == "Track start":
            self._prefer_track = True  # auto time = track start, as before
            self._autofill_start(force=True)
        # tz and sync first: changing them re-fills an automatic time
        for key, var in (("tz", self.v_tz), ("sync", self.v_sync), ("start", self.v_start),
                         ("from", self.v_in), ("to", self.v_out_pts), ("offset", self.v_offset),
                         ("fps", self.v_fps), ("distance", self.v_dist)):
            if isinstance(d.get(key), str):
                var.set(d[key])
        if self.v_out_pts.get().strip() and not self.v_adv.get():
            self.v_adv.set(True)  # make a restored end point visible
            self._toggle_advanced()
        if d.get("mode") in (MODE_TIME, MODE_DIST):
            self.v_mode.set(d["mode"])
            self._mode_changed()

    # ============================================================== readout
    def _schedule_refresh(self) -> None:
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(100, self._refresh)

    def _refresh(self) -> None:
        if self._refresh_job:  # called directly while a debounced refresh is pending
            try:
                self.after_cancel(self._refresh_job)
            except tk.TclError:
                pass
        self._refresh_job = None
        info = self.preview.info
        if info is None:
            return
        if self.interp is not None:
            try:
                if float(self.v_gap.get()) != self.interp.max_gap_s:
                    self._rebuild_interp()
            except ValueError:
                pass
        tz = self._tz()
        self.lbl_start_tz.configure(text=format_tz(tz))
        self.lbl_start_src.configure(text=f"({self._start_source()})")
        self.lbl_sync_frame.configure(text=self._frame_label(self.v_sync.get(), "video start"))
        self.lbl_from_frame.configure(text=self._frame_label(self.v_in.get(), "(video start)"))
        parts = [f"{fmt_pts(self.cur_pts)} / {fmt_pts(info.duration)}"]
        al = None
        try:
            al = self._build_alignment()
        except ValueError as exc:
            parts.append(str(exc))
        utc = None
        if al is not None:
            utc = al.to_utc(self.cur_pts)
            parts.append(fmt_local(utc, tz))
            if self.interp is not None:
                status, fix = self.interp.locate(utc)
                if status == OK and fix is not None:
                    spd = f"  {fix.speed * 3.6:.0f} km/h" if fix.speed is not None else ""
                    parts.append(f"{fix.lat:.6f}, {fix.lon:.6f}{spd}")
                elif status == OUTSIDE_COVERAGE:
                    parts.append("outside the GPS track – adjust the offset")
                else:
                    parts.append("GPS gap")
        elif self.track is None:
            parts.append("no GPS track")
        self.lbl_cur.configure(text="    ".join(parts))
        span = (al.to_utc(info.start_pts), al.to_utc(info.end_pts)) if al is not None else None
        self.plot.set_cursor(utc, span, tz)

    def _on_plot_click(self, utc: float) -> None:
        al = self._try_alignment()
        if al is not None:
            self._seek(al.to_pts(utc))

    # =============================================================== export
    def _start_export(self) -> None:
        if self._export_thread and self._export_thread.is_alive():
            return
        if self.preview.info is None:
            messagebox.showerror("Export", "Open a video first.")
            return
        try:
            cfg = self._build_config()
            alignment = self._build_alignment()
            if self.track is not None and alignment is None:
                raise ValueError("Cannot place the video on the GPS track")
            outdir = self.v_outdir.get().strip()
            if not outdir:
                raise ValueError("Choose where to save the images")
        except ValueError as exc:
            messagebox.showerror("Export", str(exc))
            return
        if self.track is None and not messagebox.askyesno(
                "Export", "No GPS track is open. Export images without coordinates?"):
            return

        video_path = self.v_video.get()
        ui_state = self._saved_state()  # read Tk variables on the UI thread only
        remember(video_path, ui_state)
        track = self.track
        self._cancel.clear()
        self.btn_export.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress.configure(value=0, maximum=1)
        self._set_status("Starting...")

        def work() -> None:
            try:
                src = open_video(video_path)
                try:
                    res = run_export(
                        src, track, alignment, cfg, outdir,
                        progress=lambda d, t, m: self.q.put(("progress", d, t, m)),
                        cancel=self._cancel,
                        extra_summary={"ui": ui_state},
                    )
                finally:
                    src.close()
                self.q.put(("done", res))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("error", str(exc)))

        self._export_thread = threading.Thread(target=work, name="export", daemon=True)
        self._export_thread.start()

    def _build_config(self) -> ExportConfig:
        def num(var: tk.StringVar, name: str) -> float:
            try:
                v = float(var.get())
            except ValueError as exc:
                raise ValueError(f"{name} must be a number") from exc
            if v < 0:
                raise ValueError(f"{name} must not be negative")
            return v

        fps, dist = 2.0, 0.0
        if self.v_mode.get() == MODE_DIST:
            dist = num(self.v_dist, "Distance")
            if dist <= 0:
                raise ValueError("Distance must be greater than 0")
            if self.track is None:
                raise ValueError("Exporting by distance needs a GPS track")
        else:
            fps = num(self.v_fps, "FPS")
            if fps <= 0:
                raise ValueError("FPS must be greater than 0")
        tz = parse_tz(self.v_tz.get())
        in_pts = _parse_position(self.v_in.get(), "Export from")
        return ExportConfig(
            rate=fps,
            distance_m=dist,
            in_pts=in_pts,
            out_pts=_parse_position(self.v_out_pts.get(), "Export to"),
            grid_start=in_pts,  # the "Export from" frame is the first image
            max_gap_s=num(self.v_gap, "Max GPS gap"),
            min_move_m=num(self.v_minmove, "Min movement"),
            image_format=self.v_format.get(),
            write_exif=self.v_exif.get(),
            tz_offset_min=tz,
            relative_date=self._default_date(tz).strftime("%Y%m%d"),
        )

    # ================================================================ queue
    def _poll(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "preview":
                    _, gen, _req, _actual, img = msg
                    if gen == self.preview.generation:
                        self._last_img = Image.fromarray(img)
                        self._redraw_preview()
                elif kind == "preview_error":
                    if msg[1] != self.preview.generation:
                        continue  # from a video that has been replaced
                    self._last_img = None
                    self.canvas.delete("all")
                    self.canvas.create_text(10, 10, anchor="nw", fill="#f88",
                                            text=f"Preview failed: {msg[3]}")
                elif kind == "progress":
                    _, done, total, text = msg
                    self.progress.configure(maximum=max(1, total), value=done)
                    self._set_status(text)
                elif kind == "done":
                    self._export_finished(msg[1])
                elif kind == "error":
                    self._export_finished(None)
                    messagebox.showerror("Export failed", msg[1])
        except queue.Empty:
            pass
        self._poll_job = self.after(30, self._poll)

    def _export_finished(self, res) -> None:
        self.btn_export.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        if res is None:
            self._set_status("Export failed.")
            return
        skipped = sum(res.skipped.values())
        head = "Cancelled" if res.cancelled else "Done"
        text = f"{head}: {res.exported} images saved" + (f", {skipped} skipped" if skipped else "")
        self._set_status(f"{text}  →  {res.job_dir}")
        if not res.cancelled and messagebox.askyesno("Export finished", text + "\n\nOpen the folder?"):
            open_folder(res.job_dir)

    def _set_status(self, text: str) -> None:
        self.lbl_status.configure(text=text)

    def _on_close(self) -> None:
        if self._export_thread and self._export_thread.is_alive():
            if not messagebox.askyesno("Quit", "An export is running. Cancel it and quit?"):
                return
            self._cancel.set()
            self._export_thread.join(timeout=5)
        self.preview.close()
        self.destroy()

    def destroy(self) -> None:
        for job in (getattr(self, "_poll_job", None), self._refresh_job):
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        super().destroy()


def main(paths: Optional[list[str]] = None) -> None:
    app = App()
    if paths:
        app.after(100, lambda: app.open_files(paths))
    app.mainloop()


if __name__ == "__main__":
    main()
