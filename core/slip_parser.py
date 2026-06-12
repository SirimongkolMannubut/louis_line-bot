from __future__ import annotations

import re


def parse_slip(text: str) -> dict:
    """Extract amount, bank, ref, datetime from OCR text."""
    return {
        "amount":   _extract_amount(text),
        "bank":     _extract_bank(text),
        "ref":      _extract_ref(text),
        "datetime": _extract_datetime(text),
    }


def _extract_amount(text: str) -> float | None:
    patterns = [
        r"(?:จำนวน|ยอด|amount|total)[^\d]*(\d[\d,]+\.?\d*)",
        r"(\d[\d,]+\.\d{2})\s*(?:บาท|THB|฿)",
        r"(\d[\d,]+)\s*(?:บาท|THB|฿)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def _extract_bank(text: str) -> str:
    banks = {
        "kbank": "KBank", "กสิกร": "KBank",
        "scb": "SCB", "ไทยพาณิชย์": "SCB",
        "ktb": "KTB", "กรุงไทย": "KTB",
        "bbl": "BBL", "กรุงเทพ": "BBL",
        "bay": "BAY", "กรุงศรี": "BAY",
        "ttb": "TTB", "ทหารไทย": "TTB",
        "gsb": "GSB", "ออมสิน": "GSB",
        "promptpay": "PromptPay", "พร้อมเพย์": "PromptPay",
    }
    lower = text.lower()
    for key, name in banks.items():
        if key in lower:
            return name
    return "ไม่ระบุ"


def _extract_ref(text: str) -> str:
    patterns = [
        r"(?:ref|เลขอ้างอิง|transaction|รายการ)[^\d]*(\d{6,})",
        r"(\d{15,})",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def _extract_datetime(text: str) -> str:
    patterns = [
        r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})\s+(\d{1,2}:\d{2}(?::\d{2})?)",
        r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return " ".join(m.groups())
    return ""
