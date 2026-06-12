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

from core.brain import ask_ai, clear_chat_history
from core.db_service import (
    add_event,
    add_transaction,
    get_monthly_summary,
    get_recent_transactions,
    get_upcoming_events,
    save_slip,
)
from core.knowledge_base import (
    add_document,
    ask_with_knowledge,
    delete_document,
    list_documents,
)
from core.line_pdf_sessions import (
    add_image,
    add_slip_amount,
    clear_session,
    get_session,
    set_waiting_for_filename,
    start_pdf_flow,
)
from core.ocr_service import OCRUnavailableError, extract_text_from_images
from core.pdf_service import build_pdf_from_images
from core.scheduler import start_scheduler
from core.slip_parser import parse_slip
from core.user_profile import extract_and_save_profile, get_profile, save_profile
from core.voice_service import transcribe_and_summarize

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
GENERATED_DIR = BASE_DIR / "storage" / "generated"
NOTES_FILE = BASE_DIR / "memory" / "notes.json"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
BOT_NAME = os.getenv("BOT_NAME", "LouisAI")

LINE_REPLY_ENDPOINT = "https://api.line.me/v2/bot/message/reply"
LINE_CONTENT_ENDPOINT = "https://api-data.line.me/v2/bot/message/{message_id}/content"

# ── Commands ──────────────────────────────────────────────────────────────────
PDF_COMMANDS = {"ทำ pdf", "ทำpdf", "รวมรูปเป็น pdf", "แปลงรูปเป็น pdf"}
OCR_COMMANDS = {
    "สรุปใบเสร็จ",
    "อ่านใบเสร็จ",
    "อ่านบิล",
    "สรุปบิล",
    "สรุปเอกสาร",
    "อ่านเอกสาร",
    "ocr",
}
SLIP_COMMANDS = {"บันทึกสลิป", "อ่านสลิป", "สแกนสลิป", "slip"}
MULTI_SLIP_COMMANDS = {"รวมสลิป", "นับสลิป", "รวมยอดสลิป", "รวมโอน", "เช็คยอดสลิป"}
TRANSLATE_COMMANDS = {"แปลภาษา", "แปล", "translate"}
NOTE_COMMANDS = {"จดบันทึก", "บันทึก", "note"}
NOTE_LIST_COMMANDS = {"ดูบันทึก", "รายการบันทึก"}
NOTE_CLEAR_COMMANDS = {"ลบบันทึก", "ล้างบันทึก"}
INCOME_COMMANDS = {"รายรับ", "รับเงิน", "income", "บันทึกรายรับ"}
EXPENSE_COMMANDS = {"รายจ่าย", "จ่ายเงิน", "expense", "ค่าใช้จ่าย", "บันทึกรายจ่าย"}
SUMMARY_COMMANDS = {"สรุปรายรับรายจ่าย", "สรุปการเงิน", "สรุปเดือนนี้", "รายงานการเงิน"}
RECENT_COMMANDS = {"รายการล่าสุด", "ประวัติรายการ"}
EVENT_COMMANDS = {"นัดหมาย", "เพิ่มนัด", "ตั้งนัด", "event", "ปฏิทิน"}
EVENT_LIST_COMMANDS = {"ดูนัดหมาย", "นัดหมายทั้งหมด", "ตารางงาน"}
RESIZE_COMMANDS = {"ปรับขนาดรูป", "resize"}
VOICE_COMMANDS = {"สรุปเสียง", "แปลงเสียง", "voice", "อ่านเสียง"}
KB_COMMANDS = {"knowledge base", "คลังความรู้", "อัปโหลดเอกสาร"}
KB_LIST_COMMANDS = {"ดูเอกสาร", "รายการเอกสาร", "list kb"}
KB_ASK_COMMANDS = {"ถามจากเอกสาร", "ค้นหาเอกสาร", "ask kb"}
PROFILE_COMMANDS = {"โปรไฟล์", "ข้อมูลฉัน", "ข้อมูลผม", "profile"}
CANCEL_COMMANDS = {"ยกเลิก", "cancel", "เริ่มใหม่"}
DONE_COMMANDS = {"เสร็จแล้ว", "ครบแล้ว", "สร้าง pdf"}
HELP_COMMANDS = {"ช่วยเหลือ", "help", "เมนู", "menu"}

app = FastAPI(title=f"{BOT_NAME} LINE Bot")
app.mount("/files", StaticFiles(directory=str(GENERATED_DIR)), name="files")

start_scheduler()


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
        raise HTTPException(status_code=401, detail="Invalid signature.")
    payload = await request.json()
    for event in payload.get("events", []):
        handle_event(event, request)
    return {"status": "ok"}


def verify_line_signature(body: bytes, signature: str, secret: str) -> bool:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


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
        handle_image_message(reply_token, session_key, message.get("id", ""), request)
    elif message_type == "audio":
        handle_audio_message(reply_token, session_key, message.get("id", ""))
    else:
        reply_text(reply_token, build_help_message())


