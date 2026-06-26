from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import uuid
import threading
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles

from core.brain import ask_ai, clear_chat_history
from core.db_service import (
    add_event,
    add_transaction,
    get_daily_summary,
    get_daily_transactions,
    get_expense_by_category,
    get_latest_slip_batch,
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
    add_slip_entry,
    clear_session,
    get_session,
    set_waiting_for_filename,
    set_waiting_for_pdf_confirm,
    set_waiting_for_slip_type,
    start_pdf_flow,
)
from core.ocr_service import OCRUnavailableError, extract_text_from_images
from core.pdf_service import (
    build_pdf_from_images,
    build_receipt_report_pdf,
    build_slip_report_pdf,
)
from core.scheduler import start_scheduler
from core.slip_parser import parse_slip
from core.user_profile import (
    extract_and_save_profile,
    get_profile,
    get_profile_summary,
    is_asking_own_name,
    is_asking_own_profile,
    save_profile,
    clear_profile,
    is_asking_own_age,
    is_asking_own_job,
)
from core.vision_service import (
    analyze_image,
    read_receipt,
    read_slip,
    extract_raw_text,
    summarize_document_text,
)
from core.voice_service import transcribe_and_summarize

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
GENERATED_DIR = BASE_DIR / "storage" / "generated"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
(BASE_DIR / "memory").mkdir(parents=True, exist_ok=True)

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
BOT_NAME = os.getenv("BOT_NAME", "LouisAI")

LIFF_DASHBOARD_URL = "https://liff.line.me/2010485952-5MZ2C6JG/dashboard"
LIFF_PROFILE_URL = "https://liff.line.me/2010485952-5MZ2C6JG/dashboard/profile"
LIFF_PDF_CREATOR_URL = "https://liff.line.me/2010485952-5MZ2C6JG/dashboard/pdf-creator"
LIFF_TRANSACTIONS_URL = "https://liff.line.me/2010485952-5MZ2C6JG/dashboard/transactions"
LIFF_SLIPS_URL = "https://liff.line.me/2010485952-5MZ2C6JG/dashboard/slips"
LIFF_CALENDAR_URL = "https://liff.line.me/2010485952-5MZ2C6JG/dashboard/calendar"


def get_thailand_now() -> datetime:
    from datetime import timezone, timedelta
    return datetime.now(timezone(timedelta(hours=7)))

LINE_REPLY_ENDPOINT = "https://api.line.me/v2/bot/message/reply"
LINE_CONTENT_ENDPOINT = "https://api-data.line.me/v2/bot/message/{message_id}/content"

# ── Commands ──────────────────────────────────────────────────────────────────
PDF_COMMANDS = {"ทำ pdf", "ทำpdf", "รวมรูปเป็น pdf", "แปลงรูปเป็น pdf"}
RECEIPT_COMMANDS = {
    "สรุปใบเสร็จ",
    "อ่านใบเสร็จ",
    "อ่านบิล",
    "สรุปบิล",
    "ocr",
}
DOC_SUMMARY_COMMANDS = {
    "สรุปเอกสาร",
    "อ่านเอกสาร",
    "สรุปชีท",
    "สรุปไฟล์",
    "สรุปงานวิจัย",
    "สรุปวิจัย",
}
DAILY_COMMANDS = {
    "สรุปวันนี้",
    "รายจ่ายวันนี้",
    "รายรับวันนี้",
    "ใช้จ่ายวันนี้",
    "ดูวันนี้",
    "วันนี้ใช้เท่าไหร่",
    "วันนี้รับเท่าไหร่",
}
CATEGORY_COMMANDS = {
    "สรุปหมวดหมู่",
    "ดูหมวดหมู่",
    "ใช้จ่ายแต่ละหมวด",
    "รายจ่ายแต่ละหมวด",
}
SLIP_COMMANDS = {"บันทึกสลิป", "อ่านสลิป", "สแกนสลิป", "slip"}
MULTI_SLIP_COMMANDS = {
    "รวมสลิป",
    "นับสลิป",
    "รวมยอดสลิป",
    "รวมโอน",
    "เช็คยอดสลิป",
    "รวมสลิปโอนเงิน",
    "สรุปสลิป",
    "สรุปยอดสลิป",
    "เช็คยอดโอน",
    "รวมยอดโอนเงิน",
    "อ่านสลิปทั้งหมด",
    "สรุปยอดโอน",
    "รวมเงินโอน",
    "สรุปยอดจากสลิป",
    "สรุปยอดสลิปทั้งหมด",
    "เช็คสลิป",
    "อ่านสลิป",
    "สรุปเงินโอน",
}
TRANSLATE_COMMANDS = {"แปลภาษา", "แปล", "translate"}
INCOME_COMMANDS = {"รายรับ", "รับเงิน", "income", "บันทึกรายรับ"}
EXPENSE_COMMANDS = {"รายจ่าย", "จ่ายเงิน", "expense", "ค่าใช้จ่าย", "บันทึกรายจ่าย"}
SUMMARY_COMMANDS = {
    "สรุปรายรับรายจ่าย",
    "สรุปการเงิน",
    "สรุปเดือนนี้",
    "รายงานการเงิน",
    "ดูยอดรวม",
    "ยอดเดือน",
    "สรุปเดือน",
}
RECENT_COMMANDS = {
    "รายการล่าสุด",
    "ประวัติรายการ",
    "ดูรายจ่าย",
    "ดูรายรับ",
    "บันทึกรายจ่าย",
    "ดูบันทึกรายจ่าย",
    "ดูการเงิน",
    "ค่าใช้จ่าย",
}
EVENT_COMMANDS = {"นัดหมาย", "เพิ่มนัด", "ตั้งนัด", "event", "ปฏิทิน"}
EVENT_LIST_COMMANDS = {"ดูนัดหมาย", "นัดหมายทั้งหมด", "ตารางงาน"}
VOICE_COMMANDS = {"สรุปเสียง", "แปลงเสียง", "voice", "อ่านเสียง"}
KB_COMMANDS = {"knowledge base", "คลังความรู้", "อัปโหลดเอกสาร"}
KB_LIST_COMMANDS = {"ดูเอกสาร", "รายการเอกสาร", "list kb"}
KB_ASK_COMMANDS = {"ถามจากเอกสาร", "ค้นหาเอกสาร", "ask kb"}
PROFILE_COMMANDS = {"โปรไฟล์", "ข้อมูลฉัน", "ข้อมูลผม", "profile", "ข้อมูลของฉัน"}
CANCEL_COMMANDS = {"ยกเลิก", "cancel", "เริ่มใหม่"}
DONE_COMMANDS = {
    "เสร็จแล้ว",
    "ครบแล้ว",
    "สร้าง pdf",
    "สรุปแบบสั้น",
    "สรุปแบบละเอียด",
    "สรุปเพื่อสอบ",
    "สรุปเป็นข้อ",
    "สรุปเป็น mind map",
}
PDF_YES_COMMANDS = {
    "สร้าง pdf",
    "ทำ pdf",
    "เอา pdf",
    "ต้องการ pdf",
    "ใช่",
    "ຕ้องการ",
    "yes",
    "ok",
    "ตกลง",
    "pdf",
}
PDF_NO_COMMANDS = {"ไม่ต้อง", "ไม่", "ไม่เอา", "จบ", "พอแล้ว", "no", "ออก"}
HELP_COMMANDS = {"ช่วยเหลือ", "help", "เมนู", "menu"}

app = FastAPI(title=f"{BOT_NAME} LINE Bot")
app.mount("/files", StaticFiles(directory=str(GENERATED_DIR)), name="files")

start_scheduler()


class ImageDebouncer:
    def __init__(self, delay_seconds: float = 2.5):
        self.delay_seconds = delay_seconds
        self.lock = threading.Lock()
        self.batches = {}  # session_key -> {"timer": threading.Timer, "images": list[dict]}

    def add_image(self, session_key: str, temp_path: str, reply_token: str, request_base_url: str, callback) -> None:
        with self.lock:
            if session_key not in self.batches:
                self.batches[session_key] = {
                    "timer": None,
                    "images": []
                }
            batch = self.batches[session_key]
            
            # Cancel existing timer
            if batch["timer"] is not None:
                batch["timer"].cancel()
            
            # Add image to batch
            batch["images"].append({
                "temp_path": temp_path,
                "reply_token": reply_token,
                "request_base_url": request_base_url
            })
            
            # Schedule timer
            timer = threading.Timer(self.delay_seconds, self._fire, args=(session_key, callback))
            batch["timer"] = timer
            timer.start()

    def _fire(self, session_key: str, callback) -> None:
        with self.lock:
            batch = self.batches.pop(session_key, None)
        if batch and batch["images"]:
            callback(session_key, batch["images"])


image_debouncer = ImageDebouncer()


def reply_image_received_with_quick_replies(reply_token, mode, count, total_count=None):
    if total_count is None:
        total_count = count
        
    suffix = {
        "ocr_summary_pdf": "ส่งเพิ่มได้อีก หรือกดปุ่มด้านล่างให้ผมวิเคราะห์ + สรุป + สร้าง PDF ครับ",
        "doc_summary": "ส่งเพิ่มได้อีก หรือกดเลือกโหมดสรุปที่ต้องการด้านล่างได้เลยครับ",
        "slip": "ส่งเพิ่มได้อีก หรือกดปุ่มด้านล่างให้ผมอ่านและบันทึกยอดครับ",
        "pdf": "ส่งเพิ่มได้อีก หรือกดปุ่มด้านล่างเพื่อรวบรวมเป็นไฟล์ PDF ครับ",
        "compress": "ส่งเพิ่มได้อีก หรือกดปุ่มด้านล่างเมื่อย่อรูปครบตามที่ต้องการแล้วครับ",
    }.get(mode, "ส่งเพิ่มได้อีก หรือกดปุ่มด้านล่างเพื่อดำเนินการต่อครับ")
    
    text = f"📥 ได้รับรูปภาพเพิ่ม {count} รูปแล้วครับ (รวมทั้งหมด {total_count} รูป)\n{suffix}"
    
    if mode == "doc_summary":
        items = [
            {"label": "📝 สรุปแบบสั้น", "text": "สรุปแบบสั้น"},
            {"label": "📖 สรุปแบบละเอียด", "text": "สรุปแบบละเอียด"},
            {"label": "🎓 สรุปเพื่อสอบ", "text": "สรุปเพื่อสอบ"},
            {"label": "📋 สรุปเป็นข้อ", "text": "สรุปเป็นข้อ"},
            {"label": "🧠 สรุปเป็น mind map", "text": "สรุปเป็น mind map"},
            {"label": "❌ ยกเลิก", "text": "ยกเลิก"}
        ]
    else:
        items = [
            {"label": "✅ เสร็จแล้ว", "text": "เสร็จแล้ว"},
            {"label": "❌ ยกเลิก", "text": "ยกเลิก"}
        ]
        
    reply_text_with_quick_replies(reply_token, text, items)


def reply_slip_selection_flex(reply_token: str, total: float, slip_data: list[dict], session_key: str) -> None:
    item_contents = []
    for idx, d in enumerate(slip_data, 1):
        bank = d.get("bank") or "สลิปโอนเงิน"
        amt = d.get("amount") or 0.0
        sender = d.get("sender") or ""
        receiver = d.get("receiver") or ""
        
        if amt > 0:
            amt_text = f"{amt:,.2f} บาท"
            amt_color = "#60A5FA"
        else:
            amt_text = "อ่านยอดไม่ได้"
            amt_color = "#F87171"
            
        contents = [
            {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                    {"type": "text", "text": f"{idx}. {bank}", "size": "sm", "color": "#F1F5F9", "weight": "bold", "flex": 6},
                    {"type": "text", "text": amt_text, "size": "sm", "color": amt_color, "weight": "bold", "align": "end", "flex": 4}
                ]
            }
        ]
        
        if sender or receiver:
            s_name = sender if sender else "ไม่ระบุ"
            r_name = receiver if receiver else "ไม่ระบุ"
            contents.append({
                "type": "text",
                "text": f"👤 {s_name} ➔ 👤 {r_name}",
                "size": "xxs",
                "color": "#94A3B8",
                "margin": "xs",
                "wrap": True
            })
            
        item_contents.append({
            "type": "box",
            "layout": "vertical",
            "margin": "sm",
            "backgroundColor": "#0F172A",
            "cornerRadius": "md",
            "paddingAll": "8px",
            "borderColor": "#334155",
            "borderWidth": "1px",
            "contents": contents
        })

    contents = {
      "type": "bubble",
      "size": "mega",
      "body": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#0F172A",
        "paddingAll": "12px",
        "contents": [
          {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1E293B",
            "cornerRadius": "20px",
            "borderColor": "#334155",
            "borderWidth": "1px",
            "paddingAll": "16px",
            "contents": [
              {
                "type": "text",
                "text": "📊 สรุปยอดสลิปทั้งหมด",
                "weight": "bold",
                "size": "lg",
                "color": "#FFFFFF"
              },
              {
                "type": "text",
                "text": f"ตรวจสอบและยืนยันข้อมูล ({len(slip_data)} ใบ)",
                "color": "#94A3B8",
                "size": "xs",
                "margin": "xs"
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "contents": [
                  {
                    "type": "text",
                    "text": "ยอดเงินรวมสะสม",
                    "size": "xs",
                    "color": "#94A3B8"
                  },
                  {
                    "type": "text",
                    "text": f"{total:,.2f} บาท",
                    "size": "xl",
                    "weight": "bold",
                    "color": "#60A5FA",
                    "margin": "xs"
                  }
                ]
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "md"
              },
              {
                "type": "text",
                "text": "📋 รายละเอียดแต่ละใบ",
                "weight": "bold",
                "size": "xs",
                "color": "#94A3B8",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "sm",
                "spacing": "xs",
                "contents": item_contents[:20]
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "md"
              },
              {
                "type": "text",
                "text": "❓ บันทึกสลิปชุดนี้เป็น รายจ่าย หรือ รายรับ ดีครับ?",
                "size": "xs",
                "color": "#E2E8F0",
                "align": "center",
                "margin": "md",
                "wrap": True
              }
            ]
          }
        ]
      }
    }
    
    alt_text = f"📊 สรุปสลิป {len(slip_data)} ใบ ยอดรวม {total:,.2f} บาท"
    
    actions = [
        {"type": "action", "action": {"type": "message", "label": "🔴 บันทึกเป็นรายจ่าย", "text": "บันทึกสลิปเป็นรายจ่าย"}},
        {"type": "action", "action": {"type": "message", "label": "🟢 บันทึกเป็นรายรับ", "text": "บันทึกสลิปเป็นรายรับ"}},
        {"type": "action", "action": {"type": "message", "label": "❌ ยกเลิก", "text": "ยกเลิก"}}
    ]
    
    requests.post(
        LINE_REPLY_ENDPOINT,
        headers={
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "replyToken": reply_token,
            "messages": [
                {
                    "type": "flex",
                    "altText": alt_text,
                    "contents": contents,
                    "quickReply": {
                        "items": actions
                    }
                }
            ],
        },
        timeout=30,
    ).raise_for_status()


@app.get("/")
def health_check() -> dict[str, str | int]:
    from core.db_service import DB_PATH

    db_exists = DB_PATH.exists()
    db_size = DB_PATH.stat().st_size if db_exists else 0
    return {
        "status": "ok",
        "service": BOT_NAME,
        "db": "ok" if db_exists else "missing",
        "db_size": db_size,
    }


