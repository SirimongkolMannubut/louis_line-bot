from __future__ import annotations

import base64
import hashlib
import hmac
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from core.brain import ask_ai
from core.line_pdf_sessions import (
    add_image,
    clear_session,
    get_session,
    set_waiting_for_filename,
    start_pdf_flow,
)
from core.ocr_service import OCRUnavailableError, extract_text_from_images
from core.pdf_service import build_pdf_from_images

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
GENERATED_DIR = BASE_DIR / "storage" / "generated"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
BOT_NAME = os.getenv("BOT_NAME", "LouisAI PDF Bot")

LINE_REPLY_ENDPOINT = "https://api.line.me/v2/bot/message/reply"
LINE_CONTENT_ENDPOINT = "https://api-data.line.me/v2/bot/message/{message_id}/content"

PDF_COMMANDS = {
    "ทำ pdf",
    "ทำpdf",
    "รวมรูปเป็น pdf",
    "รวมรูปเป็นpdf",
    "แปลงรูปเป็น pdf",
    "แปลงรูปเป็นpdf",
}
OCR_PDF_COMMANDS = {
    "สรุปใบเสร็จ",
    "อ่านใบเสร็จ",
    "อ่านบิล",
    "สรุปบิล",
    "สรุปเอกสาร",
    "อ่านเอกสาร",
    "ocr pdf",
    "ocr",
}
CANCEL_COMMANDS = {"ยกเลิก", "cancel", "เริ่มใหม่"}
DONE_COMMANDS = {"เสร็จแล้ว", "ครบแล้ว", "สร้าง pdf", "สร้างpdf"}

app = FastAPI(title="LouisAI LINE Bot")
app.mount("/files", StaticFiles(directory=str(GENERATED_DIR)), name="files")


@app.get("/")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": BOT_NAME}


@app.post("/webhook/line")
async def line_webhook(
    request: Request, x_line_signature: str = Header(default="")
) -> dict[str, str]:
    if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
        raise HTTPException(
            status_code=500, detail="LINE environment variables are not configured."
        )

    body = await request.body()
    if not verify_line_signature(body, x_line_signature, LINE_CHANNEL_SECRET):
        raise HTTPException(status_code=401, detail="Invalid LINE signature.")

    payload = await request.json()
    for event in payload.get("events", []):
        handle_event(event, request)

    return {"status": "ok"}


def verify_line_signature(body: bytes, signature: str, secret: str) -> bool:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def handle_event(event: dict[str, Any], request: Request) -> None:
    event_type = event.get("type")
    reply_token = event.get("replyToken")

    if event_type == "follow" and reply_token:
        reply_text(reply_token, build_welcome_message())
        return

    if event_type != "message" or not reply_token:
        return

    source = event.get("source", {})
    session_key = get_session_key(source)
    message = event.get("message", {})
    message_type = message.get("type")

    if message_type == "text":
        handle_text_message(reply_token, session_key, message.get("text", ""), request)
    elif message_type == "image":
        handle_image_message(reply_token, session_key, message.get("id", ""))
    else:
        reply_text(reply_token, build_help_message())


def handle_text_message(
    reply_token: str, session_key: str, text: str, request: Request
) -> None:
    raw_text = text.strip()
    normalized = normalize_text(raw_text)
    session = get_session(session_key)
    state = session.get("state", "idle")
    mode = session.get("mode", "pdf")

    if normalized in CANCEL_COMMANDS:
        old_session = clear_session(session_key)
        cleanup_images(old_session.get("images", []))
        reply_text(
            reply_token,
            "ยกเลิกงานเดิมเรียบร้อยแล้วครับ\nหากต้องการเริ่มใหม่ พิมพ์ 'ทำ PDF' หรือ 'สรุปใบเสร็จ' ได้เลย",
        )
        return

    if normalized in PDF_COMMANDS:
        restart_flow(reply_token, session_key, mode="pdf")
        return

    if normalized in OCR_PDF_COMMANDS:
        restart_flow(reply_token, session_key, mode="ocr_summary_pdf")
        return

    if normalized in {"ช่วยเหลือ", "help", "เมนู"}:
        reply_text(reply_token, build_help_message())
        return

    if state == "waiting_for_images":
        if normalized in DONE_COMMANDS:
            images = session.get("images", [])
            if not images:
                reply_text(
                    reply_token,
                    "ยังไม่มีรูปในงานนี้ครับ กรุณาส่งรูปอย่างน้อย 1 รูปก่อน แล้วค่อยพิมพ์ 'เสร็จแล้ว'",
                )
                return
            set_waiting_for_filename(session_key)
            reply_text(
                reply_token,
                "รับรูปครบแล้วครับ\nกรุณาตั้งชื่อไฟล์ PDF ที่ต้องการได้เลย เช่น เอกสารสมัครงาน หรือ ใบเสร็จ-มิถุนายน",
            )
            return

        reply_text(
            reply_token,
            waiting_for_images_message(mode),
        )
        return

    if state == "waiting_for_filename":
        safe_name = sanitize_filename(raw_text)
        if not safe_name:
            reply_text(
                reply_token,
                "ชื่อไฟล์ยังใช้ไม่ได้ครับ กรุณาตั้งชื่อใหม่โดยใช้ตัวอักษร ตัวเลข เว้นวรรค หรือขีดกลาง",
            )
            return

        images = session.get("images", [])
        if not images:
            clear_session(session_key)
            reply_text(
                reply_token,
                "ไม่พบรูปสำหรับสร้าง PDF แล้วครับ กรุณาพิมพ์ 'ทำ PDF' หรือ 'สรุปใบเสร็จ' เพื่อเริ่มใหม่",
            )
            return

        pdf_filename = f"{safe_name}-{uuid.uuid4().hex[:8]}.pdf"
        output_path = GENERATED_DIR / pdf_filename

        try:
            build_pdf_from_images(images, str(output_path))
            file_url = build_file_url(request, pdf_filename)
            result_message = build_success_message(mode, safe_name, file_url, images)
            clear_session(session_key)
            cleanup_images(images)
            reply_text(reply_token, result_message)
        except Exception as exc:
            reply_text(reply_token, f"ขออภัยครับ เกิดปัญหาระหว่างสร้าง PDF: {exc}")
        return

    ai_reply = ask_ai(raw_text)
    reply_text(reply_token, ai_reply)


