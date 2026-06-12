from __future__ import annotations

import os
from pathlib import Path
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY", ""))


def transcribe_audio(audio_path: str) -> str:
    """แปลงเสียงเป็นข้อความด้วย Groq Whisper"""
    try:
        with open(audio_path, "rb") as f:
            result = client.audio.transcriptions.create(
                file=(Path(audio_path).name, f),
                model="whisper-large-v3",
                language="th",
                response_format="text",
            )
        return result.strip() if isinstance(result, str) else result.text.strip()
    except Exception as e:
        return f"[Error] {e}"


def transcribe_and_summarize(audio_path: str, ask_ai_fn, user_id: str = "default") -> str:
    text = transcribe_audio(audio_path)
    if text.startswith("[Error]"):
        return text
    summary = ask_ai_fn(
        f"สรุปข้อความต่อไปนี้เป็นภาษาไทย กระชับ อ่านง่าย:\n{text}",
        user_id=user_id
    )
    return f"🎤 ข้อความจากเสียง:\n{text}\n\n📋 สรุป:\n{summary}"
