import os, logging, sqlite3, pytz
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from bot.handlers import BotHandlers

class SQLiteDB:
    def __init__(self, db_path="bot_data.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            # 用户表 (UID + 群ID 隔离余额)
            conn.execute("CREATE TABLE IF NOT EXISTS users (uid TEXT, chat_id TEXT, balance REAL DEFAULT 0, PRIMARY KEY (uid, chat_id))")
            # 流水表 (记录详细变动)
            conn.execute("CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, action TEXT, amount REAL, is_mine INTEGER, chat_id TEXT, timestamp TEXT)")
            # 群设置表 (自定义金额/包数范围)
            conn.execute("CREATE TABLE IF NOT EXISTS settings (chat_id TEXT PRIMARY KEY, min_amt REAL, max_amt REAL, min_cnt INTEGER, max_cnt INTEGER)")
            conn.commit()

    def get_balance(self, uid, chat_id):
        with sqlite3.connect(self.db_path) as conn:
            res = conn.execute("SELECT balance FROM users WHERE uid=? AND chat_id=?", (str(uid), str(chat_id))).fetchone()
            return res[0] if res else 0.0

    def add_balance(self, uid, chat_id, amount):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO users (uid, chat_id, balance) VALUES (?, ?, ?) ON CONFLICT(uid, chat_id) DO UPDATE SET balance = balance + ?", (str(uid), str(chat_id), amount, amount))
            conn.commit()

    def log_action(self, uid, action, amount, is_mine, chat_id):
        bj_time = datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO logs (uid, action, amount, is_mine, chat_id, timestamp) VALUES (?,?,?,?,?,?)", (str(uid), action, amount, is_mine, str(chat_id), bj_time))
            conn.commit()

    def set_config(self, chat_id, min_a, max_a, min_c, max_c):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO settings VALUES (?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET min_amt=?, max_amt=?, min_cnt=?, max_cnt=?", (str(chat_id), min_a, max_a, min_c, max_c, min_a, max_a, min_c, max_c))
            conn.commit()

    def get_config(self, chat_id):
        with sqlite3.connect(self.db_path) as conn:
            res = conn.execute("SELECT min_amt, max_amt, min_cnt, max_cnt FROM settings WHERE chat_id=?", (str(chat_id),)).fetchone()
            return res if res else (20, 1000, 1, 10)

    def get_user_logs(self, uid, chat_id, limit=100):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT timestamp, action, amount, is_mine FROM logs WHERE uid=? AND chat_id=? ORDER BY id DESC LIMIT ?", (str(uid), str(chat_id), limit)).fetchall()

    def get_group_stats(self, chat_id):
        with sqlite3.connect(self.db_path) as conn:
            ts = conn.execute("SELECT SUM(ABS(amount)) FROM logs WHERE action='发包' AND chat_id=?", (str(chat_id),)).fetchone()[0] or 0
            tm = conn.execute("SELECT COUNT(*) FROM logs WHERE action='中雷' AND chat_id=?", (str(chat_id),)).fetchone()[0] or 0
            return ts, tm

    def get_all_balances(self, chat_id):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT uid, balance FROM users WHERE chat_id=? ORDER BY balance DESC", (str(chat_id),)).fetchall()

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
logging.basicConfig(level=logging.INFO)
db = SQLiteDB()
bot_handlers = BotHandlers(db)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", bot_handlers.handle_start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), bot_handlers.handle_message))
    app.add_handler(CallbackQueryHandler(bot_handlers.handle_callback))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
