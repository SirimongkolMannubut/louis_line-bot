FROM python:3.11-slim

# ── ติดตั้ง Tesseract + ภาษาไทย + ภาษาอังกฤษ ──────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-tha \
    tesseract-ocr-eng \
    libtesseract-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ── ตรวจสอบว่าติดตั้งสำเร็จ (build จะหยุดถ้า error) ──────────────────────
RUN tesseract --version && tesseract --list-langs

# ── สร้าง working directory ─────────────────────────────────────────────────
WORKDIR /app

# ── ติดตั้ง Python packages ──────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── copy โค้ด ────────────────────────────────────────────────────────────────
COPY . .

# ── สร้าง directories ที่จำเป็น ──────────────────────────────────────────────
RUN mkdir -p storage/uploads storage/generated memory

# ── รัน server ───────────────────────────────────────────────────────────────
CMD uvicorn line_bot:app --host 0.0.0.0 --port ${PORT:-8000}