@app.post("/webhook/line")
async def line_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: str = Header(default="")
) -> dict[str, str]:
    if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="LINE env not configured.")
    body = await request.body()
    if not verify_line_signature(body, x_line_signature, LINE_CHANNEL_SECRET):
        raise HTTPException(status_code=401, detail="Invalid signature.")
    payload = await request.json()
    for event in payload.get("events", []):
        background_tasks.add_task(handle_event, event, request)
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
        handle_text_message(reply_token, session_key, message.get("text", ""), request, source=source)
    elif message_type == "image":
        handle_image_message(reply_token, session_key, message.get("id", ""), request)
    elif message_type == "audio":
        handle_audio_message(reply_token, session_key, message.get("id", ""), source=source)
    elif message_type == "file":
        file_name = message.get("fileName", "")
        ext = Path(file_name).suffix.lower()
        if ext in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma", ".webm"}:
            handle_audio_message(reply_token, session_key, message.get("id", ""), source=source, custom_ext=ext)
        elif ext in {".pdf", ".docx"}:
            handle_document_file_message(reply_token, session_key, message.get("id", ""), file_name, request)
        else:
            reply_text(
                reply_token,
                f"📁 ได้รับไฟล์ '{file_name}' เรียบร้อยแล้วครับ\n\n"
                "ขณะนี้ระบบยังไม่รองรับการวิเคราะห์ไฟล์ประเภทนี้โดยตรง\n"
                "หากต้องการสรุปเนื้อหา กรุณาส่งเป็นไฟล์ PDF, Word (.docx) หรือรูปภาพแทนนะครับ"
            )
    else:
        reply_main_menu(reply_token)


# ── Text ──────────────────────────────────────────────────────────────────────
def handle_text_message(reply_token, session_key, text, request, source=None):
    raw_text = text.strip()
    normalized = normalize_text(raw_text)
    session = get_session(session_key)
    state = session.get("state", "idle")
    mode = session.get("mode")
    current_mode = session.get("current_mode")
    user_id = session_key.split(":")[-1]
    line_user_id = (source or {}).get("userId") or session_key

    if normalized in CANCEL_COMMANDS:
        old = clear_session(session_key)
        cleanup_images(old.get("images", []))
        reply_text(
            reply_token,
            "✅ ยกเลิกเรียบร้อยแล้วครับ\n\n💬 ถามอะไรได้เลย หรือพิมพ์ 'เมนู' เพื่อดูสิ่งที่ผมช่วยได้ครับ",
        )
        return

    if normalized in HELP_COMMANDS:
        reply_main_menu(reply_token)
        return

    # ── ตรวจ waiting states ก่อน routing เมนู ──
    if state == "waiting_for_filename":
        safe_name = sanitize_filename(raw_text)
        if not safe_name:
            reply_text(
                reply_token,
                "⚠️ ชื่อไฟล์นั้นใช้ไม่ได้ครับ\nกรุณาใช้ตัวอักษร ตัวเลข เว้นวรรค หรือขีดกลางเท่านั้น",
            )
            return
        images = session.get("images", [])
        if not images:
            clear_session(session_key)
            reply_text(reply_token, "⚠️ ไม่พบรูปครับ เริ่มใหม่ได้เลยครับ")
            return
        pdf_filename = f"{safe_name}.pdf"
        output_path = GENERATED_DIR / pdf_filename
        try:
            build_pdf_from_images(images, str(output_path))
            file_url = build_file_url(request, pdf_filename)
            title = "รวมรูปภาพเป็น PDF"
            reply_pdf_success(reply_token, title, safe_name, f"{len(images)} รูป", file_url)
            clear_session(session_key)
            cleanup_images(images)
        except Exception as exc:
            reply_text(reply_token, f"เกิดปัญหาสร้าง PDF ครับ: {exc}")
        return

    # ── Sub-menu Routing ──
    if reply_submenu(reply_token, normalized):
        return

    # ── User Profile ──
    if normalized in PROFILE_COMMANDS:
        _reply_profile(reply_token, line_user_id)
        return

    # ── ถามชื่อตัวเอง ──
    if is_asking_own_name(normalized):
        profile = get_profile(line_user_id)
        if profile.get("name"):
            reply_text(reply_token, f"คุณชื่อ {profile['name']} ครับ 😊")
        else:
            reply_text(
                reply_token,
                "คุณยังไม่ได้ตั้งค่าโปรไฟล์ สามารถเข้าไปแก้ไขข้อมูลได้ทันทีที่หน้าเว็บแดชบอร์ดครับ!\n"
                f"🌐 หรือกดที่นี่เพื่อตั้งค่าโปรไฟล์: {LIFF_PROFILE_URL}"
            )
        return

    # ── ถามอายุตัวเอง ──
    if is_asking_own_age(normalized):
        profile = get_profile(line_user_id)
        if profile.get("age"):
            reply_text(reply_token, f"คุณอายุ {profile['age']} ปีครับ 🎂")
        else:
            reply_text(
                reply_token,
                "คุณยังไม่ได้ตั้งค่าโปรไฟล์ สามารถเข้าไปแก้ไขข้อมูลได้ทันทีที่หน้าเว็บแดชบอร์ดครับ!\n"
                f"🌐 หรือกดที่นี่เพื่อตั้งค่าโปรไฟล์: {LIFF_PROFILE_URL}"
            )
        return

    # ── ถามอาชีพตัวเอง ──
    if is_asking_own_job(normalized):
        profile = get_profile(line_user_id)
        if profile.get("job"):
            reply_text(reply_token, f"คุณทำอาชีพ {profile['job']} ครับ 💼")
        else:
            reply_text(
                reply_token,
                "คุณยังไม่ได้ตั้งค่าโปรไฟล์ สามารถเข้าไปแก้ไขข้อมูลได้ทันทีที่หน้าเว็บแดชบอร์ดครับ!\n"
                f"🌐 หรือกดที่นี่เพื่อตั้งค่าโปรไฟล์: {LIFF_PROFILE_URL}"
            )
        return

    if is_asking_own_profile(normalized):
        _reply_profile(reply_token, line_user_id)
        return

    # ── แก้ไขโปรไฟล์โดยตรง ──
    if normalized in {"ล้างข้อมูลของฉัน", "ลบข้อมูลของฉัน"}:
        clear_profile(line_user_id)
        reply_text(reply_token, "🗑️ ล้างข้อมูลของคุณเรียบร้อยแล้วครับ")
        return

    m_name = re.match(r"^เปลี่ยนชื่อเป็น\s*(.+)", raw_text)
    if m_name:
        new_name = m_name.group(1).strip()
        save_profile(line_user_id, {"name": new_name})
        reply_text(reply_token, f"✅ เปลี่ยนชื่อเป็นคุณ {new_name} เรียบร้อยแล้วครับ")
        return

    m_age = re.match(r"^เปลี่ยนอายุเป็น\s*(.+)", raw_text)
    if m_age:
        new_age = m_age.group(1).strip()
        new_age = re.sub(r"\s*ปี\s*$", "", new_age).strip()
        save_profile(line_user_id, {"age": new_age})
        reply_text(reply_token, f"✅ เปลี่ยนอายุเป็น {new_age} ปี เรียบร้อยแล้วครับ")
        return

    m_job = re.match(r"^เปลี่ยนอาชีพเป็น\s*(.+)", raw_text)
    if m_job:
        new_job = m_job.group(1).strip()
        save_profile(line_user_id, {"job": new_job})
        reply_text(reply_token, f"✅ เปลี่ยนอาชีพเป็น {new_job} เรียบร้อยแล้วครับ")
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

    if re.match(r"^(อัปโหลดเอกสาร|บันทึกเอกสาร|เพิ่มเอกสาร)\s*[:：]?\s+\S", normalized):
        sep = ":" if ":" in raw_text else " "
        doc_name = raw_text.split(sep, 1)[-1].strip()
        if not doc_name:
            doc_name = (
                raw_text.split(None, 1)[-1].strip() if " " in raw_text else "เอกสาร"
            )
        s = get_session(session_key)
        s["pending_kb"] = doc_name
        reply_text(
            reply_token,
            f"📚 ได้เลยครับ ส่งรูปเอกสาร '{doc_name}' มาได้เลย\n"
            "ผมจะอ่านข้อความและบันทึกไว้ให้ถามทีหลังได้ครับ",
        )
        return

    if re.match(r"^ถามจากเอกสาร\s*:", normalized):
        question = raw_text.split(":", 1)[-1].strip()
        reply_text(reply_token, ask_with_knowledge(session_key, question, ask_ai))
        return

    # ── PDF flows ──
    if _is_pdf_cmd(normalized):
        reply_pdf_creator_link(reply_token)
        return
    if normalized in RECEIPT_COMMANDS:
        restart_flow(reply_token, session_key, "ocr_summary_pdf")
        return
    if normalized in DOC_SUMMARY_COMMANDS:
        restart_flow(reply_token, session_key, "doc_summary")
        return
    if normalized in SLIP_COMMANDS:
        restart_flow(reply_token, session_key, "slip")
        return

    # ── Voice ──
    if normalized in VOICE_COMMANDS:
        reply_premium_info_flex(
            reply_token,
            title="🎙️ แปลงเสียงเป็นข้อความ",
            subtitle="ส่งไฟล์เสียงหรือบันทึกเสียงมาได้เลยครับ",
            bullet_points=[
                "รองรับไฟล์เสียงบันทึกโดยตรงจากไลน์",
                "ถอดรหัสเสียงเป็นข้อความได้รวดเร็ว แม่นยำ",
                "วิเคราะห์และสรุปประเด็นด้วย AI"
            ]
        )
        return

    # ── Translate ──
    if normalized in TRANSLATE_COMMANDS:
        reply_premium_info_flex(
            reply_token,
            title="🌐 แปลภาษา (Translate)",
            subtitle="พิมพ์ข้อความที่ต้องการแปลได้เลยครับ",
            bullet_points=[
                "แปลระหว่างภาษาไทยและภาษาอังกฤษ",
                "ตัวอย่าง: พิมพ์ 'แปลเป็นอังกฤษ: สวัสดี'",
                "ตัวอย่าง: พิมพ์ 'แปลเป็นไทย: Hello'"
            ]
        )
        return
    if re.match(r"^แปลเป็น|^translate to", normalized):
        reply_text(reply_token, ask_ai(f"แปลข้อความนี้ ตอบแค่คำแปลเท่านั้น:\n{raw_text}"))
        return

    if normalized in {"ล้างแชท", "ลบประวัติแชท", "clear chat"}:
        clear_chat_history(session_key)
        reply_text(reply_token, "ล้างประวัติการสนทนาแล้วครับ 🗑")
        return

    if normalized in {"ล้างโปรไฟล์", "ลบโปรไฟล์", "reset profile", "เริ่มใหม่โปรไฟล์", "ล้างข้อมูลของฉัน"}:
        from core.db_service import get_conn

        with get_conn() as c:
            c.execute("DELETE FROM user_profile WHERE user_id=?", (line_user_id,))
        reply_text(reply_token, "✅ ลบข้อมูลส่วนตัวของคุณเรียบร้อยแล้วครับ บอกชื่อ/อายุ/อาชีพใหม่ได้เลยครับ")
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

    # ── Daily summary ──
    if normalized in DAILY_COMMANDS:
        _show_daily_summary(reply_token, user_id, get_thailand_now().strftime("%Y-%m-%d"))
        return

    # สรุปวันที่ DD/MM
    _dm = re.match(r"^(สรุป|ดู|รายจ่าย|รายรับ)\s*(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?$", normalized)
    if _dm:
        _d, _mo = int(_dm.group(2)), int(_dm.group(3))
        _yr = int(_dm.group(4)) if _dm.group(4) else get_thailand_now().year
        if _yr < 100:
            _yr += 2000
        try:
            _date_str = f"{_yr:04d}-{_mo:02d}-{_d:02d}"
            _show_daily_summary(reply_token, user_id, _date_str)
        except Exception:
            reply_text(reply_token, "วันที่ไม่ถูกต้องครับ เช่น 'สรุป 15/7' หรือ 'สรุป 15/7/2025'")
        return

    if normalized in CATEGORY_COMMANDS:
        reply_category_summary(reply_token, user_id)
        return

    if normalized in SUMMARY_COMMANDS:
        reply_finance_summary(reply_token, user_id)
        return

    if normalized in {"รายจ่ายทั้งหมด", "ดูรายจ่ายทั้งหมด"}:
        _show_all_transactions(reply_token, user_id, "expense")
        return

    if normalized in {"รายรับทั้งหมด", "ดูรายรับทั้งหมด"}:
        _show_all_transactions(reply_token, user_id, "income")
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

    # ── Commands: ดูสลิปทั้งหมด, สลิปที่ N, ยอดรวมสลิป ──
    if normalized in {"ดูสลิปทั้งหมด", "ดูสลิป"}:
        slips = get_latest_slip_batch(user_id)
        if not slips:
            reply_text(reply_token, "📭 ยังไม่มีข้อมูลสลิปที่บันทึกไว้ในครั้งล่าสุดครับ")
            return
        lines = ["📊 รายการสลิปล่าสุด:", "─" * 28]
        for idx, s in enumerate(slips, 1):
            amt = s.get("amount") or 0.0
            bank = s.get("bank", "ไม่ระบุ")
            sender = s.get("sender") or ""
            receiver = s.get("receiver") or ""
            line = f"{idx}. {amt:,.2f} บาท ({bank})"
            if sender or receiver:
                line += f"\n   👤 {sender or 'ไม่ระบุ'} ➔ 👤 {receiver or 'ไม่ระบุ'}"
            lines.append(line)
        reply_text(reply_token, "\n".join(lines))
        return

    m = re.match(r"^สลิปที่\s*(\d+)$", normalized)
    if m:
        idx = int(m.group(1))
        slips = get_latest_slip_batch(user_id)
        if not slips:
            reply_text(reply_token, "📭 ยังไม่มีข้อมูลสลิปที่บันทึกไว้ในครั้งล่าสุดครับ")
            return
        if 1 <= idx <= len(slips):
            s = slips[idx - 1]
            amt = s.get("amount") or 0.0
            bank = s.get("bank", "ไม่ระบุ")
            ref = s.get("ref") or "ไม่ระบุ"
            dt = s.get("datetime") or "ไม่ระบุ"
            sender = s.get("sender") or "ไม่ระบุ"
            receiver = s.get("receiver") or "ไม่ระบุ"
            msg = (
                f"🧾 ข้อมูลสลิปใบที่ {idx}:\n"
                f"💰 ยอดเงิน: {amt:,.2f} บาท\n"
                f"🏦 ธนาคาร: {bank}\n"
                f"👤 ผู้โอน: {sender}\n"
                f"👤 ผู้รับ: {receiver}\n"
                f"🔖 อ้างอิง: {ref}\n"
                f"🕐 วันที่: {dt}"
            )
            reply_text(reply_token, msg)
        else:
            reply_text(reply_token, f"⚠️ ไม่พบสลิปใบที่ {idx} ครับ (ครั้งล่าสุดมีทั้งหมด {len(slips)} ใบ)")
        return

    if normalized == "ยอดรวมสลิป":
        slips = get_latest_slip_batch(user_id)
        if not slips:
            reply_text(reply_token, "📭 ยังไม่มีข้อมูลสลิปที่บันทึกไว้ในครั้งล่าสุดครับ")
            return
        total = sum(s.get("amount") or 0.0 for s in slips)
        reply_text(reply_token, f"💰 ยอดรวมสลิปของครั้งล่าสุด:\n{total:,.2f} บาท")
        return

    # ── Multi-slip command ──
    if _is_multi_slip_cmd(normalized):
        restart_flow(reply_token, session_key, "multi_slip")
        return

    # ── Waiting states ──
    if state == "waiting_for_images":
        if normalized in DONE_COMMANDS:
            images = session.get("images", [])
            extracted_text = session.get("extracted_text", "")
            
            if not images and not extracted_text:
                reply_text(reply_token, "📎 ยังไม่มีรูปหรือเอกสารครับ ส่งอย่างน้อย 1 รูปหรือไฟล์ PDF/Word ก่อนเลยครับ")
                return
                
            if mode == "multi_slip":
                _prompt_slip_type_selection(reply_token, session_key)
                return
            if mode == "ocr_summary_pdf":
                _process_and_summarize_receipts(reply_token, session_key)
                return
            if mode == "doc_summary":
                if extracted_text and not images:
                    _summarize_extracted_text(reply_token, session_key, extracted_text, summary_type=raw_text)
                else:
                    _process_and_summarize_docs(reply_token, session_key, summary_type=raw_text)
                return
            set_waiting_for_filename(session_key)
            reply_text(
                reply_token,
                f"✅ รับรูปครบแล้ว {len(images)} รูปครับ\nตั้งชื่อไฟล์ได้เลยครับ เช่น รูป-2026",
            )
            return
        # ถ้าไม่ใช่คำสั่ง ตอบคำถามผ่าน AI ได้ (ไม่ reset state)
        hint = f"[{waiting_msg(mode)}]"
        reply_text(reply_token, ask_ai(f"{raw_text}\n{hint}", user_id=user_id, profile_user_id=line_user_id))
        return

    # ── รอยืนยันประเภทสลิป (รายรับ/รายจ่าย) ──
    if state == "waiting_for_slip_type":
        if normalized == "บันทึกสลิปเป็นรายจ่าย":
            _process_and_summarize_slips(reply_token, session_key, user_id, type_="expense")
        elif normalized == "บันทึกสลิปเป็นรายรับ":
            _process_and_summarize_slips(reply_token, session_key, user_id, type_="income")
        elif normalized in CANCEL_COMMANDS:
            old = clear_session(session_key)
            cleanup_images(old.get("images", []))
            reply_text(reply_token, "✅ ยกเลิกการบันทึกสลิปเรียบร้อยแล้วครับ")
        else:
            session = get_session(session_key)
            slip_data = session.get("slip_data", [])
            total = sum(d.get("amount", 0) or 0 for d in slip_data)
            reply_text_with_quick_replies(
                reply_token,
                f"⚠️ กรุณาเลือกประเภทรายการ (ยอดรวม {total:,.2f} บาท) โดยกดปุ่มด้านล่างครับ:",
                [
                    {"label": "🔴 บันทึกเป็นรายจ่าย", "text": "บันทึกสลิปเป็นรายจ่าย"},
                    {"label": "🟢 บันทึกเป็นรายรับ", "text": "บันทึกสลิปเป็นรายรับ"},
                    {"label": "❌ ยกเลิก", "text": "ยกเลิก"}
                ]
            )
        return

    # ── รอยืนยันว่าจะสร้าง PDF ไหม ──
    if state == "waiting_for_pdf_confirm":
        if normalized in PDF_YES_COMMANDS or "pdf" in normalized:
            _create_confirmed_pdf(reply_token, session_key, mode, request)
        elif normalized in PDF_NO_COMMANDS:
            old = clear_session(session_key)
            cleanup_images(old.get("images", []))
            reply_text(reply_token, "✅ เรียบร้อยแล้วครับ\n💬 ถามอะไรเพิ่มเติมได้เลยครับ")
        else:
            reply_text(
                reply_token,
                "ต้องการสร้างรายงาน PDF ไหมครับ?\n"
                "✅ 'สร้าง PDF' → สร้างรายงานพร้อมรูป\n"
                "❌ 'ไม่ต้อง' → จบเลยครับ",
            )
        return



    # ── Auto extract profile ──
    saved = extract_and_save_profile(line_user_id, raw_text)
    if saved:
        # สร้างข้อความยืนยันการจำชื่อแทนที่จะส่งไปหา AI
        lines = ["✅ จำแล้วครับ"]
        if saved.get("name"):
            lines.append(f"👤 ชื่อ: {saved['name']}")
        if saved.get("age"):
            lines.append(f"🎂 อายุ: {saved['age']} ปี")
        if saved.get("job"):
            lines.append(f"💼 อาชีพ: {saved['job']}")
        if saved.get("location"):
            lines.append(f"📍 ที่อยู่: {saved['location']}")
        lines.append("💬 ถามอะไรได้เลยครับ")
        reply_text(reply_token, "\n".join(lines))
        return

    # ── Default AI ──
    # ถ้ามีเอกสารใน KB ใช้ความรู้จากเอกสารด้วย
    from core.knowledge_base import list_documents

    if list_documents(session_key):
        reply_text(reply_token, ask_with_knowledge(session_key, raw_text, ask_ai, profile_user_id=line_user_id))
    else:
        reply_text(reply_token, ask_ai(raw_text, user_id=session_key, profile_user_id=line_user_id))


