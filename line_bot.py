from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from core.brain import ask_ai
from core.line_pdf_sessions import (
    add_image, clear_session, get_session,
    set_waiting_for_filename, start_pdf_flow,
)
from core.ocr_service import OCRUnavailableError, extract_text_from_images
from core.pdf_service import build_pdf_from_images

load_dotenv()

BASE_DIR      = Path(__file__).resolve().parent
UPLOAD_DIR    = BASE_DIR / "storage" / "uploads"
GENERATED_DIR = BASE_DIR / "storage" / "generated"
NOTES_FILE    = BASE_DIR / "memory" / "notes.json"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)

LINE_CHANNEL_SECRET      = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
PUBLIC_BASE_URL          = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
BOT_NAME                 = os.getenv("BOT_NAME", "LouisAI")

LINE_REPLY_ENDPOINT   = "https://api.line.me/v2/bot/message/reply"
LINE_CONTENT_ENDPOINT = "https://api-data.line.me/v2/bot/message/{message_id}/content"

# ── Commands ──────────────────────────────────────────────────────────────────
PDF_COMMANDS = {"ทำ pdf","ทำpdf","รวมรูปเป็น pdf","รวมรูปเป็นpdf","แปลงรูปเป็น pdf","แปลงรูปเป็นpdf"}
OCR_COMMANDS = {"สรุปใบเสร็จ","อ่านใบเสร็จ","อ่านบิล","สรุปบิล","สรุปเอกสาร","อ่านเอกสาร","ocr pdf","ocr"}
TRANSLATE_COMMANDS = {"แปลภาษา","แปล","translate"}
NOTE_COMMANDS = {"จดบันทึก","บันทึก","จดโน้ต","note"}
NOTE_LIST_COMMANDS = {"ดูบันทึก","ดูโน้ต","รายการบันทึก","list notes"}
NOTE_CLEAR_COMMANDS = {"ลบบันทึก","ล้างบันทึก","clear notes"}
RESIZE_COMMANDS = {"ปรับขนาดรูป","ครอปรูป","resize","ปรับรูป"}
CANCEL_COMMANDS = {"ยกเลิก","cancel","เริ่มใหม่"}
DONE_COMMANDS   = {"เสร็จแล้ว","ครบแล้ว","สร้าง pdf","สร้างpdf"}
HELP_COMMANDS   = {"ช่วยเหลือ","help","เมนู","menu"}

app = FastAPI(title=f"{BOT_NAME} LINE Bot")
app.mount("/files", StaticFiles(directory=str(GENERATED_DIR)), name="files")


@app.get("/")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": BOT_NAME}


@app.post("/webhook/line")
async def line_webhook(
    request: Request, x_line_signature: str = Header(default="")
) -> dict[str, str]:
    if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="LINE env not configured.")

    body = await request.body()
    if not verify_line_signature(body, x_line_signature, LINE_CHANNEL_SECRET):
        raise HTTPException(status_code=401, detail="Invalid LINE signature.")

    payload = await request.json()
    for event in payload.get("events", []):
        handle_event(event, request)

    return {"status": "ok"}


def verify_line_signature(body: bytes, signature: str, secret: str) -> bool:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def handle_event(event: dict[str, Any], request: Request) -> None:
    event_type  = event.get("type")
    reply_token = event.get("replyToken")

    if event_type == "follow" and reply_token:
        reply_text(reply_token, build_welcome_message())
        return

    if event_type != "message" or not reply_token:
        return

    source       = event.get("source", {})
    session_key  = get_session_key(source)
    message      = event.get("message", {})
    message_type = message.get("type")

    if message_type == "text":
        handle_text_message(reply_token, session_key, message.get("text", ""), request)
    elif message_type == "image":
        handle_image_message(reply_token, session_key, message.get("id", ""), request)
    else:
        reply_text(reply_token, build_help_message())


