import json
import os
import uuid
from datetime import datetime, timedelta
from html import escape
from math import ceil
from re import findall, sub
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
    User,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChosenInlineResultHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
)

API_TOKEN = ""
ADMINS_IDS: set[int] = set()

BLACK_LIST: set[int] = set()
NOTIFICATIONS_LIST: set[int] = set()
VIPS_LIST: set[int] = set()

CONFIG_LIST = "config.jsonl"
LIST = "list.json"

messages: dict[str, tuple[int, frozenset, int, str, str, str]] = {}

SPECIAL_CHARS = "!&$"

def save_config() -> None:
    with open(CONFIG_LIST, 'w', encoding="utf-8") as f:
        f.write(json.dumps(API_TOKEN) + '\n')
        f.write(json.dumps(list(ADMINS_IDS)))

def load_config() -> None:
    global API_TOKEN, ADMINS_IDS
    try:
        with open(CONFIG_LIST, 'r', encoding="utf-8") as f:
            API_TOKEN = json.loads(f.readline().strip())
            ADMINS_IDS = set(json.loads(f.readline().strip()))
    except (FileNotFoundError, json.JSONDecodeError, IndexError):
        save_config()

def save() -> None:
    data = {
        "blacklist":     list(BLACK_LIST),
        "notifications": list(NOTIFICATIONS_LIST),
        "vips":          list(VIPS_LIST)
    }
    tmp = LIST + ".tmp"
    with open(tmp, 'w', encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii = False, indent = 4)
    os.replace(tmp, LIST)

def load() -> None:
    global BLACK_LIST, NOTIFICATIONS_LIST, VIPS_LIST
    try:
        with open(LIST, 'r', encoding="utf-8") as f: data = json.load(f)
    except FileNotFoundError:
        save()
        return
    except json.JSONDecodeError:
        os.replace(LIST, LIST + ".broken")
        save()
        return

    BLACK_LIST         = set(data.get("blacklist", []))
    NOTIFICATIONS_LIST = set(data.get("notifications", []))
    VIPS_LIST          = set(data.get("vips", []))

async def parse_msg(user_id: int, query: str):
    parts = [ part.strip() for part in query.split('|') ]

    main = parts[0].split(maxsplit=1)
    if len(main) < 2: return None

    raw = main[0]
    prefix_len = len(raw) - len(raw.lstrip(SPECIAL_CHARS))
    prefix, target = raw[:prefix_len], raw[prefix_len:]
    message = main[1].strip()

    if len(target) < 2 or not message: return None
    if target[0] == '@': target_id = target[1:]
    elif target.isdigit(): target_id = int(target)
    else: return None

    flags = ('!' in prefix) | (('&' in prefix) << 1) | (('$' in prefix) << 2)

    placeholder = parts[1] if len(parts) >= 2 and parts[1] else "Message hidden."
    others = parts[2] if len(parts) >= 3 and parts[2] else "This message is not for you."

    return user_id, frozenset((target_id, )), flags, message, others, placeholder

async def build_msg(user_id: int, query: str) -> InlineQueryResultArticle | None:
    data = await parse_msg(user_id, query)
    if data is None: return None
    _, users, flags, message, others, placeholder = data
    ln = len(users)

    targets = ", ".join(map(str, users))
    if ln < 3: target = targets
    else:      target = f"{targets}..."

    key = uuid.uuid4().hex[:16]

    if (flags & 1):
        to_target_desc = f"To anyone but {target}: {message}"
        to_others_desc = f"To them: {others}"
    else:
        to_target_desc = f"To {target}: {message}"
        to_others_desc = f"To anyone: {others}"

    keyboard = InlineKeyboardMarkup([ [InlineKeyboardButton("Message", callback_data=f"msg:{key}")] ])
    return InlineQueryResultArticle(
        id = key,
        title = f"Send msg{'(Yr msg may b cut of)' if len(query)>=256 else ''}",
        description = f"{to_target_desc[:40]}{'...' if len(to_target_desc) > 40 else ''}\nText in msg: {placeholder[:40]}{'...' if len(placeholder) > 40 else ''}\n{to_others_desc[:40]}{'...' if len(to_others_desc) > 40 else ''}",
        input_message_content = InputTextMessageContent(message_text = placeholder),
        reply_markup = keyboard
    )

