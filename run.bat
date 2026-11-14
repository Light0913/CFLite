@echo off
cd /d "%~dp0"
:loop
python main.py
if errorlevel 42 if not errorlevel 43 goto loop
pause
