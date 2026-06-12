"""
Vision service — ส่งรูปให้ LLM อ่านโดยตรง ไม่ต้องพึ่ง Tesseract
รองรับ: ใบเสร็จ / บิล / สลิปโอนเงิน / เอกสารทั่วไป
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Groq vision model — ดูรายการได้ที่ console.groq.com
VISION_MODEL = os.getenv("VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def _encode_image(image_path: str) -> tuple[str, str]:
    """คืน (base64_string, content_type)"""
    path = Path(image_path)
    ext = path.suffix.lower()
    content_type = _CONTENT_TYPES.get(ext, "image/jpeg")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return b64, content_type


def _call_vision(image_path: str, prompt: str, max_tokens: int = 1024) -> str:
    """เรียก Vision LLM และคืนข้อความ"""
    b64, ctype = _encode_image(image_path)
    resp = _client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{ctype};base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def read_receipt(image_path: str) -> str:
    """อ่านและสรุปใบเสร็จ/บิล — คืนสรุปเป็นภาษาไทย"""
    import json
    from core.ocr_service import has_tesseract, extract_text_from_image
    from core.brain import MODEL

    # 1. อ่านข้อความจากภาพ (OCR)
    ocr_text = ""
    if has_tesseract():
        try:
            ocr_text = extract_text_from_image(image_path)
        except Exception:
            ocr_text = ""

    # Fallback to Vision LLM for OCR if Tesseract is not available or failed
    if not ocr_text:
        ocr_prompt = (
            "ถอดความหรืออ่านข้อความทั้งหมดที่ปรากฏในรูปภาพนี้อย่างละเอียดคำต่อคำ (OCR) "
            "โดยไม่ต้องสรุปหรือตีความใดๆ หากภาพไม่ชัดเจนหรือไม่มีข้อความภาษาไทย/อังกฤษที่อ่านออกได้ ให้ตอบเพียง 'UNREADABLE'"
        )
        try:
            ocr_text = _call_vision(image_path, ocr_prompt, max_tokens=1024)
        except Exception:
            ocr_text = ""

    if not ocr_text or "UNREADABLE" in ocr_text.upper() or len(ocr_text.strip()) < 10:
        return "ไม่สามารถอ่านได้ชัดเจน"

    # 2-4. ส่งข้อมูลเข้า LLM เพื่อดึงข้อมูลสำคัญและสรุปเป็นภาษาไทย
    prompt = f"""คุณคือ AI ผู้ช่วยวิเคราะห์ข้อมูลใบเสร็จจากข้อความ OCR
นี่คือข้อความที่สแกนได้จากรูปภาพใบเสร็จ:
\"\"\"
{ocr_text}
\"\"\"

กรุณาดำเนินการดังนี้:
1. ตรวจสอบว่าข้อความ OCR ดังกล่าวมีข้อมูลใบเสร็จที่ชัดเจนหรือไม่ หากข้อความไม่ชัดเจน อ่านไม่รู้เรื่อง เป็นตัวอักษรขยะ หรือไม่มีข้อมูลชื่อร้าน/ยอดรวมที่อ่านได้ชัดเจน ห้ามเดาข้อมูลเด็ดขาด ให้ตอบกลับเพียงคำว่า "ไม่สามารถอ่านได้ชัดเจน" เท่านั้น ห้ามมีข้อความอื่นเพิ่มเติม

2. หากข้อมูลชัดเจนเพียงพอ ให้ดึงข้อมูลสำคัญและแสดงผลลัพธ์ในรูปแบบภาษาไทยตามตัวอย่างนี้เป๊ะๆ:

ร้าน: [ชื่อร้านค้า]

รายการ:
- [รายการสินค้า 1] [ราคา] บาท
- [รายการสินค้า 2] [ราคา] บาท

รวม: [ยอดรวม] บาท

หมวดหมู่:
[วิเคราะห์หมวดหมู่ของใบเสร็จ เช่น อาหารและเครื่องดื่ม, ของใช้ในบ้าน, เดินทาง, อื่นๆ]

