# วิธีนำโปรแกรมไปลง/ทดสอบบนเครื่องอื่น (Windows)

ทุกกรณีเครื่องปลายทางต้องมี **Python 3.10+** (โปรแกรมเขียนด้วย Python)

---

## สิ่งที่ต้องก็อปไป
ก็อป **ทั้งโฟลเดอร์ `TMC INVOICE INPUT`** ไปเครื่องใหม่ (จะได้ครบ):
```
TMC INVOICE INPUT\
├── TMC_OCR\        ← ตัวโปรแกรม + templates\ (โปรไฟล์ที่สอนไว้)
├── poppler\        ← ตัวแปลง PDF (พกพา ไม่ต้องติดตั้ง)
├── DATA\           ← คลังสินค้า.xlsx
├── CMT\ NIPPON\ ... ← โฟลเดอร์ลูกค้า (ถ้าต้องการข้อมูลเดิม)
```

---

## วิธีที่ 1 — ติดตั้งปกติ (แนะนำ)

1. **ติดตั้ง Python 3.10+** จาก python.org → ตอนติดตั้ง **ติ๊ก “Add Python to PATH”**
2. **ติดตั้ง Tesseract OCR** (แนะนำ UB Mannheim build: `tesseract-ocr-w64-setup-*.exe`)
   - ตอนติดตั้งเลือกภาษา **Thai** ด้วย (หรือก็อปไฟล์ `tha.traineddata` ไปไว้ใน
     `...\Tesseract-OCR\tessdata\`)
3. ก็อปโฟลเดอร์ `TMC INVOICE INPUT` ไปเครื่องใหม่
4. **ลบไฟล์ `TMC_OCR\settings.json`** (ถ้ามี) เพื่อให้โปรแกรมค้นหา path ใหม่ของเครื่องนี้เอง
5. ดับเบิลคลิก **`TMC_OCR\install.bat`** (ติดตั้งไลบรารี Python)
6. เช็กความพร้อม: เปิด PowerShell ในโฟลเดอร์ `TMC_OCR` แล้วรัน `python check_env.py`
   - ต้องขึ้น `[ OK ]` ทุกบรรทัด (ถ้ามี `[FAIL]` ให้แก้ตามที่บอก)
7. ดับเบิลคลิก **`run.bat`**
8. ถ้า path ไหนไม่ตรง ให้แก้ในโปรแกรม **แท็บ 4) ตั้งค่า** (Tesseract / Poppler / โฟลเดอร์หลัก /
   ไฟล์คลังสินค้า) แล้วกดบันทึก

---

## วิธีที่ 2 — พกพา ไม่อยากติดตั้ง Tesseract

1. ที่เครื่องเดิม ก็อปโฟลเดอร์ Tesseract ทั้งอัน จาก
   `%LOCALAPPDATA%\Programs\Tesseract-OCR`
   ไปวางเป็น **`TMC INVOICE INPUT\Tesseract-OCR\`** (โปรแกรมจะค้นหาเจอเอง)
   - ตรวจว่ามี `tessdata\eng.traineddata` และ `tessdata\tha.traineddata`
2. Poppler พกพามาในโปรเจกต์อยู่แล้ว (ไม่ต้องทำอะไร)
3. ยังต้องติดตั้ง **Python** + รัน `install.bat`
4. ลบ `settings.json`, รัน `python check_env.py`, แล้ว `run.bat`

---

## ปัญหาที่พบบ่อย

| อาการ | วิธีแก้ |
|-------|---------|
| ขึ้น `[FAIL] ไลบรารี ...` | รัน `install.bat` (ต้องต่อเน็ต) |
| ขึ้น `ไม่พบ Tesseract` | ติดตั้ง Tesseract หรือแก้ path ในแท็บตั้งค่า |
| อ่านภาษาไทยไม่ออก | ยังไม่มี `tha.traineddata` ในโฟลเดอร์ tessdata |
| path ไฟล์คลัง/ลูกค้าผิด | แก้ในแท็บ 4) ตั้งค่า แล้วบันทึก |
| ตัวหนังสือใน cmd เป็นภาษาต่างดาว | แค่การแสดงผล ไม่ใช่ error (run.bat ตั้ง `chcp 65001` ให้แล้ว) |

> อยากได้เป็นไฟล์ `.exe` รันได้เลยโดยไม่ต้องมี Python บนเครื่องปลายทาง?
> ทำได้ด้วย PyInstaller (ต้องแนบ Tesseract + Poppler ไปด้วย) — แจ้งได้ถ้าต้องการ
