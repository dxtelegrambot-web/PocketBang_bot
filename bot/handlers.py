import os
import uuid
import asyncio
from decimal import Decimal
from datetime import datetime, timedelta

from mysql.connector import dbapi
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup,CallbackQuery
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.models import Database, User, RedPacket, Transaction,RedPacketConfirmation
from utils.red_packet import generate_random_amounts, generate_equal_amounts, format_ton_amount
from utils.map_quene import QueueMap
from ton.client import get_ton_client
from ton.httpclient import get_http_client,HTTPTONTransaction
import traceback
import json
from telegram import CallbackQuery
from typing import Set


# 对话状态常量
SEND_AMOUNT, SEND_COUNT, SEND_MESSAGE, SEND_TYPE = range(4)
WITHDRAW_AMOUNT, WITHDRAW_ADDRESS = range(2)


class BotHandlers:
    """Bot处理器类"""
    
    def __init__(self, database: Database):
        self.db = database
        # 配置参数
        self.min_amount = Decimal(os.getenv('MIN_RED_PACKET_AMOUNT', '0.01'))
        self.max_amount = Decimal(os.getenv('MAX_RED_PACKET_AMOUNT', '1000'))
        self.min_count = int(os.getenv('MIN_PACKET_COUNT', '1'))
        self.max_count = int(os.getenv('MAX_PACKET_COUNT', '100'))
        self.expire_hours = int(os.getenv('RED_PACKET_EXPIRE_HOURS', '24'))
        self.queue_map = QueueMap(maxsize=200)
        self.cache_claim_red_packet:Set[int] = set() # 用于缓存领取红包的用户，避免重复领取,1s中清空一次
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /help 命令"""
        chat = update.effective_chat
        
        if chat.type in ['group', 'supergroup']:
            # 群组中的帮助信息，只介绍群组功能
            help_text = """🤖 TON 红包 Bot
🎁 发红包:
• `/send <金额> <数量> [类型] [祝福语]`
• 示例: `/send 1.5 5 random 新年快乐`

💡 更多功能请私聊Bot:
• 💰 查看余额
• 💳 充值 TON
• 💸 提现 TON  
• 📋 交易记录
            """
            # 获取bot信息并创建私聊按钮
            bot_info = await update.get_bot().get_me()
            bot_username = bot_info.username
            button = InlineKeyboardButton(
                text="点击查看余额／充值/提现",
                url=f"https://t.me/{bot_username}?start=from_group"
            )
            keyboard = [[button]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.safe_reply(update, help_text, reply_markup=reply_markup)
        else:
            # 私聊中的完整帮助信息
            help_text = """🤖 TON 红包 Bot 使用指南
    
📤 发红包:
1. 在群组中输入 /send
2. 按提示输入金额和数量
3. 群友点击红包按钮领取

💰 充值:
1. 输入 /deposit 获取充值地址
2. 向该地址转账 TON
3. 等待确认后余额自动更新

💸 提现:
1. 输入 /withdraw
2. 输入提现金额和地址
3. 确认后自动转账

📊 其他功能:
/balance - 查看余额
/history - 交易记录

⚠️ 注意事项:
• 红包24小时后过期
• 每人只能领取一次
• 提现需要支付网络手续费
• 请妥善保管钱包地址"""
            
            await self.safe_reply(update, help_text)
    
    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /balance 命令"""
        user = update.effective_user
        db_user = await self.get_or_create_user(user.id, user.username, user.full_name)
        
        balance_text = f"""💰 账户余额
    
👤 用户: {user.full_name}
💵 当前余额: {format_ton_amount(db_user.balance)}
📤 累计发出: {format_ton_amount(db_user.total_sent)}
📥 累计收到: {format_ton_amount(db_user.total_received)}

🔗 操作:
/deposit - 充值
/withdraw - 提现
/history - 交易记录"""
        
        await self.safe_reply(update, balance_text)
    
    async def deposit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /deposit 命令"""
        user = update.effective_user
        print(user)
        # 生成充值地址
        self.ton_client = await get_ton_client()
        db_user = await self.get_or_create_user(user.id, user.username, user.full_name)
        deposit_address = db_user.deposit_address
        
        deposit_text = f"""💳 充值 TON
    
📍 充值地址:
`{deposit_address}`

📋 充值说明:
1. 复制上方地址
2. 从您的钱包转账 TON 到此地址
3. 等待区块链确认 (通常1-2分钟)
4. 余额将自动更新

