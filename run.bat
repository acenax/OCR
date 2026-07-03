@echo off
chcp 65001 >nul
REM เปิด TMC AI OCR PROGRAM
cd /d "%~dp0"
python main.py
if errorlevel 1 pause
