from __future__ import annotations

import os
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


def extract_text_from_images(image_paths: list[str]) -> str:
    texts: list[str] = []

    for image_path in image_paths:
        text = extract_text_from_image(image_path)
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
        raise OCRUnavailableError(
            "ไม่พบโปรแกรม Tesseract OCR ในเครื่อง กรุณาติดตั้ง Tesseract และตั้งค่า TESSERACT_CMD ใน .env"
        ) from exc


def normalize_ocr_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    cleaned = [line for line in lines if line]
    return "\n".join(cleaned)


def has_tesseract() -> bool:
    import shutil
    cmd = pytesseract.pytesseract.tesseract_cmd
    if cmd and cmd != "tesseract":
        return Path(cmd).exists()
    return shutil.which("tesseract") is not None