# ── Image ─────────────────────────────────────────────────────────────────────
def handle_image_message(reply_token, session_key, message_id, request):
    try:
        content, content_type = download_line_message_content(message_id)
        ext = guess_extension(content_type)
        temp_path = UPLOAD_DIR / f"raw_recv_{uuid.uuid4().hex}{ext}"
        temp_path.write_bytes(content)
    except Exception as e:
        print(f"[LINE] Error downloading message content {message_id}: {e}")
        return

    request_base_url = str(request.base_url) if request else ""
    image_debouncer.add_image(
        session_key=session_key,
        temp_path=str(temp_path),
        reply_token=reply_token,
        request_base_url=request_base_url,
        callback=process_image_batch
    )


def image_received_msg_suffix(mode):
    return {
        "ocr_summary_pdf": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' ให้ผมวิเคราะห์ + สรุป + สร้าง PDF ครับ",
        "doc_summary": "ส่งเพิ่มได้อีก หรือพิมพ์เลือกโหมดสรุป (เช่น 'สรุปแบบสั้น' / 'เสร็จแล้ว') ได้เลยครับ",
        "slip": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' ให้ผมอ่านและบันทึกยอดครับ",
        "pdf": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' เพื่อรวบรวมเป็นไฟล์ PDF ครับ",
        "compress": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' เมื่อย่อรูปครบตามที่ต้องการแล้วครับ",
    }.get(mode, "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' ให้ผมสร้าง PDF ครับ")


def process_image_batch(session_key, batch_images):
    if not batch_images:
        return
        
    latest_reply_token = batch_images[-1]["reply_token"]
    request_base_url = batch_images[-1]["request_base_url"]
    
    session = get_session(session_key)
    state = session.get("state", "idle")
    mode = session.get("mode")
    
    user_dir = UPLOAD_DIR / session_key.replace(":", "_")
    user_dir.mkdir(parents=True, exist_ok=True)
    
    permanent_paths = []
    for item in batch_images:
        temp_path = Path(item["temp_path"])
        if temp_path.exists():
            ext = temp_path.suffix
            perm_path = user_dir / f"{uuid.uuid4().hex}{ext}"
            try:
                shutil.move(str(temp_path), str(perm_path))
                permanent_paths.append(str(perm_path))
            except Exception as e:
                print(f"[LINE] Error moving file {temp_path} to {perm_path}: {e}")
                temp_path.unlink(missing_ok=True)
                
    if not permanent_paths:
        return
        
    if state != "waiting_for_images":
        # Outside flow: Auto-detect
        slips_found = []
        for img_path in permanent_paths:
            try:
                ocr_text = extract_text_from_images([img_path])
            except Exception:
                ocr_text = ""
            if ocr_text and _looks_like_slip(ocr_text):
                try:
                    slip = parse_slip(ocr_text)
                    amount = slip.get("amount") or 0.0
                    slips_found.append({
                        "amount": amount,
                        "bank": slip.get("bank", ""),
                        "ref": slip.get("ref", ""),
                        "date": slip.get("datetime", ""),
                        "img_path": img_path,
                    })
                except Exception:
                    slips_found.append({
                        "amount": 0.0,
                        "bank": "",
                        "ref": "",
                        "date": "",
                        "img_path": img_path,
                    })
                    
        if slips_found:
            start_pdf_flow(session_key, mode="multi_slip")
            for img_path in permanent_paths:
                add_image(session_key, img_path)
                
            parsed_paths = {s["img_path"] for s in slips_found}
            for img_path in permanent_paths:
                if img_path in parsed_paths:
                    slip_entry = next(s for s in slips_found if s["img_path"] == img_path)
                    add_slip_entry(session_key, slip_entry)
                else:
                    add_slip_entry(session_key, {
                        "amount": 0.0,
                        "bank": "",
                        "ref": "",
                        "date": "",
                        "img_path": img_path,
                    })
            _prompt_slip_type_selection(latest_reply_token, session_key)
            return
            
        if len(permanent_paths) == 1:
            img_path = permanent_paths[0]
            pending_kb = get_session(session_key).get("pending_kb", "")
            if pending_kb:
                try:
                    ocr_text = extract_text_from_images([img_path])
                    if ocr_text:
                        add_document(session_key, pending_kb, ocr_text, {"source": pending_kb})
                        s = get_session(session_key)
                        s.pop("pending_kb", None)
                        reply_text(latest_reply_token, f"📚 บันทึกเอกสาร '{pending_kb}' แล้วครับ")
                    else:
                        reply_text(latest_reply_token, "อ่านข้อความไม่ได้ครับ รูปไม่ชัดพอ")
                except Exception as e:
                    reply_text(latest_reply_token, f"เกิดข้อผิดพลาดครับ: {e}")
                finally:
                    Path(img_path).unlink(missing_ok=True)
                return
                
            try:
                analysis = analyze_image(img_path)
                reply_text(latest_reply_token, f"🔍 วิเคราะห์รูป:\n{analysis}")
            except Exception:
                reply_text(
                    latest_reply_token,
                    "📸 รับรูปแล้วครับ\n\n"
                    "🧾 ถ้าเป็นสลิปโอนเงิน → พิมพ์ 'รวมสลิป'\n"
                    "📄 ถ้าต้องการรวมรูปเป็น PDF → พิมพ์ 'ทำ PDF'\n"
                    "🔍 ถ้าต้องการอ่านเอกสาร → พิมพ์ 'สรุปใบเสร็จ'",
                )
            Path(img_path).unlink(missing_ok=True)
            return
        else:
            cleanup_images(permanent_paths)
            reply_pdf_creator_link(latest_reply_token)
            return
            
    else:
        # Inside flow
        if mode == "compress":
            try:
                from PIL import Image as PILImage
                from PIL import ImageOps as PILOps
                import io
                
                compressed_urls = []
                for img_path in permanent_paths:
                    img = PILImage.open(img_path)
                    img = PILOps.exif_transpose(img)
                    img.thumbnail((1280, 1280))
                    
                    filename = f"compressed-{uuid.uuid4().hex}.jpg"
                    out_path = GENERATED_DIR / filename
                    img.save(out_path, "JPEG", optimize=True, quality=85)
                    
                    file_url = build_file_url(request_base_url, filename)
                    compressed_urls.append(file_url)
                    
                messages = []
                for url in compressed_urls[:4]:
                    messages.append({
                        "type": "image",
                        "originalContentUrl": url,
                        "previewImageUrl": url
                    })
                    
                links_text = "\n".join(f"🔗 {url}" for url in compressed_urls)
                txt_msg = f"✅ ย่อขนาดรูปภาพเรียบร้อยแล้วครับ! ({len(compressed_urls)} รูป)\n\n{links_text}\n\nส่งรูปเพิ่มเติมเพื่อย่อต่อได้เลย หรือกดปุ่ม 'เสร็จแล้ว' เพื่อเสร็จสิ้นครับ"
                messages.append({
                    "type": "text",
                    "text": txt_msg[:5000],
                    "quickReply": {
                        "items": [
                            {"type": "action", "action": {"type": "message", "label": "✅ เสร็จแล้ว", "text": "เสร็จแล้ว"}},
                            {"type": "action", "action": {"type": "message", "label": "❌ ยกเลิก", "text": "ยกเลิก"}}
                        ]
                    }
                })
                
                requests.post(
                    LINE_REPLY_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "replyToken": latest_reply_token,
                        "messages": messages,
                    },
                    timeout=30,
                ).raise_for_status()
            except Exception as e:
                reply_text(latest_reply_token, f"เกิดข้อผิดพลาดในการย่อรูปครับ: {e}")
            finally:
                cleanup_images(permanent_paths)
            return
        if mode != "multi_slip":
            for img_path in permanent_paths:
                updated_session = add_image(session_key, img_path)
            count = len(updated_session.get("images", []))
            reply_image_received_with_quick_replies(
                reply_token=latest_reply_token,
                mode=mode,
                count=len(permanent_paths),
                total_count=count
            )
            return
            
        if mode == "multi_slip":
            new_slips = []
            for img_path in permanent_paths:
                add_image(session_key, img_path)
                try:
                    slip = read_slip(img_path)
                    amount = slip.get("amount") or 0.0
                except Exception:
                    slip = {"amount": 0.0, "bank": "", "ref": "", "datetime": ""}
                    amount = 0.0
                    
                updated_session = add_slip_entry(
                    session_key,
                    {
                        "amount": amount,
                        "bank": slip.get("bank", ""),
                        "ref": slip.get("ref", ""),
                        "date": slip.get("datetime", ""),
                        "img_path": img_path,
                    }
                )
                new_slips.append((amount, slip.get("bank", "")))
                
            slip_amounts = updated_session.get("slip_amounts", [])
            total = sum(a for a in slip_amounts if a)
            n = len(slip_amounts)
            
            lines = [f"📥 ได้รับสลิปเพิ่ม {len(permanent_paths)} ใบ (รวมทั้งหมด {n} ใบ)"]
            for idx, (amt, bank) in enumerate(new_slips, 1):
                bank_str = f" ({bank})" if bank else ""
                lines.append(f"• ใบที่ {n - len(permanent_paths) + idx}: {amt:,.2f} บาท{bank_str}")
            lines.append(f"\n💰 ยอดสะสม: {total:,.2f} บาท")
            lines.append("\nส่งสลิปเพิ่มได้อีก หรือกดปุ่ม 'เสร็จแล้ว' ด้านล่าง")
            
            reply_text_with_quick_replies(
                latest_reply_token,
                "\n".join(lines),
                [
                    {"label": "✅ เสร็จแล้ว", "text": "เสร็จแล้ว"},
                    {"label": "❌ ยกเลิก", "text": "ยกเลิก"}
                ]
            )
            return


