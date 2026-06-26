from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps
from pytesseract import TesseractNotFoundError

TESSERACT_CMD = os.getenv("TESSERACT_CMD", "").strip()
OCR_LANG = os.getenv("OCR_LANG", "tha+eng").strip() or "tha+eng"

if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


class OCRUnavailableError(RuntimeError):
    pass


def _check_tesseract() -> tuple[bool, str]:
    """ตรวจสถานะ Tesseract และคืน (available, version_or_error)"""
    try:
        ver = pytesseract.get_tesseract_version()
        return True, str(ver)
    except TesseractNotFoundError:
        cmd = shutil.which("tesseract")
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
            try:
                ver = pytesseract.get_tesseract_version()
                return True, str(ver)
            except Exception:
                pass
        return False, "Tesseract not found in PATH"
    except Exception as exc:
        return False, str(exc)


# ตรวจตอน import module
_TESSERACT_OK, _TESSERACT_INFO = _check_tesseract()
if _TESSERACT_OK:
    print(f"[OCR] [OK] Tesseract ready: v{_TESSERACT_INFO}  lang={OCR_LANG}")
else:
    print(f"[OCR] [WARNING] Tesseract NOT found: {_TESSERACT_INFO}")
    print("[OCR] OCR features will be unavailable until Tesseract is installed.")



def extract_text_from_images(image_paths: list[str]) -> str:
    texts: list[str] = []
    for image_path in image_paths:
        text = ""
        if _TESSERACT_OK:
            try:
                text = extract_text_from_image(image_path)
            except Exception as e:
                print(f"[OCR] Tesseract error: {e}")
                text = ""
        
        if not text:
            # Fallback to Vision LLM
            try:
                from core.vision_service import extract_raw_text
                text = extract_raw_text(image_path)
            except Exception as e:
                print(f"[OCR] Vision fallback error: {e}")
                text = ""
        if text:
            texts.append(text)
    return "\n\n".join(texts).strip()



def extract_text_from_image(image_path: str) -> str:
    try:
        image = Image.open(image_path)
        image = ImageOps.exif_transpose(image).convert("L")
        text = pytesseract.image_to_string(image, lang=OCR_LANG)
        return normalize_ocr_text(text)
    except TesseractNotFoundError as exc:
        raise OCRUnavailableError("Tesseract OCR ยังไม่ได้ติดตั้งใน server ครับ") from exc


def normalize_ocr_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    cleaned = [line for line in lines if line]
    return "\n".join(cleaned)


def has_tesseract() -> bool:
    return _TESSERACT_OK