# ── Text ──────────────────────────────────────────────────────────────────────
def handle_text_message(reply_token, session_key, text, request):
    raw_text = text.strip()
    normalized = normalize_text(raw_text)
    session = get_session(session_key)
    state = session.get("state", "idle")
    mode = session.get("mode", "pdf")
    user_id = session_key

    if normalized in CANCEL_COMMANDS:
        old = clear_session(session_key)
        cleanup_images(old.get("images", []))
        reply_text(
            reply_token,
            "✅ ยกเลิกเรียบร้อยแล้วครับ\n\n💬 ถามอะไรได้เลย หรือพิมพ์ 'เมนู' เพื่อดูสิ่งที่ผมช่วยได้ครับ",
        )
        return

    if normalized in HELP_COMMANDS:
        reply_text(reply_token, build_help_message())
        return

    # ── User Profile ──
    if normalized in PROFILE_COMMANDS:
        profile = get_profile(session_key)
        if not profile:
            reply_text(reply_token, "ยังไม่มีข้อมูลครับ บอกชื่อหรือข้อมูลส่วนตัวได้เลยครับ")
        else:
            lines = ["👤 โปรไฟล์ของคุณ\n"]
            for k, v in profile.items():
                lines.append(f"• {k}: {v}")
            reply_text(reply_token, "\n".join(lines))
        return

    # ── Knowledge Base ──
    if normalized in KB_LIST_COMMANDS:
        docs = list_documents(session_key)
        if not docs:
            reply_text(reply_token, "ยังไม่มีเอกสารครับ ส่งรูปเอกสารมาเลยครับ")
        else:
            reply_text(
                reply_token, "📚 เอกสารที่บันทึกไว้:\n" + "\n".join(f"• {d}" for d in docs)
            )
        return

    if re.match(r"^อัปโหลดเอกสาร\s*:", normalized):
        doc_name = raw_text.split(":", 1)[-1].strip()
        s = get_session(session_key)
        s["pending_kb"] = doc_name
        reply_text(reply_token, f"ได้เลยครับ ส่งรูปเอกสาร '{doc_name}' มาได้เลย")
        return

    if re.match(r"^ถามจากเอกสาร\s*:", normalized):
        question = raw_text.split(":", 1)[-1].strip()
        reply_text(reply_token, ask_with_knowledge(session_key, question, ask_ai))
        return

    # ── PDF flows ──
    if normalized in PDF_COMMANDS:
        restart_flow(reply_token, session_key, "pdf")
        return
    if normalized in OCR_COMMANDS:
        restart_flow(reply_token, session_key, "ocr_summary_pdf")
        return
    if normalized in SLIP_COMMANDS:
        restart_flow(reply_token, session_key, "slip")
        return
    if normalized in RESIZE_COMMANDS:
        restart_flow(reply_token, session_key, "resize")
        return

    # ── Translate ──
    if normalized in TRANSLATE_COMMANDS:
        reply_text(
            reply_token,
            "พิมพ์ข้อความที่ต้องการแปลได้เลยครับ\nเช่น 'แปลเป็นอังกฤษ: สวัสดี' หรือ 'แปลเป็นไทย: Hello'",
        )
        return
    if re.match(r"^แปลเป็น|^translate to", normalized):
        reply_text(reply_token, ask_ai(f"แปลข้อความนี้ ตอบแค่คำแปลเท่านั้น:\n{raw_text}"))
        return

    # ── Notes ── (รองรับทั้งแบบ บันทึก: ข้อความ และ บันทึก ข้อความ)
    if re.match(r"^(บันทึก|จดบันทึก|note)\s*[:：]?\s+\S", normalized):
        sep = ":" if ":" in raw_text else " "
        content = raw_text.split(sep, 1)[-1].strip()
        if content:
            save_note(session_key, content)
            reply_text(reply_token, f"📝 บันทึกแล้วครับ\n{content}")
        return
    if normalized in NOTE_COMMANDS:
        reply_text(
            reply_token,
            "📝 บันทึกข้อความ\n\nพิมพ์ได้เลยครับ เช่น\n"
            "• บันทึก ประชุมพรุ่งนี้ 10 โมง\n"
            "• บันทึก ซื้อของที่ต้องการ\n"
            "• บันทึก: ไอเดียโปรเจกต์",
        )
        return
    if normalized in NOTE_LIST_COMMANDS:
        reply_text(reply_token, get_notes(session_key))
        return
    if normalized in NOTE_CLEAR_COMMANDS:
        clear_notes(session_key)
        reply_text(reply_token, "ลบบันทึกทั้งหมดแล้วครับ 🗑")
        return

    if normalized in {"ล้างแชท", "ลบประวัติแชท", "clear chat"}:
        clear_chat_history(session_key)
        reply_text(reply_token, "ล้างประวัติการสนทนาแล้วครับ 🗑")
        return

    if normalized in {"ล้างแชท_dup"}:
        clear_chat_history(session_key)
        reply_text(reply_token, "ล้างประวัติการสนทนาแล้วครับ 🗑")
        return

    # ── Finance ──
    if normalized in INCOME_COMMANDS:
        reply_text(reply_token, _finance_help_msg("income"))
        return
    if _is_income_text(normalized):
        _handle_finance(reply_token, user_id, raw_text, "income")
        return

    if normalized in EXPENSE_COMMANDS:
        reply_text(reply_token, _finance_help_msg("expense"))
        return
    if _is_expense_text(normalized):
        _handle_finance(reply_token, user_id, raw_text, "expense")
        return

    if normalized in SUMMARY_COMMANDS:
        now = datetime.now()
        s = get_monthly_summary(user_id, now.year, now.month)
        reply_text(
            reply_token,
            f"📊 สรุปการเงินเดือนนี้\n"
            f"💚 รายรับ:  {s['income']:,.2f} บาท\n"
            f"❤️ รายจ่าย: {s['expense']:,.2f} บาท\n"
            f"💰 คงเหลือ:  {s['balance']:,.2f} บาท",
        )
        return

    if normalized in RECENT_COMMANDS:
        rows = get_recent_transactions(user_id)
        if not rows:
            reply_text(reply_token, "ยังไม่มีรายการครับ")
            return
        lines = ["📋 รายการล่าสุด\n"]
        for r in rows:
            icon = "💚" if r["type"] == "income" else "❤️"
            lines.append(
                f"{icon} {r['category'] or '-'}  {r['amount']:,.2f} บาท  ({r['date']})"
            )
        reply_text(reply_token, "\n".join(lines))
        return

    # ── Events ── (รองรับ นัด: ชื่อ / นัด ชื่อ วันที่)
    if re.match(r"^(นัด|ตั้งนัด|เพิ่มนัด)\s*[:：]?\s+\S", normalized):
        _handle_event(reply_token, user_id, raw_text)
        return
    if normalized in EVENT_COMMANDS:
        reply_text(
            reply_token,
            "📅 บันทึกนัดหมาย\n\nพิมพ์ได้หลายแบบครับ เช่น\n"
            "• นัด ประชุม 2026-07-20 09:00\n"
            "• นัด หมอ 2026-07-25\n"
            "• ตั้งนัด สัมภาษณ์งาน 2026-08-01 13:00",
        )
        return
    if normalized in EVENT_LIST_COMMANDS:
        _show_events(reply_token, user_id)
        return

    # ── Multi-slip command ──
    if normalized in MULTI_SLIP_COMMANDS:
        restart_flow(reply_token, session_key, "multi_slip")
        return

    # ── Waiting states ──
    if state == "waiting_for_images":
        if normalized in DONE_COMMANDS:
            images = session.get("images", [])
            if not images:
                reply_text(
                    reply_token,
                    "📎 ยังไม่มีรูปในงานนี้ครับ\n"
                    "ส่งรูปอย่างน้อย 1 รูปก่อน แล้วพิมพ์ 'เสร็จแล้ว' ได้เลยครับ",
                )
                return
            # โหมด multi_slip: ประมวลผลทันที ไม่ต้องถามชื่อไฟล์
            if mode == "multi_slip":
                _process_multi_slip(reply_token, session_key, user_id)
                return
            set_waiting_for_filename(session_key)
            reply_text(
                reply_token,
                f"✅ รับรูปครบแล้ว {len(images)} รูปครับ\n\n"
                "📝 กรุณาตั้งชื่อไฟล์ PDF ได้เลยครับ\n"
                "เช่น  ใบเสร็จ-มิถุนายน  หรือ  เอกสารสมัครงาน",
            )
            return
        reply_text(reply_token, waiting_msg(mode))
        return

    if state == "waiting_for_filename":
        safe_name = sanitize_filename(raw_text)
        if not safe_name:
            reply_text(
                reply_token,
                "⚠️ ชื่อไฟล์นั้นใช้ไม่ได้ครับ\n"
                "กรุณาใช้ตัวอักษร ตัวเลข เว้นวรรค หรือขีดกลางเท่านั้น\n"
                "เช่น  ใบเสร็จ-มิถุนายน  หรือ  เอกสาร 2026",
            )
            return
        images = session.get("images", [])
        if not images:
            clear_session(session_key)
            reply_text(
                reply_token,
                "⚠️ ไม่พบรูปในงานนี้แล้วครับ\nพิมพ์ 'ทำ PDF' หรือ 'สรุปใบเสร็จ' เพื่อเริ่มใหม่ได้เลยครับ",
            )
            return
        pdf_filename = f"{safe_name}-{uuid.uuid4().hex[:8]}.pdf"
        output_path = GENERATED_DIR / pdf_filename
        try:
            build_pdf_from_images(images, str(output_path))
            file_url = build_file_url(request, pdf_filename)
            msg = build_success_message(mode, safe_name, file_url, images, user_id)
            clear_session(session_key)
            cleanup_images(images)
            reply_text(reply_token, msg)
        except Exception as exc:
            reply_text(reply_token, f"เกิดปัญหาสร้าง PDF ครับ: {exc}")
        return

    # ── Auto extract profile ──
    name = extract_and_save_profile(session_key, raw_text)
    if name:
        reply_text(reply_token, ask_ai(raw_text, user_id=session_key))
        return

    # ── Default AI ──
    # ถ้ามีเอกสารใน KB ใช้ความรู้จากเอกสารด้วย
    from core.knowledge_base import list_documents

    if list_documents(session_key):
        reply_text(reply_token, ask_with_knowledge(session_key, raw_text, ask_ai))
    else:
        reply_text(reply_token, ask_ai(raw_text, user_id=session_key))


