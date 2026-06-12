import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

api_key = os.getenv("GROQ_API_KEY") or ""
client = Groq(api_key=api_key) if api_key else None
MODEL  = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "คุณคือ LouisAI ผู้ช่วย AI ที่ฉลาด พูดภาษาไทยได้ ตอบกระชับ ชัดเจน เป็นมิตร "
    "ตอบตรงๆ ไม่ต้องอธิบาย reasoning"
)

def ask_ai(message: str) -> str:
    if not client:
        return "[Error] GROQ_API_KEY not set"
    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": message},
            ],
            max_tokens=800,
        )
        return res.choices[0].message.content
    except Exception as e:
        return f"[Error] {e}"
