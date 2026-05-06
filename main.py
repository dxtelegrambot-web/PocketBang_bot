import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from bot.handlers import BotHandlers

# 模拟数据库
class SimpleDB:
    def __init__(self):
        self.balances = {}
    def get_balance(self, user_id):
        return self.balances.get(str(user_id), 0.0)
    def add_balance(self, user_id, amount):
        uid = str(user_id)
        if uid not in self.balances:
            self.balances[uid] = 0.0
        self.balances[uid] = round(self.balances[uid] + amount, 2)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
db = SimpleDB()
bot_handlers = BotHandlers(db)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 机器人已就绪！\n\n- 上分：回复用户发送 +100\n- 发包：发送 金额/包数/雷号\n- 查询：发送 查询")

def main():
    if not TOKEN: return
    application = Application.builder().token(TOKEN).build()

    # 注册指令
    application.add_handler(CommandHandler("start", start))
    
    # 【核心修复】注册文本消息处理器，确保它能接收群组里的所有文字消息
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), bot_handlers.handle_message))
    
    # 注册按钮点击
    application.add_handler(CallbackQueryHandler(bot_handlers.handle_callback))

    print("🚀 机器人启动中...")
    application.run_polling(allowed_updates=Update.ALL_TYPES) # 确保接收所有更新

if __name__ == '__main__':
    main()
