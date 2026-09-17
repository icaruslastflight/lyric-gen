@echo off
REM Default launch: standalone GUI (double-click or run from Explorer)
REM Icon: .bat files cannot carry icons; create a shortcut to this file, then
REM   Shortcut properties > Change icon > browse to:
REM   assets\icons\lyric_gen_launch.ico
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" run.py %*
  if errorlevel 1 pause
  exit /b
)
where py >nul 2>&1 && (
  py -3 run.py %*
) || (
  python run.py %*
)
if errorlevel 1 pause
