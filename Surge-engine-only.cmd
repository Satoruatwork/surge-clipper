@echo off
rem Runs the engine without a window (open http://127.0.0.1:8765 in a browser). Ctrl+C stops it.
cd /d "%~dp0backend"
".venv\Scripts\python.exe" -m surge.main --server