# ── Image ─────────────────────────────────────────────────────────────────────
def handle_image_message(reply_token, session_key, message_id, request):
    session = get_session(session_key)
    state = session.get("state", "idle")
    mode = session.get("mode", "pdf")

    # ── ดาวน์โหลดรูปก่อนเสมอ ──
    content, content_type = download_line_message_content(message_id)
    ext = guess_extension(content_type)

    # ── นอก flow: auto-detect ──
    if state != "waiting_for_images":
        tmp_path = UPLOAD_DIR / f"tmp_{uuid.uuid4().hex}{ext}"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(content)

        # โหมด KB
        pending_kb = get_session(session_key).get("pending_kb", "")
        if pending_kb:
            try:
                ocr_text = extract_text_from_images([str(tmp_path)])
                if ocr_text:
                    add_document(
                        session_key, pending_kb, ocr_text, {"source": pending_kb}
                    )
                    s = get_session(session_key)
                    s.pop("pending_kb", None)
                    reply_text(reply_token, f"📚 บันทึกเอกสาร '{pending_kb}' แล้วครับ")
                else:
                    reply_text(reply_token, "อ่านข้อความไม่ได้ครับ รูปไม่ชัดพอ")
            except Exception as e:
                reply_text(reply_token, f"เกิดข้อผิดพลาดครับ: {e}")
            finally:
                tmp_path.unlink(missing_ok=True)
            return

        try:
            ocr_text = extract_text_from_images([str(tmp_path)])
        except Exception:
            ocr_text = ""

        # Auto-detect สลิป → เริ่ม multi_slip อัตโนมัติ
        if ocr_text and _looks_like_slip(ocr_text):
            user_dir = UPLOAD_DIR / session_key.replace(":", "_")
            user_dir.mkdir(parents=True, exist_ok=True)
            saved_path = user_dir / f"{uuid.uuid4().hex}{ext}"
            saved_path.write_bytes(content)
            tmp_path.unlink(missing_ok=True)

            start_pdf_flow(session_key, mode="multi_slip")
            add_image(session_key, str(saved_path))

            slip = parse_slip(ocr_text)
            amount = slip.get("amount") or 0.0
            add_slip_amount(session_key, amount)

            bank_line = f"🏦 {slip['bank']}\n" if slip.get("bank") else ""
            if amount > 0:
                reply_text(
                    reply_token,
                    f"🧾 ตรวจพบสลิปโอนเงินครับ\n"
                    f"💰 ยอด: {amount:,.2f} บาท\n"
                    f"{bank_line}\n"
                    f"ส่งสลิปเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' เพื่อดูยอดรวม",
                )
            else:
                reply_text(
                    reply_token,
                    "🧾 ตรวจพบสลิปครับ แต่อ่านยอดไม่ชัด\n"
                    "ส่งสลิปเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' เพื่อดูยอดรวม",
                )
            return

        # Auto-detect เอกสาร/รูปทั่วไป
        if ocr_text:
            analysis = ask_ai(
                f"วิเคราะห์ข้อความจากรูปนี้ให้ครับ สรุปให้กระชับ:\n{ocr_text[:3000]}"
            )
            reply_text(reply_token, f"🔍 วิเคราะห์รูป:\n{analysis}")
        else:
            reply_text(
                reply_token,
                "📸 รับรูปแล้วครับ ผมช่วยอะไรกับรูปนี้ดีครับ?\n\n"
                "🧾 ส่งสลิปมาตรง ๆ ผมอ่านยอดและรวมให้เลย\n"
                "📄 'ทำ PDF'  — รวมรูปเป็น PDF\n"
                "🔍 'สรุปใบเสร็จ'  — อ่านและสรุปเอกสาร",
            )
        tmp_path.unlink(missing_ok=True)
        return

    # ── ใน flow: บันทึกรูปตามปกติ ──
    user_dir = UPLOAD_DIR / session_key.replace(":", "_")
    user_dir.mkdir(parents=True, exist_ok=True)
    image_path = user_dir / f"{uuid.uuid4().hex}{ext}"
    image_path.write_bytes(content)

    if mode == "resize":
        try:
            from PIL import Image as PILImage

            img = PILImage.open(image_path)
            img.thumbnail((1280, 1280))
            img.save(image_path)
        except Exception:
            pass

    updated = add_image(session_key, str(image_path))
    count = len(updated.get("images", []))

    # โหมด multi_slip: OCR สลิปทันทีและแสดงยอดสะสม
    if mode == "multi_slip":
        try:
            ocr_text = extract_text_from_images([str(image_path)])
            slip = parse_slip(ocr_text) if ocr_text else {}
            amount = slip.get("amount") or 0.0
        except Exception:
            amount = 0.0
        updated2 = add_slip_amount(session_key, amount)
        slip_amounts = updated2.get("slip_amounts", [])
        total = sum(a for a in slip_amounts if a)
        n = len(slip_amounts)
        if amount > 0:
            reply_text(
                reply_token,
                f"🧾 สลิปที่ {n}: {amount:,.2f} บาท\n"
                f"💰 ยอดสะสม {n} รายการ: {total:,.2f} บาท\n\n"
                f"ส่งสลิปเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' เพื่อดูสรุป",
            )
        else:
            reply_text(
                reply_token,
                f"📥 รับสลิปที่ {n} แล้วครับ (อ่านยอดไม่ชัด)\n"
                f"💰 ยอดสะสมที่อ่านได้: {total:,.2f} บาท\n\n"
                f"ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว'",
            )
        return

    reply_text(reply_token, image_received_msg(mode, count))


