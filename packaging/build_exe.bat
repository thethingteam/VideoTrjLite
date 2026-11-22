@echo off
rem Build dist\VideoTrjLite.exe (windowed UI) and dist\vtl.exe (console CLI), one file each.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Create it with:
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
  exit /b 1
)
".venv\Scripts\python.exe" -m PyInstaller packaging\vtl.spec --noconfirm --clean --distpath dist --workpath build
if errorlevel 1 exit /b 1
echo.
echo Built dist\VideoTrjLite.exe (UI) and dist\vtl.exe (CLI)
