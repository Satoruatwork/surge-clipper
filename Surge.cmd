@echo off
rem Starts Surge in its own window. Close the window to stop the engine.
cd /d "%~dp0backend"
start "" ".venv\Scripts\pythonw.exe" -m surge.main