# ── Multi-slip processor ────────────────────────────────────────────────────
def _process_multi_slip(reply_token: str, session_key: str, user_id: str) -> None:
    session = get_session(session_key)
    images = session.get("images", [])
    cached_amounts = session.get("slip_amounts", [])

    # ถ้า slip_amounts ยังไม่ครบ (เช่น บางรูปมาก่อน multi_slip mode) ให้ re-OCR
    if len(cached_amounts) < len(images):
        cached_amounts = []
        for img_path in images:
            try:
                ocr_text = extract_text_from_images([img_path])
                slip = parse_slip(ocr_text) if ocr_text else {}
                cached_amounts.append(slip.get("amount") or 0.0)
            except Exception:
                cached_amounts.append(0.0)

    total = sum(a for a in cached_amounts if a)
    valid = [(i + 1, a) for i, a in enumerate(cached_amounts) if a and a > 0]
    failed = [i + 1 for i, a in enumerate(cached_amounts) if not a]

    lines = [f"🧾 สรุปสลิป {len(images)} รายการ", "─" * 28]
    for idx, amt in valid:
        lines.append(f"✅ สลิปที่ {idx}: {amt:,.2f} บาท")
    for idx in failed:
        lines.append(f"⚠️ สลิปที่ {idx}: อ่านยอดไม่ได้")
    lines += [
        "─" * 28,
        f"💰 ยอดรวม: {total:,.2f} บาท",
        f"📊 อ่านได้ {len(valid)}/{len(images)} รายการ",
    ]
    if total > 0:
        add_transaction(user_id, "income", total, "โอนเงิน (รวมสลิป)")
        lines.append("\n✅ บันทึกรายรับแล้วครับ")

    clear_session(session_key)
    cleanup_images(images)
    reply_text(reply_token, "\n".join(lines))


