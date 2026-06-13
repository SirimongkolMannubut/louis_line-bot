from __future__ import annotations

import textwrap
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

A4_WIDTH = 1240
A4_HEIGHT = 1754
MARGIN = 80
FONT_LARGE = 52
FONT_MED = 40
FONT_SMALL = 34

_THAI_FONT_PATHS = [
    "/usr/share/fonts/truetype/thai/TlwgMono.ttf",
    "/usr/share/fonts/truetype/thai/TlwgTypewriter.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _THAI_FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _draw_text_page(lines: list[tuple[str, int, str]]) -> Image.Image:
    """
    lines: list of (text, font_size, color)
    คืน PIL Image หน้า A4 ที่มีข้อความ
    """
    page = Image.new("RGB", (A4_WIDTH, A4_HEIGHT), "white")
    draw = ImageDraw.Draw(page)
    y = MARGIN

    for text, size, color in lines:
        font = _get_font(size)
        # word wrap สำหรับข้อความยาว
        wrapped = textwrap.wrap(
            text, width=max(1, (A4_WIDTH - MARGIN * 2) // max(1, size // 2))
        )
        if not wrapped:
            wrapped = [text]
        for wline in wrapped:
            draw.text((MARGIN, y), wline, font=font, fill=color)
            y += size + 12
            if y > A4_HEIGHT - MARGIN:
                break
        y += 8  # spacing between items
    return page


def build_pdf_from_images(image_paths: Iterable[str], output_path: str) -> str:
    """รวมรูปเป็น PDF (ขนาดเท่ารูปจริง ไม่มีขอบขาว)"""
    pages: list[Image.Image] = []
    for image_path in image_paths:
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img).convert("RGB")
        pages.append(img)

    if not pages:
        raise ValueError("No images provided.")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(
        output, "PDF", resolution=150.0, save_all=True, append_images=pages[1:]
    )
    return str(output)


def build_slip_report_pdf(
    slip_entries: list[dict],
    image_paths: list[str],
    output_path: str,
) -> str:
    """
    สร้างรายงาน PDF สลิปโอนเงิน
    slip_entries: [{bank, amount, date, ref}]
    image_paths: รายการ path รูปสลิปตาม index
    """
    total = sum(e.get("amount", 0) for e in slip_entries if e.get("amount"))
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    pages: list[Image.Image] = []

    # ── หน้า 1: Cover ──────────────────────────────────────────────────────────
    cover_lines: list[tuple[str, int, str]] = [
        ("รายงานสลิปโอนเงิน", FONT_LARGE, "#1a1a2e"),
        ("", FONT_SMALL, "#000000"),
        (f"วันที่พิมพ์: {now_str}", FONT_MED, "#333333"),
        (f"จำนวนสลิป: {len(slip_entries)} รายการ", FONT_MED, "#333333"),
        ("", FONT_SMALL, "#000000"),
        ("─" * 40, FONT_SMALL, "#888888"),
        ("", FONT_SMALL, "#000000"),
    ]
    for i, e in enumerate(slip_entries, 1):
        amt = e.get("amount", 0)
        bank = e.get("bank", "-")
        date = e.get("date", "")
        line = f"  {i}.  {bank}  {f'{amt:,.0f} บาท' if amt else '-'}"
        if date:
            line += f"  ({date})"
        cover_lines.append((line, FONT_MED, "#222222"))

    cover_lines += [
        ("", FONT_SMALL, "#000000"),
        ("─" * 40, FONT_SMALL, "#888888"),
        ("", FONT_SMALL, "#000000"),
        (f"ยอดรวมทั้งหมด: {total:,.2f} บาท", FONT_LARGE, "#1a1a2e"),
    ]
    pages.append(_draw_text_page(cover_lines))

    # ── หน้าถัดไป: รูปสลิปแต่ละใบ ─────────────────────────────────────────────
    for i, (entry, img_path) in enumerate(zip(slip_entries, image_paths), 1):
        bank = entry.get("bank", "-")
        amt = entry.get("amount", 0)
        date = entry.get("date", "")
        ref = entry.get("ref", "")

        # Header text area
        header_lines: list[tuple[str, int, str]] = [
            (f"รายการที่ {i}", FONT_MED, "#1a1a2e"),
            (f"ธนาคาร: {bank}", FONT_SMALL, "#333333"),
            (f"ยอด: {f'{amt:,.2f} บาท' if amt else '-'}", FONT_MED, "#1a1a2e"),
        ]
        if date:
            header_lines.append((f"วันที่: {date}", FONT_SMALL, "#333333"))
        if ref:
            header_lines.append((f"Ref: {ref}", FONT_SMALL, "#666666"))

        header_page = _draw_text_page(header_lines)
        header_h = MARGIN + (len(header_lines) + 1) * (FONT_SMALL + 20)

        # วางรูปสลิปด้านล่าง header
        try:
            slip_img = Image.open(img_path)
            slip_img = ImageOps.exif_transpose(slip_img).convert("RGB")
            max_w = A4_WIDTH - MARGIN * 2
            max_h = A4_HEIGHT - header_h - MARGIN
            slip_img.thumbnail((max_w, max_h))
            x = (A4_WIDTH - slip_img.width) // 2
            header_page.paste(slip_img, (x, header_h))
        except Exception:
            pass

        pages.append(header_page)

    # ── Save ───────────────────────────────────────────────────────────────────
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(
        output, "PDF", resolution=150.0, save_all=True, append_images=pages[1:]
    )
    return str(output)


def build_receipt_report_pdf(
    receipt_summaries: list[str],
    image_paths: list[str],
    output_path: str,
) -> str:
    """
    สร้างรายงาน PDF ใบเสร็จ/บิล
    receipt_summaries: สรุปแต่ละใบ
    image_paths: รูปต้นฉบับ
    """
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    pages: list[Image.Image] = []

    # ── Cover ──────────────────────────────────────────────────────────────────
    cover_lines: list[tuple[str, int, str]] = [
        ("รายงานใบเสร็จ/บิล", FONT_LARGE, "#1a1a2e"),
        (f"วันที่พิมพ์: {now_str}", FONT_MED, "#333333"),
        (f"จำนวนเอกสาร: {len(image_paths)} รายการ", FONT_MED, "#333333"),
    ]
    pages.append(_draw_text_page(cover_lines))

    # ── ใบเสร็จแต่ละใบ ─────────────────────────────────────────────────────────
    for i, (summary, img_path) in enumerate(zip(receipt_summaries, image_paths), 1):
        # Header: สรุปที่ Vision LLM อ่านได้
        header_lines: list[tuple[str, int, str]] = [
            (f"เอกสารที่ {i}", FONT_MED, "#1a1a2e"),
            ("─" * 30, FONT_SMALL, "#888888"),
        ]
        for line in summary.splitlines():
            if line.strip():
                header_lines.append((line.strip(), FONT_SMALL, "#222222"))

        header_h = MARGIN + (len(header_lines) + 2) * (FONT_SMALL + 16)
        page = _draw_text_page(header_lines)

        try:
            doc_img = Image.open(img_path)
            doc_img = ImageOps.exif_transpose(doc_img).convert("RGB")
            max_w = A4_WIDTH - MARGIN * 2
            max_h = A4_HEIGHT - header_h - MARGIN
            doc_img.thumbnail((max_w, max_h))
            x = (A4_WIDTH - doc_img.width) // 2
            page.paste(doc_img, (x, header_h))
        except Exception:
            pass

        pages.append(page)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(
        output, "PDF", resolution=150.0, save_all=True, append_images=pages[1:]
    )
    return str(output)
