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


def clear_profile(user_id: str) -> None:
    """ลบข้อมูลผู้ใช้ทั้งหมด"""
    with get_conn() as conn:
        conn.execute("DELETE FROM user_profile WHERE user_id=?", (user_id,))



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


# ── Auto-extract จากข้อความ ───────────────────────────────────────────────

# คำที่สื่อถึงตัวเอง (บุรุษและสตรี)
_FIRST_PERSON = r"(?:ผม|ฉัน|หนู|เรา|กระผม|หนูหน่อย)"

# คำต่อท้ายที่ชี้ว่าเป็นประโยคถาม
_QUESTION_ENDINGS = ("ไหม", "มั้ย", "หรอ", "หรือเปล่า", "?", "ครับ?", "ค่ะ?")

# คำถามที่ไม่ควรเป็นค่าที่ดึงมา
_QUESTION_WORDS = {
    "อะไร",
    "ใคร",
    "ยังไง",
    "อย่างไร",
    "ที่ไหน",
    "เมื่อไหร่",
    "เท่าไหร่",
    "เท่าไร",
    "กี่",
    "ทำไม",
    "อ่านว่า",
    "อะไรนะ",
}


def _is_question(text: str) -> bool:
    """ตรวจว่าประโยคนี้เป็นประโยคถามหรือเปล่า"""
    t = text.strip()
    return any(t.endswith(q) for q in _QUESTION_ENDINGS)


