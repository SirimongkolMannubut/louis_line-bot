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


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.add_job(check_notifications, "interval", minutes=1, id="notify")
        scheduler.start()
