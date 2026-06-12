import json
import os
from datetime import datetime

MEMORY_FILE = "memory/conversations.json"

def load_history() -> list:
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_history(history: list):
    os.makedirs("memory", exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def add_message(history: list, role: str, content: str) -> list:
    history.append({
        "role": role,
        "content": content,
        "time": datetime.now().strftime("%H:%M")
    })
    # เก็บแค่ 40 ข้อความล่าสุด
    if len(history) > 40:
        history = history[-40:]
    save_history(history)
    return history

def to_ollama_messages(history: list, system_prompt: str) -> list:
    msgs = [{"role": "system", "content": system_prompt}]
    for h in history:
        msgs.append({"role": h["role"], "content": h["content"]})
    return msgs

def clear_history():
    save_history([])
    return []
