import json
import os
from datetime import datetime, timezone
from typing import Any

SESSIONS_FILE = "memory/line_sessions.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_parent() -> None:
    os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)


def load_sessions() -> dict[str, Any]:
    if not os.path.exists(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_sessions(sessions: dict[str, Any]) -> None:
    _ensure_parent()
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def _default_session() -> dict[str, Any]:
    return {"state": "idle", "mode": "pdf", "images": [], "slip_amounts": []}


def get_session(session_key: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.get(session_key)
    if isinstance(session, dict):
        session.setdefault("state", "idle")
        session.setdefault("mode", "pdf")
        session.setdefault("images", [])
        session.setdefault("slip_amounts", [])
        return session
    return _default_session()


def start_pdf_flow(session_key: str, mode: str = "pdf") -> dict[str, Any]:
    sessions = load_sessions()
    session = {
        "state": "waiting_for_images",
        "mode": mode,
        "images": [],
        "slip_amounts": [],
        "updated_at": _now_iso(),
    }
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def add_image(session_key: str, image_path: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.get(session_key, _default_session())
    session.setdefault("mode", "pdf")
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


def set_waiting_for_filename(session_key: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.get(session_key, _default_session())
    session["state"] = "waiting_for_filename"
    session.setdefault("mode", "pdf")
    session.setdefault("images", [])
    session.setdefault("slip_amounts", [])
    session["updated_at"] = _now_iso()
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def clear_session(session_key: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.pop(session_key, _default_session())
    save_sessions(sessions)
    return session