คำเตือน: ห้ามเดาข้อมูลหรือเติมแต่งข้อมูลที่ไม่มีในข้อความ OCR ถ้าส่วนใดไม่ชัดเจนจริงๆ ให้เว้นว่างหรือพิมพ์ว่า "ไม่สามารถอ่านได้ชัดเจน" ในส่วนนั้นๆ หรือหากทั้งใบไม่ชัดเจนให้ตอบ "ไม่สามารถอ่านได้ชัดเจน"
"""
    try:
        resp = _client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
        )
        summary = resp.choices[0].message.content.strip()
        return summary
    except Exception:
        return "ไม่สามารถอ่านได้ชัดเจน"


def read_slip(image_path: str) -> dict:
    """อ่านสลิปโอนเงิน — คืน dict {amount, bank, ref, datetime, raw}"""
    prompt = (
        "อ่านสลิปโอนเงินนี้แล้วดึงข้อมูลต่อไปนี้:\n"
        "AMOUNT: (ยอดเงิน ตัวเลขเท่านั้น)\n"
        "BANK: (ชื่อธนาคาร)\n"
        "REF: (เลขอ้างอิงหรือเลขธุรกรรม)\n"
        "DATE: (วันเวลา)\n"
        "ถ้าไม่พบให้ใส่ NONE\n"
        "ตอบตามรูปแบบนี้เท่านั้น ห้ามเพิ่มข้อความอื่น"
    )
    raw = _call_vision(image_path, prompt, max_tokens=200)

    def _extract(key: str) -> str:
        for line in raw.splitlines():
            if line.strip().upper().startswith(key + ":"):
                val = line.split(":", 1)[-1].strip()
                return val if val.upper() != "NONE" else ""
        return ""

    amount_str = _extract("AMOUNT").replace(",", "").replace("บาท", "").strip()
    try:
        amount = float(amount_str) if amount_str else 0.0
    except ValueError:
        amount = 0.0

    return {
        "amount": amount,
        "bank": _extract("BANK"),
        "ref": _extract("REF"),
        "datetime": _extract("DATE"),
        "raw": raw,
    }


def analyze_image(image_path: str, question: str = "") -> str:
    """วิเคราะห์รูปทั่วไป"""
    prompt = question or ("อธิบายสิ่งที่เห็นในรูปนี้เป็นภาษาไทย สรุปให้กระชับ")
    return _call_vision(image_path, prompt, max_tokens=600)


def is_vision_available() -> bool:
    """ตรวจว่า Vision LLM ใช้งานได้ไหม"""
    try:
        _client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=1,
        )
        return True
    except Exception:
        return False


def extract_raw_text(image_path: str) -> str:
    """ดึงข้อความดิบออกจากรูปภาพ (Tesseract First -> Vision LLM Fallback)"""
    from core.ocr_service import has_tesseract, extract_text_from_image
    
    ocr_text = ""
    if has_tesseract():
        try:
            ocr_text = extract_text_from_image(image_path)
        except Exception:
            ocr_text = ""

    if not ocr_text:
        ocr_prompt = (
            "ถอดความหรืออ่านข้อความทั้งหมดที่ปรากฏในรูปภาพนี้อย่างละเอียดคำต่อคำ (OCR) "
            "โดยไม่ต้องสรุปหรือตีความใดๆ หากภาพไม่ชัดเจนหรือไม่มีข้อความภาษาไทย/อังกฤษที่อ่านออกได้ ให้ตอบเพียง 'UNREADABLE'"
        )
        try:
            ocr_text = _call_vision(image_path, ocr_prompt, max_tokens=1024)
        except Exception:
            ocr_text = ""
            
    if not ocr_text or "UNREADABLE" in ocr_text.upper():
        return ""
    return ocr_text.strip()


def summarize_document_text(document_text: str, summary_type: str) -> str:
    """สรุปข้อความจากเอกสารตามโหมดที่กำหนด"""
    from core.brain import MODEL
    
    prompts = {
        "สรุปแบบสั้น": f"""คุณคือผู้ช่วยสรุปเอกสารมืออาชีพ

หน้าที่:
วิเคราะห์เนื้อหาเอกสารที่ผู้ใช้ส่งมา และสรุปเป็นภาษาไทยที่อ่านง่าย

กฎการตอบ:
1. สรุปเนื้อหาสำคัญสั้นกระชับไม่เกิน 5 บรรทัด
2. สรุปเฉพาะข้อมูลที่อยู่ในเอกสาร ห้ามแต่งข้อมูลเพิ่ม
3. หากข้อมูลไม่ชัดเจนให้ระบุว่า "ไม่สามารถอ่านได้ชัดเจน"
4. ตอบในรูปแบบ Markdown

รูปแบบผลลัพธ์:
📄 ชื่อเอกสาร: (ถ้าตรวจพบ)
📝 สรุปโดยย่อ: (เนื้อหาสำคัญไม่เกิน 5 บรรทัด)

เนื้อหาเอกสาร:
{document_text}""",

        "สรุปเพื่อสอบ": f"""คุณคือ AI ผู้ช่วยนักศึกษา

