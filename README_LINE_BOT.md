# LouisAI LINE Bot

LINE Bot เวอร์ชันนี้รองรับ 2 โหมดหลัก:

## โหมด 1: รวมรูปเป็น PDF

1. ผู้ใช้พิมพ์ `ทำ PDF`
2. บอทขอให้ส่งรูป
3. ผู้ใช้ส่งรูปได้หลายรูป
4. ผู้ใช้พิมพ์ `เสร็จแล้ว`
5. บอทถามชื่อไฟล์
6. บอทสร้าง PDF และส่งลิงก์ดาวน์โหลดกลับ

## โหมด 2: OCR + สรุปเอกสาร + ทำ PDF

1. ผู้ใช้พิมพ์ `สรุปใบเสร็จ` หรือ `อ่านบิล`
2. บอทขอให้ส่งรูป
3. ผู้ใช้ส่งรูปได้หลายรูป
4. ผู้ใช้พิมพ์ `เสร็จแล้ว`
5. บอทถามชื่อไฟล์
6. บอทอ่านข้อความจากภาพ, สรุปข้อมูลด้วย AI, แล้วสร้าง PDF ส่งกลับ

## ต้องตั้งค่า `.env`

อย่าใส่ secret ลงในโค้ด ให้ใส่ในไฟล์ `.env` เท่านั้น

```env
GROQ_API_KEY=your_groq_api_key
LINE_CHANNEL_SECRET=your_line_channel_secret
LINE_CHANNEL_ACCESS_TOKEN=your_line_channel_access_token
PUBLIC_BASE_URL=https://your-public-domain
BOT_NAME=LouisAI PDF Bot
TESSERACT_CMD=C:\\Program Files\\Tesseract-OCR\\tesseract.exe
OCR_LANG=tha+eng
```

> `PUBLIC_BASE_URL` ควรเป็น URL ที่ LINE เรียกถึงได้จริง เช่นโดเมนบนเซิร์ฟเวอร์หรือ URL จาก `ngrok`/`cloudflared`
>
> ถ้าจะใช้โหมด OCR ต้องติดตั้งโปรแกรม Tesseract OCR ในเครื่องด้วย แล้วตั้ง `TESSERACT_CMD` ให้ถูกต้อง

## ติดตั้งแพ็กเกจ

```bash
pip install -r requirements.txt
```

## รันเซิร์ฟเวอร์

```bash
uvicorn line_bot:app --host 0.0.0.0 --port 8000
```

## ตั้งค่าใน LINE Developers Console

Webhook URL:

```text
https://your-public-domain/webhook/line
```

เปิดใช้งาน webhook แล้วทดสอบส่งข้อความใน LINE

## คำสั่งที่ผู้ใช้พิมพ์ได้

- `ทำ PDF`
- `สรุปใบเสร็จ`
- `อ่านบิล`
- `เสร็จแล้ว`
- `ยกเลิก`

## พฤติกรรมปัจจุบัน

- ถ้าไม่ได้อยู่ในโหมดสร้าง PDF/สรุปเอกสาร: ข้อความทั่วไปจะถูกส่งไปถาม AI เดิม
- โหมด `ทำ PDF`: บอทจะเก็บรูปทีละรูปจนกว่าจะพิมพ์ `เสร็จแล้ว`
- โหมด `สรุปใบเสร็จ` / `อ่านบิล`: บอทจะ OCR ข้อความจากรูป แล้วให้ AI สรุปก่อนส่งผลลัพธ์กลับพร้อม PDF
- เมื่อผู้ใช้ตั้งชื่อไฟล์: ระบบจะสร้าง PDF จากรูปทั้งหมดและเปิดให้ดาวน์โหลดผ่าน `/files/...`

## หมายเหตุสำคัญ

- โหมด OCR ใช้ `pytesseract` + โปรแกรม `Tesseract OCR` ที่ต้องติดตั้งในเครื่องหรือเซิร์ฟเวอร์เอง
- ถ้าไม่มี Tesseract ระบบจะยังสร้าง PDF ได้ แต่จะสรุปข้อความจากภาพไม่ได้
- หากต้องการใช้งานจริง ควร deploy ไปยังเซิร์ฟเวอร์สาธารณะและตั้ง reverse proxy/HTTPS ให้เรียบร้อย
