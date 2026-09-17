import logging
import hashlib
import urllib.parse
import time
import asyncio
import aiohttp
import sqlite3
import os
import contextlib
import ssl
from aiohttp import web
import pytz
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    CopyTextButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest

# ─── LOGGING CONFIGURATION ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── CONFIGURATION (REPLACED WITH ENVIRONMENT/PLACEHOLDERS) ────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
ROBO_LOGIN = os.getenv("ROBO_LOGIN", "your_merchant_login")
ROBO_PASS1 = os.getenv("ROBO_PASS1", "your_merchant_pass1")
ROBO_PASS2 = os.getenv("ROBO_PASS2", "your_merchant_pass2")
IS_TEST = int(os.getenv("IS_TEST", 0))
BOT_USERNAME = "your_vpn_bot"
CHANNEL_ID = "@your_vpn_channel"

ADMIN_ID = int(os.getenv("ADMIN_ID", 123456789))
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.yourdomain.com:3001")

PHOTO_MAIN = "https://placehold.co/600x400/png?text=Locis+VPN+Main"
PHOTO_VPN = "https://placehold.co/600x400/png?text=Locis+VPN+Dashboard"

FILE_ID_MAIN = None
MOSCOW = pytz.timezone("Europe/Moscow")
bot_app = None

PLANS = {
    "plan_1m": ("🟢 1 Month", 80),
    "plan_3m": ("🔵 3 Months", 200),
    "plan_1y": ("⭐ 1 Year", 700),
}

PLAN_DAYS = {
    "🟢 1 Month": 30,
    "🔵 3 Months": 90,
    "⭐ 1 Year": 365,
}

HAPP_LINKS = {
    "windows": "https://github.com/Happ-proxy/happ-desktop/releases/latest",
    "android": "https://play.google.com/store/apps/details?id=com.happproxy",
    "macos": "https://apps.apple.com/app/happ-proxy-utility-plus/id6746188973",
    "ios": "https://apps.apple.com/app/happ-proxy-utility/id6504287215",
}

WELCOME_CAPTION = (
    "🔒 *Welcome to Locis VPN!*\n\n"
    "⚡ High-speed connection\n"
    "💰 Affordable access\n"
    "🛡 Robust privacy protection\n\n"
    "Select an option below 👇"
)

MAIN_KEYBOARD = [
    [InlineKeyboardButton("🖥 Manage Subscription", callback_data="subscription")],
    [InlineKeyboardButton("❓ About Locis VPN", callback_data="about_locis")],
    [InlineKeyboardButton("👥 Referral Program", callback_data="referral")],
    [InlineKeyboardButton("📢 Telegram Channel", url="https://t.me/your_vpn_channel")],
    [InlineKeyboardButton("📖 Setup Guide", callback_data="manual")],
    [InlineKeyboardButton("📜 Terms of Service", url="https://yourdomain.github.io/terms/")],
]

# ─── HELPER FUNCTIONS ──────────────────────────────────────────────────
def is_sub_active(expire_str: str) -> bool:
    try:
        exp_date = datetime.strptime(expire_str, "%d.%m.%Y").date()
        now_date = datetime.now(MOSCOW).date()
        return exp_date >= now_date
    except Exception:
        return False

async def check_user_sub(user_id: int, bot) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logger.error(f"Channel subscription check error: {e}")
        return False

