"""Small platform differences for the UI (fonts, opening a folder)."""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter.font as tkfont

IS_WINDOWS = sys.platform == "win32"


def _family(widget, named_font: str, windows_family: str) -> str:
    if IS_WINDOWS:
        return windows_family
    try:
        return tkfont.nametofont(named_font, root=widget).actual("family")
    except Exception:  # noqa: BLE001 - any Tk hiccup: let Tk pick its default
        return named_font


def ui_family(widget) -> str:
    """Proportional UI font family (Segoe UI on Windows, the Tk default elsewhere)."""
    return _family(widget, "TkDefaultFont", "Segoe UI")


def mono_family(widget) -> str:
    """Fixed-width font family (Consolas on Windows, the Tk fixed font elsewhere)."""
    return _family(widget, "TkFixedFont", "Consolas")


def open_folder(path: str) -> None:
    """Show ``path`` in the system file manager (best effort, never raises)."""
    try:
        if IS_WINDOWS:
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass
