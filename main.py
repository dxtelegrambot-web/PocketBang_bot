import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.handlers import (
    handle_message, handle_callback, start_command, 
    init_db, scheduler, OWNER_ID
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

async def post_init(application):
    init_db()
    scheduler.start()
    logging.info("🚀 扫雷机器人已就绪，震动模式开启！")

if __name__ == "__main__":
    TOKEN = "YOUR_BOT_TOKEN_HERE"
    
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    # 基础指令
    app.add_handler(CommandHandler("start", start_command))
    # 抢红包按钮点击
    app.add_handler(CallbackQueryHandler(handle_callback))
    # 所有群内文本
    app.add_handler(MessageHandler(
        filters.CHAT & filters.TEXT & (~filters.COMMAND), 
        handle_message
    ))

    app.run_polling(allowed_updates=Update.ALL_TYPES)