วิเคราะห์เอกสารและสรุปให้อ่านภายใน 1 นาที

กฎการตอบ:
1. ต้องตอบตามหัวข้อและรูปแบบที่ระบุไว้เท่านั้น
2. ห้ามคัดลอกข้อความยาวจากเอกสาร ให้สรุปด้วยภาษาของตนเอง
3. ห้ามแต่งข้อมูลที่ไม่มีในเอกสาร
4. ตอบในรูปแบบ Markdown

รูปแบบผลลัพธ์:
📚 หัวข้อเอกสาร
🎯 วัตถุประสงค์
📝 สรุปเนื้อหา
🔑 ประเด็นสำคัญ 5 ข้อ
💡 สิ่งที่ควรจำสำหรับสอบ
📌 บทสรุป

เนื้อหา:
{document_text}""",

        "สรุปเป็นข้อ": f"""คุณคือผู้ช่วยสรุปเอกสารมืออาชีพ

หน้าที่:
สรุปเนื้อหาสำคัญในเอกสารโดยแบ่งแยกเป็นหัวข้อและข้อกระชับ เข้าใจง่าย

กฎการตอบ:
1. ห้ามแต่งข้อมูลเพิ่ม
2. ลิสต์ประเด็นสำคัญเป็นข้อๆ อย่างชัดเจน
3. ใช้ภาษาเข้าใจง่าย
4. ตอบในรูปแบบ Markdown

รูปแบบผลลัพธ์:
📄 หัวข้อเอกสาร: (ถ้าตรวจพบ)
📝 ประเด็นหลัก:
• ...
• ...

เนื้อหาเอกสาร:
{document_text}""",

        "สรุปเป็น mind map": f"""คุณคือผู้ช่วยสรุปเอกสารมืออาชีพ

หน้าที่:
สรุปเนื้อหาของเอกสารในรูปแบบแผนผังความคิด (Mind Map) โครงสร้างข้อความเชื่อมโยงความสัมพันธ์

กฎการตอบ:
1. แสดงโครงสร้างหลัก-รอง-ย่อยให้เห็นภาพชัดเจนโดยใช้เครื่องหมายและย่อหน้า (เช่น # หัวข้อหลัก -> ## หัวข้อย่อย -> - รายละเอียด)
2. เชื่อมโยงความสัมพันธ์ของแนวคิด
3. ตอบในรูปแบบ Markdown

รูปแบบผลลัพธ์:
🧠 แผนผังความคิด (Mind Map)
- [หัวข้อหลัก]
  └─ [หัวข้อย่อย 1]
       ├─ [ประเด็นย่อย 1.1]
       └─ [ประเด็นย่อย 1.2]
  └─ ...

เนื้อหาเอกสาร:
{document_text}"""
    }
    
    default_prompt = f"""คุณคือผู้ช่วยสรุปเอกสารมืออาชีพ

หน้าที่:
วิเคราะห์เนื้อหาเอกสารที่ผู้ใช้ส่งมา และสรุปเป็นภาษาไทยที่อ่านง่าย

กฎการตอบ:
1. สรุปเฉพาะข้อมูลที่อยู่ในเอกสาร
2. ห้ามแต่งข้อมูลเพิ่ม
3. หากข้อมูลไม่ชัดเจนให้ระบุว่า "ไม่สามารถอ่านได้ชัดเจน"
4. ใช้ภาษาทางการ เข้าใจง่าย
5. หากเอกสารมีหลายหน้า ให้รวมข้อมูลทั้งหมดก่อนสรุป
6. หากเป็นรูปภาพที่ OCR มาแล้ว ให้ใช้ข้อความ OCR เป็นหลัก
7. ตอบในรูปแบบ Markdown

รูปแบบผลลัพธ์:
📄 ชื่อเอกสาร
(ถ้าตรวจพบ)

📝 สรุปโดยย่อ
สรุปเนื้อหาสำคัญไม่เกิน 5 บรรทัด

🔑 ประเด็นสำคัญ
• ...
• ...
• ...
• ...

📌 ข้อมูลที่ควรรู้
• ...
• ...
• ...

✅ ข้อสรุป
สรุปใจความสำคัญของเอกสาร 1-3 ประโยค

เนื้อหาเอกสาร:
{document_text}"""

    # Normalize summary_type just in case
    st_normalized = summary_type.strip().lower()
    prompt = prompts.get(st_normalized, default_prompt)
    if st_normalized == "สรุปแบบละเอียด":
        prompt = default_prompt
    
    try:
        resp = _client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"ไม่สามารถสรุปได้เนื่องจากเกิดข้อผิดพลาด: {e}"