# ── Slip / Receipt processors ─────────────────────────────────────────────────
def _prompt_slip_type_selection(reply_token: str, session_key: str) -> None:
    """แสดงสรุปยอดรวมของสลิปในแชท ด้วย Flex Message ดีไซน์พรีเมียม พร้อมส่ง Quick Reply"""
    session = get_session(session_key)
    images = session.get("images", [])
    slip_data = session.get("slip_data", [])

    # fallback: ถ้า slip_data ยังไม่ครบ (กรณี session เก่า)
    if len(slip_data) < len(images):
        missing = images[len(slip_data) :]
        for img_path in missing:
            try:
                data = read_slip(img_path)
                slip_data.append(data)
            except Exception:
                slip_data.append({"amount": 0.0, "bank": "", "ref": "", "datetime": "", "sender": "", "receiver": ""})
    total = sum(d.get("amount", 0) or 0 for d in slip_data)

    set_waiting_for_slip_type(session_key, slip_data)
    
    try:
        reply_slip_selection_flex(reply_token, total, slip_data, session_key)
    except Exception as e:
        print(f"[LINE] Flex selection error: {e}, falling back to text.")
        valid_count = sum(1 for d in slip_data if d.get("amount"))
        lines = [
            f"📊 สรุปยอดสลิปทั้งหมด ({len(slip_data)} ใบ)",
            "─" * 28,
            f"💰 ยอดรวมสะสม: {total:,.2f} บาท",
            f"✅ อ่านสำเร็จ: {valid_count} ใบ",
            "─" * 28,
            "❓ ต้องการบันทึกสลิปชุดนี้เป็น รายจ่าย หรือ รายรับ ดีครับ? (กรุณากดเลือกปุ่มด้านล่าง)"
        ]
        reply_text_with_quick_replies(
            reply_token,
            "\n".join(lines),
            [
                {"label": "🔴 บันทึกเป็นรายจ่าย", "text": "บันทึกสลิปเป็นรายจ่าย"},
                {"label": "🟢 บันทึกเป็นรายรับ", "text": "บันทึกสลิปเป็นรายรับ"},
                {"label": "❌ ยกเลิก", "text": "ยกเลิก"}
            ]
        )


def _process_and_summarize_slips(
    reply_token: str, session_key: str, user_id: str, type_: str = "expense"
) -> None:
    """สรุปสลิปจาก slip_data ที่เก็บไว้แล้ว (ไม่ต้อง re-read)"""
    session = get_session(session_key)
    images = session.get("images", [])
    try:
        # ใช้ slip_data ที่เก็บไว้ตอนรับรูปก่อนเลย – ไม่ต้อง API call เพิ่ม
        slip_data: list[dict] = session.get("slip_data", [])

        # fallback: ถ้า slip_data ยังไม่ครบ (session เก่าแบบที่ไม่มี slip_data)
        if len(slip_data) < len(images):
            missing = images[len(slip_data) :]
            for img_path in missing:
                try:
                    data = read_slip(img_path)
                    slip_data.append(data)
                except Exception:
                    slip_data.append({"amount": 0.0, "bank": "", "ref": "", "datetime": "", "sender": "", "receiver": ""})
        else:
            # ข้อมูลครบพร้อมแล้ว ไม่ต้องรอ
            pass

        total = sum(d.get("amount", 0) or 0 for d in slip_data)
        valid = [(i + 1, d) for i, d in enumerate(slip_data) if d.get("amount")]
        failed = [i + 1 for i, d in enumerate(slip_data) if not d.get("amount")]

        lines = [f"📊 สรุปสลิปโอนเงิน", "─" * 28]
        for idx, d in valid:
            bank = d.get("bank", "")
            amt = d.get("amount", 0)
            date = d.get("datetime") or d.get("date") or ""
            sender = d.get("sender") or ""
            receiver = d.get("receiver") or ""
            line = f"{idx}. {amt:,.2f} บาท"
            if bank:
                line += f" ({bank})"
            if date:
                line += f" [{date}]"
            if sender or receiver:
                line += f"\n   👤 {sender or 'ไม่ระบุ'} ➔ 👤 {receiver or 'ไม่ระบุ'}"
            lines.append(line)
        for idx in failed:
            lines.append(f"{idx}. — (อ่านไม่ได้)")
        lines += [
            "─" * 28,
            f"💰 ยอดรวมทั้งหมด",
            f"{total:,.2f} บาท",
        ]
        if total > 0:
            for idx, d in valid:
                ref_val = d.get("ref") or ""
                note_str = f"Parsed from slip: Ref {ref_val}" if ref_val else "Parsed from slip"
                add_transaction(
                    user_id=user_id,
                    type_=type_,
                    amount=d.get("amount", 0.0),
                    category="Transfer",
                    note=note_str
                )
            thai_type = "รายจ่าย" if type_ == "expense" else "รายรับ"
            lines.append(f"✅ บันทึก{thai_type} {len(valid)} รายการแล้ว")

        # บันทึกข้อมูลสลิปแต่ละใบลงฐานข้อมูลแยกกัน
        batch_id = session.get("batch_id") or uuid.uuid4().hex
        for d in slip_data:
            save_slip(
                user_id=user_id,
                amount=d.get("amount"),
                bank=d.get("bank", ""),
                ref=d.get("ref", ""),
                dt=d.get("datetime") or d.get("date") or "",
                raw_text=json.dumps(d),
                batch_id=batch_id,
                sender=d.get("sender", ""),
                receiver=d.get("receiver", ""),
            )

        reply_text(reply_token, "\n".join(lines))
    finally:
        clear_session(session_key)
        cleanup_images(images)


def _process_and_summarize_receipts(reply_token: str, session_key: str) -> None:
    """อ่านใบเสร็จทั้งหมด → สรุปในแชท → ถาม PDF"""
    session = get_session(session_key)
    images = session.get("images", [])

    summaries: list[str] = []
    for img_path in images:
        try:
            s = read_receipt(img_path)
            summaries.append(s)
        except Exception as e:
            summaries.append(f"(อ่านไม่ได้: {e})")

    if len(images) == 1:
        # ใบเสร็จใบเดียว: แสดงสรุปตรงๆ
        msg = f"🧾 สรุปใบเสร็จ\n\n{summaries[0]}\n\n─ ─ ─\nต้องการ PDF ไหมครับ?\n✅ 'สร้าง PDF'  ❌ 'ไม่ต้อง'"
    else:
        # หลายใบ: แสดงทีละใบ
        parts = [f"🧾 สรุปเอกสาร {len(images)} รายการ"]
        for i, s in enumerate(summaries, 1):
            parts.append(f"\n─ เอกสารที่ {i} ─\n{s}")
        parts.append(f"\n─ ─ ─\nต้องการ PDF ไหมครับ?\n✅ 'สร้าง PDF'  ❌ 'ไม่ต้อง'")
        msg = "\n".join(parts)
    reply_text(reply_token, msg[:5000])
    set_waiting_for_pdf_confirm(session_key, receipt_summaries=summaries)


def _process_and_summarize_docs(reply_token: str, session_key: str, summary_type: str) -> None:
    """อ่านเอกสารทั้งหมดด้วย OCR -> สรุปด้วย AI ตามรูปแบบ -> ถามความต้องการสร้าง PDF"""
    session = get_session(session_key)
    images = session.get("images", [])

    # 1. OCR ทุกรูปภาพและนำข้อความมารวมกัน
    all_texts = []
    for idx, img_path in enumerate(images, 1):
        try:
            txt = extract_raw_text(img_path)
            if txt:
                all_texts.append(f"--- หน้าที่ {idx} ---\n{txt}")
        except Exception:
            pass

    document_text = "\n\n".join(all_texts)
    if not document_text.strip():
        reply_text(reply_token, "ไม่สามารถอ่านข้อความจากเอกสารที่ส่งมาได้เลยครับ กรุณาลองส่งรูปภาพที่ชัดเจนขึ้น หรือพิมพ์ 'ยกเลิก'")
        return

    # 2. ส่งสรุปตามโหมด
    summary = summarize_document_text(document_text, summary_type)
    
    # 3. จัดทำข้อความตอบกลับ
    mode_title = summary_type if summary_type in {"สรุปแบบสั้น", "สรุปแบบละเอียด", "สรุปเพื่อสอบ", "สรุปเป็นข้อ", "สรุปเป็น mind map"} else "สรุปแบบละเอียด"
    msg = (
        f"📝 ผลลัพธ์ {mode_title}\n"
        f"════════════════════\n"
        f"{summary}\n"
        f"════════════════════\n"
        f"ต้องการรวบรวมรูปเอกสารเหล่านี้เป็นไฟล์ PDF หรือไม่ครับ?\n"
        f"✅ 'สร้าง PDF'  ❌ 'ไม่ต้อง'"
    )
    
    reply_text(reply_token, msg[:5000])
    
    # บันทึกผลสรุปไว้เผื่อนำไปสร้าง PDF
    set_waiting_for_pdf_confirm(session_key, receipt_summaries=[summary])


def _create_confirmed_pdf(
    reply_token: str, session_key: str, mode: str, request
) -> None:
    """สร้าง PDF หลังผู้ใช้ยืนยัน"""
    session = get_session(session_key)
    images = session.get("images", [])
    slip_data = session.get("slip_data", [])
    receipt_summaries = session.get("receipt_summaries", [])

    if not images:
        clear_session(session_key)
        reply_text(reply_token, "⚠️ ไม่พบรูปเพื่อสร้าง PDF ครับ")
        return

    from datetime import datetime as _dt

    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    pdf_filename = f"{_mode_prefix(mode)}-{ts}-{uuid.uuid4().hex[:6]}.pdf"
    output_path = GENERATED_DIR / pdf_filename

    try:
        if mode == "multi_slip" and slip_data:
            build_slip_report_pdf(slip_data, images, str(output_path))
        elif mode in {"ocr_summary_pdf", "doc_summary"} and receipt_summaries:
            build_receipt_report_pdf(receipt_summaries, images, str(output_path))
        else:
            build_pdf_from_images(images, str(output_path))

        file_url = build_file_url(request, pdf_filename)
        clear_session(session_key)
        cleanup_images(images)
        title = "รวมรูปภาพเป็น PDF"
        detail_text = f"{len(images)} รูป"
        if mode == "multi_slip":
            title = "รายงานสลิปโอนเงิน"
            detail_text = f"รวมยอดสลิป {len(slip_data)} ใบ"
        elif mode == "ocr_summary_pdf":
            title = "รายงานสรุปใบเสร็จ/บิล"
            detail_text = f"วิเคราะห์ใบเสร็จ {len(receipt_summaries)} ใบ"
        elif mode == "doc_summary":
            title = "รายงานสรุปเอกสาร"
            detail_text = "สรุปเนื้อหาบทเรียน"
        reply_pdf_success(reply_token, title, pdf_filename[:-4], detail_text, file_url)
    except Exception as exc:
        reply_text(reply_token, f"เกิดปัญหาสร้าง PDF ครับ: {exc}")


def _mode_prefix(mode: str) -> str:
    return {
        "multi_slip": "สลิป",
        "ocr_summary_pdf": "ใบเสร็จ",
        "slip": "สลิป",
        "doc_summary": "สรุปเอกสาร",
    }.get(mode, "report")


def _reply_profile(reply_token: str, user_id: str) -> None:
    """แสดงโปรไฟล์ของผู้ใช้"""
    profile = get_profile(user_id)
    if not profile or not (profile.get("name") or profile.get("age") or profile.get("job") or profile.get("location")):
        reply_text(
            reply_token,
            "คุณยังไม่ได้ตั้งค่าโปรไฟล์ สามารถเข้าไปแก้ไขข้อมูลได้ทันทีที่หน้าเว็บแดชบอร์ดครับ!\n"
            f"🌐 หรือกดที่นี่เพื่อตั้งค่าโปรไฟล์: {LIFF_PROFILE_URL}"
        )
        return
        
    avatar = profile.get("avatar") or "👤"
    lines = [f"{avatar} โปรไฟล์ของฉัน", "─" * 28]
    
    if profile.get("name"):
        lines.append(f"👤 ชื่อ: {profile['name']}")
    if profile.get("age"):
        lines.append(f"🎂 อายุ: {profile['age']} ปี")
    if profile.get("job"):
        lines.append(f"💼 อาชีพ: {profile['job']}")
    if profile.get("location"):
        lines.append(f"📍 ที่อยู่: {profile['location']}")
        
    # Finance info if set
    limit = profile.get("monthlyLimit")
    goal = profile.get("savingsGoal")
    if limit or goal:
        lines.append("─" * 28)
        lines.append("📊 เป้าหมายการเงินรายเดือน:")
        if limit:
            try:
                limit_val = float(limit)
                lines.append(f"   💸 จำกัดรายจ่าย: {limit_val:,.2f} บาท")
            except ValueError:
                lines.append(f"   💸 จำกัดรายจ่าย: {limit} บาท")
        if goal:
            try:
                goal_val = float(goal)
                lines.append(f"   🎯 เป้าหมายออม: {goal_val:,.2f} บาท")
            except ValueError:
                lines.append(f"   🎯 เป้าหมายออม: {goal} บาท")
                
    lines.append(f"\n🌐 หรือกดที่นี่เพื่อแก้ไขโปรไฟล์: {LIFF_PROFILE_URL}")
    reply_text(reply_token, "\n".join(lines))


def _is_pdf_cmd(text: str) -> bool:
    """จับคำสั่งถามเกี่ยวกับ PDF หรือ ลิงก์ทำ PDF หรือจัดรูป A4"""
    if text in PDF_COMMANDS:
        return True
    patterns = [
        r"ทำ.*pdf",
        r"รวม.*pdf",
        r"แปลง.*pdf",
        r"สร้าง.*pdf",
        r"ขอ.*pdf",
        r"ลิงก์.*pdf",
        r"ลิ้ง.*pdf",
        r"เว็บ.*pdf",
        r"web.*pdf",
        r"pdf.*อย่างไร",
        r"pdf.*ยังไง",
        r"จัด.*a4",
        r"a4.*pdf",
        r"จัดหน้า.*pdf",
        r"จัดรูป.*pdf",
        r"จัดรูปลง.*a4",
        r"ปรับ.*a4",
        r"ปรับสัดส่วนรูป",
    ]
    return any(re.search(p, text) for p in patterns)


def _is_multi_slip_cmd(text: str) -> bool:
    """จับคำสั่งรวมสลิปได้หลายแบบ ทั้ง exact match และ pattern"""
    if text in MULTI_SLIP_COMMANDS:
        return True
    patterns = [
        r"รวม.{0,6}สลิป",
        r"สลิป.{0,6}รวม",
        r"สลิป.{0,6}สรุป",
        r"นับ.{0,4}สลิป",
        r"รวม.{0,6}โอน",
        r"สรุป.{0,6}โอน",
        r"รวม.{0,6}ยอดสลิป",
        r"สลิป.*หลาย",
        r"หลาย.*สลิป",
    ]
    return any(re.search(p, text) for p in patterns)


def _looks_like_slip(text: str) -> bool:
    """ตรวจว่าข้อความ OCR น่าจะเป็นสลิปโอนเงินหรือไม่"""
    if not text:
        return False
    
    # ถ้ามีคำที่บ่งบอกว่าเป็นบิล/ใบส่งของ/ใบกำกับภาษีเชิงการค้า ไม่ควรจัดว่าเป็นสลิปโอนเงิน
    lower_text = text.lower()
    bill_keywords = [
        "บิลเงินสด", "ใบส่งของ", "ใบกำกับภาษี", "tax invoice", "cash sale",
        "เลขประจำตัวผู้เสียภาษี", "tax id", "หน่วยละ", "ผู้รับเงิน", "collector"
    ]
    if any(kw in lower_text for kw in bill_keywords):
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
    hit = sum(1 for kw in slip_keywords if kw in lower_text)
    has_number = bool(_AMOUNT_RE.search(text))
    return hit >= 2 and has_number


