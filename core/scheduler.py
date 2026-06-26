from __future__ import annotations

import os
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from core.db_service import get_pending_notifications, mark_notified

LINE_PUSH_ENDPOINT    = "https://api.line.me/v2/bot/message/push"
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")

scheduler = BackgroundScheduler(timezone="Asia/Bangkok")


def push_message(user_id: str, text: str) -> None:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return
    try:
        requests.post(
            LINE_PUSH_ENDPOINT,
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"to": user_id, "messages": [{"type": "text", "text": text}]},
            timeout=10,
        )
    except Exception as e:
        print(f"[Push] {e}")


def check_notifications() -> None:
    events = get_pending_notifications()
    for ev in events:
        user_id = ev["user_id"].split(":")[-1]
        msg = (
            f"🔔 แจ้งเตือนนัดหมาย\n"
            f"📌 {ev['title']}\n"
            f"📅 {ev['event_date']} {ev['event_time']}"
        )
        push_message(user_id, msg)
        mark_notified(ev["id"])


def cleanup_expired_files() -> None:
    """ลบรูปภาพและไฟล์สรุป PDF ใน storage ที่มีอายุเกิน 24 ชั่วโมงเพื่อความปลอดภัยและประหยัดพื้นที่ดิสก์"""
    from pathlib import Path
    import time

    base_dir = Path(__file__).resolve().parent.parent
    folders = [
        base_dir / "storage" / "uploads",
        base_dir / "storage" / "generated"
    ]
    now = time.time()
    one_day = 24 * 60 * 60  # 24 ชั่วโมง

    for folder in folders:
        if not folder.exists():
            continue
        
        # วนลูปสแกนไฟล์ทั้งหมดแบบ recursive
        for path in folder.rglob("*"):
            if path.is_file():
                try:
                    mtime = path.stat().st_mtime
                    if now - mtime > one_day:
                        path.unlink()
                        print(f"[Cleanup] ลบไฟล์หมดอายุ: {path.name}")
                except Exception as e:
                    print(f"[Cleanup] เกิดข้อผิดพลาดในการลบไฟล์ {path}: {e}")
        
        # ลบโฟลเดอร์ย่อยที่ว่างเปล่าใน uploads
        if folder.name == "uploads":
            for sub in folder.iterdir():
                if sub.is_dir():
                    try:
                        if not any(sub.iterdir()):
                            sub.rmdir()
                            print(f"[Cleanup] ลบโฟลเดอร์ว่างเปล่า: {sub.name}")
                    except Exception:
                        pass


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.add_job(check_notifications, "interval", minutes=1, id="notify")
        scheduler.add_job(cleanup_expired_files, "interval", hours=12, id="cleanup")
        scheduler.start()
