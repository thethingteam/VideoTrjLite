# AGENTS.md

## Project purpose

Single-video desktop tool (Windows x64 is the verified platform; Linux runs from source and is not yet verified - keep the README "支援平台" table honest; EXEs are Windows only): align one video with a GPX or NMEA track, extract frames at a fixed rate,
attach interpolated WGS84 positions, write CSV + optional EXIF GPS. Lite sibling of
`../video_trj_integration` (multi-camera, StoneX CSV). Design: `ARCHITECTURE.md`.

## Code map

- `main.py`: no args -> UI (`vtl_ui.app`), otherwise CLI (`vtl.cli`).
- `vtl/models.py`: dataclasses and sample status constants.
- `vtl/track/`: `gpx.py`, `nmea.py`, `interp.py` (bracket interpolation, gap, haversine); `__init__.load_track` dispatches by extension/sniffing via `READERS`.
- `vtl/video/`: `pyav_source.py` (primary), `opencv_source.py` (fallback), `open_video()`.
- `vtl/align.py`: PTS<->UTC mappings (single / two anchors / metadata) and per-video settings memory.
- `vtl/timefmt.py`: user time/timezone parsing and all formatting.
- `vtl/sampler.py`: export schedule + filters. `vtl/export.py`: decode/write pipeline. `vtl/geotag.py`: EXIF.
- `vtl_ui/`: Tkinter app, preview worker thread, speed plot; `compat.py` holds the platform differences (font families, opening a folder) - no hard-coded Windows fonts or `os.startfile` elsewhere.
- `packaging/`: PyInstaller spec building two one-file EXEs (windowed UI `VideoTrjLite.exe`, console CLI `vtl.exe` from `vtl_cli.py`; bundles PyAV FFmpeg DLLs, tkdnd for the UI, excludes cv2) and `build_exe.bat`.
- Version lives in `vtl/__init__.py` (`pyproject.toml` reads it).
- `data/` is the developer's local test data (real videos/tracks); never commit it.

## Engineering rules

- `vtl` must not import tkinter; keep logic testable from CLI/unittest.
- Absolute time is UTC epoch float everywhere internally; timezones only at input/output boundaries. GPX/NMEA are UTC.
- Video time is PTS seconds (never frame_index / fps) so VFR works. Seeks must start at or before the target (`_decode_from` backs off for MPEG-TS style late keyframes).
- Sampling grid starts at `cfg.grid_start` when set (the UI passes "Export from"), otherwise passes through Anchor 1 (`anchor_pts + n / rate`); `in_pts` alone only clips. The export range is clipped to the video ∩ mapped track ∩ in/out.
- UI alignment: `utc = time at sync frame + (pts - sync frame) + offset`; sync frame and export start are independent.
- Do not interpolate across gaps > `max_gap_s` or across GPX segments; reset the min-movement reference after a gap.
- Export decodes sequentially (no per-image seek); only exported frames are converted to RGB.
- An image's time, position, EXIF and filename come from the PTS of the frame actually written, not from the scheduled slot.
- Never overwrite a previous run: each export creates `vtl_YYYYMMDD_HHMMSS[_n]`.
- Worker threads never touch Tk widgets or Tk variables; read UI state on the main thread and pass values in. Results go through `App.q`.
- UI text, errors and skip reasons in English; accept 上午/下午 time input. Save sources as UTF-8.

## Validation

- `.venv\Scripts\python.exe -m unittest discover -s tests -v`
- `.venv\Scripts\python.exe -m compileall -q vtl vtl_ui main.py`
- `.venv\Scripts\python.exe -m pyflakes vtl vtl_ui main.py tests`
- EXE: `packaging\build_exe.bat`, then smoke-test `dist\vtl.exe export ...` on a short clip (it must block and return the exit code).
- Manually verify drag/drop, scrubbing, speed-curve click, and a short export with a real camera file before release.