# ── Text handler ──────────────────────────────────────────────────────────────
def handle_text_message(
    reply_token: str, session_key: str, text: str, request: Request
) -> None:
    raw_text   = text.strip()
    normalized = normalize_text(raw_text)
    session    = get_session(session_key)
    state      = session.get("state", "idle")
    mode       = session.get("mode", "pdf")

    # ── Cancel ──
    if normalized in CANCEL_COMMANDS:
        old = clear_session(session_key)
        cleanup_images(old.get("images", []))
        reply_text(reply_token, "ยกเลิกเรียบร้อยแล้วครับ พิมพ์ 'เมนู' เพื่อดูคำสั่งทั้งหมด")
        return

    # ── Help ──
    if normalized in HELP_COMMANDS:
        reply_text(reply_token, build_help_message())
        return

    # ── Start flows ──
    if normalized in PDF_COMMANDS:
        restart_flow(reply_token, session_key, mode="pdf")
        return

    if normalized in OCR_COMMANDS:
        restart_flow(reply_token, session_key, mode="ocr_summary_pdf")
        return

    if normalized in RESIZE_COMMANDS:
        restart_flow(reply_token, session_key, mode="resize")
        return

    # ── Translate ──
    if normalized in TRANSLATE_COMMANDS:
        reply_text(reply_token,
            "ได้เลยครับ พิมพ์ข้อความที่ต้องการแปล แล้วบอกด้วยว่าจะแปลเป็นภาษาอะไร\n"
            "เช่น: 'แปลเป็นอังกฤษ: สวัสดีครับ' หรือ 'แปลเป็นไทย: Hello world'")
        return

    if normalized.startswith("แปลเป็น") or normalized.startswith("translate to"):
        result = ask_ai(f"แปลข้อความนี้ให้ถูกต้อง แล้วตอบแค่คำแปลเท่านั้น ไม่ต้องอธิบาย:\n{raw_text}")
        reply_text(reply_token, result)
        return

    # ── Notes ──
    if normalized in NOTE_COMMANDS:
        reply_text(reply_token, "ได้เลยครับ พิมพ์บันทึกที่ต้องการได้เลย\nเช่น: 'บันทึก: ประชุมวันพรุ่งนี้ 10 โมง'")
        return

    if normalized.startswith("บันทึก:") or normalized.startswith("note:") or normalized.startswith("จดบันทึก:"):
        content = raw_text.split(":", 1)[-1].strip()
        if content:
            save_note(session_key, content)
            reply_text(reply_token, f"บันทึกแล้วครับ ✅\n📝 {content}")
        else:
            reply_text(reply_token, "กรุณาพิมพ์ข้อความหลัง 'บันทึก:' ครับ")
        return

    if normalized in NOTE_LIST_COMMANDS:
        reply_text(reply_token, get_notes(session_key))
        return

    if normalized in NOTE_CLEAR_COMMANDS:
        clear_notes(session_key)
        reply_text(reply_token, "ลบบันทึกทั้งหมดแล้วครับ 🗑")
        return

    # ── Waiting for images state ──
    if state == "waiting_for_images":
        if normalized in DONE_COMMANDS:
            images = session.get("images", [])
            if not images:
                reply_text(reply_token, "ยังไม่มีรูปครับ กรุณาส่งรูปก่อน แล้วค่อยพิมพ์ 'เสร็จแล้ว'")
                return
            set_waiting_for_filename(session_key)
            reply_text(reply_token, "รับรูปครบแล้วครับ\nกรุณาตั้งชื่อไฟล์ PDF ได้เลย เช่น ใบเสร็จ-มิถุนายน")
            return
        reply_text(reply_token, waiting_for_images_message(mode))
        return

    # ── Waiting for filename state ──
    if state == "waiting_for_filename":
        safe_name = sanitize_filename(raw_text)
        if not safe_name:
            reply_text(reply_token, "ชื่อไฟล์ยังใช้ไม่ได้ครับ กรุณาตั้งชื่อใหม่")
            return

        images = session.get("images", [])
        if not images:
            clear_session(session_key)
            reply_text(reply_token, "ไม่พบรูปครับ กรุณาเริ่มใหม่")
            return

        pdf_filename = f"{safe_name}-{uuid.uuid4().hex[:8]}.pdf"
        output_path  = GENERATED_DIR / pdf_filename

        try:
            build_pdf_from_images(images, str(output_path))
            file_url       = build_file_url(request, pdf_filename)
            result_message = build_success_message(mode, safe_name, file_url, images)
            clear_session(session_key)
            cleanup_images(images)
            reply_text(reply_token, result_message)
        except Exception as exc:
            reply_text(reply_token, f"เกิดปัญหาระหว่างสร้าง PDF ครับ: {exc}")
        return

    # ── Default: AI chat ──
    ai_reply = ask_ai(raw_text)
    reply_text(reply_token, ai_reply)


