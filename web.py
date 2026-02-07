import os
from flask import Flask
import threading
import asyncio

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_bot():
    from bot import main   # 👈 فقط این خط عوض شده
    asyncio.run(main())    # 👈 این هم

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port),
        daemon=True
    ).start()

    run_bot()
