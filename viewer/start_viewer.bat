@echo off
setlocal
cd /d "%~dp0"
py launch_viewer.py
if errorlevel 1 (
  echo The py launcher failed. Trying python...
  python launch_viewer.py
)
if errorlevel 1 pause
