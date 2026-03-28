@echo off
cd /d "%~dp0"
call ..\.venv\Scripts\activate 2>nul
python The_Eclipse.py
pause