# ── Image handler ─────────────────────────────────────────────────────────────
def handle_image_message(
    reply_token: str, session_key: str, message_id: str, request: Request
) -> None:
    session = get_session(session_key)
    state   = session.get("state", "idle")
    mode    = session.get("mode", "pdf")

    if state != "waiting_for_images":
        reply_text(reply_token,
            "หากต้องการทำ PDF พิมพ์ 'ทำ PDF' ก่อนครับ\n"
            "พิมพ์ 'เมนู' เพื่อดูคำสั่งทั้งหมด")
        return

    content, content_type = download_line_message_content(message_id)
    ext      = guess_extension(content_type)
    user_dir = UPLOAD_DIR / session_key.replace(":", "_")
    user_dir.mkdir(parents=True, exist_ok=True)

    image_path = user_dir / f"{uuid.uuid4().hex}{ext}"
    image_path.write_bytes(content)

    # resize mode — resize image immediately
    if mode == "resize":
        try:
            from PIL import Image as PILImage
            img = PILImage.open(image_path)
            img.thumbnail((1280, 1280))
            img.save(image_path)
        except Exception:
            pass

    updated = add_image(session_key, str(image_path))
    count   = len(updated.get("images", []))
    reply_text(reply_token, image_received_message(mode, count))


# ── Flow helpers ──────────────────────────────────────────────────────────────
def restart_flow(reply_token: str, session_key: str, mode: str) -> None:
    old = clear_session(session_key)
    cleanup_images(old.get("images", []))
    start_pdf_flow(session_key, mode=mode)
    reply_text(reply_token, start_flow_message(mode))


def start_flow_message(mode: str) -> str:
    msgs = {
        "ocr_summary_pdf": (
            "โหมดอ่านบิล/ใบเสร็จ สรุปข้อมูล แล้วสร้าง PDF ครับ\n"
            "ส่งรูปมาได้เลย แล้วพิมพ์ 'เสร็จแล้ว' เมื่อครบ\nพิมพ์ 'ยกเลิก' เพื่อยกเลิก"
        ),
        "resize": (
            "โหมดปรับขนาดรูป ครับ\n"
            "ส่งรูปมาได้เลย แล้วพิมพ์ 'เสร็จแล้ว' เมื่อครบ\nพิมพ์ 'ยกเลิก' เพื่อยกเลิก"
        ),
    }
    return msgs.get(mode,
        "ได้เลยครับ ส่งรูปที่ต้องการรวมเป็น PDF มาได้เลย\n"
        "แล้วพิมพ์ 'เสร็จแล้ว' เมื่อครบ\nพิมพ์ 'ยกเลิก' เพื่อยกเลิก"
    )


def waiting_for_images_message(mode: str) -> str:
    if mode == "ocr_summary_pdf":
        return "กำลังรอรูปอยู่ครับ ส่งเพิ่มได้ หรือพิมพ์ 'เสร็จแล้ว'"
    if mode == "resize":
        return "กำลังรอรูปอยู่ครับ ส่งเพิ่มได้ หรือพิมพ์ 'เสร็จแล้ว'"
    return "กำลังรอรูปอยู่ครับ ส่งเพิ่มได้ หรือพิมพ์ 'เสร็จแล้ว'"


def image_received_message(mode: str, count: int) -> str:
    suffix = {
        "ocr_summary_pdf": "ส่งเพิ่มได้ หรือพิมพ์ 'เสร็จแล้ว' ให้ผมอ่าน สรุป และสร้าง PDF",
        "resize": "ส่งเพิ่มได้ หรือพิมพ์ 'เสร็จแล้ว' ให้ผมสร้าง PDF จากรูปที่ปรับขนาดแล้ว",
    }.get(mode, "ส่งเพิ่มได้ หรือพิมพ์ 'เสร็จแล้ว' เพื่อสร้าง PDF")
    return f"รับรูปแล้ว {count} รูปครับ\n{suffix}"


def build_success_message(mode: str, safe_name: str, file_url: str, images: list[str]) -> str:
    base = f"✅ สร้าง PDF เรียบร้อยแล้วครับ\nชื่อไฟล์: {safe_name}.pdf\nดาวน์โหลด:\n{file_url}"

    if mode != "ocr_summary_pdf":
        return base

    try:
        ocr_text = extract_text_from_images(images)
    except OCRUnavailableError as exc:
        return f"{base}\n\nหมายเหตุ: ยังสรุปข้อความไม่ได้ เพราะ {exc}"

    if not ocr_text:
        return f"{base}\n\nหมายเหตุ: ไม่สามารถอ่านข้อความจากรูปได้ชัดเจนพอครับ"

    summary = summarize_document_text(ocr_text)
    return f"📋 สรุปข้อมูล:\n{summary}\n\n{base}"


