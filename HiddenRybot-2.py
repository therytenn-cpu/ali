# -*- coding: utf-8 -*-
import logging
import secrets
import sqlite3
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
BOT_USERNAME = "HiddenRybot"
CHANNEL_USERNAME = "Rytenam"
DB_FILE = Path(__file__).with_name("hiddenrybot.db")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row


def init_db():
    with db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                start_text TEXT NOT NULL DEFAULT '',
                link_code TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL DEFAULT '',
                chat_closed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS relations (
                owner_id INTEGER NOT NULL,
                guest_id INTEGER NOT NULL,
                is_banned INTEGER NOT NULL DEFAULT 0,
                is_vip INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (owner_id, guest_id)
            );
            CREATE TABLE IF NOT EXISTS forwarded (
                owner_id INTEGER NOT NULL,
                owner_message_id INTEGER NOT NULL,
                guest_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (owner_id, owner_message_id)
            );
            CREATE TABLE IF NOT EXISTS rate_limits (
                owner_id INTEGER NOT NULL,
                guest_id INTEGER NOT NULL,
                sent_at INTEGER NOT NULL
            );
            """
        )
        columns = {r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()}
        if "chat_closed" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN chat_closed INTEGER NOT NULL DEFAULT 0")


def get_user(user_id):
    return db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


def ensure_user(user_id):
    row = get_user(user_id)
    if row:
        return row
    code = secrets.token_hex(6)
    while db.execute("SELECT 1 FROM users WHERE link_code = ?", (code,)).fetchone():
        code = secrets.token_hex(6)
    with db:
        db.execute("INSERT INTO users(user_id, link_code) VALUES (?, ?)", (user_id, code))
    return get_user(user_id)


def set_state(user_id, state):
    with db:
        db.execute("UPDATE users SET state = ? WHERE user_id = ?", (state, user_id))


def relation(owner_id, guest_id):
    return db.execute("SELECT * FROM relations WHERE owner_id = ? AND guest_id = ?", (owner_id, guest_id)).fetchone()


def set_relation(owner_id, guest_id, banned=None, vip=None):
    old = relation(owner_id, guest_id)
    current_banned = old["is_banned"] if old else 0
    current_vip = old["is_vip"] if old else 0
    if banned is not None:
        current_banned = int(bool(banned))
    if vip is not None:
        current_vip = int(bool(vip))
    with db:
        db.execute(
            """
            INSERT INTO relations(owner_id, guest_id, is_banned, is_vip)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_id, guest_id) DO UPDATE SET
                is_banned=excluded.is_banned,
                is_vip=excluded.is_vip
            """,
            (owner_id, guest_id, current_banned, current_vip),
        )


def save_forwarded(owner_id, owner_message_id, guest_id):
    with db:
        db.execute(
            "INSERT OR REPLACE INTO forwarded(owner_id, owner_message_id, guest_id, created_at) VALUES (?, ?, ?, ?)",
            (owner_id, owner_message_id, guest_id, int(time.time())),
        )


def get_forwarded(owner_id, owner_message_id):
    return db.execute(
        "SELECT guest_id FROM forwarded WHERE owner_id = ? AND owner_message_id = ?",
        (owner_id, owner_message_id),
    ).fetchone()


def record_rate_and_check(owner_id, guest_id):
    now = int(time.time())
    cutoff = now - 120
    with db:
        db.execute("DELETE FROM rate_limits WHERE sent_at < ?", (cutoff,))
        count = db.execute(
            "SELECT COUNT(*) FROM rate_limits WHERE owner_id = ? AND guest_id = ? AND sent_at >= ?",
            (owner_id, guest_id, cutoff),
        ).fetchone()[0]
        if count >= 12:
            return False
        db.execute(
            "INSERT INTO rate_limits(owner_id, guest_id, sent_at) VALUES (?, ?, ?)",
            (owner_id, guest_id, now),
        )
    return True


def cleanup_old_forwarded():
    with db:
        total = db.execute("SELECT COUNT(*) FROM forwarded").fetchone()[0]
        if total > 40:
            remove_count = total - 40
            db.execute(
                "DELETE FROM forwarded WHERE rowid IN (SELECT rowid FROM forwarded ORDER BY created_at ASC LIMIT ?)",
                (remove_count,),
            )


def styled_button(text, callback_data, style):
    return InlineKeyboardButton(text=text, callback_data=callback_data, style=style)


def main_menu():
    return InlineKeyboardMarkup([
        [styled_button("تنظیمات", "menu_settings", "primary"), styled_button("ساخت لینک", "make_link", "primary")],
        [styled_button("تنظیمات پیشرفته", "advanced", "primary"), InlineKeyboardButton("کانال", url=f"https://t.me/{CHANNEL_USERNAME}", style="primary")],
    ])


def settings_menu():
    return InlineKeyboardMarkup([
        [styled_button("تنظیم نام نمایشی", "set_display", "success"), styled_button("تنظیم متن استارت", "set_start_text", "success")],
        [styled_button("بازگشت", "back_main", "success")],
    ])


def advanced_menu():
    return InlineKeyboardMarkup([
        [styled_button("لیست ویژه", "vip_list", "danger"), styled_button("لیست بن", "ban_list", "danger")],
        [styled_button("بازگشت", "back_main", "danger")],
    ])


def link_menu():
    return InlineKeyboardMarkup([
        [styled_button("بستن چت", "close_chat", "danger"), styled_button("تغییر لینک", "change_link", "primary")],
        [styled_button("بازگشت", "back_main", "primary")],
    ])


def message_actions(guest_id):
    return InlineKeyboardMarkup([
        [styled_button("بن", f"ban:{guest_id}", "danger"), styled_button("ویژه", f"vip:{guest_id}", "success")],
    ])


def confirm_menu(action, guest_id):
    return InlineKeyboardMarkup([
        [styled_button("تأیید", f"confirm_{action}:{guest_id}", "danger"), styled_button("لغو", "cancel_action", "primary")],
    ])


def list_buttons(rows, action, style):
    buttons = []
    current = []
    for row in rows:
        current.append(styled_button(str(row["label"]), f"{action}:{row['guest_id']}", style))
        if len(current) == 2:
            buttons.append(current)
            current = []
    if current:
        buttons.append(current)
    return buttons


async def safe_user_info(context, user_id):
    try:
        chat = await context.bot.get_chat(user_id)
        if chat.username:
            return f"@{chat.username}"
        if chat.full_name:
            return chat.full_name
    except Exception:
        pass
    return str(user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id)
    if context.args:
        code = context.args[0].strip()
        target = db.execute("SELECT * FROM users WHERE link_code = ?", (code,)).fetchone()
        if target:
            owner_id = target["user_id"]
            if owner_id == user.id:
                await update.message.reply_text("نمی‌توانید وارد لینک ناشناس خودتان شوید.")
                return
            if target["chat_closed"]:
                await update.message.reply_text("این چت بسته شده است.")
                return
            rel = relation(owner_id, user.id)
            if rel and rel["is_banned"]:
                return
            set_relation(owner_id, user.id)
            context.user_data["active_owner"] = owner_id
            text = target["start_text"] or "شما وارد چت ناشناس شدید."
            text = text.replace(".نام", target["display_name"] or "")
            await update.message.reply_text(text)
            return
    await update.message.reply_text("به ربات چت ناشناس خوش آمدید.", reply_markup=main_menu())


async def show_link(query, user_id):
    row = get_user(user_id)
    link = f"https://t.me/{BOT_USERNAME}?start={row['link_code']}"
    await query.edit_message_text(f"لینک ناشناس تو\n{link}", reply_markup=link_menu())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    ensure_user(user_id)
    data = query.data

    if data == "menu_settings":
        await query.edit_message_text("تنظیمات", reply_markup=settings_menu())
        return
    if data == "advanced":
        await query.edit_message_text("تنظیمات پیشرفته", reply_markup=advanced_menu())
        return
    if data == "back_main":
        set_state(user_id, "")
        await query.edit_message_text("منوی اصلی", reply_markup=main_menu())
        return
    if data == "make_link":
        await show_link(query, user_id)
        return
    if data == "close_chat":
        with db:
            db.execute("UPDATE users SET chat_closed = 1 WHERE user_id = ?", (user_id,))
        await query.edit_message_text("چت بسته شد.", reply_markup=main_menu())
        return
    if data == "change_link":
        new_code = secrets.token_hex(6)
        while db.execute("SELECT 1 FROM users WHERE link_code = ?", (new_code,)).fetchone():
            new_code = secrets.token_hex(6)
        with db:
            db.execute("UPDATE users SET link_code = ?, chat_closed = 0 WHERE user_id = ?", (new_code, user_id))
        await show_link(query, user_id)
        return
    if data == "set_display":
        set_state(user_id, "display")
        await query.edit_message_text("نامی که میخواهید کاربران آن را ببینند را وارد کنید", reply_markup=InlineKeyboardMarkup([[styled_button("بازگشت", "menu_settings", "success")]]))
        return
    if data == "set_start_text":
        set_state(user_id, "start_text")
        await query.edit_message_text("متن استارتی که میخواهید کاربران با استارت لینک شما مشاهده کنند را ارسال کنید\nبا استفاده از تگ .نام ، داخل متن استارت خود از نام نمایشی که تنظیم کردید استفاده کنید", reply_markup=InlineKeyboardMarkup([[styled_button("بازگشت", "menu_settings", "success")]]))
        return
    if data == "vip_list":
        rows = db.execute("SELECT guest_id FROM relations WHERE owner_id = ? AND is_vip = 1 ORDER BY guest_id", (user_id,)).fetchall()
        if not rows:
            text = "لیست ویژه خالیه"
            markup = advanced_menu()
        else:
            lines = []
            prepared = []
            for i, row in enumerate(rows, 1):
                name = await safe_user_info(context, row["guest_id"])
                lines.append(f"{i}-{name}")
                prepared.append({"guest_id": row["guest_id"], "label": f"حذف {i}"})
            markup = InlineKeyboardMarkup(list_buttons(prepared, "remove_vip", "danger") + [[styled_button("بازگشت", "advanced", "danger")]])
            text = "\n".join(lines)
        await query.edit_message_text(text, reply_markup=markup)
        return
    if data == "ban_list":
        rows = db.execute("SELECT guest_id FROM relations WHERE owner_id = ? AND is_banned = 1 ORDER BY guest_id", (user_id,)).fetchall()
        if not rows:
            text = "لیست بن خالیه"
            markup = advanced_menu()
        else:
            lines = []
            prepared = []
            for i, row in enumerate(rows, 1):
                name = await safe_user_info(context, row["guest_id"])
                lines.append(f"{i}-{name}")
                prepared.append({"guest_id": row["guest_id"], "label": f"رفع بن {i}"})
            markup = InlineKeyboardMarkup(list_buttons(prepared, "unban", "danger") + [[styled_button("بازگشت", "advanced", "danger")]])
            text = "\n".join(lines)
        await query.edit_message_text(text, reply_markup=markup)
        return
    if data == "cancel_action":
        await query.edit_message_text("عملیات لغو شد.", reply_markup=main_menu())
        return
    if data.startswith("ban:"):
        guest_id = int(data.split(":", 1)[1])
        await query.edit_message_text("آیا مطمئنید میخواهید این کاربر را بن کنید؟", reply_markup=confirm_menu("ban", guest_id))
        return
    if data.startswith("vip:"):
        guest_id = int(data.split(":", 1)[1])
        await query.edit_message_text("آیا مطمئنید میخواهید این کاربر را ویژه کنید؟", reply_markup=confirm_menu("vip", guest_id))
        return
    if data.startswith("confirm_ban:"):
        guest_id = int(data.split(":", 1)[1])
        set_relation(user_id, guest_id, banned=True)
        await query.edit_message_text(f"کاربر {guest_id} بن شد و تو لیست بن قرار گرفت.", reply_markup=main_menu())
        return
    if data.startswith("confirm_vip:"):
        guest_id = int(data.split(":", 1)[1])
        set_relation(user_id, guest_id, vip=True)
        owner = get_user(user_id)
        display_name = owner["display_name"] or "سازنده"
        try:
            await context.bot.send_message(chat_id=guest_id, text=f"از طرف {display_name} ویژه شدی")
        except Exception:
            pass
        await query.edit_message_text(f"کاربر {guest_id} ویژه شد و تو لیست ویژه قرار گرفت.", reply_markup=main_menu())
        return
    if data.startswith("remove_vip:"):
        guest_id = int(data.split(":", 1)[1])
        set_relation(user_id, guest_id, vip=False)
        await query.edit_message_text("کاربر از لیست ویژه حذف شد.", reply_markup=advanced_menu())
        return
    if data.startswith("unban:"):
        guest_id = int(data.split(":", 1)[1])
        set_relation(user_id, guest_id, banned=False)
        await query.edit_message_text("کاربر از لیست بن خارج شد.", reply_markup=advanced_menu())
        return


async def forward_anonymous(update, context, owner_id, guest_id, is_vip):
    if not record_rate_and_check(owner_id, guest_id):
        await update.effective_message.reply_text("محدودیت ارسال فعال شد. حداکثر ۱۲ پیام در هر ۲ دقیقه مجاز است.")
        return
    if is_vip:
        try:
            chat = await context.bot.get_chat(guest_id)
            username = f"@{chat.username}" if chat.username else str(guest_id)
        except Exception:
            username = str(guest_id)
        await context.bot.send_message(chat_id=owner_id, text=f"{username} :")
    copied = await context.bot.copy_message(
        chat_id=owner_id,
        from_chat_id=update.effective_chat.id,
        message_id=update.effective_message.message_id,
        reply_markup=message_actions(guest_id),
    )
    save_forwarded(owner_id, copied.message_id, guest_id)
    cleanup_old_forwarded()


async def send_owner_reply(update, context, guest_id):
    rel = relation(update.effective_user.id, guest_id)
    if not rel or rel["is_banned"]:
        return
    try:
        await context.bot.send_message(chat_id=guest_id, text="پیامتو دید")
        await context.bot.copy_message(
            chat_id=guest_id,
            from_chat_id=update.effective_chat.id,
            message_id=update.effective_message.message_id,
        )
    except Exception:
        pass


async def owner_reply_if_valid(update, context):
    message = update.effective_message
    if not message or not message.reply_to_message:
        return False
    mapped = get_forwarded(update.effective_user.id, message.reply_to_message.message_id)
    if not mapped:
        return False
    await send_owner_reply(update, context, mapped["guest_id"])
    return True


async def text_message(update, context):
    user = update.effective_user
    ensure_user(user.id)
    if await owner_reply_if_valid(update, context):
        return
    row = get_user(user.id)
    if row["state"] == "display":
        value = (update.message.text or "").strip()
        if not value:
            await update.message.reply_text("لطفا یک نام وارد کنید.")
            return
        with db:
            db.execute("UPDATE users SET display_name = ?, state = '' WHERE user_id = ?", (value, user.id))
        await update.message.reply_text("نام نمایشی شما سیو شد", reply_markup=settings_menu())
        return
    if row["state"] == "start_text":
        with db:
            db.execute("UPDATE users SET start_text = ?, state = '' WHERE user_id = ?", (update.message.text or "", user.id))
        await update.message.reply_text("متن استارت شما سیو شد", reply_markup=settings_menu())
        return
    owner_id = context.user_data.get("active_owner")
    if not owner_id:
        await update.message.reply_text("ابتدا از لینک ناشناس یک کاربر وارد شوید.", reply_markup=main_menu())
        return
    owner = get_user(owner_id)
    if not owner or owner["chat_closed"] or owner_id == user.id:
        return
    rel = relation(owner_id, user.id)
    if not rel or rel["is_banned"]:
        return
    await forward_anonymous(update, context, owner_id, user.id, rel["is_vip"])


async def non_text_message(update, context):
    user = update.effective_user
    ensure_user(user.id)
    if await owner_reply_if_valid(update, context):
        return
    owner_id = context.user_data.get("active_owner")
    if not owner_id or owner_id == user.id:
        return
    owner = get_user(owner_id)
    if not owner or owner["chat_closed"]:
        return
    rel = relation(owner_id, user.id)
    if not rel or rel["is_banned"]:
        return
    await forward_anonymous(update, context, owner_id, user.id, rel["is_vip"])


async def error_handler(update, context):
    logger.error("Unhandled exception", exc_info=context.error)


def main():
    if BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("توکن ربات را در BOT_TOKEN وارد کنید.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, non_text_message))
    app.add_error_handler(error_handler)
    print("HiddenRybot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
