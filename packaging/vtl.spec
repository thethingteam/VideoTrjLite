# PyInstaller spec: two single-file EXEs.
#   .venv\Scripts\python.exe -m PyInstaller packaging\vtl.spec --noconfirm
# Output:
#   dist\VideoTrjLite.exe  windowed UI (drag files onto it)
#   dist\vtl.exe           console CLI (cmd / PowerShell wait for it and get the exit code)
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821 (SPECPATH from PyInstaller)

ffmpeg = collect_dynamic_libs("av")  # FFmpeg DLLs shipped in the PyAV wheel
# cv2 is the optional fallback decoder; leaving it (and vtl.video.opencv_source,
# which only imports it) out keeps the EXEs ~100 MB smaller.
common_excludes = [
    "cv2", "vtl.video.opencv_source",
    "matplotlib", "scipy", "pandas", "IPython", "pytest", "unittest.mock",
]

ui = Analysis(  # noqa: F821
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=ffmpeg,
    datas=collect_data_files("tkinterdnd2"),  # tkdnd Tcl extension (drag & drop)
    hiddenimports=["vtl.video.pyav_source"],
    excludes=common_excludes,
    noarchive=False,
)
ui_exe = EXE(  # noqa: F821
    PYZ(ui.pure),  # noqa: F821
    ui.scripts,
    ui.binaries,
    ui.datas,
    [],
    name="VideoTrjLite",
    console=False,
    upx=False,
    runtime_tmpdir=None,
)

cli = Analysis(  # noqa: F821
    [os.path.join(SPECPATH, "vtl_cli.py")],  # noqa: F821
    pathex=[ROOT],
    binaries=ffmpeg,
    datas=[],
    hiddenimports=["vtl.video.pyav_source"],
    excludes=common_excludes + ["tkinter", "_tkinter", "tkinterdnd2", "vtl_ui", "PIL.ImageTk"],
    noarchive=False,
)
cli_exe = EXE(  # noqa: F821
    PYZ(cli.pure),  # noqa: F821
    cli.scripts,
    cli.binaries,
    cli.datas,
    [],
    name="vtl",
    console=True,
    upx=False,
    runtime_tmpdir=None,
)
