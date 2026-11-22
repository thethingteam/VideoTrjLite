"""Time alignment between video PTS and UTC."""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

from .models import Alignment, Anchor

MAX_SCALE_DEVIATION = 0.001  # 0.1 %: larger values usually mean a typo


def from_single_anchor(anchor: Anchor) -> Alignment:
    return Alignment(anchor.pts, anchor.utc, 1.0)


def from_two_anchors(a1: Anchor, a2: Anchor) -> Alignment:
    if abs(a2.pts - a1.pts) < 1.0:
        raise ValueError("The two anchors must be at least 1 second apart in the video")
    scale = (a2.utc - a1.utc) / (a2.pts - a1.pts)
    if scale <= 0:
        raise ValueError("Anchor times run backwards relative to the video; check both times")
    first, _ = (a1, a2) if a1.pts <= a2.pts else (a2, a1)
    return Alignment(first.pts, first.utc, scale)


def scale_warning(al: Alignment) -> Optional[str]:
    dev = al.scale - 1.0
    if abs(dev) > MAX_SCALE_DEVIATION:
        return (
            f"Clock drift {dev * 100:+.3f}% is unusually large (>0.1%). "
            "Check that both anchor times are correct."
        )
    return None


def from_metadata(creation_time: float, start_pts: float, offset_s: float = 0.0) -> Alignment:
    """creation_time is assumed to correspond to the first frame."""
    return Alignment(start_pts, creation_time + offset_s, 1.0)


def _store_path() -> str:
    """Per-user settings file: %APPDATA% on Windows, XDG config dir elsewhere."""
    base = os.environ.get("APPDATA")
    if not base:
        if sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        else:
            base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "video_trj_integration_lite", "alignments.json")


def _video_key(video_path: str) -> str:
    st = os.stat(video_path)
    return f"{os.path.abspath(video_path).lower()}|{st.st_size}|{int(st.st_mtime)}"


def _read_store() -> dict:
    try:
        with open(_store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(video_path: str, data: dict) -> None:
    """Persist alignment settings for a video (best effort, never raises)."""
    try:
        store = _read_store()
        store[_video_key(video_path)] = data
        # keep the store small
        if len(store) > 200:
            for k in list(store)[: len(store) - 200]:
                del store[k]
        path = _store_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(store, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass


def recall(video_path: str) -> Optional[dict]:
    try:
        data = _read_store().get(_video_key(video_path))
    except OSError:
        return None
    return data if isinstance(data, dict) else None  # ignore a damaged entry


def alignment_to_dict(al: Alignment) -> dict:
    return {"anchor_pts": al.anchor_pts, "anchor_utc": al.anchor_utc, "scale": al.scale}


def alignment_from_dict(d: dict) -> Alignment:
    return Alignment(float(d["anchor_pts"]), float(d["anchor_utc"]), float(d.get("scale", 1.0)))
