import os
import random
import re
import time
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

# --- 配置参数 ---
ADMIN_IDS = "7653037721" # 你的ID
MIN_AMOUNT = 20
MAX_AMOUNT = 1000
COMMISSION = 0.05 # 5% 抽成 (仅中雷赔付时扣除)

# 全局变量：存储红包状态
active_packets = {}

class BotHandlers:
    def __init__(self, db):
        self.db = db

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        user = update.effective_user
        if not text: return

        # 1. 管理员人工上下分
        if (text.startswith('+') or text.startswith('-')) and str(user.id) == str(ADMIN_IDS):
            if update.message.reply_to_message:
                target_user = update.message.reply_to_message.from_user
                try:
                    change = float(text)
                    self.db.add_balance(target_user.id, change) 
                    await update.message.reply_text(f"✅ 操作成功！\n用户: {target_user.first_name}\n变动: {change}\n当前余额: {self.db.get_balance(target_user.id)}")
                except: pass
            return

        # 2. 查询
        if text == "查询":
            balance = self.db.get_balance(user.id)
            await update.message.reply_text(f"👤 用户: {user.first_name}\n💰 余额: {balance}")
            return

        # 3. 发红包逻辑 (100/5/7)
        pattern = r'^(\d+)(?:/(\d+))?/(\d)$'
        match = re.match(pattern, text)
        if match:
            amount = float(match.group(1))
            count = int(match.group(2)) if match.group(2) else 10
            mine = int(match.group(3))

            if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
                return await update.message.reply_text(f"❌ 金额限制 {MIN_AMOUNT}-{MAX_AMOUNT}")
            if count < 1 or count > 10:
                return await update.message.reply_text("❌ 包数限制 1-10")
            if self.db.get_balance(user.id) < amount:
                return await update.message.reply_text("❌ 余额不足")

            self.db.add_balance(user.id, -amount)
            packet_id = f"pk_{int(time.time())}_{user.id}"
            
            # 生成随机金额列表（提前生成，确保随机且总额相等）
            amounts = self.generate_amounts(amount, count)
            
            active_packets[packet_id] = {
                "total": amount,
                "amounts": amounts, # 预设好的金额列表
                "total_count": count,
                "mine": mine,
                "owner_id": user.id,
                "owner_name": user.first_name,
                "grabbers": [], # 存储已抢用户的信息 [{"id":, "name":, "amount":}]
                "status": "active",
                "chat_id": update.effective_chat.id
            }

            keyboard = [[InlineKeyboardButton("🧧 立即抢红包", callback_data=f"grab_{packet_id}")]]
            msg = await update.message.reply_text(
                f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n"
                f"发包老总：{user.first_name}\n红包金额：{amount}\n"
                f"红包包数：{count}\n雷号：{mine}\n━━━━━━━━━━━━━━\n"
                f"正在抢包：(已抢 0/{count})",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            # 启动10分钟过期任务 (按你的新要求改为10分钟)
            asyncio.create_task(self.expire_packet(packet_id, msg.message_id, context))

    def generate_amounts(self, total, count):
        """生成随机金额列表"""
        amounts = []
        remaining = total
        for i in range(count - 1):
            amt = round(random.uniform(0.01, (remaining / (count - i)) * 2), 2)
            amounts.append(amt)
            remaining -= amt
        amounts.append(round(remaining, 2))
        random.shuffle(amounts) # 打乱顺序
        return amounts

    async def expire_packet(self, packet_id, msg_id, context):
        """10分钟后强制开奖"""
        await asyncio.sleep(600) # 10分钟
        if packet_id in active_packets and active_packets[packet_id]["status"] == "active":
            await self.finalize_packet(packet_id, msg_id, context)

    async def finalize_packet(self, packet_id, msg_id, context):
        """统一结算并公布结果"""
        data = active_packets[packet_id]
        data["status"] = "finished"
        
        chat_id = data["chat_id"]
        result_lines = [f"🧧 红包结算 (总额:{data['total']} 雷:{data['mine']})", "━━━━━━━━━━━━━━"]
        
        # 结算已抢到的用户
        for i, grabber in enumerate(data["grabbers"]):
            grab_amount = data["amounts"][i]
            last_digit = int(str(grab_amount)[-1])
            is_mine = (last_digit == data["mine"])
            
            h_name = f"{grabber['name']}*{grabber['name'][-1]}" if len(grabber['name'])>1 else grabber['name']+"*"
            line = f"{h_name} -> {grab_amount:.2f}"
            
            if is_mine:
                line += " 💣"
                # 中雷扣费 100，返还发包者 95
                comp = round(data["total"] * (1 - COMMISSION), 2)
                self.db.add_balance(data["owner_id"], comp)
                self.db.add_balance(grabber['id'], -data["total"])
            else:
                # 未中雷全额到账
                self.db.add_balance(grabber['id'], grab_amount)
            
            result_lines.append(line)

        # 退还未领取的金额
        grabbed_count = len(data["grabbers"])
        if grabbed_count < data["total_count"]:
            unclaimed = sum(data["amounts"][grabbed_count:])
            self.db.add_balance(data["owner_id"], unclaimed)
            result_lines.append(f"━━━━━━━━━━━━━━\n未领完退还：{unclaimed:.2f}")

        # 更新消息，移除按钮
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text="\n".join(result_lines)
        )
        if packet_id in active_packets: del active_packets[packet_id]

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = query.from_user
        packet_id = query.data.replace("grab_", "")

        if packet_id not in active_packets:
            return await query.answer("❌ 红包已失效", show_alert=True)

        data = active_packets[packet_id]
        if data["status"] != "active": return

        # 1. 防止重复
        if any(g['id'] == user.id for g in data["grabbers"]):
            return await query.answer("❌ 你已经点过了，等待开奖", show_alert=True)
        
        # 2. 记录“抢包”动作（暂不分配金额）
        data["grabbers"].append({"id": user.id, "name": user.first_name})
        grabbed_count = len(data["grabbers"])
        
        await query.answer("✅ 抢包成功！等待红包领完或到期开奖。")

        # 3. 更新界面显示进度
        new_text = (
            f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n"
            f"发包老总：{data['owner_name']}\n红包金额：{data['total']}\n"
            f"红包包数：{data['total_count']}\n雷号：{data['mine']}\n━━━━━━━━━━━━━━\n"
            f"正在抢包：(已抢 {grabbed_count}/{data['total_count']})\n"
        )
        # 显示已抢名单（不显金额）
        for g in data["grabbers"]:
            h_name = f"{g['name']}*{g['name'][-1]}" if len(g['name'])>1 else g['name']+"*"
            new_text += f"\n{h_name} 已抢入"

        if grabbed_count >= data["total_count"]:
            # 领完了，立即结算
            await self.finalize_packet(packet_id, query.message.message_id, context)
        else:
            await query.edit_message_text(text=new_text, reply_markup=query.message.reply_markup)
