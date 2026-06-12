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
    # ── ชื่อ: first-person + ชื่อ/นามว่า/ชื่อว่า (ไม่รวม คือ เพราะ ambiguous)
    (r"(?:ผม|ฉัน|หนู|เรา|กระผม)\s*(?:ชื่อ|นามว่า|ชื่อว่า)\s*(\S+)", "name"),
    (r"ชื่อ(?:ผม|ฉัน|หนู|เรา|กระผม)\s*(?:ว่า|คือ)?\s*(\S+)", "name"),
    # ── อายุ: first-person + อายุ X หรือ ขึ้นต้นด้วย อายุ
    (r"(?:ผม|ฉัน|หนู|เรา|กระผม).*?อายุ\s*(\d{1,3})\s*(?:ปี)?", "age"),
    (r"^อายุ\s*(\d{1,3})\s*(?:ปี)?", "age"),
    # ── อาชีพ: ต้องมี keyword ที่ชัดเจน (ไม่ใช้ เป็น เพียงอย่างเดียว)
    (
        r"(?:ผม|ฉัน|หนู|เรา|กระผม)\s*(?:ทำงานเป็น|ทำงานที่|มีอาชีพ|ประกอบอาชีพ)\s*(?:เป็น|ว่า|คือ)?\s*(\S+)",
        "job",
    ),
    (r"^อาชีพ(?:ของ)?(?:ผม|ฉัน|หนู|เรา|กระผม)?\s*(?:เป็น|คือ)\s*(\S+)", "job"),
    # ── ที่อยู่: ต้องมี first-person นำ
    (r"(?:ผม|ฉัน|หนู|เรา|กระผม)\s*(?:อยู่ที่|อาศัยอยู่ที่|บ้านอยู่|อยู่แถว|อยู่ย่าน)\s*(\S+)", "location"),
]


def extract_and_save_profile(user_id: str, text: str) -> dict:
    """
    ดึงข้อมูลส่วนตัวจากข้อความแล้วบันทึกลง SQLite
    คืน dict ของข้อมูลที่บันทึกใหม่ (ว่างถ้าไม่พบอะไร)
    """
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
