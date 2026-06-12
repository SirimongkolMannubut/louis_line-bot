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
    prompt = (
        "คุณเป็นผู้ช่วยอ่านเอกสารการเงิน\n"
        "อ่านใบเสร็จ/บิลนี้แล้วสรุปเป็นภาษาไทยให้ครบ:\n"
        "1. ร้านค้า / หน่วยงาน\n"
        "2. วันที่\n"
        "3. รายการสินค้า/บริการ (ชื่อ จำนวน ราคา)\n"
        "4. ยอดรวม\n"
        "5. ข้อมูลอื่นที่สำคัญ (เช่น เลข Tax ID, เลขที่ใบเสร็จ)\n\n"
        "ถ้าอ่านข้อมูลใดไม่ได้ให้บอกว่า 'ไม่ชัดเจน'\n"
        "ตอบเป็นภาษาไทย กระชับ อ่านง่าย"
    )
    return _call_vision(image_path, prompt, max_tokens=800)


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
