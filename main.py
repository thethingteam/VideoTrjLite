"""Entry point.

- no arguments            -> desktop UI
- file paths              -> desktop UI with those files opened (drag files onto the EXE)
- a CLI subcommand (probe / track-info / export / -h) -> command line
"""
import os
import sys

CLI_COMMANDS = {"probe", "track-info", "export", "-h", "--help", "--version"}


def _attach_console() -> None:
    """Windowed EXE builds have no stdout; borrow the parent console for CLI use.

    cmd / PowerShell do not wait for a windowed program, so the prompt returns
    early and the exit code is lost. Scripts should use the console build
    (vtl.exe); this only keeps quick interactive use working.
    """
    if sys.stdout is not None or os.name != "nt":
        return
    import ctypes

    if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
        print("\nNote: use vtl.exe for command-line work; VideoTrjLite.exe does not "
              "make the console wait or report an exit code.", file=sys.stderr)


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in CLI_COMMANDS:
        _attach_console()
        from vtl.cli import main as cli_main

        return cli_main()
    from vtl_ui.app import main as ui_main

    ui_main([a for a in args if os.path.isfile(a)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