async def send_msg(user_id: int, query: str) -> InlineQueryResultArticle | None:
    if user_id in BLACK_LIST: return None
    qparts = query.split()
    if len(qparts) < 2 or not (msg := messages.get(qparts[1])) or user_id != msg[0]: return None
    key = qparts[1]
    if (msg[2] & 1):
        to_target_desc = f"To anyone but {', '.join(map(str, msg[1]))}: {msg[3]}"
        to_others_desc = f"To them: {msg[4]}"
    else:
        to_target_desc = f"To {', '.join(map(str, msg[1]))}: {msg[3]}"
        to_others_desc = f"To anyone: {msg[4]}"
    keyboard = InlineKeyboardMarkup([ [InlineKeyboardButton("Message", callback_data=f"msg:{key}")] ])
    return InlineQueryResultArticle(
        id = key,
        title = f"Send msg{'(Yr msg may b cut of)' if len(query)>=256 else ''}",
        description = f"{to_target_desc[:40]}{'...' if len(to_target_desc) > 40 else ''}\nText in msg: {msg[5][:40]}{'...' if len(msg[5]) > 40 else ''}\n{to_others_desc[:40]}{'...' if len(to_others_desc) > 40 else ''}",
        input_message_content = InputTextMessageContent(message_text = msg[5]),
        reply_markup = keyboard
    )

async def build_info(user: User, user_id: int) -> InlineQueryResultArticle:
    info_text = (
        f"Your first name: {user.first_name or '-'}\n"
        f"Your last name: {user.last_name or '-'}\n"
        f"Your username: @{user.username or '-'}\n"
        f"Your telegram ID: {user_id}"
    )
    if user_id in ADMINS_IDS: info_text += "\nYou are a bot admin"

    return InlineQueryResultArticle(
        id="info",
        title="Send my info",
        description=info_text.replace("\n", " | "),
        input_message_content=InputTextMessageContent(message_text=info_text)
    )

def istarget(msg, user_id: int, username: str | None) -> bool:
    if user_id in msg[1] or username in msg[1]: return True
    if not username: return False
    username = username.lower()
    return any(isinstance(target, str) and target.lower() == username for target in msg[1])

def may_delete(msg, user_id: int, username: str | None) -> bool:
    if user_id == msg[0]: return True
    return not (msg[2] & 1) and istarget(msg, user_id, username)

async def build_delete(user_id: int, username: str | None, key: str) -> InlineQueryResultArticle | None:
    msg = messages.get(key)
    if msg is None or msg[2] & 4 or not may_delete(msg, user_id, username): return None

    keyboard = InlineKeyboardMarkup( [[InlineKeyboardButton("Confirm delete", callback_data=f"del:{key}")]] )

    return InlineQueryResultArticle(
        id=f"del-{key}",
        title="Delete message",
        description=f"Message id: {key}",
        input_message_content=InputTextMessageContent( message_text=f"Delete message {key}?" ),
        reply_markup=keyboard
    )

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.inline_query.from_user.id
    if user_id in BLACK_LIST: return

    query = update.inline_query.query
    if not query: return
    results = [ ]
    result = None

    if query.lower() == "info": result = await build_info(update.inline_query.from_user, user_id)
    elif query.lower().startswith("delete"): result = await build_delete(user_id, update.inline_query.from_user.username, query[6:].strip())
    elif query.lower().startswith("send"): result = await send_msg(user_id, query)
    else: result = await build_msg(user_id, query)

    if result: results.append(result)

    try: await update.inline_query.answer(results, cache_time=5, is_personal=True)
    except BadRequest: return

async def chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = update.chosen_inline_result
    from_user = result.from_user.id
    msg = await parse_msg(from_user, result.query)
    if not msg: return
    messages[result.result_id] = msg
    notify(context, f"New message sent from: {from_user}")

