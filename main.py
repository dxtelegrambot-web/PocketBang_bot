import os, logging, sqlite3, pytz
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from bot.handlers import BotHandlers

# --- 数据库持久化层 (修复时区与数据表) ---
class SQLiteDB:
    def __init__(self, db_path="bot_data.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS users (uid TEXT PRIMARY KEY, balance REAL DEFAULT 0)")
            conn.execute("CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, action TEXT, amount REAL, is_mine INTEGER, chat_id TEXT, timestamp TEXT)")
            conn.commit()

    def get_bj_time(self):
        return datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')

    def get_balance(self, uid):
        with sqlite3.connect(self.db_path) as conn:
            res = conn.execute("SELECT balance FROM users WHERE uid=?", (str(uid),)).fetchone()
            return res[0] if res else 0.0

    def add_balance(self, uid, amount):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO users (uid, balance) VALUES (?, ?) ON CONFLICT(uid) DO UPDATE SET balance = balance + ?", (str(uid), amount, amount))
            conn.commit()

    def log_action(self, uid, action, amount, is_mine, chat_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO logs (uid, action, amount, is_mine, chat_id, timestamp) VALUES (?,?,?,?,?,?)", 
                         (str(uid), action, amount, 1 if is_mine else 0, str(chat_id), self.get_bj_time()))
            conn.commit()

    def get_user_logs(self, uid, limit=20):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT timestamp, action, amount, is_mine FROM logs WHERE uid=? ORDER BY id DESC LIMIT ?", (str(uid), limit)).fetchall()

    def get_group_stats(self, chat_id):
        with sqlite3.connect(self.db_path) as conn:
            total_send = conn.execute("SELECT SUM(amount) FROM logs WHERE action='发包' AND chat_id=?", (str(chat_id),)).fetchone()[0] or 0
            total_mine = conn.execute("SELECT COUNT(*) FROM logs WHERE action='抢包中雷' AND chat_id=?", (str(chat_id),)).fetchone()[0] or 0
            return total_send, total_mine

    def get_all_balances(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT uid, balance FROM users ORDER BY balance DESC").fetchall()

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
logging.basicConfig(level=logging.INFO)

db = SQLiteDB()
bot_handlers = BotHandlers(db)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("🤖 系统就绪。")))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), bot_handlers.handle_message))
    app.add_handler(CallbackQueryHandler(bot_handlers.handle_callback))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
