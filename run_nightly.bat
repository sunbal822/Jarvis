@echo off
rem Jarvis nightly run. Meant to be started by Windows Task Scheduler.
rem Record your voice note, save it as tonight.ogg in this folder, go to sleep.

cd /d "%~dp0"

if not exist tonight.ogg (
  echo No tonight.ogg found. Nothing to do.>> nightly_log.txt
  exit /b 0
)

echo ===== %date% %time% =====>> nightly_log.txt
python run_overnight.py tonight.ogg >> nightly_log.txt 2>&1

rem Keep last night's note so tomorrow's run doesn't redo it
move /y tonight.ogg last_night.ogg >nul
