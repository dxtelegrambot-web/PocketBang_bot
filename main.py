#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TON 红包 Bot 主程序

功能:
- 发送和领取红包
- TON充值和提现
- 余额管理
- 交易记录
"""

import os
import sys
import asyncio
import logging
from datetime import datetime



from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters
)
from telegram import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats


from database.models import Database
from bot.handlers import BotHandlers, SEND_AMOUNT, SEND_COUNT, SEND_MESSAGE, SEND_TYPE, WITHDRAW_AMOUNT, WITHDRAW_ADDRESS
from ton.client import get_ton_client

# 加载环境变量
load_dotenv()


class PytoniqNetworkFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        # 过滤掉所有包含这些关键词的pytoniq日志
        filtered_keywords = [
            'getAllShardsInfo',
            'requesting getAllShardsInfo',
            'LiteClient - INFO',
            'requesting getMasterchainInfo',
            'requesting getBlockHeader',
            'getUpdates'
        ]
        
        for keyword in filtered_keywords:
            if keyword in message:
                return False
        return True

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 创建并应用过滤器
network_filter = PytoniqNetworkFilter()

# 应用过滤器到所有可能的pytoniq相关logger
for logger_name in ['tonutils','telegram', 'telegram.ext']:
    target_logger = logging.getLogger(logger_name)
    target_logger.addFilter(network_filter)
    target_logger.setLevel(logging.WARNING)

# 也应用到根logger以防万一
logging.getLogger().addFilter(network_filter)

# 设置不同组件的日志级别
logging.getLogger('pytoniq.liteclient').setLevel(logging.ERROR)  # 只显示错误
logging.getLogger('pytoniq.litebalancer').setLevel(logging.WARNING)  # 显示警告和错误
logging.getLogger('pytoniq').setLevel(logging.WARNING)  # 其他pytoniq组件显示警告和错误

# 保持你的应用日志为INFO级别
logging.getLogger('__main__').setLevel(logging.INFO)
logging.getLogger('bot').setLevel(logging.INFO)
logging.getLogger('database').setLevel(logging.INFO)
logging.getLogger('ton').setLevel(logging.INFO)


class LuckyPackBot:
    """TON红包Bot主类"""
    
    def __init__(self):
        # 获取配置
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not self.bot_token:
            raise ValueError("请在.env文件中设置TELEGRAM_BOT_TOKEN")
        
        # 初始化数据库
        self.database = Database()
        
        # 初始化处理器
        self.handlers = BotHandlers(self.database)
        
        # TON客户端将在异步初始化中设置
        self.ton_client = None
        
        # 创建应用
        self.application = Application.builder().token(self.bot_token).read_timeout(30).write_timeout(30).build()
        
        # 注册处理器
        self._register_handlers()
        
        logger.info("LuckyPack Bot 初始化完成")
    
    def _register_handlers(self):
        """注册消息处理器"""
        
        # 基础命令处理器
        self.application.add_handler(CommandHandler("start", self.handlers.start_command, filters=filters.ChatType.PRIVATE))
        self.application.add_handler(CommandHandler("help", self.handlers.help_command))
        self.application.add_handler(CommandHandler("balance", self.handlers.balance_command,filters=filters.ChatType.PRIVATE))
        self.application.add_handler(CommandHandler("deposit", self.handlers.deposit_command,filters=filters.ChatType.PRIVATE))
        self.application.add_handler(CommandHandler("history", self.handlers.history_command,filters=filters.ChatType.PRIVATE))
        self.application.add_handler(CommandHandler("collect", self.handlers.collect_command,filters=filters.ChatType.PRIVATE))
        
        # 发红包命令 - 改为单步操作
        self.application.add_handler(CommandHandler("send", self.handlers.send_command))
        
        # 提现对话处理器
        withdraw_conversation = ConversationHandler(
            entry_points=[CommandHandler("withdraw", self.handlers.withdraw_command, filters=filters.ChatType.PRIVATE)],
            states={
                WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.withdraw_amount_handler)],
                WITHDRAW_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.withdraw_address_handler)],
            },
            fallbacks=[
                CommandHandler("cancel", self.handlers.cancel_command),
                MessageHandler(filters.COMMAND, self.handlers.cancel_command)
            ],
            per_chat=True,
            per_user=True
        )
        self.application.add_handler(withdraw_conversation)
        
        # 回调查询处理器，处理内联键盘按钮的回调查询，创建内联键盘时 ，你会设置 callback_data，用于唯一标识每个按钮。
        self.application.add_handler(CallbackQueryHandler(self.handlers.handle_quick_actions, pattern=r'^quick_'))
        self.application.add_handler(CallbackQueryHandler(self.handlers.claim_red_packet, pattern=r'^claim_'))
        self.application.add_handler(CallbackQueryHandler(self.handlers.confirm_withdraw, pattern=r'^confirm_withdraw_'))
        self.application.add_handler(CallbackQueryHandler(self.handlers.cancel_withdraw, pattern=r'^cancel_withdraw'))
        self.application.add_handler(CallbackQueryHandler(self.handlers.refresh_balance, pattern=r'^refresh_balance_'))
        self.application.add_handler(CallbackQueryHandler(self.handlers.handle_red_packet_confirm_send, pattern=r'^confirm_send_'))
        self.application.add_handler(CallbackQueryHandler(self.handlers.handle_red_packet_cancel_send, pattern=r'^cancel_send_'))
        
        # 错误处理器
        self.application.add_error_handler(self._error_handler)
        
        logger.info("消息处理器注册完成")

    async def _setup_bot_commands(self, application: Application):
        """设置Bot命令菜单"""
        # 私聊命令
        private_commands = [
            BotCommand("help", "查看帮助信息"),
            BotCommand("balance", "查看余额"),
            BotCommand("deposit", "充值TON"),
            BotCommand("history", "查看交易记录"),
            BotCommand("withdraw", "提现TON"),
        ]
        
        # 群聊命令
        group_commands = [
            BotCommand("help", "查看帮助信息"),
            BotCommand("send", "发红包"),
        ]
        
        try:
            # 设置私聊命令菜单
            await application.bot.set_my_commands(
                commands=private_commands,
                scope=BotCommandScopeAllPrivateChats()
            )
            
            # 设置群聊命令菜单
            await application.bot.set_my_commands(
                commands=group_commands,
                scope=BotCommandScopeAllGroupChats()
            )
            
            logger.info("命令菜单设置完成")
        except Exception as e:
            logger.error(f"设置命令菜单失败: {e}")
    
    async def _error_handler(self, update: Update, context):
        """错误处理器"""
        # 获取错误发生的文件名和行号
        exc_info = sys.exc_info()
        if exc_info[2]:
            file_name = exc_info[2].tb_frame.f_code.co_filename
            line_number = exc_info[2].tb_lineno
            logger.error(f"错误位置: {file_name}:{line_number}")
        
        # 记录详细错误信息
        logger.error(f"更新 {update} 引发错误: {context.error}", exc_info=True)
        
        # 向用户发送错误消息
        if update and update.effective_message:
            try:
                error_message = (
                    "❌ 系统出现错误，请稍后重试！\n\n"
                    f"错误类型: {type(context.error).__name__}\n"
                    "如果问题持续存在，请联系管理员。"
                )
                await update.effective_message.reply_text(error_message)
            except Exception as e:
                logger.error(f"发送错误消息失败: {e}", exc_info=True)
    
    async def _post_init(self, application: Application):
        """应用初始化后的回调"""
        logger.info("Bot 启动完成")
        
        # 设置命令菜单
        await self._setup_bot_commands(application)
        
        # 异步初始化TON客户端
        try:
            self.ton_client = await get_ton_client()
        except Exception as e:
            logger.error(f"TON客户端初始化失败: {e}")
            return
        
        # 获取Bot信息
        bot_info = await application.bot.get_me()
        logger.info(f"Bot用户名: @{bot_info.username}")
        logger.info(f"Bot名称: {bot_info.full_name}")
        
        # 检查TON客户端状态
        try:
            if self.ton_client:
                balance = await self.ton_client.get_bot_balance()
                logger.info(f"Bot钱包余额: {balance} TON")
        except Exception as e:
            logger.warning(f"获取Bot钱包余额失败: {e}")
        
        # 启动充值监控后台任务
        if self.ton_client:
            self.deposit_monitor_task = asyncio.create_task(
                self._run_deposit_monitor()
            )
            logger.info("充值监控任务已启动")
        # 启动收集红包监控后台任务
            self.collect_monitor_task = asyncio.create_task(
                self._run_collect_monitor()
            )
            logger.info("收集红包监控任务已启动")
        # 启动发消息的后台任务 1s发一条
            self.send_message_task = asyncio.create_task(
                self._run_send_message()
            )
            logger.info("发消息任务已启动")
    
    async def _run_deposit_monitor(self):
        """运行充值监控的后台任务"""
        while True:
            try:
                await self.handlers.monitor_user_deposits()
                # 每30秒检查一次
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"充值监控出错: {e}")
                # 出错后等待60秒再重试
                await asyncio.sleep(60)
    async def _run_collect_monitor(self):
        """运行收集红包的后台任务"""
        while True:
            try:
                # 每1小时检查一次
                await asyncio.sleep(3600*24)
                await self.handlers.handle_collect()
            except Exception as e:
                logger.error(f"收集红包监控出错: {e}")
                await asyncio.sleep(3600)
    async def _run_send_message(self):
        """运行发消息的后台任务"""
        while True:
            try:
                # 每1秒发一条消息
                await asyncio.sleep(1)
                await self.handlers.safe_edit_message_text_send()
            except Exception as e:
                logger.error(f"发消息监控出错: {e}")
                await asyncio.sleep(1)
    
    async def _post_shutdown(self, application):
        """应用关闭前的回调"""
        logger.info("Bot 正在关闭...")
        
        # 取消充值监控任务
        if self.deposit_monitor_task:
            self.deposit_monitor_task.cancel()
            try:
                await self.deposit_monitor_task
            except asyncio.CancelledError:
                logger.info("充值监控任务已取消")
        # 取消收集红包监控任务
        if self.collect_monitor_task:
            self.collect_monitor_task.cancel()
            try:
                await self.collect_monitor_task
            except asyncio.CancelledError:
                logger.info("收集红包监控任务已取消")
        # 取消发消息任务
        if self.send_message_task:
            self.send_message_task.cancel()
            try:
                await self.send_message_task
            except asyncio.CancelledError:
                logger.info("发消息任务已取消")
        
        # 关闭TON客户端
        try:
            if self.ton_client:
                await self.ton_client.close()
        except Exception as e:
            logger.error(f"关闭TON客户端失败: {e}")
        
        logger.info("Bot 已关闭")
    
    def run(self):
        """运行Bot"""
        logger.info("启动 LuckyPack Bot...")
        
        # 设置回调
        self.application.post_init = self._post_init
        self.application.post_shutdown = self._post_shutdown
        
        # 运行Bot
        self.application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )


def main():
    """主函数"""
    try:
        # 检查必要的环境变量
        required_env_vars = ['TELEGRAM_BOT_TOKEN']
        missing_vars = [var for var in required_env_vars if not os.getenv(var)]
        
        if missing_vars:
            print(f"❌ 缺少必要的环境变量: {', '.join(missing_vars)}")
            print("请复制 .env.example 为 .env 并填写相应配置")
            sys.exit(1)
        
        # 创建并运行Bot
        bot = LuckyPackBot()
        bot.run()
        
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭Bot...")
    except Exception as e:
        logger.error(f"Bot运行失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