def notify(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if NOTIFICATIONS_LIST: context.application.create_task(notify_admins(context, text))

async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    for admin_id in NOTIFICATIONS_LIST:
        try: await context.bot.send_message(chat_id=admin_id, text=text)
        except TelegramError as e: print(f"Failed to notify admin {admin_id}: {e}")

def mark_seen(data: tuple) -> tuple:
    if data[2] & 8: return data
    return data[:2] + (data[2] | 8, ) + data[3:]

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    clicker_id = query.from_user.id
    if clicker_id in BLACK_LIST:
        await query.answer(cache_time=3600)
        return
    clicker_username = query.from_user.username

    message = query.data.split(':')
    act, message_key = message[0], message[1]

    if act == "msg":
        msg = messages.get(message_key)
        if not msg:
            try: await query.answer(text="No message in temp data.", show_alert=False, cache_time=3600)
            except BadRequest: return
            return

        exc, vis  = msg[2] & 1, msg[2] & 2
        targets = ", ".join(map(str, msg[1]))
        tick = "  ✓✓" if msg[2] & 8 else ""

        if clicker_id == msg[0]:
            if exc: text = f"Message for anyone but {targets}:\n{msg[3]}\n\nMessage for {targets}:\n{msg[4]}\n\n{message_key}"
            else: text = f"Message for {targets}:\n{msg[3]}{tick}\n\nMessage for anyone:\n{msg[4]}\n\n{message_key}"
        elif istarget(msg, clicker_id, clicker_username):
            if exc: text = f"Message:\n{msg[4]}"
            else:
                if vis: text = f"Message for you:\n{msg[3]}{tick}\n\nMessage for anyone:\n{msg[4]}\n\n{message_key}"
                else: text = f"Message:\n{msg[3]}{tick}\n\n{message_key}"
                messages[message_key] = mark_seen(msg)
        else:
            if exc:
                if vis: text = f"Message for anyone:\n{msg[3]}\n\nMessage for {targets}:\n{msg[4]}"
                else: text = f"Message for anyone but {targets}:\n{msg[3]}"
            else: text = f"Message:\n{msg[4]}"

        notify(context = context, text = f"User {clicker_username} clicked on {message_key}")

        if len(text) <= 200: await query.answer(text = text, show_alert = True, cache_time = 10)
        else:
            pages = ceil(len(text) / 180)
            if len(message) < 3:
                buttons = iter(
                    InlineKeyboardButton(f"Message(Part {i+1})", callback_data=f"msg:{message_key}:{i}")
                    for i in range(pages)
                )
                rows_count  = ceil(pages / 3)
                base, extra = divmod(pages, rows_count)

                keyboard = [
                    [next(buttons) for _ in range(base + (r < extra))]
                    for r in range(rows_count)
                ]

                try: await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
                except TelegramError as e: print(e)
                await query.answer()
                return

            page = min(max(int(message[2]), 0), pages - 1)
            chunk = text[page * 180:(page + 1) * 180]
            await query.answer(text=f"{chunk}\n\n({page + 1}/{pages})", show_alert=True, cache_time = 10)
            return
    elif act == "del":
        if (msg := messages.get(message_key)):
            if msg[2] & 4: await query.answer("This message cannot be deleted.", show_alert=False, cache_time=3600)
            elif not may_delete(msg, clicker_id, clicker_username): await query.answer("You can't delete this message.", show_alert=False, cache_time=3600)
            else:
                del messages[message_key]
                await query.edit_message_text(f"Message {message_key} burned 🔥.")
                await query.answer()
        else: await query.answer("Message is not in db.", show_alert=False, cache_time=3600)

async def notifications_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMINS_IDS: return
    if user_id in NOTIFICATIONS_LIST:
        NOTIFICATIONS_LIST.discard(user_id)
        save()
        await update.message.reply_text("Notifications disabled.")
    else:
        NOTIFICATIONS_LIST.add(user_id)
        save()
        await update.message.reply_text("Notifications enabled.")

async def block_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMINS_IDS: return
    if not context.args:
        await update.message.reply_text("Usage: /block <id>")
        return

    try: target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid id.")
        return

    if target in ADMINS_IDS:
        await update.message.reply_text("Can't block an admin.")
        return

    if target not in BLACK_LIST:
        BLACK_LIST.add(target)
        save()
        await update.message.reply_text(f"User {target} blocked.")
    else:
        await update.message.reply_text(f"User {target} is already blocked.")

async def unblock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMINS_IDS: return
    if not context.args:
        await update.message.reply_text("Usage: /unblock <id>")
        return

    try: target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid id.")
        return

    if target in BLACK_LIST:
        BLACK_LIST.discard(target)
        save()
        await update.message.reply_text(f"User {target} unblocked.")
    else:
        await update.message.reply_text(f"User {target} is not in the blacklist.")

async def blacklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMINS_IDS: return
    if not BLACK_LIST:
        await update.message.reply_text("Blacklist is empty.")
        return
    await update.message.reply_text("Blacklist:\n" + "\n".join(str(i) for i in BLACK_LIST))

async def message_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use /message in a private chat with the bot.")
        return
    user_id = update.effective_user.id
    if user_id in BLACK_LIST: return
    if not context.args:
        await update.message.reply_text("Usage: /message <message>")
        return

    diff = end_time - datetime.now(tz=ZoneInfo("Europe/Moscow"))
    hours, rem       = divmod(int(diff.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)

    raw = " ".join(context.args)
    matches = findall(r"=([!&$]*)@(\w+)", raw)
    usernames = frozenset(u for _, u in matches)
    if not usernames:
        await update.message.reply_text("You need to specify one or more recipients in the message.")
        return
    prefix = ''.join(p for p, _ in matches)
    flags = ('!' in prefix) | (('&' in prefix) << 1) | (('$' in prefix) << 2)
    message    = sub(r"=[!&$]*@\w+", "", raw).strip()[:3500]

    if not message:
        await update.message.reply_text("Message is empty.")
        return

    key = uuid.uuid4().hex[:16]
    messages[key] = (user_id, usernames, flags, message, "This message is not for you.", "Message hidden.")

    await update.message.reply_text(
        text = f"Remaining time: {hours}h. {minutes}min. {seconds}s.\nMessage key: <code>{key}</code>\nUsers who can see message by key:\n{'Anyone but:\n' if flags & 1 else ''}@{'\n@'.join(usernames)}\nMessage:\n{escape(message[:1000])}",
        parse_mode=ParseMode.HTML
    )
    notify(context, f"New message from {user_id}")

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    photos = await update.effective_user.get_profile_photos(limit=1)
    text = (
        f"Your first name: {update.effective_user.first_name}\n"
        f"Your last name: {update.effective_user.last_name}\n"
        f"Your username: @{update.effective_user.username}\n"
        f"Your telegram ID: {user_id}"
    )
    if (user_id in ADMINS_IDS): text+="\nYou are an admin"

    if photos.total_count > 0:
        photo = photos.photos[0][-1].file_id
        await update.message.reply_photo(photo=photo, caption=text)
    else: await update.message.reply_text(text)

def main():
    app = Application.builder().token(API_TOKEN).connect_timeout(60).read_timeout(60).build()

    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result))
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    app.add_handler(CommandHandler("notifications", notifications_cmd))
    app.add_handler(CommandHandler(["block",   "ban"],     block_cmd))
    app.add_handler(CommandHandler(["unblock", "unban"],   unblock_cmd))
    app.add_handler(CommandHandler("blacklist", blacklist_cmd))
    app.add_handler(CommandHandler(["message", "msg"], message_cmd))
    app.add_handler(CommandHandler("info", info))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    start_time = datetime.now(tz=ZoneInfo("Europe/Moscow"))
    end_time   = start_time + timedelta(days = 1)
    load_config()
    load()
    main()
