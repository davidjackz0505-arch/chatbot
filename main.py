import logging
import sqlite3
import os
import threading
import time
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji, BotCommand, constants
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# --------------------------------------------------------------------------------
# ⚙️ SYSTEM CONFIGURATION
# --------------------------------------------------------------------------------
BOT_TOKEN = "8420582565:AAFNPu1P7Qp-sgtrGKaZlCFxNstShgwbilI"
ADMIN_GROUP_ID = -1003325498790
DB_NAME = "relay_bot.db"

# --------------------------------------------------------------------------------
# 🛠️ HIGH-PERFORMANCE DATABASE MANAGER
# --------------------------------------------------------------------------------
class DatabaseManager:
    def __init__(self, db_name):
        self.db_name = db_name
        self.lock = threading.Lock()
        self.conn = None
        self.init_db()

    def get_connection(self):
        # Creates a persistent connection
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_name, check_same_thread=False)
            # WAL Mode = Faster concurrency (Write-Ahead Logging)
            self.conn.execute("PRAGMA journal_mode=WAL;") 
            self.conn.execute("PRAGMA synchronous=NORMAL;")
        return self.conn

    def init_db(self):
        with self.lock:
            conn = self.get_connection()
            c = conn.cursor()
            
            # 1. Message Map
            c.execute('''CREATE TABLE IF NOT EXISTS message_map (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_message_id INTEGER,
                user_id INTEGER,
                user_name TEXT,
                display_id TEXT,
                question_text TEXT,
                created_at TIMESTAMP,
                status TEXT DEFAULT 'PENDING',
                answer_text TEXT,
                admin_responder TEXT
            )''')
            
            # 2. Users
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                display_id TEXT, 
                joined_at TIMESTAMP
            )''')

            # 3. Reply Tracking
            c.execute('''CREATE TABLE IF NOT EXISTS reply_tracking (
                admin_msg_id INTEGER PRIMARY KEY,
                user_chat_id INTEGER,
                sent_msg_id INTEGER,
                admin_name TEXT,
                user_name TEXT
            )''')

            # Indices for speed
            c.execute("CREATE INDEX IF NOT EXISTS idx_msg_admin_id ON message_map(admin_message_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_users_uid ON users(user_id)")
            
            # Migrations (Safe to run every time)
            try: c.execute("ALTER TABLE users ADD COLUMN display_id TEXT")
            except: pass
            try: c.execute("ALTER TABLE message_map ADD COLUMN display_id TEXT")
            except: pass
            try: c.execute("ALTER TABLE message_map ADD COLUMN answer_text TEXT")
            except: pass
            
            conn.commit()

    def execute_write(self, query, params=()):
        with self.lock:
            conn = self.get_connection()
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            return c.lastrowid

    def execute_read_one(self, query, params=()):
        with self.lock:
            conn = self.get_connection()
            c = conn.cursor()
            c.execute(query, params)
            return c.fetchone()

    def execute_read_all(self, query, params=()):
        with self.lock:
            conn = self.get_connection()
            c = conn.cursor()
            c.execute(query, params)
            return c.fetchall()

    def vacuum_db(self):
        with self.lock:
            conn = self.get_connection()
            conn.execute("VACUUM")
            print("♻️ Database Optimized (VACUUM completed)")

# Initialize Global DB
db = DatabaseManager(DB_NAME)

# --------------------------------------------------------------------------------
# 🧹 AUTO CLEANUP TASK (Background Thread)
# --------------------------------------------------------------------------------
def auto_cleanup_task():
    while True:
        try:
            time.sleep(3600) # 1 Hour
            
            cutoff_time = datetime.now() - timedelta(days=1)
            
            # Delete old messages
            db.execute_write("DELETE FROM message_map WHERE created_at < ?", (cutoff_time,))
            
            # Optimize Storage
            db.vacuum_db()
            
        except Exception as e:
            print(f"⚠️ Cleanup Error: {e}")

# --------------------------------------------------------------------------------
# 🌐 FAKE WEB SERVER
# --------------------------------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is active.")

def start_web_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --------------------------------------------------------------------------------
# 🇰🇭 LANGUAGE PACK
# --------------------------------------------------------------------------------
LANG = {
    "brand_header": "🏢 <b>ប្រព័ន្ធជំនួយនិស្សិតហាត់ការគ្រប់ជំនាន់</b>",
    "reply_header": "👨‍💼 <b>ដំណោះស្រាយពីក្រុមការងារ IT_Support</b>",
    "reply_footer": "\n\n🙏 អរគុណ <b>{name}</b> ដែលបានប្រើប្រាស់ Chat_Bot របស់យើង! បើមានសំណើរឬបញ្ហាផ្សេងទៀត សូមទាក់ទងមកក្រុមការងារយើងវិញ។",
    "broadcast_header": "📢 <b>សេចក្តីជូនដំណឹងផ្លូវការ</b>",
    "admin_help_text": (
        "🛠 <b>មជ្ឈមណ្ឌលបញ្ជា</b>\n"
        "───────────────\n"
        "• <code>/broadcast [msg]</code> : ផ្ញើសារជូនដំណឹងទៅកាន់អ្នកទាំងអស់គ្នា\n"
        "• <code>/help</code> : បង្ហាញបញ្ជីនេះម្តងទៀត\n\n"
        "ℹ️ <i>ទិន្នន័យចាស់ៗនឹងត្រូវលុបចោលដោយស្វ័យប្រវត្តិដើម្បីសន្សំទំហំផ្ទុក។</i>"
    ),
    "menu_main_text": (
        "សួស្តី, <b>{name}</b>! 👋\n"
        "សូមស្វាគមន៍មកកាន់ប្រព័ន្ធដោះស្រាយបញ្ហា។\n\n"
        "🆔 លេខសម្គាល់របស់អ្នក: <code>{display_id}</code>\n\n"
        "យើងខ្ញុំត្រៀមខ្លួនជាស្រេចដើម្បីជួយដោះស្រាយនិងសម្រួលបញ្ហារបស់លោកអ្នក។\n"
        "សូមចុចប៊ូតុងខាងក្រោម👇"
    ),
    "menu_btn_support": "💬 សុំជំនួយពីក្រុមការងារ IT_Support",
    "contact_intro": (
        "💬 <b>ដោះស្រាយបញ្ហាផ្សេងៗតាម Chat_Bot</b>\n"
        "───────────────\n"
        "📝 តើអ្នកមានបញ្ហាអ្វី​? តើអ្នកមានអ្វីឳ្យជួយដោះស្រាយ?\n"
    ),
    "session_cleared": "♻️ <b>ការសន្ទនាត្រូវបានបិទបញ្ចប់។</b>",
}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------
# 🧠 ASYNC DATABASE HELPERS (NON-BLOCKING)
# --------------------------------------------------------------------------------
async def get_or_create_user(user):
    def _ops():
        row = db.execute_read_one("SELECT display_id FROM users WHERE user_id=?", (user.id,))
        if row and row[0]:
            display_id = row[0]
            db.execute_write("UPDATE users SET first_name=?, username=? WHERE user_id=?", (user.first_name, user.username, user.id))
        else:
            count = db.execute_read_one("SELECT COUNT(*) FROM users")[0]
            display_id = f"DI-{count + 1:03d}"
            db.execute_write("INSERT OR REPLACE INTO users (user_id, first_name, username, display_id, joined_at) VALUES (?, ?, ?, ?, ?)",
                             (user.id, user.first_name, user.username, display_id, datetime.now()))
        return display_id
    return await asyncio.to_thread(_ops)

async def get_all_users_details():
    return await asyncio.to_thread(db.execute_read_all, "SELECT user_id FROM users")

async def save_message(admin_msg_id, user_id, user_name, display_id, question):
    await asyncio.to_thread(db.execute_write, 
        "INSERT INTO message_map (admin_message_id, user_id, user_name, display_id, question_text, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (admin_msg_id, user_id, user_name, display_id, question, datetime.now(), 'PENDING'))

async def update_message_answer(admin_msg_id, answer, admin_name):
    await asyncio.to_thread(db.execute_write,
        "UPDATE message_map SET status='SOLVED', answer_text=?, admin_responder=? WHERE admin_message_id=?",
        (answer, admin_name, admin_msg_id))

async def get_message_context(admin_msg_id):
    return await asyncio.to_thread(db.execute_read_one,
        "SELECT user_id, user_name, display_id FROM message_map WHERE admin_message_id=?", (admin_msg_id,))

async def save_reply_tracking(admin_msg_id, user_chat_id, sent_msg_id, admin_name, user_name):
    await asyncio.to_thread(db.execute_write,
        "INSERT OR REPLACE INTO reply_tracking (admin_msg_id, user_chat_id, sent_msg_id, admin_name, user_name) VALUES (?, ?, ?, ?, ?)",
        (admin_msg_id, user_chat_id, sent_msg_id, admin_name, user_name))

async def get_reply_tracking(admin_msg_id):
    return await asyncio.to_thread(db.execute_read_one,
        "SELECT user_chat_id, sent_msg_id, admin_name, user_name FROM reply_tracking WHERE admin_msg_id=?", (admin_msg_id,))

# --------------------------------------------------------------------------------
# ⚡ HANDLERS
# --------------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    display_id = await get_or_create_user(user)

    keyboard = [[InlineKeyboardButton(LANG["menu_btn_support"], callback_data="btn_support")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_html(
        f"{LANG['brand_header']}\n\n" + 
        LANG['menu_main_text'].format(name=user.first_name, display_id=display_id),
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "btn_support":
        await query.message.reply_html(LANG["contact_intro"])

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id == ADMIN_GROUP_ID: return
    if update.message.text and update.message.text.upper() == "CLEAR":
        await update.message.reply_html(LANG["session_cleared"])
        return

    user = update.effective_user
    display_id = await get_or_create_user(user)
    question_content = update.message.text or "[Media/File]"
    
    # Send typing action to show user the bot is working
    await context.bot.send_chat_action(chat_id=ADMIN_GROUP_ID, action=constants.ChatAction.TYPING)

    admin_text = f"👤 <b>ឈ្មោះ:</b> {user.full_name}\n───────────────\n"

    sent_msg = None
    try:
        if update.message.text:
            admin_text += f"💬 <b>សំណួរ :</b> {update.message.text}"
            sent_msg = await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=admin_text, parse_mode=ParseMode.HTML)
        elif update.message.photo:
            admin_text += f"🖼 <b>រូបភាព</b>\n{update.message.caption or ''}"
            sent_msg = await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=update.message.photo[-1].file_id, caption=admin_text, parse_mode=ParseMode.HTML)
        elif update.message.document:
            admin_text += f"📂 <b>ឯកសារ</b>\n{update.message.caption or ''}"
            sent_msg = await context.bot.send_document(chat_id=ADMIN_GROUP_ID, document=update.message.document.file_id, caption=admin_text, parse_mode=ParseMode.HTML)
        elif update.message.video:
            admin_text += f"🎥 <b>វីដេអូ</b>\n{update.message.caption or ''}"
            sent_msg = await context.bot.send_video(chat_id=ADMIN_GROUP_ID, video=update.message.video.file_id, caption=admin_text, parse_mode=ParseMode.HTML)
        elif update.message.voice:
            admin_text += "🎤 <b>សំឡេង</b>"
            sent_msg = await context.bot.send_voice(chat_id=ADMIN_GROUP_ID, voice=update.message.voice.file_id, caption=admin_text, parse_mode=ParseMode.HTML)

        if sent_msg:
            # Async Save
            await save_message(sent_msg.message_id, user.id, user.full_name, display_id, question_content)
            try: await update.message.set_reaction(reaction=[ReactionTypeEmoji("❤")])
            except: pass
            
    except Exception as e:
        logger.error(f"Relay Error: {e}")

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID or not update.message.reply_to_message: return 

    mapping = await get_message_context(update.message.reply_to_message.message_id)
    
    if mapping:
        user_id, user_name, display_id = mapping
        admin_name = update.effective_user.full_name or "Support Agent"
        
        sent_user_msg = None
        try:
            header = f"{LANG['reply_header']}\n───────────────\n"
            footer = LANG["reply_footer"].format(name=user_name)
            admin_label = f"<b>ឆ្លើយតប :</b> "

            if update.message.text:
                full_text = f"{header}{admin_label}{update.message.text}{footer}"
                sent_user_msg = await context.bot.send_message(chat_id=user_id, text=full_text, parse_mode=ParseMode.HTML)
            elif update.message.photo:
                caption = f"{header}{admin_label}{update.message.caption or ''}{footer}"
                sent_user_msg = await context.bot.send_photo(chat_id=user_id, photo=update.message.photo[-1].file_id, caption=caption, parse_mode=ParseMode.HTML)
            elif update.message.document:
                caption = f"{header}{admin_label}{update.message.caption or ''}{footer}"
                sent_user_msg = await context.bot.send_document(chat_id=user_id, document=update.message.document.file_id, caption=caption, parse_mode=ParseMode.HTML)
            elif update.message.video:
                caption = f"{header}{admin_label}{update.message.caption or ''}{footer}"
                sent_user_msg = await context.bot.send_video(chat_id=user_id, video=update.message.video.file_id, caption=caption, parse_mode=ParseMode.HTML)
            elif update.message.voice:
                caption = f"{header}{admin_label}(Voice Message){footer}"
                sent_user_msg = await context.bot.send_voice(chat_id=user_id, voice=update.message.voice.file_id, caption=caption, parse_mode=ParseMode.HTML)

            # DB Operations
            await update_message_answer(update.message.reply_to_message.message_id, update.message.text or "[Media]", admin_name)
            
            try: await update.message.set_reaction(reaction=[ReactionTypeEmoji("❤")])
            except: pass
            
            if sent_user_msg:
                await save_reply_tracking(update.message.message_id, user_id, sent_user_msg.message_id, admin_name, user_name)

        except Exception as e:
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"❌ Failed: {e}")
    else:
        if not update.message.text.startswith("/"):
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text="⚠️ Ticket context lost.")

async def handle_admin_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    edited_msg = update.edited_message
    if not edited_msg: return

    tracking = await get_reply_tracking(edited_msg.message_id)
    if tracking:
        user_chat_id, sent_msg_id, admin_name, user_name = tracking
        try:
            header = f"{LANG['reply_header']}\n───────────────\n"
            footer = LANG["reply_footer"].format(name=user_name)
            admin_label = f"<b>ឆ្លើយតប :</b> "
            
            if edited_msg.text:
                await context.bot.edit_message_text(chat_id=user_chat_id, message_id=sent_msg_id, text=f"{header}{admin_label}{edited_msg.text}{footer}", parse_mode=ParseMode.HTML)
            elif edited_msg.caption:
                await context.bot.edit_message_caption(chat_id=user_chat_id, message_id=sent_msg_id, caption=f"{header}{admin_label}{edited_msg.caption}{footer}", parse_mode=ParseMode.HTML)
        except Exception:
            pass

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    msg = " ".join(context.args)
    if not msg: 
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /broadcast [Message]")
        return
    
    users = await get_all_users_details()
    count = 0
    formatted = f"{LANG['broadcast_header']}\n───────────────\n{msg}"
    
    status = await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"⏳ Sending to {len(users)} users...")
    for (uid,) in users:
        try:
            await context.bot.send_message(chat_id=uid, text=formatted, parse_mode=ParseMode.HTML)
            count += 1
        except: pass
    await context.bot.edit_message_text(chat_id=ADMIN_GROUP_ID, message_id=status.message_id, text=f"✅ Sent to {count} users.")

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    await update.message.reply_html(LANG["admin_help_text"])

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Start Menu"),
        BotCommand("help", "Help"),
        BotCommand("clear", "End Chat")
    ])

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# --------------------------------------------------------------------------------
# 🚀 MAIN
# --------------------------------------------------------------------------------
def main() -> None:
    threading.Thread(target=start_web_server, daemon=True).start()
    threading.Thread(target=auto_cleanup_task, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("help", admin_help_command))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND & (filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.VOICE), handle_user_message))
    application.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_GROUP_ID) & filters.REPLY & ~filters.UpdateType.EDITED_MESSAGE, handle_admin_reply))
    application.add_handler(MessageHandler(filters.Chat(chat_id=ADMIN_GROUP_ID) & filters.UpdateType.EDITED_MESSAGE, handle_admin_edit))

    application.add_error_handler(error_handler)

    print("🚀 Enterprise Infinity Bot v18 (Turbo + WAL Mode) is ONLINE...")
    application.run_polling()

if __name__ == "__main__":
    main()
