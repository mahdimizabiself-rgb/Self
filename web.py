# web.py
import os
from flask import Flask
import threading
import asyncio

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_bot():
    import bot
    asyncio.run(bot.main())

if __name__ == "__main__":
    # 🔴 این خط کلید حل مشکل Render هست
    port = int(os.environ.get("PORT", 10000))

    # ✅ اول Flask رو اجرا کن (PORT فوراً bind میشه)
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port),
        daemon=True
    ).start()

    # ✅ بعدش ربات تلگرام رو اجرا کن
    run_bot()
