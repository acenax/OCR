@echo off
cd /d %~dp0
call .venv_paddle27\Scripts\activate.bat
python main.py
pause
