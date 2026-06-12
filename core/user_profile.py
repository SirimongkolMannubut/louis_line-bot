"""
user_profile.py  —  เก็บข้อมูลผู้ใช้ลง SQLite (ไม่หายแม้ deploy ใหม่)
ใช้ LINE userId เป็น primary key โดยตรง ไม่ต้อง login
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from core.db_service import get_conn

# ── CRUD ─────────────────────────────────────────────────────────────────────


def get_profile(user_id: str) -> dict:
    """คืน dict ข้อมูลผู้ใช้ หรือ {} ถ้าไม่มี"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name, age, job, location, data_json FROM user_profile WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if not row:
        return {}
    result: dict = {}
    if row["name"]:
        result["name"] = row["name"]
    if row["age"]:
        result["age"] = row["age"]
    if row["job"]:
        result["job"] = row["job"]
    if row["location"]:
        result["location"] = row["location"]
    try:
        extra = json.loads(row["data_json"] or "{}")
        result.update(extra)
    except Exception:
        pass
    return result


def save_profile(user_id: str, data: dict) -> None:
    """Upsert ข้อมูลผู้ใช้ (merge กับที่มีอยู่แล้ว)"""
    existing = get_profile(user_id)
    merged = {**existing, **data}

    name = merged.pop("name", None)
    age = merged.pop("age", None)
    job = merged.pop("job", None)
    location = merged.pop("location", None)
    extra = json.dumps(merged, ensure_ascii=False)
    now = datetime.now().isoformat(timespec="seconds")

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_profile (user_id, name, age, job, location, data_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name       = excluded.name,
                age        = excluded.age,
                job        = excluded.job,
                location   = excluded.location,
                data_json  = excluded.data_json,
                updated_at = excluded.updated_at
            """,
            (user_id, name, age, job, location, extra, now),
        )


def get_profile_summary(user_id: str) -> str:
    """คืนสรุปโปรไฟล์เป็น string สำหรับ inject เข้า system prompt"""
    profile = get_profile(user_id)
    if not profile:
        return ""
    lines: list[str] = []
    if profile.get("name"):
        lines.append(f"ชื่อผู้ใช้: {profile['name']}")
    if profile.get("age"):
        lines.append(f"อายุ: {profile['age']} ปี")
    if profile.get("job"):
        lines.append(f"อาชีพ: {profile['job']}")
    if profile.get("location"):
        lines.append(f"ที่อยู่: {profile['location']}")
    for k, v in profile.items():
        if k not in {"name", "age", "job", "location"}:
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


# ── Auto-extract จากข้อความ ───────────────────────────────────────────────────

_PATTERNS: list[tuple[str, str]] = [
    (
        r"(?:ผม|ฉัน|หนู|เรา|กระผม)(?:ชื่อ|นามว่า|คือ|คือคน|ชื่อว่า)\s*([ก-๙a-zA-Z][ก-๙a-zA-Z\s]{0,20})",
        "name",
    ),
    (
        r"(?:ชื่อ)(?:ผม|ฉัน|หนู|เรา|กระผม)(?:ว่า|คือ|คือ)?\s*([ก-๙a-zA-Z][ก-๙a-zA-Z\s]{0,20})",
        "name",
    ),
    (r"(?:อายุ)\s*(\d{1,3})\s*(?:ปี)?", "age"),
    (
        r"(?:ทำงาน|อาชีพ|ทำงานเป็น|เป็น)(?:เป็น|ที่|ว่า|งาน)?\s*([ก-๙a-zA-Z][ก-๙a-zA-Z\s]{0,20})",
        "job",
    ),
    (
        r"(?:อยู่ที่|อาศัยอยู่ที่|บ้านอยู่|อยู่แถว|อยู่ย่าน)\s*([ก-๙a-zA-Z][ก-๙a-zA-Z\s]{0,30})",
        "location",
    ),
]


def extract_and_save_profile(user_id: str, text: str) -> dict:
    """
    ดึงข้อมูลส่วนตัวจากข้อความแล้วบันทึกลง SQLite
    คืน dict ของข้อมูลที่บันทึกใหม่ (ว่างถ้าไม่พบอะไร)
    """
    saved: dict = {}
    for pattern, key in _PATTERNS:
        m = re.search(pattern, text.strip())
        if m:
            value = m.group(1).strip().rstrip("ครับค่ะนะ ")
            if value:
                saved[key] = value

    if saved:
        save_profile(user_id, saved)
    return saved


# ── Query helpers ─────────────────────────────────────────────────────────────

_ASKING_NAME_RE = re.compile(
    r"ผม.*ชื่อ.*อะไร|เรา.*ชื่อ.*อะไร|ฉัน.*ชื่อ.*อะไร|"
    r"ชื่อ.*ผม.*คือ|ชื่อ.*เรา.*คือ|"
    r"จำ.*ชื่อ.*ผม|จำ.*ชื่อ.*เรา|"
    r"ผมชื่ออะไร|เราชื่ออะไร|ฉันชื่ออะไร|"
    r"บอก.*ชื่อ.*ผม|บอก.*ชื่อ.*เรา"
)

_ASKING_PROFILE_RE = re.compile(
    r"ผมเป็นใคร|ฉันเป็นใคร|เราเป็นใคร|"
    r"รู้จัก.*ผม|รู้จัก.*เรา|รู้จัก.*ฉัน|"
    r"ข้อมูล.*ผม|ข้อมูล.*เรา|"
    r"จำ.*ผมได้|จำ.*เราได้"
)


def is_asking_own_name(text: str) -> bool:
    return bool(_ASKING_NAME_RE.search(text))


def is_asking_own_profile(text: str) -> bool:
    return bool(_ASKING_PROFILE_RE.search(text))