# ── Finance helpers ─────────────────────────────────────────────────────────
_AMOUNT_RE = re.compile(r"[\d,]+(?:\.\d+)?")

# Pattern ที่ชัดเจน: คำสั่ง + ตัวเลข + (หมวด) ต้องไม่มีคำถามต่อท้าย
_QUESTION_WORDS = re.compile(r"(ไหม|มั้ย|ไหมครับ|ไหมคะ|หรอ|หรือ|เท่าไร|เท่าไหร่|ใช่ไหม|รึเปล่า|\?)")

# Expense: ต้องขึ้นต้นด้วยคำสั่งที่ชัดเจน + ตัวเลข
_EXPENSE_PATTERN = re.compile(
    r"^(รายจ่าย|จ่ายเงิน|ใช้ไป|บันทึกรายจ่าย|ค่า\S+|ซื้อ\S+)\s+[\d,]+"
)
# Income: ต้องขึ้นต้นด้วยคำสั่งที่ชัดเจน + ตัวเลข
_INCOME_PATTERN = re.compile(
    r"^(รายรับ|รับเงิน|ได้รับ|โอนเข้า|income|เงินเดือน|ค่าจ้าง|โบนัส|บันทึกรายรับ)\s+[\d,]+"
)


def _is_expense_text(text: str) -> bool:
    if _QUESTION_WORDS.search(text):
        return False
    return bool(_EXPENSE_PATTERN.match(text))


def _is_income_text(text: str) -> bool:
    if _QUESTION_WORDS.search(text):
        return False
    return bool(_INCOME_PATTERN.match(text))


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


def _show_all_transactions(reply_token: str, user_id: str, type_: str) -> None:
    with __import__('contextlib').suppress(Exception):
        from core.db_service import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE user_id=? AND type=? ORDER BY id ASC",
                (user_id, type_),
            ).fetchall()
        if not rows:
            icon = "❤️" if type_ == "expense" else "💚"
            reply_text(reply_token, f"{icon} ยังไม่มีรายการครับ")
            return
        icon = "❤️" if type_ == "expense" else "💚"
        label = "รายจ่าย" if type_ == "expense" else "รายรับ"
        lines = [f"📋 {label}ทั้งหมด\n"]
        total = 0.0
        for i, r in enumerate(rows, 1):
            amt = r["amount"]
            total += amt
            cat = r["category"] or "-"
            lines.append(f"{i}. {cat}  {amt:,.0f} บาท  ({r['date']})") 
        lines.append(f"\n─" * 20)
        lines.append(f"💰 รวม {total:,.2f} บาท")
        reply_text(reply_token, "\n".join(lines))
        return
    reply_text(reply_token, "เกิดข้อผิดพลาดครับ")


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
    # ตัด keyword นำหน้าออก: นัด / ตั้งนัด / เพิ่มนัด / นัด:
    text = re.sub(r"^(นัด|ตั้งนัด|เพิ่มนัด)\s*[:：]?\s*", "", raw_text.strip())
    parts = text.split()
    if len(parts) < 2:
        reply_text(
            reply_token,
            "📅 บอกชื่อและวันที่ด้วยนะครับ\n"
            "เช่น\n"
            "• นัด ประชุม 2026-07-20 09:00\n"
            "• นัด หมอ 2026-07-25",
        )
        return
    title = parts[0]
    event_date = parts[1] if len(parts) > 1 else get_thailand_now().strftime("%Y-%m-%d")
    event_time = parts[2] if len(parts) > 2 else ""
    add_event(user_id, title, event_date, event_time)
    time_str = f" {event_time}" if event_time else ""
    reply_text(
        reply_token,
        f"📅 บันทึกนัดหมายแล้วครับ\n"
        f"📌 {title}\n"
        f"🗓 {event_date}{time_str}\n"
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
    reply_flow_start_flex(reply_token, mode)


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
        "doc_summary": (
            "📝 สรุปชีทเรียน & เอกสาร\n\n"
            "ส่งรูปเอกสารหรือชีทเรียนมาได้เลยครับ\n"
            "เมื่อส่งรูปภาพครบถ้วนแล้ว สามารถพิมพ์เลือกโหมดสรุปที่ต้องการ:\n"
            "• 'สรุปแบบสั้น' (ไม่เกิน 5 บรรทัด)\n"
            "• 'สรุปแบบละเอียด' (หรือพิมพ์ 'เสร็จแล้ว')\n"
            "• 'สรุปเพื่อสอบ' (มีประเด็นสำคัญ/สิ่งที่ควรจำ)\n"
            "• 'สรุปเป็นข้อ' (แบ่งเป็นหัวข้อย่อยชัดเจน)\n"
            "• 'สรุปเป็น mind map' (โครงสร้างแผนผังความคิด)"
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
        "pdf": (
            "📄 แปลงรูปเป็น PDF\n\n"
            "ส่งรูปถ่ายเอกสารหรือรูปทั่วไปมาได้เลยครับ\n"
            "ผมจะรวบรวมและสร้างเป็นไฟล์ PDF ไฟล์เดียวให้ครับ\n\n"
            "เมื่อส่งครบพิมพ์ 'เสร็จแล้ว' เพื่อตั้งชื่อไฟล์และดาวน์โหลด"
        ),
        "compress": (
            "🖼️ ย่อขนาดไฟล์รูป (Compress Image)\n\n"
            "เหมาะสำหรับ: ลดขนาดไฟล์รูป (ลด KB/MB) หรือปรับความกว้างสูงไม่เกิน 1280px\n"
            "ผมจะส่งรูปที่ย่อขนาดแล้วกลับให้คุณทันทีแบบรูปภาพ\n\n"
            "ส่งรูปที่ต้องการย่อขนาดไฟล์มาได้เลยครับ\n"
            "เมื่อย่อครบหมดแล้ว พิมพ์ 'เสร็จแล้ว' เพื่อเสร็จงาน"
        ),
    }
    return msgs.get(mode, "📎 ส่งรูปมาได้เลยครับ\nเมื่อครบพิมพ์ 'เสร็จแล้ว'")


def waiting_msg(mode):
    msgs = {
        "ocr_summary_pdf": "📎 ส่งรูปเพิ่มได้เลยครับ หรือพิมพ์ 'เสร็จแล้ว' ให้ผมวิเคราะห์",
        "doc_summary": "📎 ส่งรูปเพิ่มได้เลยครับ หรือเลือกประเภทสรุป (เช่น 'สรุปแบบสั้น' / 'เสร็จแล้ว')",
        "slip": "📎 ส่งสลิปเพิ่มได้เลยครับ หรือพิมพ์ 'เสร็จแล้ว' ให้ผมรวมยอด",
        "multi_slip": "📎 ส่งสลิปเพิ่มได้เลยครับ หรือพิมพ์ 'เสร็จแล้ว' เพื่อดูยอดรวม",
        "pdf": "📎 ส่งรูปที่ต้องการรวมเป็น PDF เพิ่มได้เลยครับ หรือพิมพ์ 'เสร็จแล้ว' เพื่อสร้างไฟล์",
        "compress": "📎 ส่งรูปเพิ่มเพื่อย่อขนาดไฟล์ต่อได้เลยครับ หรือพิมพ์ 'เสร็จแล้ว' เพื่อเสร็จสิ้น",
    }
    return msgs.get(mode, "📎 ส่งรูปเพิ่มได้เลยครับ หรือพิมพ์ 'เสร็จแล้ว' ให้ผมสร้าง PDF")


def image_received_msg(mode, count):
    suffix = {
        "ocr_summary_pdf": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' ให้ผมวิเคราะห์ + สรุป + สร้าง PDF ครับ",
        "doc_summary": "ส่งเพิ่มได้อีก หรือพิมพ์เลือกโหมดสรุป (เช่น 'สรุปแบบสั้น' / 'เสร็จแล้ว') ได้เลยครับ",
        "slip": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' ให้ผมอ่านและบันทึกยอดครับ",
        "pdf": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' เพื่อรวบรวมเป็นไฟล์ PDF ครับ",
        "compress": "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' เมื่อย่อรูปครบตามที่ต้องการแล้วครับ",
    }.get(mode, "ส่งเพิ่มได้อีก หรือพิมพ์ 'เสร็จแล้ว' ให้ผมสร้าง PDF ครับ")
    return f"📥 รับรูปแล้ว {count} รูปครับ\n{suffix}"


def build_success_message(mode, safe_name, file_url, images, user_id):
    base = f"✅ สร้าง PDF แล้วครับ\nชื่อ: {safe_name}.pdf\n🔗 {file_url}"

    if mode == "slip":
        try:
            ocr_text = extract_text_from_images(images)
            slip = parse_slip(ocr_text)
            session = get_session(user_id)
            batch_id = session.get("batch_id") or uuid.uuid4().hex
            save_slip(
                user_id,
                slip["amount"],
                slip["bank"],
                slip["ref"],
                slip["datetime"],
                ocr_text,
                batch_id,
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
        # ใช้ Vision LLM อ่านใบเสร็จโดยตรง (ไม่ต้องผ่าน OCR อีกต่อ)
        summaries: list[str] = []
        for img_path in images:
            try:
                s = read_receipt(img_path)  # Vision LLM อ่านใบเสร็จโดยตรง
                summaries.append(s)
            except Exception as e:
                summaries.append(f"(อ่านไม่ได้: {e})")
        summary = "\n\n".join(summaries) if summaries else "ไม่สามารถอ่านเอกสารได้ครับ"
        sep = "─" * 25
        return f"🧠 วิเคราะห์เอกสารแล้วครับ\n{sep}\n{summary}\n\n{base}"

    if mode == "pdf":
        return (
            f"✅ รวมรูปภาพเป็นไฟล์ PDF เรียบร้อยครับ\n"
            f"📄 {len(images)} รูป\n"
            f"ชื่อ: {safe_name}.pdf\n"
            f"🔗 {file_url}"
        )

    if mode == "compress":
        return "✅ เสร็จสิ้นการย่อขนาดไฟล์รูปภาพเรียบร้อยแล้วครับ 💬"

    return base


# ── Messages ──────────────────────────────────────────────────────────────────
def build_welcome_message():
    return (
        f"สวัสดีครับ ผมคือ {BOT_NAME} 🤖\n\n"
        "ผมช่วยได้หลายอย่างครับ เช่น\n"
        "🧾 ส่งสลิปมาตรง ๆ → ผมอ่านยอดและรวมให้เลย\n"
        "📄 'ทำ PDF' → ทำไฟล์ PDF ผ่านเว็บ\n"
        "💰 'ค่าน้ำ 270' → บันทึกรายจ่าย\n\n"
        "พิมพ์ 'เมนู' เพื่อดูทุกฟีเจอร์ หรือถามได้เลยครับ 💬"
    )


def _show_daily_summary(reply_token: str, user_id: str, date: str) -> None:
    reply_daily_summary(reply_token, user_id, date)


def reply_daily_summary(reply_token: str, user_id: str, date: str) -> None:
    s = get_daily_summary(user_id, date)
    rows = get_daily_transactions(user_id, date)
    try:
        y, mo, d = date.split("-")
        display_date = f"{int(d):02d}/{int(mo):02d}/{y}"
    except Exception:
        display_date = date
        
    if not rows:
        reply_text(reply_token, f"📅 {display_date}\nยังไม่มีรายการครับ")
        return

    # Build rows contents for Flex
    item_contents = []
    for r in rows:
        icon = "💚" if r["type"] == "income" else "❤️"
        color = "#34D399" if r["type"] == "income" else "#F87171"
        cat_name = r.get("category") or "อื่นๆ"
        item_contents.append({
            "type": "box",
            "layout": "horizontal",
            "margin": "sm",
            "contents": [
                {"type": "text", "text": f"{icon} {cat_name}", "size": "xs", "color": "#E2E8F0", "flex": 5},
                {"type": "text", "text": f"{r['amount']:,.2f} บาท", "size": "xs", "color": color, "weight": "bold", "align": "end", "flex": 5}
            ]
        })

    contents = {
      "type": "bubble",
      "size": "mega",
      "body": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#0F172A",
        "paddingAll": "12px",
        "contents": [
          {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1E293B",
            "cornerRadius": "20px",
            "borderColor": "#334155",
            "borderWidth": "1px",
            "paddingAll": "16px",
            "contents": [
              {
                "type": "text",
                "text": f"📅 สรุปยอดวันที่ {display_date}",
                "weight": "bold",
                "size": "lg",
                "color": "#FFFFFF"
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "xs",
                "contents": [
                  {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                      {"type": "text", "text": "รายรับ", "size": "sm", "color": "#94A3B8", "flex": 5},
                      {"type": "text", "text": f"{s['income']:,.2f} บาท", "size": "sm", "color": "#34D399", "weight": "bold", "align": "end", "flex": 5}
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                      {"type": "text", "text": "รายจ่าย", "size": "sm", "color": "#94A3B8", "flex": 5},
                      {"type": "text", "text": f"{s['expense']:,.2f} บาท", "size": "sm", "color": "#F87171", "weight": "bold", "align": "end", "flex": 5}
                    ]
                  },
                  {
                    "type": "separator",
                    "color": "#334155",
                    "margin": "xs"
                  },
                  {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                      {"type": "text", "text": "คงเหลือสุทธิ", "size": "sm", "color": "#FFFFFF", "weight": "bold", "flex": 5},
                      {"type": "text", "text": f"{s['balance']:,.2f} บาท", "size": "sm", "color": "#60A5FA", "weight": "bold", "align": "end", "flex": 5}
                    ]
                  }
                ]
              },
              {
                "type": "text",
                "text": "📋 รายการของวัน",
                "weight": "bold",
                "size": "xs",
                "color": "#94A3B8",
                "margin": "lg"
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "xs"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "sm",
                "spacing": "xs",
                "contents": item_contents[:20]
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "xs",
                "contents": [
                  {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#2563EB",
                    "cornerRadius": "lg",
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "alignItems": "center",
                    "action": {
                      "type": "uri",
                      "label": "🌐 ดูรายการทั้งหมดบนหน้าเว็บ",
                      "uri": LIFF_TRANSACTIONS_URL
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "🌐 ดูรายการทั้งหมดบนหน้าเว็บ",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "sm"
                      }
                    ]
                  }
                ]
              }
            ]
          }
        ]
      }
    }

    alt_text = f"📅 สรุปยอดวันที่ {display_date}: คงเหลือ {s['balance']:,.2f} บาท"

    try:
        res = requests.post(
            LINE_REPLY_ENDPOINT,
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "replyToken": reply_token,
                "messages": [
                    {
                        "type": "flex",
                        "altText": alt_text,
                        "contents": contents
                    }
                ],
            },
            timeout=30,
        )
        if res.status_code != 200:
            print(f"[LINE] Daily summary Flex failed: {res.status_code}: {res.text}")
        res.raise_for_status()
    except Exception as e:
        print(f"[LINE] Daily summary Flex exception: {e}. Falling back to text.")
        lines = [f"📅 สรุปวันที่ {display_date}\n"]
        if s["income"] > 0:
            lines.append(f"💚 รายรับ:  {s['income']:,.2f} บาท")
        if s["expense"] > 0:
            lines.append(f"❤️ รายจ่าย: {s['expense']:,.2f} บาท")
        lines.append(f"💰 คงเหลือ:  {s['balance']:,.2f} บาท")
        lines.append("\n─ รายการ ─")
        for r in rows:
            icon = "💚" if r["type"] == "income" else "❤️"
            lines.append(f"{icon} {r['category'] or '-'}  {r['amount']:,.2f} บาท")
        reply_text(reply_token, "\n".join(lines))


