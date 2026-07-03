@echo off
chcp 65001 >nul
REM สร้างไฟล์ .exe (ต้องรันบนเครื่องที่มี Python + ติดตั้ง Tesseract แล้ว)
cd /d "%~dp0"
echo ==== ติดตั้ง PyInstaller (ถ้ายังไม่มี) ====
python -m pip install --quiet pyinstaller
echo ==== กำลัง build (ใช้เวลาสักครู่) ====
python -m PyInstaller --noconfirm --clean TMC_OCR.spec
echo.
echo ==== เสร็จแล้ว! ผลลัพธ์อยู่ที่โฟลเดอร์  dist\TMC_OCR\  ====
echo ก็อปทั้งโฟลเดอร์ dist\TMC_OCR ไปเครื่องปลายทางได้เลย (ไม่ต้องลง Python/Tesseract)
pause
