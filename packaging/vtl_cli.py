"""Entry point of the console EXE (vtl.exe): command line only, no Tk."""
import sys

from vtl.cli import main

if __name__ == "__main__":
    sys.exit(main())
