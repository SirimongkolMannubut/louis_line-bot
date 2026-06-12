import os
import json
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY", ""))
MODEL  = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "คุณคือ LouisAI ผู้ช่วย AI ที่ฉลาด พูดภาษาไทยได้ ตอบกระชับ ชัดเจน เป็นมิตร "
    "คุณไม่มีความสามารถในการบันทึก จำ หรือลบข้อมูลส่วนตัว (เช่น ชื่อ อายุ อาชีพ) นัดหมาย การเงิน หรือสลิปด้วยตนเอง "
    "ระบบหลังบ้านจะเป็นผู้ดึงข้อมูลเหล่านี้จากฐานข้อมูลมาใส่ในพรอมต์ให้คุณโดยอัตโนมัติหากมีข้อมูล "
    "ห้ามเสนอตัวที่จะจำข้อมูลหรือบอกผู้ใช้ว่าจำข้อมูลได้ด้วยตนเองโดยเด็ดขาด ตอบตรงๆ ไม่ต้องอธิบาย reasoning"
)

CHAT_HISTORY_DIR = Path(__file__).resolve().parent.parent / "memory" / "chat_history"
CHAT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
MAX_HISTORY = 20  # เก็บแค่ 20 ข้อความล่าสุด


def _history_path(user_id: str) -> Path:
    safe = user_id.replace(":", "_").replace("/", "_")
    return CHAT_HISTORY_DIR / f"{safe}.json"


def _load_history(user_id: str) -> list:
    path = _history_path(user_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_history(user_id: str, history: list) -> None:
    _history_path(user_id).write_text(
        json.dumps(history[-MAX_HISTORY:], ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def ask_ai(message: str, user_id: str = "default", profile_user_id: str | None = None) -> str:
    try:
        from core.user_profile import get_profile_summary
        target_profile_id = profile_user_id or user_id
        profile = get_profile_summary(target_profile_id)
        system  = SYSTEM_PROMPT
        if profile:
            system += f"\n\nข้อมูลผู้ใช้:\n{profile}"

        history = _load_history(user_id)
        history.append({"role": "user", "content": message})
        messages = [{"role": "system", "content": system}] + history[-MAX_HISTORY:]

        res = client.chat.completions.create(
            model=MODEL, messages=messages, max_tokens=800,
        )
        reply = res.choices[0].message.content
        history.append({"role": "assistant", "content": reply})
        _save_history(user_id, history)
        return reply
    except Exception as e:
        return f"[Error] {e}"


def clear_chat_history(user_id: str) -> None:
    path = _history_path(user_id)
    if path.exists():
        path.unlink()
