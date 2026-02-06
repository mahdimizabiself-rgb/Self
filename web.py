# web.py
import os
import threading
import asyncio
from flask import Flask
import signal
import sys

app = Flask(__name__)

# مهم: این import باید بعد از شرط if __name__ تغییر کند توی bot.py، 
# یعنی bot.main() فقط وقتی صدا زده بشه که ما صریح بخوایم اجراش کنیم.
import bot

@app.route("/")
def home():
    return "Bot is running"

def start_bot_loop():
    # این تابع در thread جدا اجرا میشه و event loop خودش رو میسازه
    asyncio.run(bot.main())

def handle_exit(signum, frame):
    # تمیز بستن (دلخواه)
    try:
        sys.exit(0)
    except SystemExit:
        os._exit(0)

if __name__ == "__main__":
    # ثبت سیگنال‌ها برای خاموشی تمیز
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # شروع بات در thread پس‌زمینه (daemon=True تا با خروج پروسس کنار بره)
    t = threading.Thread(target=start_bot_loop, daemon=True)
    t.start()

    # پورت را از متغیر محیطی read کن (Render آن را قرار می‌دهد)
    port = int(os.environ.get("PORT", 10000))

    # حالا Flask را روی 0.0.0.0:PORT اجرا کن — این باز شدن پورت را تضمین می‌کند
    app.run(host="0.0.0.0", port=port)
