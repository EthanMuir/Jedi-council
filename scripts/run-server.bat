@echo off
REM Runs The High Council's web UI bound to every network interface (not
REM just localhost), so other devices on your home network can reach it at
REM http://<this-machine's-LAN-IP>:8000 -- find that IP with `ipconfig`.
REM No --reload here on purpose: that flag is a dev convenience (restarts on
REM file changes) and has no place in an always-on run.
cd /d "%~dp0.."
.venv\Scripts\python.exe -m uvicorn council.api.main:app --host 0.0.0.0 --port 8000