def _looks_like_slip(text: str) -> bool:
    """ตรวจว่าข้อความ OCR น่าจะเป็นสลิปโอนเงินหรือไม่"""
    if not text:
        return False
    slip_keywords = [
        "โอน",
        "transfer",
        "ธนาคาร",
        "bank",
        "บาท",
        "thb",
        "เลขอ้างอิง",
        "ref",
        "สำเร็จ",
        "success",
        "ยอดเงิน",
        "จ่ายเงิน",
    ]
    hit = sum(1 for kw in slip_keywords if kw in text.lower())
    has_number = bool(_AMOUNT_RE.search(text))
    return hit >= 2 and has_number


# ── Finance helpers ─────────────────────────────────────────────────────────
_EXPENSE_KEYWORDS = r"(รายจ่าย|จ่าย|ค่า|ใช้ไป|ซื้อ|expense)"
_INCOME_KEYWORDS = r"(รายรับ|รับ|ได้รับ|โอนเข้า|income|เงินเดือน|ค่าจ้าง|โบนัส)"
_AMOUNT_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def _is_expense_text(text: str) -> bool:
    has_kw = bool(re.search(_EXPENSE_KEYWORDS, text))
    has_number = bool(_AMOUNT_RE.search(text))
    return has_kw and has_number


def _is_income_text(text: str) -> bool:
    has_kw = bool(re.search(_INCOME_KEYWORDS, text))
    has_number = bool(_AMOUNT_RE.search(text))
    return has_kw and has_number