async def ensure_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if await check_user_sub(uid, context.bot):
        return True

    sub_caption = (
        "⚠️ *Please subscribe to our Telegram channel to use this bot!*\n\n"
        "Join [Locis VPN Channel](https://t.me/your_vpn_channel) and click **«✅ I Subscribed»**."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url="https://t.me/your_vpn_channel")],
        [InlineKeyboardButton("✅ I Subscribed", callback_data="check_sub")]
    ])

    if update.callback_query:
        query = update.callback_query
        await query.answer("❌ Please subscribe first!", show_alert=True)
        try:
            await query.edit_message_caption(caption=sub_caption, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
    elif update.message:
        await update.message.reply_photo(
            photo=FILE_ID_MAIN or PHOTO_MAIN,
            caption=sub_caption,
            parse_mode="Markdown",
            reply_markup=kb
        )
    return False

# ─── DATABASE (SQLite) ─────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), 'bot_data.db')

def db_execute(query, params=(), fetchone=False, fetchall=False):
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetchone: return cursor.fetchone()
            if fetchall: return cursor.fetchall()

def init_db():
    db_execute('''CREATE TABLE IF NOT EXISTS subscriptions
                  (user_id INTEGER, plan TEXT, expire TEXT, key TEXT, sub_link TEXT)''')
    db_execute('''CREATE TABLE IF NOT EXISTS trials (user_id INTEGER PRIMARY KEY)''')
    db_execute('''CREATE TABLE IF NOT EXISTS referrals
                  (user_id INTEGER PRIMARY KEY, inviter_id INTEGER, reward_given INTEGER DEFAULT 0)''')
    db_execute('''CREATE TABLE IF NOT EXISTS orders
                  (inv_id INTEGER PRIMARY KEY, user_id INTEGER, plan_name TEXT, days INTEGER, extend_sub_idx INTEGER)''')
    db_execute('''CREATE TABLE IF NOT EXISTS warnings
                  (key TEXT PRIMARY KEY, warn_date TEXT)''')

init_db()

def db_save_sub(uid, plan, expire, key, sub_link):
    db_execute("INSERT INTO subscriptions VALUES (?, ?, ?, ?, ?)", (uid, plan, expire, key, sub_link))

def db_get_subs(uid):
    rows = db_execute("SELECT plan, expire, key, sub_link FROM subscriptions WHERE user_id = ?", (uid,), fetchall=True)
    return [{"plan": r[0], "expire": r[1], "key": r[2], "sub_link": r[3]} for r in rows]

def db_update_sub(key, new_expire, new_plan=None):
    if new_plan:
        db_execute("UPDATE subscriptions SET expire = ?, plan = ? WHERE key = ?", (new_expire, new_plan, key))
    else:
        db_execute("UPDATE subscriptions SET expire = ? WHERE key = ?", (new_expire, key))

def db_check_trial(uid):
    res = db_execute("SELECT 1 FROM trials WHERE user_id = ?", (uid,), fetchone=True)
    return res is not None

def db_add_trial(uid):
    db_execute("INSERT OR IGNORE INTO trials VALUES (?)", (uid,))

def db_add_ref(uid, inviter):
    db_execute("INSERT OR IGNORE INTO referrals (user_id, inviter_id) VALUES (?, ?)", (uid, inviter))

def db_get_inviter(uid):
    res = db_execute("SELECT inviter_id FROM referrals WHERE user_id = ?", (uid,), fetchone=True)
    return res[0] if res else None

def db_check_ref_rewarded(uid):
    res = db_execute("SELECT reward_given FROM referrals WHERE user_id = ?", (uid,), fetchone=True)
    return res[0] == 1 if res else True

def db_mark_ref_rewarded(uid):
    db_execute("UPDATE referrals SET reward_given = 1 WHERE user_id = ?", (uid,))

def db_get_ref_count(inviter):
    res = db_execute("SELECT COUNT(*) FROM referrals WHERE inviter_id = ?", (inviter,), fetchone=True)
    return res[0] if res else 0

def db_save_order(inv_id, uid, plan_name, days, ext_idx):
    db_execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", (inv_id, uid, plan_name, days, ext_idx))

def db_get_order(inv_id):
    res = db_execute("SELECT user_id, plan_name, days, extend_sub_idx FROM orders WHERE inv_id = ?", (inv_id,), fetchone=True)
    if res:
        return {"user_id": res[0], "plan": res[1], "days": res[2], "extend_sub_idx": res[3]}
    return None

def db_reset_referrals():
    db_execute("DELETE FROM referrals")

# ─── API INTERACTION ──────────────────────────────────────────────────
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def get_link(inv_id, amount, user_id):
    signature_string = f"{ROBO_LOGIN}:{amount}:{inv_id}:{ROBO_PASS1}:Shp_user_id={user_id}"
    signature = hashlib.md5(signature_string.encode()).hexdigest()
    success_url = f"https://t.me/{BOT_USERNAME}?start=pay_ok"
    return (f"https://auth.robokassa.ru/Merchant/Index.aspx?MerchantLogin={ROBO_LOGIN}"
            f"&OutSum={amount}&InvId={inv_id}&Description=VPN_Subscription"
            f"&SignatureValue={signature}&IsTest={IS_TEST}&Shp_user_id={user_id}"
            f"&SuccessURL={urllib.parse.quote(success_url)}")

async def request_vless_key(days: int):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{API_BASE_URL}/create-key", json={"days": days}, ssl=ssl_ctx, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("key"), data.get("sub_link"), None
                return None, None, f"API Error: {resp.status}"
    except Exception as e: return None, None, str(e)

async def extend_vless_subscription(key: str, days: int):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{API_BASE_URL}/extend-subscription", json={"key": key, "days": days}, ssl=ssl_ctx, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("new_expiry"), None
                return None, f"Extension Error: {resp.status}"
    except Exception as e: return None, str(e)

# ─── PAYMENT WEBHOOK ──────────────────────────────────────────────────
async def robokassa_result(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        out_sum, inv_id = data.get('OutSum'), data.get('InvId')
        signature = data.get('SignatureValue', '').upper()
        user_id = int(data.get('Shp_user_id'))

        check_string = f"{out_sum}:{inv_id}:{ROBO_PASS2}:Shp_user_id={user_id}"
        if hashlib.md5(check_string.encode()).hexdigest().upper() == signature:
            order_info = db_get_order(int(inv_id))
            if not order_info:
                logger.error(f"Order {inv_id} not found in DB!")
                return web.Response(text="error")

            days = order_info["days"]
            sub_idx = order_info["extend_sub_idx"]
            subs = db_get_subs(user_id)

            if sub_idx is not None and sub_idx < len(subs):
                target_sub = subs[sub_idx]
                new_ms, _ = await extend_vless_subscription(target_sub['key'], days)
                if new_ms:
                    expire_str = datetime.fromtimestamp(new_ms/1000, MOSCOW).strftime("%d.%m.%Y")
                    db_update_sub(target_sub['key'], expire_str, new_plan=order_info["plan"])
                    msg = f"✅ *Subscription Extended!*\nUpdated Plan: *{order_info['plan']}*\nNew Expiry: *{expire_str}*"
                else: msg = "⚠️ API error during extension."
            else:
                key, sub_link, _ = await request_vless_key(days)
                if key:
                    expire_date = (datetime.now(MOSCOW) + timedelta(days=days)).strftime("%d.%m.%Y")
                    db_save_sub(user_id, order_info["plan"], expire_date, key, sub_link)
                    msg = f"🎉 *Payment Successful!*\nPlan: *{order_info['plan']}*\nYour key has been added to your account."
                else: msg = "⚠️ Error generating subscription key."

            inviter_id = db_get_inviter(user_id)
            if inviter_id and not db_check_ref_rewarded(user_id):
                db_mark_ref_rewarded(user_id)
                inv_subs = db_get_subs(inviter_id)
                if inv_subs:
                    res, _ = await extend_vless_subscription(inv_subs[-1]['key'], 30)
                    if res:
                        new_d = datetime.fromtimestamp(res/1000, MOSCOW).strftime("%d.%m.%Y")
                        db_update_sub(inv_subs[-1]['key'], new_d)
                        try: await bot_app.bot.send_message(inviter_id, f"🎁 Your referred friend paid for a subscription!\nYou received **+30 bonus days**. New expiry date: *{new_d}*", parse_mode="Markdown")
                        except: pass

            await bot_app.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
            return web.Response(text=f"OK{inv_id}")
    except Exception as e: logger.error(f"Error in result: {e}")
    return web.Response(text="error")

# ─── EXPIRATION MONITOR TASK ──────────────────────────────────────────
async def check_expiring_subs_task(app: Application):
    while True:
        try:
            now = datetime.now(MOSCOW)
            tomorrow_str = (now + timedelta(days=1)).strftime("%d.%m.%Y")

            rows = db_execute("SELECT user_id, plan, key FROM subscriptions WHERE expire = ?", (tomorrow_str,), fetchall=True)

            if rows:
                for row in rows:
                    uid, plan, key = row
                    already_warned = db_execute("SELECT 1 FROM warnings WHERE key = ? AND warn_date = ?", (key, tomorrow_str), fetchone=True)

                    if not already_warned:
                        msg = (
                            f"⏳ *Reminder!*\n\n"
                            f"Your subscription *{plan}* expires tomorrow ({tomorrow_str}).\n"
                            f"Please extend it via '🖥 Manage Subscription' to stay connected!"
                        )
                        try:
                            await app.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                            db_execute("INSERT OR REPLACE INTO warnings VALUES (?, ?)", (key, tomorrow_str))
                        except Exception as e:
                            logger.error(f"Failed to notify user {uid}: {e}")

        except Exception as e:
            logger.error(f"Expiration checker loop error: {e}")

        await asyncio.sleep(3600)

# ─── BOT HANDLERS ──────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global FILE_ID_MAIN
    uid = update.effective_user.id

    if not await ensure_subscribed(update, context):
        return

    msg = await update.message.reply_photo(
        photo=FILE_ID_MAIN or PHOTO_MAIN,
        caption=WELCOME_CAPTION,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(MAIN_KEYBOARD),
    )
    if not FILE_ID_MAIN: FILE_ID_MAIN = msg.photo[-1].file_id

async def main():
    req = HTTPXRequest(read_timeout=30, write_timeout=30)
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).request(req).build()

    bot_app.add_handler(CommandHandler("start", start))

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)

    wa = web.Application()
    wa.router.add_post('/robokassa/result/', robokassa_result)
    runner = web.AppRunner(wa)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()

    logger.info("Bot and Webhook service started successfully.")
    asyncio.create_task(check_expiring_subs_task(bot_app))
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())