def handle_image_message(reply_token: str, session_key: str, message_id: str) -> None:
    session = get_session(session_key)
    state = session.get("state", "idle")
    mode = session.get("mode", "pdf")

    if state != "waiting_for_images":
        reply_text(
            reply_token,
            "หากต้องการรวมรูปเป็น PDF ให้พิมพ์ 'ทำ PDF' ก่อนครับ\nถ้าต้องการอ่านบิล/ใบเสร็จแล้วสรุปก่อนทำ PDF ให้พิมพ์ 'สรุปใบเสร็จ'",
        )
        return

    if not message_id:
        reply_text(reply_token, "ไม่พบรหัสรูปภาพจาก LINE ครับ ลองส่งใหม่อีกครั้งได้เลย")
        return

    content, content_type = download_line_message_content(message_id)
    ext = guess_extension(content_type)
    user_dir = UPLOAD_DIR / session_key.replace(":", "_")
    user_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{ext}"
    image_path = user_dir / filename
    image_path.write_bytes(content)

    updated_session = add_image(session_key, str(image_path))
    count = len(updated_session.get("images", []))
    reply_text(reply_token, image_received_message(mode, count))


def restart_flow(reply_token: str, session_key: str, mode: str) -> None:
    old_session = clear_session(session_key)
    cleanup_images(old_session.get("images", []))
    start_pdf_flow(session_key, mode=mode)
    reply_text(reply_token, start_flow_message(mode))


def start_flow_message(mode: str) -> str:
    if mode == "ocr_summary_pdf":
        return (
            "ได้เลยครับ โหมดนี้จะอ่านบิล/ใบเสร็จ/เอกสารจากภาพ สรุปข้อมูลให้ แล้วสร้าง PDF ต่อให้ครับ\n"
            "กรุณาส่งรูปที่ต้องการเข้ามาได้เลย ส่งได้หลายรูป\n"
            "เมื่อส่งครบแล้วพิมพ์ 'เสร็จแล้ว'\n"
            "หากต้องการยกเลิก ให้พิมพ์ 'ยกเลิก'"
        )
    return (
        "ได้เลยครับ กรุณาส่งรูปที่ต้องการรวมเป็น PDF มาได้เลย\n"
        "ส่งได้หลายรูป และเมื่อส่งครบแล้วพิมพ์ 'เสร็จแล้ว'\n"
        "หากต้องการยกเลิก ให้พิมพ์ 'ยกเลิก'"
    )


def waiting_for_images_message(mode: str) -> str:
    if mode == "ocr_summary_pdf":
        return (
            "ตอนนี้ผมกำลังรอรูปเอกสารอยู่ครับ\n"
            "ส่งรูปเพิ่มได้เลย หรือพิมพ์ 'เสร็จแล้ว' เมื่อต้องการให้ผมอ่านข้อมูล สรุป และทำ PDF"
        )
    return "ตอนนี้ผมกำลังรอรูปอยู่ครับ\nส่งรูปเพิ่มได้เลย หรือพิมพ์ 'เสร็จแล้ว' เมื่อต้องการสร้าง PDF"


def image_received_message(mode: str, count: int) -> str:
    if mode == "ocr_summary_pdf":
        return (
            f"รับรูปแล้ว {count} รูปครับ\n"
            "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' เมื่อต้องการให้ผมอ่านข้อมูล สรุป และสร้าง PDF"
        )
    return f"รับรูปแล้ว {count} รูปครับ\nส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' เมื่อต้องการสร้าง PDF"


