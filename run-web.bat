@echo off
REM Launch the AI Software Engineer Agent web UI (live pipeline).
cd /d "%~dp0"
echo Starting agent web UI...
"%USERPROFILE%\miniconda3\envs\tf-2.10\python.exe" web.py
pause