def reply_finance_summary(reply_token: str, user_id: str) -> None:
    now = get_thailand_now()
    s = get_monthly_summary(user_id, now.year, now.month)
    
    income = s.get("income", 0.0)
    expense = s.get("expense", 0.0)
    balance = s.get("balance", 0.0)

    contents = {
      "type": "bubble",
      "size": "mega",
      "body": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#0F172A",
        "paddingAll": "12px",
        "contents": [
          {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1E293B",
            "cornerRadius": "20px",
            "borderColor": "#334155",
            "borderWidth": "1px",
            "paddingAll": "16px",
            "contents": [
              {
                "type": "text",
                "text": "📊 สรุปการเงินเดือนนี้",
                "weight": "bold",
                "size": "lg",
                "color": "#FFFFFF"
              },
              {
                "type": "text",
                "text": f"ประจำปี {now.year} เดือน {now.month}",
                "size": "xs",
                "color": "#94A3B8",
                "margin": "xs"
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "sm",
                "contents": [
                  {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                      {"type": "text", "text": "🟢 รายรับทั้งหมด", "size": "sm", "color": "#94A3B8", "flex": 3},
                      {"type": "text", "text": f"{income:,.2f} บาท", "size": "sm", "color": "#34D399", "weight": "bold", "align": "end", "flex": 5}
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                      {"type": "text", "text": "🔴 รายจ่ายทั้งหมด", "size": "sm", "color": "#94A3B8", "flex": 3},
                      {"type": "text", "text": f"{expense:,.2f} บาท", "size": "sm", "color": "#F87171", "weight": "bold", "align": "end", "flex": 5}
                    ]
                  },
                  {
                    "type": "separator",
                    "color": "#334155",
                    "margin": "xs"
                  },
                  {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                      {"type": "text", "text": "💰 คงเหลือสุทธิ", "size": "sm", "color": "#FFFFFF", "weight": "bold", "flex": 3},
                      {"type": "text", "text": f"{balance:,.2f} บาท", "size": "md", "color": "#60A5FA", "weight": "bold", "align": "end", "flex": 5}
                    ]
                  }
                ]
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "xs",
                "contents": [
                  {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#2563EB",
                    "cornerRadius": "lg",
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "alignItems": "center",
                    "action": {
                      "type": "uri",
                      "label": "🌐 เปิดแดชบอร์ดการเงิน",
                      "uri": LIFF_DASHBOARD_URL
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "🌐 เปิดแดชบอร์ดการเงิน",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "sm"
                      }
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#334155",
                    "cornerRadius": "lg",
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "margin": "sm",
                    "alignItems": "center",
                    "action": {
                      "type": "uri",
                      "label": "🌐 ดูประวัติธุรกรรมทั้งหมด",
                      "uri": LIFF_TRANSACTIONS_URL
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "🌐 ดูประวัติธุรกรรมทั้งหมด",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "sm"
                      }
                    ]
                  }
                ]
              }
            ]
          }
        ]
      }
    }
    
    alt_text = f"📊 สรุปการเงินเดือนนี้: {balance:,.2f} บาท"
    
    try:
        res = requests.post(
            LINE_REPLY_ENDPOINT,
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "replyToken": reply_token,
                "messages": [
                    {
                        "type": "flex",
                        "altText": alt_text,
                        "contents": contents
                    }
                ],
            },
            timeout=30,
        )
        if res.status_code != 200:
            print(f"[LINE] Finance summary Flex failed: {res.status_code}: {res.text}")
        res.raise_for_status()
    except Exception as e:
        print(f"[LINE] Finance summary Flex exception: {e}. Falling back to text.")
        reply_text(
            reply_token,
            f"📊 สรุปการเงินเดือนนี้\n"
            f"💚 รายรับ:  {income:,.2f} บาท\n"
            f"❤️ รายจ่าย: {expense:,.2f} บาท\n"
            f"💰 คงเหลือ:  {balance:,.2f} บาท",
        )


def reply_category_summary(reply_token: str, user_id: str) -> None:
    now = get_thailand_now()
    rows = get_expense_by_category(user_id, now.year, now.month)
    if not rows:
        reply_text(reply_token, "ยังไม่มีรายจ่ายเดือนนี้ครับ")
        return

    # Build category list items for Flex
    item_contents = []
    total_all = 0.0
    for r in rows:
        cat_name = r.get("category") or "อื่นๆ"
        cat_total = r.get("total") or 0.0
        total_all += cat_total
        item_contents.append({
            "type": "box",
            "layout": "horizontal",
            "margin": "sm",
            "contents": [
                {"type": "text", "text": f"• {cat_name}", "size": "sm", "color": "#E2E8F0", "flex": 5},
                {"type": "text", "text": f"{cat_total:,.2f} บาท", "size": "sm", "color": "#F87171", "weight": "bold", "align": "end", "flex": 5}
            ]
        })
        
    contents = {
      "type": "bubble",
      "size": "mega",
      "body": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#0F172A",
        "paddingAll": "12px",
        "contents": [
          {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1E293B",
            "cornerRadius": "20px",
            "borderColor": "#334155",
            "borderWidth": "1px",
            "paddingAll": "16px",
            "contents": [
              {
                "type": "text",
                "text": "📊 รายจ่ายแยกหมวดหมู่เดือนนี้",
                "weight": "bold",
                "size": "lg",
                "color": "#FFFFFF"
              },
              {
                "type": "text",
                "text": f"ประจำปี {now.year} เดือน {now.month}",
                "size": "xs",
                "color": "#94A3B8",
                "margin": "xs"
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "xs",
                "contents": item_contents
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                  {"type": "text", "text": "รวมรายจ่ายทั้งหมด", "size": "sm", "color": "#FFFFFF", "weight": "bold", "flex": 5},
                  {"type": "text", "text": f"{total_all:,.2f} บาท", "size": "md", "color": "#F87171", "weight": "bold", "align": "end", "flex": 5}
                ]
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "xs",
                "contents": [
                  {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#2563EB",
                    "cornerRadius": "lg",
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "alignItems": "center",
                    "action": {
                      "type": "uri",
                      "label": "🌐 ดูสถิติบนเว็บแดชบอร์ด",
                      "uri": LIFF_DASHBOARD_URL
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "🌐 ดูสถิติบนเว็บแดชบอร์ด",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "sm"
                      }
                    ]
                  }
                ]
              }
            ]
          }
        ]
      }
    }
    
    alt_text = f"📊 สรุปรายจ่ายแยกหมวดหมู่เดือนนี้: {total_all:,.2f} บาท"
    
    try:
        res = requests.post(
            LINE_REPLY_ENDPOINT,
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "replyToken": reply_token,
                "messages": [
                    {
                        "type": "flex",
                        "altText": alt_text,
                        "contents": contents
                    }
                ],
            },
            timeout=30,
        )
        if res.status_code != 200:
            print(f"[LINE] Category summary Flex failed: {res.status_code}: {res.text}")
        res.raise_for_status()
    except Exception as e:
        print(f"[LINE] Category summary Flex exception: {e}. Falling back to text.")
        lines = ["📊 รายจ่ายแยกหมวดหมู่เดือนนี้\n"]
        for r in rows:
            lines.append(f"• {r['category'] or 'อื่นๆ'}: {r['total']:,.2f} บาท")
        reply_text(reply_token, "\n".join(lines))


def build_help_message():
    return (
        f"🤖 {BOT_NAME}\n"
        "─────────────────\n"
        "⭐ ยอดนิยม\n"
        "📚 สรุปเอกสาร\n"
        "🧾 สรุปใบเสร็จ\n"
        "📄 ทำ PDF\n"
        "💸 รวมสลิป\n"
        "🎤 แปลงเสียง\n"
        "─────────────────\n"
        "📄 เอกสาร\n"
        "💰 การเงิน\n"
        "🧾 สลิป\n"
        "📅 นัดหมาย\n"
        "👤 โปรไฟล์\n"
        "⚙️ เครื่องมือ\n"
        "🌐 AI Assistant\n"
        "─────────────────\n"
        "👉 พิมพ์ชื่อเมนูเพื่อใช้งาน"
    )


def build_submenu_message(category: str) -> str:
    cat = category.strip().lower()

    if cat in {"เอกสาร", "document"}:
        return (
            "📄 เอกสาร\n"
            "─────────────────\n"
            "📚 สรุปเอกสาร\n"
            "→ PDF, Word, รูปภาพ (5 โหมด)\n\n"
            "🧾 สรุปใบเสร็จ\n"
            "→ อ่านบิลและใบเสร็จ\n\n"
            "📄 ทำ PDF\n"
            "→ ทำไฟล์ PDF ผ่านเว็บ\n"
            "─────────────────\n"
            "👉 พิมพ์ เมนู เพื่อกลับ"
        )
    elif cat in {"สลิป", "slip"}:
        return (
            "🧾 สลิป\n"
            "─────────────────\n"
            "📲 ส่งรูปสลิปตรงๆ\n"
            "→ อ่านยอดเงินอัตโนมัติ\n\n"
            "💸 รวมสลิป\n"
            "→ ส่งหลายใบ + รวมยอด\n\n"
            "📊 ดูสลิปทั้งหมด\n"
            "📊 ยอดรวมสลิป\n"
            "🔍 สลิปที่ [เลข]\n"
            "─────────────────\n"
            "👉 พิมพ์ เมนู เพื่อกลับ"
        )
    elif cat in {"การเงิน", "finance"}:
        return (
            "💰 การเงิน\n"
            "─────────────────\n"
            "✍️ บันทึกรายรับ-จ่าย\n"
            "• ค่าน้ำ 270\n"
            "• เงินเดือน 15000\n\n"
            "📊 ดูข้อมูล\n"
            "• สรุปเดือนนี้\n"
            "• รายการล่าสุด\n"
            "• รายจ่ายทั้งหมด\n"
            "• รายรับทั้งหมด\n\n"
            "🗑️ จัดการ\n"
            "• ลบรายการ [เลข]\n"
            "• ลบรายจ่ายทั้งหมด\n"
            "─────────────────\n"
            "👉 พิมพ์ เมนู เพื่อกลับ"
        )
    elif cat in {"นัดหมาย", "schedule"}:
        return (
            "📅 นัดหมาย\n"
            "─────────────────\n"
            "📌 เพิ่มนัด\n"
            "• นัด ประชุม 2026-07-20 09:00\n\n"
            "📅 ดูนัดหมาย\n"
            "─────────────────\n"
            "👉 พิมพ์ เมนู เพื่อกลับ"
        )
    elif cat in {"โปรไฟล์", "profile"}:
        return (
            "👤 โปรไฟล์\n"
            "─────────────────\n"
            "• ข้อมูลของฉัน\n"
            "• เปลี่ยนชื่อเป็น [ชื่อ]\n"
            "• เปลี่ยนอายุเป็น [อายุ]\n"
            "• เปลี่ยนอาชีพเป็น [อาชีพ]\n"
            "• ล้างข้อมูลของฉัน\n"
            "─────────────────\n"
            "👉 พิมพ์ เมนู เพื่อกลับ"
        )
    elif cat in {"เครื่องมือ", "tools", "⚙️"}:
        return (
            "⚙️ เครื่องมือ\n"
            "─────────────────\n"
            "🖼️ รูปภาพ\n"
            "• ทำ PDF\n\n"
            "🎤 เสียง\n"
            "• ส่งไฟล์เสียงหรืออัดเสียงใน LINE\n"
            "• แปลงเสียงเป็นข้อความ\n\n"
            "🌐 AI\n"
            "• แปลเป็นอังกฤษ: [ข้อความ]\n"
            "• ถามอะไรก็ได้\n"
            "• ล้างแชท\n"
            "─────────────────\n"
            "👉 พิมพ์ เมนู เพื่อกลับ"
        )
    elif cat in {"ai assistant", "ai", "🌐"}:
        return (
            "🌐 AI Assistant\n"
            "─────────────────\n"
            "• ถามอะไรก็ได้\n"
            "• แปลเป็นอังกฤษ: [ข้อความ]\n"
            "• ล้างแชท\n"
            "─────────────────\n"
            "👉 พิมพ์ เมนู เพื่อกลับ"
        )
    return ""

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


def reply_text_with_quick_replies(reply_token: str, text: str, items: list[dict]) -> None:
    actions = []
    for item in items:
        actions.append({
            "type": "action",
            "action": {
                "type": "message",
                "label": item["label"],
                "text": item["text"]
            }
        })
    requests.post(
        LINE_REPLY_ENDPOINT,
        headers={
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "replyToken": reply_token,
            "messages": [
                {
                    "type": "text",
                    "text": text[:5000],
                    "quickReply": {
                        "items": actions
                    }
                }
            ],
        },
        timeout=30,
    ).raise_for_status()


def reply_pdf_success(reply_token, title, safe_name, detail_text, file_url):
    alt_text = f"✅ สร้าง PDF สำเร็จแล้ว: {safe_name}.pdf"
    
    contents = {
      "type": "bubble",
      "body": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#0F172A",
        "paddingAll": "12px",
        "contents": [
          {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1E293B",
            "cornerRadius": "20px",
            "borderColor": "#334155",
            "borderWidth": "1px",
            "paddingAll": "16px",
            "contents": [
              {
                "type": "text",
                "text": "📄 PDF CREATED",
                "weight": "bold",
                "color": "#34D399",
                "size": "sm"
              },
               {
                "type": "text",
                "text": title,
                "weight": "bold",
                "size": "xl",
                "margin": "md",
                "color": "#FFFFFF"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "spacing": "sm",
                "contents": [
                  {
                    "type": "box",
                    "layout": "baseline",
                    "spacing": "sm",
                    "contents": [
                      {
                        "type": "text",
                        "text": "ชื่อไฟล์",
                        "color": "#94A3B8",
                        "size": "sm",
                        "flex": 2
                      },
                      {
                        "type": "text",
                        "text": f"{safe_name}.pdf",
                        "wrap": True,
                        "color": "#E2E8F0",
                        "size": "sm",
                        "flex": 5
                      }
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "baseline",
                    "spacing": "sm",
                    "contents": [
                      {
                        "type": "text",
                        "text": "รายละเอียด",
                        "color": "#94A3B8",
                        "size": "sm",
                        "flex": 2
                      },
                      {
                        "type": "text",
                        "text": detail_text,
                        "wrap": True,
                        "color": "#E2E8F0",
                        "size": "sm",
                        "flex": 5
                      }
                    ]
                  }
                ]
              },
              {
                "type": "separator",
                "color": "#334155",
                "margin": "lg"
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "sm",
                "contents": [
                  {
                    "type": "button",
                     "style": "primary",
                     "height": "sm",
                     "color": "#2563EB",
                     "action": {
                       "type": "uri",
                       "label": "📂 เปิดไฟล์ PDF",
                       "uri": file_url
                     }
                  }
                ]
              }
            ]
          }
        ]
      }
    }
    
    try:
        res = requests.post(
            LINE_REPLY_ENDPOINT,
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "replyToken": reply_token,
                "messages": [
                    {
                        "type": "flex",
                        "altText": alt_text,
                        "contents": contents
                    }
                ],
            },
            timeout=30,
        )
        if res.status_code != 200:
            print(f"[LINE] Flex message failed with status {res.status_code}: {res.text}")
        res.raise_for_status()
    except Exception as e:
        print(f"[LINE] Flex message exception: {e}. Falling back to text.")
        fallback_text = f"✅ สร้าง PDF เรียบร้อยแล้วครับ 📄\n{detail_text} → {safe_name}.pdf\n🔗 {file_url}"
        try:
            reply_text(reply_token, fallback_text)
        except Exception as fallback_err:
            print(f"[LINE] Fallback reply_text also failed: {fallback_err}")


