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


def get_session(session_key: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.get(session_key)
    if isinstance(session, dict):
        session.setdefault("state", "idle")
        session.setdefault("mode", "pdf")
        session.setdefault("images", [])
        return session
    return {"state": "idle", "mode": "pdf", "images": []}


def start_pdf_flow(session_key: str, mode: str = "pdf") -> dict[str, Any]:
    sessions = load_sessions()
    session = {
        "state": "waiting_for_images",
        "mode": mode,
        "images": [],
        "updated_at": _now_iso(),
    }
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def add_image(session_key: str, image_path: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.get(
        session_key, {"state": "waiting_for_images", "mode": "pdf", "images": []}
    )
    session.setdefault("mode", "pdf")
    session.setdefault("images", [])
    session["state"] = "waiting_for_images"
    session["images"].append(image_path)
    session["updated_at"] = _now_iso()
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def set_waiting_for_filename(session_key: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.get(session_key, {"state": "idle", "mode": "pdf", "images": []})
    session["state"] = "waiting_for_filename"
    session.setdefault("mode", "pdf")
    session.setdefault("images", [])
    session["updated_at"] = _now_iso()
    sessions[session_key] = session
    save_sessions(sessions)
    return session


def clear_session(session_key: str) -> dict[str, Any]:
    sessions = load_sessions()
    session = sessions.pop(session_key, {"state": "idle", "mode": "pdf", "images": []})
    save_sessions(sessions)
    return session