⚠️ 注意:
• 仅支持 TON 主网转账
• 最小充值金额: {format_ton_amount(self.min_amount)}
• 请勿向此地址转入其他代币"""
        
        keyboard = [[
            InlineKeyboardButton("🔄 刷新余额", callback_data=f"refresh_balance_{user.id}")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.safe_reply(
            update,
            deposit_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令"""
        user = update.effective_user
        chat = update.effective_chat
        
        # 确保只在私聊中处理
        if chat.type != 'private':
            return
        
        # 获取或创建用户
        db_user = await self.get_or_create_user(user.id, user.username, user.full_name)
        
        # 检查是否有启动参数
        args = context.args
        start_param = args[0] if args else None
        
        if start_param == 'from_group':
            # 从群组跳转过来的用户
            welcome_text = f"""👋 欢迎使用 TON 红包 Bot！
    
💰 当前余额: {format_ton_amount(db_user.balance)}

🔧 可用功能:
• /balance - 查看详细余额
• /deposit - 充值 TON
• /withdraw - 提现 TON
• /history - 交易记录
• /help - 查看帮助

💡 提示: 在群组中使用 /send 命令发红包"""
            # 创建快捷操作按钮
            keyboard = [
                [
                    InlineKeyboardButton("💰 查看余额", callback_data="quick_balance"),
                    InlineKeyboardButton("💳 充值", callback_data="quick_deposit")
                ],
                [
                    InlineKeyboardButton("📋 交易记录", callback_data="quick_history"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.safe_reply(update, welcome_text, reply_markup=reply_markup)
        else:
            # 普通启动
            welcome_text = f"""👋 欢迎使用 TON 红包 Bot！
    
💰 当前余额: {format_ton_amount(db_user.balance)}

🚀 快速开始:
1. 使用 /deposit 充值 TON
2. 在群组中使用 /send 发红包
3. 使用 /withdraw 提现
📖 输入 /help 查看详细使用说明"""
            await self.safe_reply(update, welcome_text)
    
    async def send_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /send 命令 - 单步发红包"""
        chat = update.effective_chat
        user = update.effective_user
        
        # 检查是否在群组中
        if chat.type not in ['group', 'supergroup']:
            await self.safe_reply(update, "❌ 红包只能在群组中发送！")
            return
        
        # 解析命令参数
        args = context.args
        if len(args) < 2:
            help_text = (
                "🎁 发红包使用方法：\n\n"
                "`/send <金额> <数量> [类型] [祝福语]`\n\n"
                "参数说明：\n"
                "• 金额：红包总金额 (TON)\n"
                "• 数量：红包个数\n"
                "• 类型：random(拼手气) 或 equal(等额)，默认 random\n"
                "• 祝福语：可选\n\n"
                "示例：\n"
                "• `/send 1.5 5` - 1.5 TON，5个拼手气红包\n"
                "• `/send 2.0 3 equal` - 2.0 TON，3个等额红包\n"
                "• `/send 1.0 10 random 新年快乐` - 带祝福语"
            )
            await self.safe_reply(update, help_text, parse_mode='Markdown')
            return
        
        try:
            # 解析参数
            amount = Decimal(args[0])
            count = int(args[1])
            red_packet_type = args[2] if len(args) > 2 and args[2] in ['random', 'equal'] else 'random'
            message = ' '.join(args[3:]) if len(args) > 3 else None
            
            # 如果第3个参数不是类型，则当作祝福语处理
            if len(args) > 2 and args[2] not in ['random', 'equal']:
                message = ' '.join(args[2:])
                red_packet_type = 'random'
            
            # 验证参数
            if amount < self.min_amount or amount > self.max_amount:
                await self.safe_reply(
                    update,
                    f"❌ 金额超出范围！请输入 {format_ton_amount(self.min_amount)} - {format_ton_amount(self.max_amount)} 之间的金额。"
                )
                return
            
            if count < self.min_count or count > self.max_count:
                await self.safe_reply(
                    update,
                    f"❌ 数量超出范围！请输入 {self.min_count} - {self.max_count} 之间的数量。"
                )
                return
            if message is not None and len(message) > 20:
                await self.safe_reply(
                    update,
                    "❌ 祝福语过长！请限制在20个字符以内。"
                )
                return
            # 检查用户余额
            db_user = await self.get_or_create_user(user.id, user.username, user.full_name)
            if db_user.balance < amount:
                await self.safe_reply(
                    update,
                    f"❌ 余额不足！\n\n💰 当前余额: {format_ton_amount(db_user.balance)}\n💸 需要金额: {format_ton_amount(amount)}\n\n请先使用 /deposit 充值。"
                )
                return
            
            # 显示确认弹窗
            await self._show_red_packet_confirmation(update, amount, count, red_packet_type, message, db_user.balance)
            
        except (ValueError, TypeError):
            traceback.print_exc()
            await self.safe_reply(update, "❌ 参数格式错误！请检查金额和数量是否为有效数字。")

    async def _show_red_packet_confirmation(self, update: Update, amount: Decimal, count: int, red_packet_type: str, message: str = None, user_balance: Decimal = None):
        """显示红包发送确认弹窗"""
        user = update.effective_user
        chat = update.effective_chat
        
        # 红包类型显示
        type_name = "拼手气红包" if red_packet_type == 'random' else "等额红包"
        type_emoji = "🎲" if red_packet_type == 'random' else "💰"
        # 构建确认信息
        confirm_text = f"""🎁 红包发送确认
    
{type_emoji} 红包类型: {type_name}
💰 总金额: {format_ton_amount(amount)}
🔢 红包数量: {count} 个"""
        if message:
            confirm_text += f"\n💬 祝福语: {message}"
        
        confirm_text += f"\n⚠️ 请仔细核对信息，确认无误后点击发送！"
        
        # 创建确认按钮 - 在callback_data数据入库
        red_packet_data = RedPacketConfirmation(
                            sender_id=user.id,
                            amount=amount,
                            count=count,
                            type=red_packet_type,
                            message=message,
                            group_id=chat.id, 
                            created_at=datetime.now(),
                            )
        red_packet_data.id = self.db.create_red_packet_confirmation(red_packet_data)
        callback_data = f"confirm_send_{red_packet_data.id}"
        # 取消按钮也包含红包ID，便于取消操作
        cancel_callback_data = f"cancel_send_{red_packet_data.id}"
        
        keyboard = [
            [
                InlineKeyboardButton("✅ 确认发送", callback_data=callback_data),
                InlineKeyboardButton("❌ 取消", callback_data=cancel_callback_data)
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.safe_reply(
            update,
            confirm_text,
            reply_markup=reply_markup
        )

    async def handle_red_packet_cancel_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理红包发送取消回调"""
        query = update.callback_query
        user = query.from_user
        
        if query.data.startswith("cancel_send_"):
            confirm_id = int(query.data.replace("cancel_send_", ""))
            confirm_data = self.db.get_red_packet_confirmation(confirm_id)
            
            if not confirm_data:
                # 数据不存在时，直接删除消息
                try:
                    await query.message.delete()
                except Exception:
                    # await query.answer("❌ 红包发送已取消", show_alert=True)
                    pass
                return
                
            if confirm_data.group_id != query.message.chat.id:
                return
                
            if confirm_data.sender_id != user.id:
                # await query.answer("❌ 只有发起人可以取消红包", show_alert=True)
                return
            
            # 删除确认数据
            self.db.delete_red_packet_confirmation(confirm_id)
            
            # 删除确认消息而不是编辑
            try:
                await query.message.delete()
                # 发送一条新的取消消息，几秒后自动删除
                cancel_msg = await query.message.reply_text("❌ 红包发送已取消")
                # 3秒后删除取消消息
                context.job_queue.run_once(
                    lambda context: cancel_msg.delete(),
                    when=3
                )
            except Exception as e:
                # 如果删除失败，使用 answer 显示提示
                # await query.answer("❌ 红包发送已取消", show_alert=True)
                return
            return
        
    async def handle_red_packet_confirm_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = query.from_user
        if query.data.startswith("confirm_send_"):
            try:
                # 解析红包数据
                confirm_id = int(query.data.replace("confirm_send_", ""))
                confirm_data = self.db.get_red_packet_confirmation(confirm_id)
                if not confirm_data:
                    return
                # 红包已过期（超过24小时）
                if confirm_data.group_id != query.message.chat.id:
                    return
                if confirm_data.sender_id != user.id:
                    # await query.answer("❌ 只有发起人可以确认发送红包", show_alert=True)
                    return
                if datetime.now() > confirm_data.created_at + timedelta(hours=24):
                    self.db.delete_red_packet_confirmation(confirm_id)
                    await query.edit_message_text("❌ 红包确认数据不存在或已过期！")
                    return
                # 再次检查用户余额（防止确认期间余额变化）
                db_user = await self.get_or_create_user(user.id, user.username, user.full_name)
                if db_user.balance < confirm_data.amount:
                    await query.edit_message_text(
                        f"❌ 余额不足！\n\n💰 当前余额: {format_ton_amount(db_user.balance)}\n💸 需要金额: {format_ton_amount(confirm_data.amount)}\n\n请先使用 /deposit 充值。"
                    )
                    return
                
                # 直接创建红包并编辑消息
                await self._create_and_edit_red_packet_message(
                    update=update,
                    amount=confirm_data.amount,
                    count=confirm_data.count,
                    red_packet_type=confirm_data.type,
                    message=confirm_data.message
                )
                # 删除确认数据
                self.db.delete_red_packet_confirmation(confirm_id)
            except Exception as e:
                print(f"处理红包确认失败: {e}")
                traceback.print_exc()
                await self.safe_edit_message_text(query=query,text="❌ 红包发送失败，请重试！")

    async def _create_and_edit_red_packet_message(self, update: Update, amount: Decimal, count: int, red_packet_type: str, message: str = None):
        """创建红包并编辑消息为红包消息"""
        query = update.callback_query
        user = query.from_user
        chat = query.message.chat
        
        try:
            # 分配红包金额
            if red_packet_type == 'random':
                amounts = generate_random_amounts(amount, count)
            else:
                amounts = generate_equal_amounts(amount, count)
            
            # 创建红包对象（不设置ID，让数据库自增）
            red_packet = RedPacket(
                sender_id=user.id,
                group_id=chat.id,
                group_name=chat.title,
                total_amount=amount,
                packet_count=count,
                remaining_count=count,
                remaining_amount=amount,
                message=message,
                status='active',
                amounts=amounts,
                created_at=datetime.now(),
                expires_at=datetime.now() + timedelta(hours=self.expire_hours)
            )
            
            transaction = Transaction(
                telegram_id=red_packet.sender_id,
                lt=0,
                type='red_packet_send',
                amount=red_packet.total_amount,
                ton_tx_hash=None,
                status='confirmed',
                created_at=red_packet.created_at or datetime.now()
            )
            
            # 处理sql的事务，1:扣除用户余额，2:创建红包，3:创建交易记录
            red_packet_id =  self.db.create_red_packet_tran(transaction, red_packet, user.id, -amount)
            if not red_packet_id:
                await query.edit_message_text("❌ 红包创建失败，请重试！")
                return
            red_packet.id = red_packet_id
            
            # 红包类型显示
            type_emoji = "🎲" if red_packet.packet_count > 1 else "💰"
            type_name = "拼手气红包" if len(set(red_packet.amounts)) > 1 else "普通红包"
            
            # 红包消息文本
            red_packet_text = f"""{type_emoji} {user.full_name} 发了一个红包
    
💰 总金额: {format_ton_amount(red_packet.total_amount)}
🔢 数量: {red_packet.packet_count} 个
📝 类型: {type_name}"""

            if red_packet.message:
                red_packet_text += f"\n💬 祝福: {red_packet.message}"
            
            red_packet_text += f"\n\n⏰ {self.expire_hours}小时后过期"
            
            # 创建领取按钮
            keyboard = [[
                InlineKeyboardButton(
                    "🧧 点击领取红包",
                    callback_data=f"claim_{red_packet.id}"
                )
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # 编辑消息为红包消息
            await query.edit_message_text(
                red_packet_text,
                reply_markup=reply_markup
            )
            
        except Exception as e:
            print(f"创建红包失败: {e}")
            traceback.print_exc()
            await self.safe_edit_message_text(query=query,text="❌ 红包创建失败，请重试！")

    async def claim_red_packet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """红包领取"""
        query = update.callback_query
        user_id = query.from_user.id
        # 1s内只能领取一次红包，避免重复领取
        if user_id in self.cache_claim_red_packet:
            return
        self.cache_claim_red_packet.add(user_id)
        try:
            user = query.from_user
            red_packet_id = int(query.data.replace('claim_', ''))
            # 检查是否已领取
            if self.db.has_user_claimed(red_packet_id, user.id):
                print(f"用户 {user.id} 已经领取了红包 {red_packet_id}")
                return
            # 获取红包信息
            red_packet = self.db.get_red_packet(red_packet_id)
            if not red_packet:
                await self.safe_edit_message_text(query=query,text="❌ 红包不存在或已过期！")
                return
            
            # 检查红包状态
            if red_packet.status != 'active':
                await self.safe_edit_message_text(query=query,text="❌ 红包已过期或被抢完！")
                return
            # 检查是否过期
            if red_packet.expires_at and datetime.now() > red_packet.expires_at:
                await self.safe_edit_message_text(query=query,text="❌ 红包已过期！")
                return
            # 检查是否还有红包
            if red_packet.remaining_count <= 0:
                await self.safe_edit_message_text(query=query,text="❌ 红包已被抢完！")
                return
            # 确保用户存在于数据库中
            await self.get_or_create_user(user.id, user.username, user.full_name)
            # 计算领取金额 (取第一个可用金额)
            claim_amount = red_packet.amounts[red_packet.packet_count - red_packet.remaining_count]
            
            # 领取红包
            transaction = Transaction(
                    telegram_id=user.id,
                    lt=0,
                    red_packet_id=red_packet_id,
                    ton_tx_hash=None,
                    type='red_packet_receive',
                    amount=claim_amount,
                    status='confirmed',
                    created_at=datetime.now()
                )
            if self.db.claim_red_packet_tran(red_packet_id,transaction, user.id, claim_amount):
                updated_red_packet = self.db.get_red_packet(red_packet_id)
                # 发送领取成功消息
                success_text = ""
                claims = self.db.get_red_packet_claims(red_packet_id)
                claims_text = "\n\n📋 领取记录:\n"
                for i, claim in enumerate(claims, 1):
                    name = claim.display_name or claim.username or f"用户{claim.claimer_id}"
                    claims_text += f"{i}. {name}: {format_ton_amount(claim.amount)}\n"
                
                if updated_red_packet.remaining_count == 0:
                    # 红包被抢完
                    success_text += "\n\n🎊 红包已被抢完！" + claims_text
                    await self.safe_edit_message_text(query=query,text=success_text)
                else:
                    # 这里需要合并edit_message_text请求
                    success_text += f"\n\n剩余: {updated_red_packet.remaining_count} 个"+claims_text
                    # 更新红包消息,保留领取按钮
                    keyboard = [[
                        InlineKeyboardButton(
                            "🧧 点击领取红包",
                            callback_data=f"claim_{red_packet_id}"
                        )
                    ]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    # 把消息写入队列
                    await self.safe_edit_message_text_put(update=update, text=success_text, reply_markup=reply_markup)
                # 私聊通知领取者
                try:
                    await context.bot.send_message(
                        chat_id=user.id,
                        text=f"🎉 您成功领取了 {format_ton_amount(claim_amount)}！\n\n📍 来源群组: {red_packet.group_name}\n💰 当前余额: {format_ton_amount(self.db.get_user_balance(user.id))}\n\n💡 提示: 您可以返回群组查看更多红包"
                    )
                except Exception as e:
                    return  # 如果私聊失败，可能是用户设置了隐私或没有开启私聊，忽略错误
            else:
                return
        except Exception as e:
            print(f"领取红包失败: {e}")
            traceback.print_exc()
            return
        
    async def safe_edit_message_text_send(self):
        try:
            for id, queue in self.queue_map._map.items():
                if not isinstance(queue, asyncio.Queue):
                    continue
                print(f"group ID: {id}, queue size: {queue.qsize()}")
                last_request = None
                while not queue.empty():
                    last_request = await queue.get() #清空并且获取最后一个请求
                if not last_request:
                    return
                query = last_request.get('query')
                # print(f"编辑消息: {last_request['text']}")
                # 检查 query 是否为 CallbackQuery 对象
                if isinstance(query, CallbackQuery):
                    await query.edit_message_text(
                        last_request['text'],
                        reply_markup=last_request.get('reply_markup')
                    )
        except Exception as e:
            print(f"send编辑消息失败: {e}")
            return
        
    async def safe_edit_message_text_put(self, update: Update, text: str, reply_markup=None):
        """安全编辑消息文本"""
        try:
            group_id = update.effective_chat.id
            await self.queue_map.put_queue(group_id,{
                'text': text,
                'query': update.callback_query,
                'reply_markup': reply_markup
            })
        except Exception as e:
            print(f"safe_edit_message_text_put 编辑消息失败: {e}")

    async def withdraw_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /withdraw 命令"""
        user = update.effective_user
        db_user = self.db.get_user_by_telegram_id(user.id)
        balance = db_user.balance if db_user else Decimal('0')
        if balance <= Decimal('0'):
            # 使用安全的回复方法
            await self.safe_reply(update, "❌ 余额不足，无法提现！")
            return ConversationHandler.END
        
        min_withdraw = Decimal('0.01')  # 最小提现金额
        if balance < min_withdraw:
            await self.safe_reply(
                update,
                f"❌ 余额不足！\n\n💰 当前余额: {format_ton_amount(balance)}\n💸 最小提现: {format_ton_amount(min_withdraw)}"
            )
            return ConversationHandler.END
       
        
        await self.safe_reply(
            update,
            f"💸 提现 TON\n\n💰 可提现余额: {format_ton_amount(balance)}\n💸 最小提现: {format_ton_amount(min_withdraw)}\n\n请输入提现金额:"
        )
        return WITHDRAW_AMOUNT
    
    async def withdraw_amount_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理提现金额输入"""
        try:
            amount = Decimal(update.message.text.strip())
            user = update.effective_user
            balance = self.db.get_user_balance(user.id)
            
            min_withdraw = Decimal('0.01')
            if amount < min_withdraw:
                await self.safe_reply(
                    update,
                    f"❌ 提现金额过小！\n\n💸 最小提现: {format_ton_amount(min_withdraw)}"
                )
                return WITHDRAW_AMOUNT
            
            if amount > balance:
                await self.safe_reply(
                    update,
                    f"❌ 余额不足！\n\n💰 当前余额: {format_ton_amount(balance)}\n💸 提现金额: {format_ton_amount(amount)}"
                )
                return WITHDRAW_AMOUNT
            
            # 估算手续费
            self.ton_client = await get_ton_client()
            estimated_fee = await self.ton_client.estimate_fee("", amount)
            actual_amount = amount - estimated_fee
            
            if actual_amount <= Decimal('0'):
                await self.safe_reply(
                    update,
                    f"❌ 提现金额过小，无法支付手续费！\n\n💸 提现金额: {format_ton_amount(amount)}\n💰 预估手续费: {format_ton_amount(estimated_fee)}"
                )
                return WITHDRAW_AMOUNT
            
            context.user_data['withdraw_amount'] = amount
            context.user_data['withdraw_fee'] = estimated_fee
            
            await self.safe_reply(
                update,
                f"💸 提现金额: {format_ton_amount(amount)}\n💰 预估手续费: {format_ton_amount(estimated_fee)}\n📥 实际到账: {format_ton_amount(actual_amount)}\n\n请输入提现地址 (TON地址):"
            )
            
            return WITHDRAW_ADDRESS
            
        except (ValueError, TypeError):
            await self.safe_reply(update, "❌ 请输入有效的数字金额！")
            return WITHDRAW_AMOUNT
    
    async def withdraw_address_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理提现地址输入"""
        address = update.message.text.strip()
        
        # 验证地址格式
        if not self.ton_client.validate_address(address):
            await self.safe_reply(
                update,
                "❌ 无效的TON地址格式！\n\n💡 请输入正确的TON地址 (以EQ开头)"
            )
            return WITHDRAW_ADDRESS
        
        user = update.effective_user
        amount = context.user_data['withdraw_amount']
        fee = context.user_data['withdraw_fee']
        actual_amount = amount - fee
        
         # 判断bot_address余额是否充足
        bot_balance = await self.ton_client.get_bot_balance()
        if bot_balance < amount+ Decimal('0.005'):
            print(f"Bot余额不足，当前余额: {format_ton_amount(bot_balance)}, 需要至少: {format_ton_amount(amount + Decimal('0.005'))}")
            await self.safe_reply(
                update,
                f"❌ 服务升级中,请稍后再试！"
            )
            return ConversationHandler.END
        
        # 确认提现
        keyboard = [[
            InlineKeyboardButton("✅ 确认提现", callback_data=f"confirm_withdraw_{user.id}"),
            InlineKeyboardButton("❌ 取消", callback_data="cancel_withdraw")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        context.user_data['withdraw_address'] = address
        
        confirm_text = f"""💸 提现确认

📍 提现地址:
`{address}`

💰 提现金额: {format_ton_amount(amount)}
💸 网络手续费: {format_ton_amount(fee)}
📥 实际到账: {format_ton_amount(actual_amount)}

⚠️ 请仔细核对地址，转账后无法撤销！"""
        
        await self.safe_reply(
            update,
            confirm_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        
        return ConversationHandler.END

    async def confirm_withdraw(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """确认提现"""
        query = update.callback_query
        try:
            await query.answer()
            
            user = query.from_user
            amount = context.user_data.get('withdraw_amount')
            address = context.user_data.get('withdraw_address')
            fee = context.user_data['withdraw_fee']
            actual_amount = amount - fee
            
            if not amount or not address:
                await self.safe_edit_message_text(update=update,text="❌ 提现信息丢失，请重新操作！")
                return
            
            # 检查余额
            balance = self.db.get_user_balance(user.id)
            if amount > balance:
                await self.safe_edit_message_text(update=update,text="❌ 余额不足，提现失败！")
                return
        except Exception as e:
            return
        
        try:
            # 先扣钱，防止重放攻击
            self.db.update_user_balance(user.id, -amount)
            # 发送TON,要使用actual_amount，扣掉手续费
            tx_hash = await self.ton_client.withdraw_user_funds(address, actual_amount)
            if tx_hash:
                # 创建交易记录
                transaction = Transaction(
                    telegram_id=user.id,
                    lt=0,
                    type='withdraw',
                    amount=amount,
                    ton_tx_hash=tx_hash,
                    status='confirmed',
                    created_at=datetime.now()
                )
                # 确认提款 1:扣除余额 2:创建交易记录
                self.db.create_transaction(transaction)
                await self.safe_edit_message_text(
                    update=update,
                    text=f"✅ 提现申请已提交！\n\n💸 金额: {format_ton_amount(amount)}\n📍 地址: {self.ton_client.format_address(address)}\n🔗 交易哈希: `{tx_hash}`\n\n⏳ 请等待区块链确认...",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                # 回滚余额
                self.db.update_user_balance(user.id, amount)
                await self.safe_edit_message_text("❌ 提现失败，请稍后重试！")
        except Exception as e:
            # 回滚余额
            self.db.update_user_balance(user.id, amount)
            print(f"提现失败: {e}")
            await self.safe_edit_message_text("❌ 提现失败，请稍后重试！")
    
    async def cancel_withdraw(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """取消提现"""
        query = update.callback_query
        try:
            await query.answer()
            await self.safe_edit_message_text("❌ 提现已取消")
        except Exception as e:
            return
    
    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /history 命令"""
        user = update.effective_user
        transactions = self.db.get_user_transactions(user.id, 10)
        
        if not transactions:
            await self.safe_reply(update, "📋 暂无交易记录")
            return
        
        history_text = "📋 交易记录 (最近10条)\n\n"
        
        for tx in transactions:
            type_emoji = {
                'deposit': '📥',
                'withdraw': '📤',
                'red_packet_send': '🎁',
                'red_packet_receive': '🧧'
            }.get(tx.type, '💰')
            
            type_name = {
                'deposit': '充值',
                'withdraw': '提现',
                'red_packet_send': '发红包',
                'red_packet_receive': '领红包'
            }.get(tx.type, '未知')
            
            status_emoji = {
                'pending': '⏳',
                'confirmed': '✅',
                'failed': '❌'
            }.get(tx.status, '❓')
            
            history_text += f"{type_emoji} {type_name} {status_emoji}\n"
            history_text += f"💰 {format_ton_amount(tx.amount)}\n"
            history_text += f"📅 {tx.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        await self.safe_reply(update, history_text)
    
    async def collect_command(self,update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /collect 命令"""
        user = update.effective_user
        if user.id != 7531161199:
            return
        # 获取所有用户的充值地址，然后统一转账到bot_address
        try:
            ton_client = await get_ton_client()
            total_amount = await self.handle_collect()
            bot_balance = await ton_client.get_bot_balance()
            await self.safe_reply(
                update,
                f"✅ 收集完成！\n\n💰 Bot钱包当前余额: {format_ton_amount(bot_balance)}\n💰 总收集金额: {format_ton_amount(total_amount)}"
            )
        except Exception as e:
            print(f"收集用户充值失败: {e}")
            await self.safe_reply(update, "❌ 收集用户充值失败，请稍后重试！")
            traceback.print_exc()
            return
    async def handle_collect(self)->Decimal:
        try:
            ton_client = await get_ton_client()
            total_amount = Decimal('0')
            users = self.db.get_all_user_addresses()
            print(f"开始收集 {len(users)} 个用户的充值地址")
            for telegram_id, address, lt in users:
                if telegram_id is None or address is None:
                    continue
                balance = await ton_client.get_balance(address)
                if balance <= Decimal('0.01'):
                    continue
                print(f"用户 {telegram_id} 的充值地址 {address} 余额: {format_ton_amount(balance)}")
                transfer_amount = balance - Decimal('0.01')  # 保留0.01 TON
                user_wallet = await ton_client.get_user_wallet(telegram_id)
                # 检查地址是否部署
                if not await ton_client.check_account_active(address):
                    print(f"用户 {telegram_id} 的充值地址 {address} 未部署，尝试部署...")
                    await user_wallet.deploy()
                    continue
                tx_hash = await user_wallet.transfer(
                    destination=ton_client.bot_address,
                    amount=float(transfer_amount),
                    body = f"collect"
                )
                total_amount += transfer_amount
                print(f"用户 {telegram_id} 的充值地址 {address} 收集成功，转账 {format_ton_amount(transfer_amount)} TON, txhash:{tx_hash}")
            return total_amount
        except Exception as e:
            print(f"收集用户充值失败")
            traceback.print_exc()
           
        
    async def refresh_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """刷新余额"""
        try:
            query = update.callback_query
            await query.answer()
            
            user_id = int(query.data.replace('refresh_balance_', ''))
            if user_id != query.from_user.id:
                # await query.answer("❌ 只能刷新自己的余额！", show_alert=True)
                return
            
            balance = self.db.get_user_balance(user_id)
        except Exception as e:
            return
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """取消当前操作"""
        try:
            await self.safe_reply(update, "❌ 操作已取消")
            return ConversationHandler.END
        except Exception as e:
            return


    async def safe_reply(self, update: Update, text: str, **kwargs):
        """安全的消息回复方法"""
        try:
            if update.message:
                await update.message.reply_text(text, **kwargs)
            elif update.callback_query:
                await update.callback_query.message.reply_text(text, **kwargs)
            elif update.edited_message:
                await update.edited_message.reply_text(text, **kwargs)
            else:
                # 如果都没有，使用 bot.send_message
                chat_id = update.effective_chat.id if update.effective_chat else update.effective_user.id
                await update.get_bot().send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception as e:
            print(f"发送消息失败: {e}")

    async def safe_edit_message_text(self, query:CallbackQuery, text: str, **kwargs):
        """安全的编辑消息文本方法"""
        try:
            query.edit_message_text(text=text,**kwargs)
        except Exception as e:
            print(f"编辑消息失败: {e}")

    async def monitor_user_deposits(self):
        """监控所有用户地址的充值"""
        try:
            ton_client = await get_http_client()
            users = self.db.get_all_user_addresses()
            print(f"开始监控 {len(users)} 个用户的充值地址")
            for user_id, address,lt in users:
                if user_id is None or address is None:
                    continue
                if lt is None or lt == 0:
                    transactions = await ton_client.get_transactions(address, count=100)
                else:
                    transactions = await ton_client.get_transactions(address, from_lt=lt+1, count=100)
                for tx in transactions:
                    if tx.to_address == address and tx.amount > 0:
                            if self.db.check_transaction(user_id,tx.lt):
                                await self.process_user_deposit(user_id,tx)
        except Exception as e:
            traceback.format_exc()
            print(f"监控用户充值失败: {e}")
    
    async def process_user_deposit(self, user_id: int, tx:HTTPTONTransaction):
        """处理用户充值"""
        try:
            # 创建交易记录
            transaction = Transaction(
                telegram_id=user_id,
                lt=tx.lt,
                type='deposit',
                amount=tx.amount,
                ton_tx_hash=tx.hash,
                status='confirmed',
                created_at=datetime.now(),
                confirmed_at=tx.timestamp
            )
            
            # 更新用户余额
            if self.db.create_transaction(transaction):
                self.db.update_user_balance(user_id, tx.amount)
            
            print(f"用户 {user_id} 充值 {tx.amount} TON 已自动确认")
            
        except Exception as e:
            print(f"处理用户充值失败: {e}")

    async def get_or_create_user(self, telegram_id: int, username: str = None, display_name: str = None) -> User:
        """获取或创建用户"""
        user = self.db.get_user_by_telegram_id(telegram_id)
        if not user:
            now = datetime.now()
            ton_client = await get_ton_client()
            deposit_address = await ton_client.generate_user_deposit_address(telegram_id)
            user = User(
                telegram_id=telegram_id,
                username=username,
                display_name=display_name,
                balance=Decimal('0'),
                total_sent=Decimal('0'),
                total_received=Decimal('0'),
                deposit_address=deposit_address,
                created_at=now,
                updated_at=now
            )
            self.db.create_user(user)
        return user

    async def handle_quick_actions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理快捷操作按钮"""
        try:
            query = update.callback_query
            await query.answer()
            
            action = query.data
            
            if action == "quick_balance":
                await self.balance_command(update, context)
            elif action == "quick_deposit":
                await self.deposit_command(update, context)
            elif action == "quick_history":
                await self.history_command(update, context)
        except Exception as e:
            return
    