def summarize_document_text(ocr_text: str) -> str:
    prompt = (
        "อ่านข้อความ OCR ต่อไปนี้ แล้วสรุปเป็นภาษาไทยแบบกระชับ อ่านง่าย\n"
        "ถ้าเป็นใบเสร็จ/บิล ให้สรุป: ร้านค้า, วันที่, ยอดรวม, รายการสำคัญ\n"
        "ถ้าเป็นเอกสารทั่วไป ให้สรุปใจความสำคัญ\n\n"
        f"OCR TEXT:\n{ocr_text[:12000]}"
    )
    return ask_ai(prompt).strip()


# ── Notes ─────────────────────────────────────────────────────────────────────
def _load_notes() -> dict:
    if not NOTES_FILE.exists():
        return {}
    try:
        return json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_notes(data: dict) -> None:
    NOTES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def save_note(session_key: str, content: str) -> None:
    data = _load_notes()
    if session_key not in data:
        data[session_key] = []
    data[session_key].append({
        "content": content,
        "time": datetime.now().strftime("%d/%m/%Y %H:%M")
    })
    _save_notes(data)

def get_notes(session_key: str) -> str:
    data  = _load_notes()
    notes = data.get(session_key, [])
    if not notes:
        return "ยังไม่มีบันทึกครับ พิมพ์ 'บันทึก: ข้อความ' เพื่อบันทึก"
    lines = [f"📝 บันทึกของคุณ ({len(notes)} รายการ)\n"]
    for i, n in enumerate(notes[-20:], 1):
        lines.append(f"{i}. {n['content']}\n   🕐 {n['time']}")
    return "\n".join(lines)

def clear_notes(session_key: str) -> None:
    data = _load_notes()
    data.pop(session_key, None)
    _save_notes(data)


# ── Messages ──────────────────────────────────────────────────────────────────
def build_welcome_message() -> str:
    return (
        f"สวัสดีครับ ผมคือ {BOT_NAME} 🤖\n\n"
        "พิมพ์ 'เมนู' เพื่อดูสิ่งที่ผมช่วยได้ครับ"
    )

def build_help_message() -> str:
    return (
        f"🤖 {BOT_NAME} ช่วยได้ดังนี้\n\n"
        "📄 PDF\n"
        "  • 'ทำ PDF' — รวมรูปเป็น PDF\n"
        "  • 'สรุปใบเสร็จ' — OCR + สรุป + PDF\n"
        "  • 'ปรับขนาดรูป' — ปรับรูป + PDF\n\n"
        "🌐 แปลภาษา\n"
        "  • 'แปลเป็นอังกฤษ: ข้อความ'\n"
        "  • 'แปลเป็นไทย: text'\n\n"
        "📝 บันทึก\n"
        "  • 'บันทึก: ข้อความ' — บันทึก\n"
        "  • 'ดูบันทึก' — ดูรายการ\n"
        "  • 'ลบบันทึก' — ลบทั้งหมด\n\n"
        "💬 อื่นๆ\n"
        "  • พิมพ์ถามอะไรก็ได้ ผม AI ครับ\n"
        "  • 'ยกเลิก' — ยกเลิกงานปัจจุบัน"
    )


# ── Utils ─────────────────────────────────────────────────────────────────────
def download_line_message_content(message_id: str) -> tuple[bytes, str]:
    headers  = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    response = requests.get(
        LINE_CONTENT_ENDPOINT.format(message_id=message_id),
        headers=headers, timeout=30
    )
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type", "application/octet-stream")

def reply_text(reply_token: str, text: str) -> None:
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]}
    requests.post(LINE_REPLY_ENDPOINT, headers=headers, json=payload, timeout=30).raise_for_status()

def build_file_url(request: Request, filename: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/files/{filename}"
    return str(request.base_url).rstrip("/") + f"/files/{filename}"

def get_session_key(source: dict[str, Any]) -> str:
    for key in ("userId", "groupId", "roomId"):
        val = source.get(key)
        if val:
            return f"{source.get('type','user')}:{val}"
    return f"unknown:{uuid.uuid4().hex}"

def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "", name).strip()
    return re.sub(r"\s+", " ", cleaned).rstrip(".")[:80]

def guess_extension(content_type: str) -> str:
    ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
    return ext or ".jpg"

def cleanup_images(image_paths: list[str]) -> None:
    for p in image_paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass
