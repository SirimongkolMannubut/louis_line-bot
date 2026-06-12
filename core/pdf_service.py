from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PIL import Image, ImageOps

A4_WIDTH = 1240
A4_HEIGHT = 1754
PAGE_MARGIN = 60


def build_pdf_from_images(image_paths: Iterable[str], output_path: str) -> str:
    pages: list[Image.Image] = []

    for image_path in image_paths:
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((A4_WIDTH - PAGE_MARGIN * 2, A4_HEIGHT - PAGE_MARGIN * 2))

        page = Image.new("RGB", (A4_WIDTH, A4_HEIGHT), "white")
        x = (A4_WIDTH - img.width) // 2
        y = (A4_HEIGHT - img.height) // 2
        page.paste(img, (x, y))
        pages.append(page)

    if not pages:
        raise ValueError("No images were provided for PDF generation.")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(
        output, "PDF", resolution=150.0, save_all=True, append_images=pages[1:]
    )
    return str(output)