def _extract_amount_category(raw_text: str, type_: str) -> tuple[float | None, str]:
    """รองรับหลายรูปแบบ: ค่าน้ำ 270 / 270 ค่าน้ำ / รายจ่าย 270 ค่าน้ำ / รายจ่าย: 270 ค่าน้ำ"""
    text = raw_text.strip()
    # ตัด prefix คำสั่ง เช่น รายจ่าย / รายรับ / จ่าย ออกก่อน
    text = re.sub(
        r"^(รายจ่าย|รายรับ|จ่ายเงิน|รับเงิน|ใช้ไป|ได้รับ|โอนเข้า|บันทึกรายจ่าย|บันทึกรายรับ)\s*:?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    nums = _AMOUNT_RE.findall(text)
    if not nums:
        return None, text or ("รายรับ" if type_ == "income" else "รายจ่าย")

    for raw_num in nums:
        try:
            amount = float(raw_num.replace(",", ""))
            if amount <= 0:
                continue
            # หมวดหมู่ = ข้อความที่เหลือหลังเอาตัวเลขออก
            category = re.sub(re.escape(raw_num), "", text).strip()
            category = re.sub(r"\s*บาท\s*", " ", category).strip()
            category = re.sub(r"\s+", " ", category).strip(" ,.-")
            if not category:
                category = "รายรับ" if type_ == "income" else "รายจ่าย"
            return amount, category
        except ValueError:
            continue

    return None, text


def _finance_help_msg(type_: str) -> str:
    if type_ == "income":
        return (
            "💚 บันทึกรายรับ\n\n"
            "พิมพ์ได้หลายแบบครับ เช่น\n"
            "• รายรับ 5000 เงินเดือน\n"
            "• เงินเดือน 15000\n"
            "• รับ 500 ค่าล่วงเวลา\n"
            "• โบนัส 3000"
        )
    return (
        "❤️ บันทึกรายจ่าย\n\n"
        "พิมพ์ได้หลายแบบครับ เช่น\n"
        "• ค่าน้ำ 270\n"
        "• จ่ายค่าไฟ 700\n"
        "• รายจ่าย 250 ค่าอาหาร\n"
        "• ซื้อของ 150"
    )


def _handle_finance(reply_token, user_id, raw_text, type_):
    amount, category = _extract_amount_category(raw_text, type_)
    if amount is None:
        reply_text(reply_token, _finance_help_msg(type_))
        return
    add_transaction(user_id, type_, amount, category)
    icon = "💚" if type_ == "income" else "❤️"
    reply_text(
        reply_token,
        f"{icon} บันทึกแล้วครับ\n"
        f"• หมวด: {category}\n"
        f"• จำนวน: {amount:,.2f} บาท\n\n"
        "พิมพ์ 'สรุปเดือนนี้' เพื่อดูยอดรวมได้เลยครับ",
    )


def _handle_event(reply_token, user_id, raw_text):
    content = raw_text.split(":", 1)[-1].strip()
    parts = content.split()
    if len(parts) < 2:
        reply_text(
            reply_token, "รูปแบบ: 'นัด: ชื่อ วันที่ เวลา'\nเช่น 'นัด: ประชุม 2026-07-15 09:00'"
        )
        return
    title = parts[0]
    event_date = parts[1] if len(parts) > 1 else datetime.now().strftime("%Y-%m-%d")
    event_time = parts[2] if len(parts) > 2 else ""
    add_event(user_id, title, event_date, event_time)
    reply_text(
        reply_token,
        f"📅 บันทึกนัดหมายแล้วครับ\n"
        f"📌 {title}\n"
        f"🗓 {event_date} {event_time}\n"
        f"🔔 จะแจ้งเตือนตรงเวลาครับ",
    )


def _show_events(reply_token, user_id):
    events = get_upcoming_events(user_id)
    if not events:
        reply_text(reply_token, "ยังไม่มีนัดหมายครับ พิมพ์ 'นัด: ชื่อ วันที่ เวลา' เพื่อเพิ่ม")
        return
    lines = ["📅 นัดหมายที่กำลังจะมาถึง\n"]
    for e in events:
        lines.append(f"📌 {e['title']}  {e['event_date']} {e['event_time'] or ''}")
    reply_text(reply_token, "\n".join(lines))


# ── Flow helpers ──────────────────────────────────────────────────────────────
def restart_flow(reply_token, session_key, mode):
    old = clear_session(session_key)
    cleanup_images(old.get("images", []))
    start_pdf_flow(session_key, mode=mode)
    reply_text(reply_token, start_flow_msg(mode))


def start_flow_msg(mode):
    msgs = {
        "ocr_summary_pdf": (
            "🧠 AI วิเคราะห์เอกสารการเงิน\n\n"
            "รองรับ: ใบเสร็จ / บิล / สลิปโอนเงิน\n\n"
            "ผมจะช่วย:\n"
            "✓ อ่านข้อความจากภาพอัตโนมัติ\n"
            "✓ วิเคราะห์และสรุปข้อมูลสำคัญ\n"
            "✓ แยกรายการ หมวดหมู่ ยอดรวม\n"
            "✓ สร้างรายงาน PDF\n\n"
            "📎 ส่งรูปมาได้เลยครับ\n"
            "เมื่อครบพิมพ์ 'เสร็จแล้ว'"
        ),
        "slip": (
            "🧾 อ่านสลิป + บันทึกยอด\n\n"
            "ส่งรูปสลิปมาได้เลยครับ ส่งได้หลายรูป\n"
            "ผมจะอ่านยอดเงินและบันทึกให้\n\n"
            "เมื่อครบพิมพ์ 'เสร็จแล้ว'"
        ),
        "multi_slip": (
            "🧾 รวมยอดสลิป\n\n"
            "ส่งรูปสลิปมาได้เลยครับ ส่งได้เยอะแค่ไหนก็ได้\n"
            "ผมจะอ่านยอดและรวมให้อัตโนมัติ\n\n"
            "เมื่อส่งครบพิมพ์ 'เสร็จแล้ว' เพื่อดูยอดรวม"
        ),
        "resize": (
            "🖼️ ปรับขนาดรูปภาพ\n\n"
            "ส่งรูปมาได้เลยครับ ผมจะปรับขนาดและรวมเป็น PDF\n"
            "เมื่อครบพิมพ์ 'เสร็จแล้ว'"
        ),
    }
    return msgs.get(mode, "📎 ส่งรูปมาได้เลยครับ\nเมื่อครบพิมพ์ 'เสร็จแล้ว'")


def waiting_msg(mode):
    msgs = {
        "ocr_summary_pdf": "📎 ส่งรูปเพิ่มได้เลยครับ  หรือพิมพ์ 'เสร็จแล้ว' ให้ผมวิเคราะห์",
        "slip": "📎 ส่งสลิปเพิ่มได้เลยครับ  หรือพิมพ์ 'เสร็จแล้ว' ให้ผมรวมยอด",
        "multi_slip": "📎 ส่งสลิปเพิ่มได้เลยครับ  หรือพิมพ์ 'เสร็จแล้ว' เพื่อดูยอดรวม",
        "resize": "📎 ส่งรูปเพิ่มได้เลยครับ  หรือพิมพ์ 'เสร็จแล้ว' ให้ผมสร้าง PDF",
    }
    return msgs.get(mode, "📎 ส่งรูปเพิ่มได้เลยครับ  หรือพิมพ์ 'เสร็จแล้ว' ให้ผมสร้าง PDF")


def image_received_msg(mode, count):
    suffix = {
        "ocr_summary_pdf": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' ให้ผมวิเคราะห์ + สรุป + สร้าง PDF ครับ",
        "slip": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' ให้ผมอ่านและบันทึกยอดครับ",
        "resize": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' ให้ผมสร้าง PDF ครับ",
    }.get(mode, "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' ให้ผมสร้าง PDF ครับ")
    return f"📥 รับรูปแล้ว {count} รูปครับ\n{suffix}"


def build_success_message(mode, safe_name, file_url, images, user_id):
    base = f"✅ สร้าง PDF แล้วครับ\nชื่อ: {safe_name}.pdf\n🔗 {file_url}"

    if mode == "slip":
        try:
            ocr_text = extract_text_from_images(images)
            slip = parse_slip(ocr_text)
            save_slip(
                user_id,
                slip["amount"],
                slip["bank"],
                slip["ref"],
                slip["datetime"],
                ocr_text,
            )
            return (
                f"🧾 อ่านสลิปแล้วครับ\n💰 ยอด: {slip['amount']:,.2f} บาท\n"
                if slip["amount"]
                else "" + f"🏦 ธนาคาร: {slip['bank']}\n"
                f"🔖 Ref: {slip['ref']}\n"
                f"🕐 วันที่: {slip['datetime']}\n\n{base}"
            )
        except Exception:
            return base

    if mode == "ocr_summary_pdf":
        try:
            ocr_text = extract_text_from_images(images)
        except OCRUnavailableError as exc:
            return f"{base}\n\nหมายเหตุ: สรุปไม่ได้ เพราะ {exc}"
        if not ocr_text:
            return f"{base}\n\nหมายเหตุ: อ่านข้อความไม่ได้ครับ"
        summary = ask_ai(
            "อ่านข้อความ OCR ต่อไปนี้แล้วสรุปเป็นภาษาไทย กระชับ อ่านง่าย\n"
            "ถ้าเป็นใบเสร็จหรือบิล ให้สรุป: ร้านค้า/หน่วยงาน วันที่ รายการสำคัญ ยอดรวม\n"
            "ถ้าเป็นเอกสารทั่วไป ให้สรุปใจความเป็นหัวข้อย่อย\n"
            "ถ้าข้อมูลไม่ชัดเจน ให้ระบุตามจริงว่าอ่านได้ไม่ครบ\n\n"
            f"OCR TEXT:\n{ocr_text[:12000]}"
        ).strip()
        return f"🧠 วิเคราะห์เอกสารแล้วครับ\n{'─' * 25}\n{summary}\n\n{base}"

    return base


# ── Notes ─────────────────────────────────────────────────────────────────────
def _load_notes():
    if not NOTES_FILE.exists():
        return {}
    try:
        return json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_notes(data):
    NOTES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save_note(session_key, content):
    data = _load_notes()
    data.setdefault(session_key, []).append(
        {"content": content, "time": datetime.now().strftime("%d/%m/%Y %H:%M")}
    )
    _save_notes(data)


def get_notes(session_key):
    notes = _load_notes().get(session_key, [])
    if not notes:
        return "ยังไม่มีบันทึกครับ พิมพ์ 'บันทึก: ข้อความ' เพื่อบันทึก"
    lines = [f"📝 บันทึก {len(notes)} รายการ\n"]
    for i, n in enumerate(notes[-20:], 1):
        lines.append(f"{i}. {n['content']}\n   🕐 {n['time']}")
    return "\n".join(lines)


def clear_notes(session_key):
    data = _load_notes()
    data.pop(session_key, None)
    _save_notes(data)


# ── Messages ──────────────────────────────────────────────────────────────────
def build_welcome_message():
    return (
        f"สวัสดีครับ ผมคือ {BOT_NAME} 🤖\n\n"
        "ผมช่วยได้หลายอย่างครับ เช่น\n"
        "🧾 ส่งสลิปมาตรง ๆ → ผมอ่านยอดและรวมให้เลย\n"
        "📄 'ทำ PDF' → รวมรูปเป็น PDF\n"
        "💰 'ค่าน้ำ 270' → บันทึกรายจ่าย\n"
        "📝 'บันทึก ข้อความ' → จดบันทึก\n\n"
        "พิมพ์ 'เมนู' เพื่อดูทุกฟีเจอร์ หรือถามได้เลยครับ 💬"
    )


def build_help_message():
    return (
        f"🤖 {BOT_NAME}  ใช้งานได้ดังนี้\n"
        "════════════════════\n"
        "🧾 สลิปโอนเงิน\n"
        "  ส่งสลิปมาตรง ๆ → อ่านยอดอัตโนมัติ\n"
        "  รวมสลิป → รวมยอดหลายสลิปพร้อมกัน\n\n"
        "📄 PDF & เอกสาร\n"
        "  ทำ PDF → รวมรูปเป็น PDF\n"
        "  สรุปใบเสร็จ → อ่าน+สรุปเอกสาร\n\n"
        "💰 บันทึกการเงิน (พิมพ์ตามสบาย)\n"
        "  ค่าน้ำ 270\n"
        "  เงินเดือน 15000\n"
        "  สรุปเดือนนี้ / รายการล่าสุด\n\n"
        "📅 นัดหมาย\n"
        "  นัด ประชุม 2026-07-20 09:00\n"
        "  ดูนัดหมาย\n\n"
        "📝 บันทึก\n"
        "  บันทึก ข้อความใดก็ได้\n"
        "  ดูบันทึก / ลบบันทึก\n\n"
        "🌐 อื่น ๆ\n"
        "  แปลเป็นอังกฤษ: ข้อความ\n"
        "  ถามอะไรก็ได้ครับ 💬\n"
        "════════════════════\n"
        "พิมพ์ 'ยกเลิก' เพื่อหยุดงานปัจจุบัน"
    )


# ── Utils ─────────────────────────────────────────────────────────────────────
def download_line_message_content(message_id):
    r = requests.get(
        LINE_CONTENT_ENDPOINT.format(message_id=message_id),
        headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "application/octet-stream")


def reply_text(reply_token, text):
    requests.post(
        LINE_REPLY_ENDPOINT,
        headers={
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": text[:5000]}],
        },
        timeout=30,
    ).raise_for_status()


def build_file_url(request, filename):
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/files/{filename}"
    return str(request.base_url).rstrip("/") + f"/files/{filename}"


def get_session_key(source):
    for key in ("userId", "groupId", "roomId"):
        val = source.get(key)
        if val:
            return f"{source.get('type', 'user')}:{val}"
    return f"unknown:{uuid.uuid4().hex}"


def normalize_text(text):
    return re.sub(r"\s+", " ", text.strip().lower())


def sanitize_filename(name):
    cleaned = re.sub(r'[\\/:*?"<>|]+', "", name).strip()
    return re.sub(r"\s+", " ", cleaned).rstrip(".")[:80]


def guess_extension(content_type):
    return mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".jpg"


def cleanup_images(image_paths):
    for p in image_paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass


# ── Audio handler ────────────────────────────────────────────────────
def handle_audio_message(reply_token: str, session_key: str, message_id: str) -> None:
    content, _ = download_line_message_content(message_id)
    tmp_path = UPLOAD_DIR / f"audio_{uuid.uuid4().hex}.m4a"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(content)
    try:
        result = transcribe_and_summarize(str(tmp_path), ask_ai, user_id=session_key)
        reply_text(reply_token, result)
    finally:
        tmp_path.unlink(missing_ok=True)
