"""
รัน ngrok + uvicorn LINE Bot อัตโนมัติ
"""
import subprocess
import time
import os
import re
import requests
import sys

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
NGROK_EXE = os.path.join(os.path.dirname(__file__), "ngrok.exe")
PORT = 8000


def update_env_url(url: str):
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    content = re.sub(
        r"^PUBLIC_BASE_URL=.*$", f"PUBLIC_BASE_URL={url}", content, flags=re.MULTILINE
    )
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[ENV] อัปเดต PUBLIC_BASE_URL={url}")


def get_ngrok_url(retries=10) -> str:
    for i in range(retries):
        try:
            resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=3)
            tunnels = resp.json().get("tunnels", [])
            for t in tunnels:
                if t.get("proto") == "https":
                    return t["public_url"]
        except Exception:
            pass
        time.sleep(2)
        print(f"  รอ ngrok... ({i+1}/{retries})")
    raise RuntimeError("ไม่พบ ngrok URL — ตรวจสอบว่า ngrok รันอยู่และ authtoken ถูกต้อง")


def main():
    # 1. รัน ngrok
    print(f"[START] กำลังรัน ngrok port {PORT}...")
    ngrok_proc = subprocess.Popen(
        [NGROK_EXE, "http", str(PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 2. รอแล้วดึง URL
    try:
        url = get_ngrok_url()
        print(f"[OK] ngrok URL: {url}")
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        ngrok_proc.terminate()
        sys.exit(1)

    # 3. อัปเดต .env
    update_env_url(url)
    print(f"[WEBHOOK] ตั้งใน LINE Developers:\n   {url}/webhook/line\n")

    # 4. รัน uvicorn ผ่าน python -m
    print(f"[SERVER] กำลังรัน LINE Bot server ที่ port {PORT}...")
    try:
        subprocess.run(
            ["py", "-3", "-m", "uvicorn", "line_bot:app",
             "--host", "0.0.0.0", "--port", str(PORT), "--reload"],
            check=True,
        )
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[STOP] หยุด ngrok...")
        ngrok_proc.terminate()


if __name__ == "__main__":
    main()
