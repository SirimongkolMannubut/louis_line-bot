from __future__ import annotations

import json
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parent.parent / "memory" / "profiles"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def _path(user_id: str) -> Path:
    return PROFILE_DIR / f"{user_id.replace(':', '_')}.json"


def get_profile(user_id: str) -> dict:
    p = _path(user_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_profile(user_id: str, data: dict) -> None:
    profile = get_profile(user_id)
    profile.update(data)
    _path(user_id).write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_profile_summary(user_id: str) -> str:
    profile = get_profile(user_id)
    if not profile:
        return ""
    lines = []
    if profile.get("name"):
        lines.append(f"ชื่อผู้ใช้: {profile['name']}")
    for k, v in profile.items():
        if k != "name":
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


def extract_and_save_profile(user_id: str, text: str) -> str | None:
    """ดึงข้อมูลส่วนตัวจากข้อความแล้วบันทึก"""
    import re
    patterns = [
        (r"(?:ผม|ฉัน|หนู|เรา)(?:ชื่อ|นามว่า|คือ)\s*(\S+)", "name"),
        (r"(?:อายุ)\s*(\d+)", "age"),
        (r"(?:ทำงาน|อาชีพ)(?:เป็น|ที่|ว่า)?\s*(\S+)", "job"),
        (r"(?:อยู่ที่|อาศัยอยู่ที่|บ้านอยู่)\s*(\S+)", "location"),
    ]
    saved = {}
    for pattern, key in patterns:
        m = re.search(pattern, text)
        if m:
            saved[key] = m.group(1)

    if saved:
        save_profile(user_id, saved)
        return saved.get("name")
    return None
