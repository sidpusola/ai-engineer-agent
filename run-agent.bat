@echo off
REM Run the AI Software Engineer Agent.
REM Usage:  run-agent.bat "write a program that prints the first 20 primes"
cd /d "%~dp0"
"%USERPROFILE%\miniconda3\envs\tf-2.10\python.exe" agent.py %*
pause
