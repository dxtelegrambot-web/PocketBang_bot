import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# 导入你刚才修改好的 handlers
from bot.handlers import BotHandlers

# 模拟数据库（由于你没有现成数据库，这里先用内存模拟，重启会清零）
# 如果需要永久保存，后期需要对接数据库文件
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

# 加载配置
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# 初始化
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
db = SimpleDB()
bot_handlers = BotHandlers(db)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 扫雷机器人已上线！\n\n指令说明：\n1. 查询余额：发送 [查询]\n2. 发红包：发送 [金额/雷号] 或 [金额/包数/雷号]\n3. 管理员上分：回复用户发送 [+数字]")

def main():
    if not TOKEN:
        print("❌ 错误：请在 .env 文件中配置 TELEGRAM_BOT_TOKEN")
        return

    # 创建机器人应用
    application = Application.builder().token(TOKEN).build()

    # 注册指令处理器
    application.add_handler(CommandHandler("start", start))
    
    # 注册文本消息处理器（处理发红包、查询、上分）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handlers.handle_message))
    
    # 注册按钮点击处理器（处理抢红包）
    application.add_handler(CallbackQueryHandler(bot_handlers.handle_callback))

    # 启动机器人
    print("🚀 机器人启动中...")
    application.run_polling()

if __name__ == '__main__':
    main()
