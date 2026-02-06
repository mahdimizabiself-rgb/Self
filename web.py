# web.py
from flask import Flask
import threading
import asyncio
import bot

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_bot():
    asyncio.run(bot.main())

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=10000)