def _clean(value: str) -> str:
    """ลบคำลงท้ายที่ไม่ใช่ส่วนหนึ่งของชื่อ/ค่า เช่น นะ ครับ เลย"""
    for suffix in ("นะครับ", "นะค่ะ", "ครับ", "ค่ะ", "นะ", "เลย", "จ้า", "จ้านะ"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    return value.strip()


_PATTERNS: list[tuple[str, str]] = [
    # ── ชื่อ
    (r"(?:\u0e1c\u0e21|\u0e09\u0e31\u0e19|\u0e2b\u0e19\u0e39|\u0e40\u0e23\u0e32|\u0e01\u0e23\u0e30\u0e1c\u0e21)\s*(?:\u0e0a\u0e37\u0e48\u0e2d|\u0e19\u0e32\u0e21\u0e27\u0e48\u0e32|\u0e0a\u0e37\u0e48\u0e2d\u0e27\u0e48\u0e32)\s*(\S+)", "name"),
    (r"\u0e0a\u0e37\u0e48\u0e2d(?:\u0e1c\u0e21|\u0e09\u0e31\u0e19|\u0e2b\u0e19\u0e39|\u0e40\u0e23\u0e32|\u0e01\u0e23\u0e30\u0e1c\u0e21)\s*(?:\u0e27\u0e48\u0e32|\u0e04\u0e37\u0e2d)?\s*(\S+)", "name"),
    (r"^\u0e0a\u0e37\u0e48\u0e2d\s*(\S+)", "name"),
    # ── อายุ
    (r"(?:\u0e1c\u0e21|\u0e09\u0e31\u0e19|\u0e2b\u0e19\u0e39|\u0e40\u0e23\u0e32|\u0e01\u0e23\u0e30\u0e1c\u0e21).*?\u0e2d\u0e32\u0e22\u0e38\s*(\d{1,3})\s*(?:\u0e1b\u0e35)?", "age"),
    (r"^\u0e2d\u0e32\u0e22\u0e38\s*(\d{1,3})\s*(?:\u0e1b\u0e35)?", "age"),
    # ── อาชีพ
    (
        r"(?:\u0e1c\u0e21|\u0e09\u0e31\u0e19|\u0e2b\u0e19\u0e39|\u0e40\u0e23\u0e32|\u0e01\u0e23\u0e30\u0e1c\u0e21)\s*(?:\u0e17\u0e33\u0e07\u0e32\u0e19\u0e40\u0e1b\u0e47\u0e19|\u0e17\u0e33\u0e07\u0e32\u0e19\u0e17\u0e35\u0e48|\u0e21\u0e35\u0e2d\u0e32\u0e0a\u0e35\u0e1e|\u0e1b\u0e23\u0e30\u0e01\u0e2dบอาชีพ)\s*(?:\u0e40\u0e1b\u0e47\u0e19|\u0e27\u0e48\u0e32|\u0e04\u0e37\u0e2d)?\s*(\S+)",
        "job",
    ),
    (r"^\u0e2d\u0e32\u0e0a\u0e35\u0e1e(?:\u0e02\u0e2d\u0e07)?(?:\u0e1c\u0e21|\u0e09\u0e31\u0e19|\u0e2b\u0e19\u0e39|\u0e40\u0e23\u0e32|\u0e01\u0e23\u0e30\u0e1c\u0e21)?\s*(?:\u0e40\u0e1b\u0e47\u0e19|\u0e04\u0e37\u0e2d)\s*(\S+)", "job"),
    # ── ที่อยู่
    (r"(?:\u0e1c\u0e21|\u0e09\u0e31\u0e19|\u0e2b\u0e19\u0e39|\u0e40\u0e23\u0e32|\u0e01\u0e23\u0e30\u0e1c\u0e21)\s*(?:\u0e2d\u0e22\u0e39\u0e48\u0e17\u0e35่|\u0e2d\u0e32\u0e28\u0e31\u0e22\u0e2d\u0e22\u0e39\u0e48\u0e17\u0e35่|\u0e1a\u0e49\u0e32\u0e19\u0e2d\u0e22\u0e39\u0e48|\u0e2d\u0e22\u0e39\u0e48\u0e41\u0e16\u0e27|\u0e2d\u0e22\u0e39\u0e48\u0e22\u0e48\u0e32\u0e19)\s*(\S+)", "location"),
]


def extract_and_save_profile(user_id: str, text: str) -> dict:
    """
    ดึงข้อมูลส่วนตัวจากข้อความแล้วบันทึกลง SQLite
    คืน dict ของข้อมูลที่บันทึกใหม่ (ว่างถ้าไม่พบอะไร)
    """
    # ห้ามบันทึกข้อมูลผู้ใช้หากข้อความไม่มีคำสำคัญที่ระบุ
    allowed_keywords = [
        "ชื่อ", "อายุ", "อาชีพ",
        "ทำงาน", "อยู่ที่", "อาศัยอยู่",
    ]
    if not any(kw in text for kw in allowed_keywords):
        return {}

    # ไม่ดึงจากประโยคถาม
    if _is_question(text):
        return {}

    saved: dict = {}
    for pattern, key in _PATTERNS:
        try:
            m = re.search(pattern, text.strip())
        except re.error:
            continue
        if m:
            value = _clean(m.group(1).strip())
            # ห้าม: ค่าสั้นเกินไป, มีช่องว่าง, หรือเป็นคำถาม
            if (
                value
                and len(value) >= 2
                and " " not in value
                and value not in _QUESTION_WORDS
            ):
                saved[key] = value

    if saved:
        save_profile(user_id, saved)
    return saved


# ── Query helpers ─────────────────────────────────────────────────────────────

_ASKING_NAME_RE = re.compile(
    r"ผม.*ชื่อ.*อะไร|เรา.*ชื่อ.*อะไร|ฉัน.*ชื่อ.*อะไร|"
    r"ชื่อ.*ผม.*คือ|ชื่อ.*เรา.*คือ|ชื่อ.*ฉัน.*คือ|"
    r"จำ.*ชื่อ.*ผม|จำ.*ชื่อ.*เรา|จำ.*ชื่อ.*ฉัน|"
    r"ผมชื่ออะไร|เราชื่ออะไร|ฉันชื่ออะไร|"
    r"บอก.*ชื่อ.*ผม|บอก.*ชื่อ.*เรา|บอก.*ชื่อ.*ฉัน"
)

_ASKING_PROFILE_RE = re.compile(
    r"ผมเป็นใคร|ฉันเป็นใคร|เราเป็นใคร|"
    r"รู้จัก.*ผม|รู้จัก.*เรา|รู้จัก.*ฉัน|"
    r"ข้อมูล.*ผม|ข้อมูล.*เรา|ข้อมูล.*ฉัน|"
    r"จำ.*ผมได้|จำ.*เราได้|จำ.*ฉันได้"
)

_ASKING_AGE_RE = re.compile(
    r"อายุเท่าไร|อายุเท่าไหร่|อายุของฉัน|อายุของผม|อายุของเรา|อายุเท่าไหร่แล้ว|เราอายุเท่าไหร่"
)

_ASKING_JOB_RE = re.compile(
    r"อาชีพอะไร|ทำอาชีพอะไร|ทำงานอะไร|อาชีพของฉัน|อาชีพของผม|อาชีพของเรา|เราทำงานอะไร"
)


def is_asking_own_name(text: str) -> bool:
    return bool(_ASKING_NAME_RE.search(text))


def is_asking_own_profile(text: str) -> bool:
    return bool(_ASKING_PROFILE_RE.search(text))


def is_asking_own_age(text: str) -> bool:
    return bool(_ASKING_AGE_RE.search(text))


def is_asking_own_job(text: str) -> bool:
    return bool(_ASKING_JOB_RE.search(text))