def reply_pdf_creator_link(reply_token):
    alt_text = "📄 แปลงรูปภาพเป็นไฟล์ PDF"
    
    contents = {
      "type": "bubble",
      "body": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {
            "type": "text",
            "text": "📄 PDF CREATOR",
            "weight": "bold",
            "color": "#10b981",
            "size": "sm"
          },
          {
            "type": "text",
            "text": "แปลงรูปภาพเป็น PDF",
            "weight": "bold",
            "size": "xl",
            "margin": "md",
            "color": "#111111"
          },
          {
            "type": "text",
            "text": "สามารถแปลงรูปภาพ หรือเอกสารหลายๆ รูป ให้เป็นไฟล์ PDF บนเว็บแอปพลิเคชันของเราได้เลยครับ มีระบบครอปตัดและหมุนรูปภาพให้ใช้งานด้วย",
            "wrap": True,
            "color": "#555555",
            "size": "sm",
            "margin": "md"
          }
        ]
      },
      "footer": {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "contents": [
          {
            "type": "button",
            "style": "primary",
            "height": "sm",
            "color": "#10b981",
            "action": {
              "type": "uri",
              "label": "👉 เริ่มทำ PDF บนเว็บ",
              "uri": LIFF_PDF_CREATOR_URL
            }
          }
        ]
      }
    }
    
    try:
        res = requests.post(
            LINE_REPLY_ENDPOINT,
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "replyToken": reply_token,
                "messages": [
                    {
                        "type": "flex",
                        "altText": alt_text,
                        "contents": contents
                    }
                ],
            },
            timeout=30,
        )
        if res.status_code != 200:
            print(f"[LINE] PDF creator link Flex message failed with status {res.status_code}: {res.text}")
        res.raise_for_status()
    except Exception as e:
        print(f"[LINE] PDF creator link Flex message exception: {e}. Falling back to text.")
        fallback_text = (
            "📄 รวมรูปเป็น PDF ผ่านเว็บแอปพลิเคชัน\n\n"
            "ท่านสามารถแปลงรูปเป็น PDF พร้อมทั้งครอบภาพและหมุนภาพได้อย่างอิสระผ่านลิงก์ด้านล่างนี้ได้เลยครับ:\n"
            f"🔗 {LIFF_PDF_CREATOR_URL}"
        )
        try:
            reply_text(reply_token, fallback_text)
        except Exception as fallback_err:
            print(f"[LINE] Fallback reply_text also failed: {fallback_err}")


def reply_premium_info_flex(
    reply_token: str,
    title: str,
    subtitle: str,
    bullet_points: list[str],
    footer_text: str = None,
    quick_replies: list[dict] = None
) -> None:
    body_contents = [
        {
            "type": "text",
            "text": title,
            "weight": "bold",
            "size": "lg",
            "color": "#FFFFFF"
        }
    ]
    if subtitle:
        body_contents.append({
            "type": "text",
            "text": subtitle,
            "size": "xs",
            "color": "#94A3B8",
            "margin": "xs",
            "wrap": True
        })
        
    body_contents.append({
        "type": "separator",
        "color": "#334155",
        "margin": "md"
    })
    
    bullets_box = {
        "type": "box",
        "layout": "vertical",
        "margin": "md",
        "spacing": "sm",
        "contents": []
    }
    for bp in bullet_points:
        bullets_box["contents"].append({
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "✓",
                    "color": "#3B82F6",
                    "size": "sm",
                    "flex": 1,
                    "weight": "bold"
                },
                {
                    "type": "text",
                    "text": bp,
                    "color": "#E2E8F0",
                    "size": "sm",
                    "flex": 11,
                    "wrap": True
                }
            ]
        })
    body_contents.append(bullets_box)
    
    if footer_text:
        body_contents.append({
            "type": "separator",
            "color": "#334155",
            "margin": "md"
        })
        body_contents.append({
            "type": "text",
            "text": footer_text,
            "size": "xxs",
            "color": "#94A3B8",
            "margin": "sm",
            "wrap": True,
            "align": "center"
        })
        
    contents = {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#0F172A",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#1E293B",
                    "cornerRadius": "20px",
                    "borderColor": "#334155",
                    "borderWidth": "1px",
                    "paddingAll": "16px",
                    "contents": body_contents
                }
            ]
        }
    }
    
    alt_text = f"🤖 {title}"
    
    message_payload = {
        "type": "flex",
        "altText": alt_text,
        "contents": contents
    }
    
    if quick_replies:
        actions = []
        for item in quick_replies:
            if "uri" in item:
                action_def = {
                    "type": "uri",
                    "label": item["label"],
                    "uri": item["uri"]
                }
            else:
                action_def = {
                    "type": "message",
                    "label": item["label"],
                    "text": item["text"]
                }
            actions.append({
                "type": "action",
                "action": action_def
            })
        message_payload["quickReply"] = {
            "items": actions
        }
        
    try:
        res = requests.post(
            LINE_REPLY_ENDPOINT,
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "replyToken": reply_token,
                "messages": [message_payload],
            },
            timeout=30,
        )
        if res.status_code != 200:
            print(f"[LINE] reply_premium_info_flex failed: {res.status_code}: {res.text}")
        res.raise_for_status()
    except Exception as e:
        print(f"[LINE] reply_premium_info_flex exception: {e}. Falling back to text.")
        fallback = f"🤖 {title}\n{subtitle}\n" + "\n".join(f"- {bp}" for bp in bullet_points)
        if footer_text:
            fallback += f"\n\n{footer_text}"
        try:
            if quick_replies:
                reply_text_with_quick_replies(reply_token, fallback, quick_replies)
            else:
                reply_text(reply_token, fallback)
        except Exception:
            pass


def reply_flow_start_flex(reply_token, mode):
    cancel_qr = [
        {"label": "❌ ยกเลิก", "text": "ยกเลิก"}
    ]

    if mode == "ocr_summary_pdf":
        reply_premium_info_flex(
            reply_token,
            title="🧠 AI วิเคราะห์เอกสารการเงิน",
            subtitle="วิเคราะห์ ใบเสร็จ / บิล / สลิปโอนเงิน",
            bullet_points=[
                "อ่านข้อความและวิเคราะห์ด้วย AI อัตโนมัติ",
                "แยกรายการ หมวดหมู่ รายรับ-รายจ่าย และยอดรวม",
                "บันทึกข้อมูลและส่งออกรายงานสรุปการเงินได้",
                "ส่งรูปภาพเพิ่มเติมได้ตลอดเวลา"
            ],
            footer_text="📎 ส่งรูปบิลหรือสลิปมาได้เลยครับ",
            quick_replies=cancel_qr
        )
    elif mode == "doc_summary":
        reply_premium_info_flex(
            reply_token,
            title="📝 สรุปชีทเรียน & เอกสาร",
            subtitle="สรุปความรู้ด้วยระบบประมวลผลภาษาของ AI",
            bullet_points=[
                "ส่งรูปชีทเรียน หนังสือเรียน หรือเอกสารได้หลายภาพ",
                "วิเคราะห์และสรุปภาษาไทยและอังกฤษอย่างชาญฉลาด",
                "เลือกโหมดสรุปเมื่อส่งรูปภาพครบถ้วนแล้ว"
            ],
            footer_text="📎 ส่งรูปเอกสารมาได้เลยครับ",
            quick_replies=cancel_qr
        )
    elif mode == "slip":
        reply_premium_info_flex(
            reply_token,
            title="🧾 อ่านสลิป + บันทึกยอด",
            subtitle="ตรวจสอบสลิปโอนเงินและบันทึกรายรับ-จ่าย",
            bullet_points=[
                "สแกนข้อมูลจากสลิปอัตโนมัติ (ยอดเงิน/วันเวลา)",
                "ช่วยจดบันทึกประวัติการเงินของคุณให้อย่างรวดเร็ว"
            ],
            footer_text="📎 ส่งรูปสลิปมาได้เลยครับ",
            quick_replies=cancel_qr
        )
    elif mode == "multi_slip":
        reply_premium_info_flex(
            reply_token,
            title="🧾 รวมยอดสลิป",
            subtitle="วิเคราะห์และรวมยอดจากหลายสลิปในแชทเดียว",
            bullet_points=[
                "ดึงยอดเงินโอน วันเวลา และข้อมูลปลายทาง",
                "คำนวณและแสดงผลยอดรวมทั้งหมด in ใบเดียว",
                "สะดวกและรวดเร็ว ไม่จำกัดจำนวนสลิป"
            ],
            footer_text="📎 ส่งรูปสลิปมาได้เลยครับ",
            quick_replies=cancel_qr
        )
    elif mode == "pdf":
        reply_premium_info_flex(
            reply_token,
            title="📄 แปลงรูปเป็น PDF",
            subtitle="รวบรวมรูปภาพของคุณเป็นเอกสาร PDF ลิงก์เดียว",
            bullet_points=[
                "สร้างหน้าเอกสาร PDF คุณภาพสูงจากภาพถ่าย",
                "สามารถส่งได้ทีละหลายรูป"
            ],
            footer_text="📎 ส่งรูปภาพมาได้เลยครับ",
            quick_replies=cancel_qr
        )
    elif mode == "compress":
        reply_premium_info_flex(
            reply_token,
            title="🖼️ ย่อขนาดไฟล์รูป",
            subtitle="ลดขนาดไฟล์รูปภาพเพื่อความสะดวกในการส่งต่อ",
            bullet_points=[
                "บีบอัดขนาดไฟล์ (KB/MB) โดยไม่เสียสัดส่วนเดิม",
                "ส่งรูปที่ย่อแล้วกลับไปให้ในแชททันที"
            ],
            footer_text="📎 ส่งรูปภาพมาได้เลยครับ",
            quick_replies=cancel_qr
        )
    else:
        reply_text_with_quick_replies(
            reply_token,
            "📎 ส่งรูปมาได้เลยครับ หรือกด 'ยกเลิก' เพื่อออก",
            cancel_qr
        )


def reply_submenu(reply_token, category: str) -> bool:
    cat = category.strip().lower()
    
    title = ""
    subtitle = "เลือกฟังก์ชันที่ต้องการ"
    buttons = []
    
    if cat in {"เอกสาร", "document"}:
        title = "📄 เมนูเอกสาร"
        buttons = [
            {"label": "📝 สรุปเอกสาร", "text": "สรุปเอกสาร"},
            {"label": "🧾 สรุปใบเสร็จ", "text": "สรุปใบเสร็จ"},
            {"label": "📄 ทำ PDF", "uri": LIFF_PDF_CREATOR_URL}
        ]
    elif cat in {"สลิป", "slip"}:
        title = "🧾 เมนูสลิป"
        buttons = [
            {"label": "💸 รวมยอดหลายสลิป", "text": "รวมสลิป"},
            {"label": "🌐 คลังสลิปโอนเงินบนเว็บ", "uri": LIFF_SLIPS_URL},
            {"label": "📊 ยอดรวมสลิปล่าสุด", "text": "ยอดรวมสลิป"}
        ]
    elif cat in {"การเงิน", "finance"}:
        title = "💰 เมนูการเงิน"
        buttons = [
            {"label": "🌐 แดชบอร์ดสรุปการเงิน", "uri": LIFF_DASHBOARD_URL},
            {"label": "🌐 ประวัติธุรกรรมทั้งหมด", "uri": LIFF_TRANSACTIONS_URL},
            {"label": "📊 สรุปยอดเดือนนี้", "text": "สรุปเดือนนี้"},
            {"label": "📊 สรุปยอดวันนี้", "text": "สรุปวันนี้"},
            {"label": "📊 รายจ่ายแยกหมวดหมู่", "text": "สรุปหมวดหมู่"}
        ]
    elif cat in {"นัดหมาย", "schedule"}:
        title = "📅 เมนูนัดหมาย"
        buttons = [
            {"label": "🌐 ปฏิทินนัดหมายบนเว็บ", "uri": LIFF_CALENDAR_URL},
            {"label": "📅 ตารางนัดหมายทั้งหมด", "text": "ดูนัดหมาย"}
        ]
    elif cat in {"โปรไฟล์", "profile"}:
        title = "👤 เมนูโปรไฟล์"
        buttons = [
            {"label": "👤 ดูข้อมูลโปรไฟล์ของฉัน", "text": "ข้อมูลของฉัน"},
            {"label": "🌐 แก้ไขโปรไฟล์บนเว็บ", "uri": LIFF_PROFILE_URL},
            {"label": "🗑️ ลบข้อมูลโปรไฟล์ทั้งหมด", "text": "ล้างข้อมูลของฉัน"}
        ]
    elif cat in {"เครื่องมือ", "tools", "⚙️"}:
        title = "⚙️ เมนูเครื่องมือ"
        buttons = [
            {"label": "📄 สร้างไฟล์ PDF จากรูปภาพ", "uri": LIFF_PDF_CREATOR_URL},
            {"label": "🎤 ถอดรหัสข้อความจากเสียง", "text": "แปลงเสียง"},
            {"label": "🧹 ล้างประวัติสนทนากับ AI", "text": "ล้างแชท"}
        ]
    elif cat in {"ai assistant", "ai", "🌐"}:
        title = "🌐 AI Assistant"
        buttons = [
            {"label": "🧹 ล้างประวัติแชท/ความจำ AI", "text": "ล้างแชท"}
        ]
    else:
        return False
        
    flex_buttons = []
    for btn in buttons:
        action_obj = {}
        if "uri" in btn:
            action_obj = {
                "type": "uri",
                "label": btn["label"],
                "uri": btn["uri"]
            }
        else:
            action_obj = {
                "type": "message",
                "label": btn["label"],
                "text": btn["text"]
            }
        flex_buttons.append({
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#2ECC71",
            "cornerRadius": "md",
            "paddingTop": "8px",
            "paddingBottom": "8px",
            "alignItems": "center",
            "action": action_obj,
            "contents": [
                {
                    "type": "text",
                    "text": btn["label"],
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "sm"
                }
            ]
        })
        
    contents = {
      "type": "bubble",
      "size": "mega",
      "body": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#F8F9FA",
        "paddingAll": "12px",
        "contents": [
          {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#FFFFFF",
            "cornerRadius": "20px",
            "borderColor": "#EAEAEA",
            "borderWidth": "1px",
            "paddingAll": "16px",
            "contents": [
              {
                "type": "box",
                "layout": "vertical",
                "paddingBottom": "8px",
                "contents": [
                  {
                    "type": "text",
                    "text": title,
                    "weight": "bold",
                    "size": "lg",
                    "color": "#333333"
                  },
                  {
                    "type": "text",
                    "text": subtitle,
                    "size": "xs",
                    "color": "#777777",
                    "margin": "xs"
                  }
                ]
              },
              {
                "type": "separator",
                "color": "#EAEAEA"
              },
              {
                "type": "box",
                "layout": "vertical",
                "spacing": "xs",
                "margin": "sm",
                "contents": flex_buttons
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "xs",
                "contents": [
                  {
                    "type": "separator",
                    "color": "#EAEAEA"
                  },
                  {
                    "type": "box",
                    "layout": "vertical",
                    "borderWidth": "1px",
                    "borderColor": "#EAEAEA",
                    "cornerRadius": "md",
                    "paddingTop": "6px",
                    "paddingBottom": "6px",
                    "alignItems": "center",
                    "margin": "sm",
                    "action": {
                      "type": "message",
                      "label": "⬅️ กลับเมนูหลัก",
                      "text": "เมนู"
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "⬅️ กลับเมนูหลัก",
                        "color": "#777777",
                        "weight": "bold",
                        "size": "xs"
                      }
                    ]
                  },
                  {
                    "type": "separator",
                    "color": "#EAEAEA",
                    "margin": "sm"
                  },
                  {
                    "type": "text",
                    "text": "👉 แตะปุ่มเพื่อเริ่มใช้งาน",
                    "size": "xxs",
                    "color": "#777777",
                    "align": "center",
                    "margin": "sm",
                    "wrap": True
                  }
                ]
              }
            ]
          }
        ]
      }
    }
    
    alt_text = f"🤖 LouisAI Assistant - {title}"
    
    try:
        res = requests.post(
            LINE_REPLY_ENDPOINT,
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "replyToken": reply_token,
                "messages": [
                    {
                        "type": "flex",
                        "altText": alt_text,
                        "contents": contents
                    }
                ],
            },
            timeout=30,
        )
        if res.status_code != 200:
            print(f"[LINE] Submenu Flex message failed with status {res.status_code}: {res.text}")
        res.raise_for_status()
    except Exception as e:
        print(f"[LINE] Submenu Flex exception: {e}. Falling back to text.")
        try:
            fallback_msg = build_submenu_message(category)
            if fallback_msg:
                reply_text(reply_token, fallback_msg)
        except Exception as fallback_err:
            print(f"[LINE] Submenu fallback reply_text also failed: {fallback_err}")
            
    return True


