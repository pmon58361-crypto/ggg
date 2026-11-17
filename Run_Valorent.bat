@echo off
cd /d "%~dp0"
if exist "dist\Valorent.exe" (
  start "" "dist\Valorent.exe"
) else (
  echo dist\Valorent.exe not found. Run build\build_exe.bat first or start with: python main.py
  pause
)
