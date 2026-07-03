@echo off
chcp 65001 >nul
REM ติดตั้งไลบรารีที่จำเป็นสำหรับ TMC AI OCR PROGRAM
cd /d "%~dp0"
echo ==== Installing Python packages ====
python -m pip install -r requirements.txt
echo.
echo ==== Done. ถ้าไม่มี error ให้รัน run.bat เพื่อเปิดโปรแกรม ====
pause