def reply_main_menu(reply_token):
    alt_text = "🤖 LouisAI Assistant - เมนูหลัก"
    contents = {
      "type": "bubble",
      "size": "mega",
      "body": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#0F172A",
        "paddingAll": "12px",
        "contents": [
          {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1E293B",
            "cornerRadius": "20px",
            "borderColor": "#334155",
            "borderWidth": "1px",
            "paddingAll": "16px",
            "contents": [
              {
                "type": "box",
                "layout": "vertical",
                "paddingBottom": "8px",
                "contents": [
                  {
                    "type": "text",
                    "text": "🤖 LouisAI Assistant",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#FFFFFF"
                  },
                  {
                    "type": "text",
                    "text": "AI เอกสาร • PDF • การเงิน",
                    "size": "xs",
                    "color": "#94A3B8",
                    "margin": "xs"
                  }
                ]
              },
              {
                "type": "separator",
                "color": "#334155"
              },
              {
                "type": "text",
                "text": "⭐ ฟังก์ชันยอดนิยม",
                "weight": "bold",
                "size": "sm",
                "color": "#FFFFFF",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "vertical",
                "spacing": "xs",
                "margin": "sm",
                "contents": [
                  {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#334155",
                    "cornerRadius": "lg",
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "alignItems": "center",
                    "action": {
                      "type": "message",
                      "label": "📝 สรุปเอกสาร",
                      "text": "สรุปเอกสาร"
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "📝 สรุปเอกสาร",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "sm"
                      }
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#334155",
                    "cornerRadius": "lg",
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "alignItems": "center",
                    "action": {
                      "type": "message",
                      "label": "🧾 สรุปใบเสร็จ",
                      "text": "สรุปใบเสร็จ"
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "🧾 สรุปใบเสร็จ",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "sm"
                      }
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#2563EB",
                    "cornerRadius": "lg",
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "alignItems": "center",
                    "action": {
                      "type": "uri",
                      "label": "📄 ทำ PDF",
                      "uri": "https://liff.line.me/2010485952-5MZ2C6JG/dashboard/pdf-creator"
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "📄 ทำ PDF บนเว็บ",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "sm"
                      }
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#334155",
                    "cornerRadius": "lg",
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "alignItems": "center",
                    "action": {
                      "type": "message",
                      "label": "📸 รวมสลิป",
                      "text": "รวมสลิป"
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "📸 รวมสลิป",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "sm"
                      }
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#334155",
                    "cornerRadius": "lg",
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "alignItems": "center",
                    "action": {
                      "type": "message",
                      "label": "🎙️ แปลงเสียง",
                      "text": "แปลงเสียง"
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "🎙️ แปลงเสียง",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "sm"
                      }
                    ]
                  }
                ]
              },
              {
                "type": "text",
                "text": "📂 บริการอื่นๆ",
                "weight": "bold",
                "size": "sm",
                "color": "#FFFFFF",
                "margin": "md"
              },
              {
                "type": "box",
                "layout": "vertical",
                "spacing": "xs",
                "margin": "sm",
                "contents": [
                  {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "xs",
                    "contents": [
                      {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#334155",
                        "cornerRadius": "lg",
                        "paddingTop": "10px",
                        "paddingBottom": "10px",
                        "alignItems": "center",
                        "justifyContent": "center",
                        "action": {
                          "type": "message",
                          "label": "📁 เอกสาร",
                          "text": "เอกสาร"
                        },
                        "contents": [
                          {
                            "type": "text",
                            "text": "📁 เอกสาร",
                            "weight": "bold",
                            "color": "#FFFFFF",
                            "size": "xs"
                          }
                        ]
                      },
                      {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#334155",
                        "cornerRadius": "lg",
                        "paddingTop": "10px",
                        "paddingBottom": "10px",
                        "alignItems": "center",
                        "justifyContent": "center",
                        "action": {
                          "type": "message",
                          "label": "💰 การเงิน",
                          "text": "การเงิน"
                        },
                        "contents": [
                          {
                            "type": "text",
                            "text": "💰 การเงิน",
                            "weight": "bold",
                            "color": "#FFFFFF",
                            "size": "xs"
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "xs",
                    "contents": [
                      {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#334155",
                        "cornerRadius": "lg",
                        "paddingTop": "10px",
                        "paddingBottom": "10px",
                        "alignItems": "center",
                        "justifyContent": "center",
                        "action": {
                          "type": "message",
                          "label": "🎫 สลิป",
                          "text": "สลิป"
                        },
                        "contents": [
                          {
                            "type": "text",
                            "text": "🎫 สลิป",
                            "weight": "bold",
                            "color": "#FFFFFF",
                            "size": "xs"
                          }
                        ]
                      },
                      {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#334155",
                        "cornerRadius": "lg",
                        "paddingTop": "10px",
                        "paddingBottom": "10px",
                        "alignItems": "center",
                        "justifyContent": "center",
                        "action": {
                          "type": "message",
                          "label": "📅 นัดหมาย",
                          "text": "นัดหมาย"
                        },
                        "contents": [
                          {
                            "type": "text",
                            "text": "📅 นัดหมาย",
                            "weight": "bold",
                            "color": "#FFFFFF",
                            "size": "xs"
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "xs",
                    "contents": [
                      {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#334155",
                        "cornerRadius": "lg",
                        "paddingTop": "10px",
                        "paddingBottom": "10px",
                        "alignItems": "center",
                        "justifyContent": "center",
                        "action": {
                          "type": "message",
                          "label": "👤 โปรไฟล์",
                          "text": "โปรไฟล์"
                        },
                        "contents": [
                          {
                            "type": "text",
                            "text": "👤 โปรไฟล์",
                            "weight": "bold",
                            "color": "#FFFFFF",
                            "size": "xs"
                          }
                        ]
                      },
                      {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#334155",
                        "cornerRadius": "lg",
                        "paddingTop": "10px",
                        "paddingBottom": "10px",
                        "alignItems": "center",
                        "justifyContent": "center",
                        "action": {
                          "type": "message",
                          "label": "⚙️ เครื่องมือ",
                          "text": "เครื่องมือ"
                        },
                        "contents": [
                          {
                            "type": "text",
                            "text": "⚙️ เครื่องมือ",
                            "weight": "bold",
                            "color": "#FFFFFF",
                            "size": "xs"
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#334155",
                    "cornerRadius": "lg",
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "alignItems": "center",
                    "justifyContent": "center",
                    "action": {
                      "type": "message",
                      "label": "🌐 AI Assistant",
                      "text": "AI Assistant"
                    },
                    "contents": [
                      {
                        "type": "text",
                        "text": "🌐 AI Assistant",
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "xs"
                      }
                    ]
                  }
                ]
              },
              {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "spacing": "xs",
                "contents": [
                  {
                    "type": "separator",
                    "color": "#334155"
                  },
                  {
                    "type": "text",
                    "text": "👉 แตะปุ่มเพื่อเริ่มใช้งาน",
                    "size": "xxs",
                    "color": "#94A3B8",
                    "align": "center",
                    "margin": "sm",
                    "wrap": True
                  }
                ]
              }
            ]
          }
        ]
      }
    }

    try:
        res = requests.post(
            LINE_REPLY_ENDPOINT,
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "replyToken": reply_token,
                "messages": [
                    {
                        "type": "flex",
                        "altText": alt_text,
                        "contents": contents
                    }
                ],
            },
            timeout=30,
        )
        if res.status_code != 200:
            print(f"[LINE] Main menu Flex message failed with status {res.status_code}: {res.text}")
        res.raise_for_status()
    except Exception as e:
        print(f"[LINE] Main menu Flex exception: {e}. Falling back to text.")
        try:
            reply_text(reply_token, build_help_message())
        except Exception as fallback_err:
            print(f"[LINE] Main menu fallback reply_text also failed: {fallback_err}")



def build_file_url(request, filename):
    import urllib.parse
    encoded = urllib.parse.quote(filename)
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/files/{encoded}"
    if isinstance(request, str):
        return request.rstrip("/") + f"/files/{encoded}"
    return str(getattr(request, "base_url", request)).rstrip("/") + f"/files/{encoded}"


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
def handle_audio_message(reply_token: str, session_key: str, message_id: str, source: dict | None = None, custom_ext: str | None = None) -> None:
    content, content_type = download_line_message_content(message_id)
    if custom_ext:
        ext = custom_ext
    else:
        ext = guess_extension(content_type)
        if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma", ".webm"}:
            ext = ".m4a"
    tmp_path = UPLOAD_DIR / f"audio_{uuid.uuid4().hex}{ext}"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(content)
    try:
        line_user_id = (source or {}).get("userId") or session_key
        result = transcribe_and_summarize(str(tmp_path), ask_ai, user_id=session_key, profile_user_id=line_user_id)
        reply_text(reply_token, result)
    finally:
        tmp_path.unlink(missing_ok=True)


def handle_document_file_message(reply_token: str, session_key: str, message_id: str, file_name: str, request: Request) -> None:
    content, content_type = download_line_message_content(message_id)
    ext = Path(file_name).suffix.lower()

    user_dir = UPLOAD_DIR / session_key.replace(":", "_")
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / f"{uuid.uuid4().hex}{ext}"
    file_path.write_bytes(content)

    document_text = ""
    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(str(file_path))
            text_parts = []
            for i, page in enumerate(reader.pages, 1):
                t = page.extract_text()
                if t:
                    text_parts.append(t)
            document_text = "\n\n".join(text_parts).strip()
        except ImportError:
            reply_text(reply_token, "⚠️ ระบบยังไม่ได้ติดตั้งโมดูลสำหรับอ่าน PDF (pypdf)")
            file_path.unlink(missing_ok=True)
            return
        except Exception as e:
            reply_text(reply_token, f"เกิดข้อผิดพลาดในการอ่านไฟล์ PDF: {e}")
            file_path.unlink(missing_ok=True)
            return
    elif ext == ".docx":
        try:
            import docx
            doc = docx.Document(str(file_path))
            text_parts = [para.text for para in doc.paragraphs if para.text]
            document_text = "\n".join(text_parts).strip()
        except ImportError:
            reply_text(reply_token, "⚠️ ระบบยังไม่ได้ติดตั้งโมดูลสำหรับอ่าน Word (python-docx)")
            file_path.unlink(missing_ok=True)
            return
        except Exception as e:
            reply_text(reply_token, f"เกิดข้อผิดพลาดในการอ่านไฟล์ Word: {e}")
            file_path.unlink(missing_ok=True)
            return

    file_path.unlink(missing_ok=True)

    if not document_text.strip():
        reply_text(reply_token, f"❌ ไม่พบข้อความในไฟล์ '{file_name}' หรือไฟล์นี้ไม่มีข้อความที่สามารถอ่านได้ครับ")
        return

    # เริ่มโหมดสรุปเอกสาร
    start_pdf_flow(session_key, mode="doc_summary")
    session = get_session(session_key)
    session["extracted_text"] = document_text

    msg = (
        f"📄 ได้รับไฟล์เอกสาร '{file_name}' เรียบร้อยแล้วครับ\n"
        f"อ่านข้อความพบประมาณ {len(document_text):,} ตัวอักษร\n\n"
        f"กรุณาพิมพ์หรือกดเลือกโหมดที่ต้องการสรุป:\n"
        f"• 'สรุปแบบสั้น' (ไม่เกิน 5 บรรทัด)\n"
        f"• 'สรุปแบบละเอียด' (หรือพิมพ์ 'เสร็จแล้ว')\n"
        f"• 'สรุปเพื่อสอบ' (มีประเด็นสำคัญ/สิ่งที่ควรจำ)\n"
        f"• 'สรุปเป็นข้อ' (แบ่งเป็นประเด็นย่อยอย่างชัดเจน)\n"
        f"• 'สรุปเป็น mind map' (โครงสร้างแผนผังความคิด)"
    )
    reply_text(reply_token, msg)


def _summarize_extracted_text(reply_token: str, session_key: str, document_text: str, summary_type: str) -> None:
    summary = summarize_document_text(document_text, summary_type)
    mode_title = summary_type if summary_type in {"สรุปแบบสั้น", "สรุปแบบละเอียด", "สรุปเพื่อสอบ", "สรุปเป็นข้อ", "สรุปเป็น mind map"} else "สรุปแบบละเอียด"
    msg = (
        f"📝 ผลลัพธ์ {mode_title}\n"
        f"════════════════════\n"
        f"{summary}\n"
        f"════════════════════\n"
        f"ล้างข้อมูลเซสชันเรียบร้อยแล้วครับ พิมพ์คำสั่งอื่นต่อได้เลย 💬"
    )
    clear_session(session_key)
    reply_text(reply_token, msg[:5000])