def build_success_message(
    mode: str, safe_name: str, file_url: str, images: list[str]
) -> str:
    if mode != "ocr_summary_pdf":
        return (
            f"สร้าง PDF เรียบร้อยแล้วครับ\nชื่อไฟล์: {safe_name}.pdf\nดาวน์โหลดได้ที่:\n{file_url}"
        )

    try:
        ocr_text = extract_text_from_images(images)
    except OCRUnavailableError as exc:
        return (
            f"สร้าง PDF เรียบร้อยแล้วครับ\n"
            f"ชื่อไฟล์: {safe_name}.pdf\n"
            f"ดาวน์โหลดได้ที่:\n{file_url}\n\n"
            f"หมายเหตุ: ยังสรุปข้อความจากภาพไม่ได้ เพราะ {exc}"
        )

    if not ocr_text:
        return (
            f"สร้าง PDF เรียบร้อยแล้วครับ\n"
            f"ชื่อไฟล์: {safe_name}.pdf\n"
            f"ดาวน์โหลดได้ที่:\n{file_url}\n\n"
            "หมายเหตุ: ผมไม่สามารถอ่านข้อความจากรูปได้ชัดเจนพอสำหรับการสรุปครับ"
        )

    summary = summarize_document_text(ocr_text)
    return (
        f"สรุปข้อมูลจากเอกสาร:\n{summary}\n\n"
        f"สร้าง PDF เรียบร้อยแล้วครับ\n"
        f"ชื่อไฟล์: {safe_name}.pdf\n"
        f"ดาวน์โหลดได้ที่:\n{file_url}"
    )


def summarize_document_text(ocr_text: str) -> str:
    prompt = (
        "อ่านข้อความ OCR ต่อไปนี้ แล้วสรุปเป็นภาษาไทยแบบกระชับ อ่านง่าย\n"
        "ถ้าเป็นใบเสร็จหรือบิล ให้พยายามสรุป: ร้านค้า/หน่วยงาน, วันที่, ยอดรวม, รายการสำคัญ\n"
        "ถ้าเป็นเอกสารทั่วไป ให้สรุปใจความสำคัญเป็นหัวข้อ\n"
        "ถ้าข้อความไม่ชัด ให้บอกตามจริงว่าอ่านได้ไม่ครบ\n\n"
        f"OCR TEXT:\n{ocr_text[:12000]}"
    )
    return ask_ai(prompt).strip()


def build_welcome_message() -> str:
    return (
        f"สวัสดีครับ ผมคือ {BOT_NAME}\n\n"
        "ผมช่วยได้ 2 แบบ\n"
        "1) พิมพ์ 'ทำ PDF' เพื่อรวมรูปเป็น PDF\n"
        "2) พิมพ์ 'สรุปใบเสร็จ' เพื่ออ่านภาพ สรุปข้อมูล และทำ PDF\n\n"
        "เมื่อส่งรูปครบแล้ว ให้พิมพ์ 'เสร็จแล้ว' จากนั้นตั้งชื่อไฟล์ได้เลยครับ"
    )


def build_help_message() -> str:
    return (
        f"{BOT_NAME} ใช้งานได้ดังนี้\n\n"
        "- พิมพ์ 'ทำ PDF' เพื่อรวมหลายรูปเป็น PDF\n"
        "- พิมพ์ 'สรุปใบเสร็จ' หรือ 'อ่านบิล' เพื่อให้ผม OCR + สรุป + ทำ PDF\n"
        "- ส่งรูปเข้ามาได้หลายรูป\n"
        "- พิมพ์ 'เสร็จแล้ว' เมื่อส่งครบ\n"
        "- พิมพ์ 'ยกเลิก' เพื่อล้างงานปัจจุบัน"
    )


def download_line_message_content(message_id: str) -> tuple[bytes, str]:
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    response = requests.get(
        LINE_CONTENT_ENDPOINT.format(message_id=message_id), headers=headers, timeout=30
    )
    response.raise_for_status()
    return response.content, response.headers.get(
        "Content-Type", "application/octet-stream"
    )


def reply_text(reply_token: str, text: str) -> None:
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:5000]}],
    }
    response = requests.post(
        LINE_REPLY_ENDPOINT, headers=headers, json=payload, timeout=30
    )
    response.raise_for_status()


def build_file_url(request: Request, filename: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/files/{filename}"
    return str(request.base_url).rstrip("/") + f"/files/{filename}"


def get_session_key(source: dict[str, Any]) -> str:
    source_type = source.get("type", "user")
    user_id = source.get("userId")
    if user_id:
        return f"{source_type}:{user_id}"
    group_id = source.get("groupId")
    if group_id:
        return f"{source_type}:{group_id}"
    room_id = source.get("roomId")
    if room_id:
        return f"{source_type}:{room_id}"
    return f"unknown:{uuid.uuid4().hex}"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(".")
    return cleaned[:80]


def guess_extension(content_type: str) -> str:
    ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
    if ext:
        return ext
    return ".jpg"


def cleanup_images(image_paths: list[str]) -> None:
    for image_path in image_paths:
        try:
            path = Path(image_path)
            if path.exists():
                path.unlink()
        except Exception:
            pass
