import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

SESSIONS_FILE = "memory/line_sessions.json"
_sessions_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_parent() -> None:
    os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)


def load_sessions() -> dict[str, Any]:
    with _sessions_lock:
        if not os.path.exists(SESSIONS_FILE):
            return {}
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def save_sessions(sessions: dict[str, Any]) -> None:
    with _sessions_lock:
        _ensure_parent()
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)


def _default_session() -> dict[str, Any]:
    return {"state": "idle", "mode": None, "current_mode": None, "images": [], "slip_amounts": []}


def get_session(session_key: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.get(session_key)
    if isinstance(session, dict):
        session.setdefault("state", "idle")
        session.setdefault("mode", None)
        session.setdefault("current_mode", None)
        session.setdefault("images", [])
        session.setdefault("slip_amounts", [])
        return session
    return _default_session()


def start_pdf_flow(session_key: str, mode: str = "pdf") -> dict[str, Any]:
    import uuid
    sessions = load_sessions()
    session = {
        "state": "waiting_for_images",
        "mode": mode,
        "current_mode": mode,
        "images": [],
        "slip_amounts": [],
        "batch_id": uuid.uuid4().hex,
        "updated_at": _now_iso(),
    }
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def add_image(session_key: str, image_path: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.get(session_key, _default_session())
    session.setdefault("mode", None)
    session.setdefault("current_mode", None)
    session.setdefault("images", [])
    session.setdefault("slip_amounts", [])
    session["state"] = "waiting_for_images"
    session["images"].append(image_path)
    session["updated_at"] = _now_iso()
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def add_slip_amount(session_key: str, amount: float) -> dict[str, Any]:
    """เพิ่มยอดสลิปเข้า session สำหรับโหมด multi_slip"""
    sessions = load_sessions()
    session = sessions.get(session_key, _default_session())
    session.setdefault("slip_amounts", [])
    session["slip_amounts"].append(amount)
    session["updated_at"] = _now_iso()
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def add_slip_entry(session_key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """เพิ่มข้อมูลสลิปเต็ม (bank, amount, ref, date) พร้อมกันกับสิ่งที่เก็บไว้แล้ว"""
    sessions = load_sessions()
    session = sessions.get(session_key, _default_session())
    session.setdefault("slip_data", [])
    session["slip_data"].append(entry)
    session.setdefault("slip_amounts", [])
    session["slip_amounts"].append(entry.get("amount") or 0.0)
    session["updated_at"] = _now_iso()
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def set_waiting_for_filename(session_key: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.get(session_key, _default_session())
    session["state"] = "waiting_for_filename"
    session.setdefault("mode", None)
    session.setdefault("current_mode", None)
    session.setdefault("images", [])
    session.setdefault("slip_amounts", [])
    session["updated_at"] = _now_iso()
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def set_waiting_for_slip_type(
    session_key: str,
    slip_data: list | None = None,
) -> dict[str, Any]:
    """ตั้งค่า state รอยืนยันประเภทสลิป (รายรับ/รายจ่าย)"""
    sessions = load_sessions()
    session = sessions.get(session_key, _default_session())
    session["state"] = "waiting_for_slip_type"
    if slip_data is not None:
        session["slip_data"] = slip_data
    session["updated_at"] = _now_iso()
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def set_waiting_for_pdf_confirm(
    session_key: str,
    slip_data: list | None = None,
    receipt_summaries: list[str] | None = None,
) -> dict[str, Any]:
    """ตั้งค่า state รอยืนยัน PDF หลังสรุปในแชทแล้ว"""
    sessions = load_sessions()
    session = sessions.get(session_key, _default_session())
    session["state"] = "waiting_for_pdf_confirm"
    session.setdefault("mode", None)
    session.setdefault("current_mode", None)
    if slip_data is not None:
        session["slip_data"] = slip_data
    if receipt_summaries is not None:
        session["receipt_summaries"] = receipt_summaries
    session["updated_at"] = _now_iso()
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def clear_session(session_key: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.pop(session_key, _default_session())
    session["state"] = "idle"
    session["mode"] = None
    session["current_mode"] = None
    save_sessions(sessions)
    return session
