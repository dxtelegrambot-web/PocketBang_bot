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
COMMISSION = 0.05 # 5% 抽成

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
                    new_bal = self.db.get_balance(target_user.id)
                    await update.message.reply_text(f"✅ 操作成功！\n用户: {target_user.first_name}\n变动: {change}\n当前余额: {new_bal:.2f}")
                except: pass
            return

        # 2. 查询
        if text == "查询":
            balance = self.db.get_balance(user.id)
            await update.message.reply_text(f"👤 用户: {user.first_name}\n💰 余额: {balance:.2f}")
            return

        # 3. 发红包逻辑 (100/5/7)
        pattern = r'^(\d+)(?:/(\d+))?/(\d)$'
        match = re.match(pattern, text)
        if match:
            amount = float(match.group(1))
            count = int(match.group(2)) if match.group(2) else 10
            mine = int(match.group(3))

            if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
                return await update.message.reply_text(f"❌ 金额需在 {MIN_AMOUNT}-{MAX_AMOUNT} 之间")
            
            # 严格检查发包者余额
            if self.db.get_balance(user.id) < amount:
                return await update.message.reply_text(f"❌ 余额不足以发包\n当前余额: {self.db.get_balance(user.id):.2f}")

            # 扣除分数
            self.db.add_balance(user.id, -amount)
            packet_id = f"pk_{int(time.time()*1000)}" # 使用毫秒级ID防止冲突
            
            active_packets[packet_id] = {
                "total": amount,
                "amounts": self.generate_amounts(amount, count),
                "total_count": count,
                "mine": mine,
                "owner_id": user.id,
                "owner_name": user.first_name,
                "grabbers": [],
                "status": "active",
                "chat_id": update.effective_chat.id
            }

            keyboard = [[InlineKeyboardButton("🧧 立即抢红包", callback_data=f"grab_{packet_id}")]]
            await update.message.reply_text(
                f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n"
                f"发包老总：{user.first_name}\n红包金额：{amount}\n"
                f"红包包数：{count}\n雷号：{mine}\n"
                f"抢包门槛：需持分 {amount}\n━━━━━━━━━━━━━━\n"
                f"等待抢包... (0/{count})",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    def generate_amounts(self, total, count):
        amounts = []
        remaining = total
        for i in range(count - 1):
            amt = round(random.uniform(0.01, (remaining / (count - i)) * 2), 2)
            amounts.append(amt)
            remaining -= amt
        amounts.append(round(remaining, 2))
        random.shuffle(amounts)
        return amounts

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = query.from_user
        packet_id = query.data.replace("grab_", "")

        if packet_id not in active_packets:
            return await query.answer("❌ 红包已过期或不存在", show_alert=True)

        data = active_packets[packet_id]
        
        # --- 这里的逻辑是解决“0元抢包”的关键 ---
        # 1. 实时读取数据库最新余额，不信任缓存
        current_balance = self.db.get_balance(user.id)
        
        # 2. 强制校验：余额必须大于等于红包总额
        if current_balance < data["total"]:
            return await query.answer(f"⚠️ 余额不足！\n当前余额: {current_balance:.2f}\n抢此包需满足: {data['total']:.2f}\n请联系管理员上分。", show_alert=True)

        # 3. 防止红包已领完
        if len(data["grabbers"]) >= data["total_count"]:
            return await query.answer("❌ 手慢了，红包已抢完", show_alert=True)

        # 4. 防止重复抢包
        if any(g['id'] == user.id for g in data["grabbers"]):
            return await query.answer("❌ 你已经抢过此包，请等待结算", show_alert=True)

        # 5. 记录抢包
        data["grabbers"].append({"id": user.id, "name": user.first_name})
        grabbed_count = len(data["grabbers"])
        
        await query.answer("✅ 抢包成功，请等待开奖...")

        # 更新红包消息
        new_text = (
            f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n"
            f"发包老总：{data['owner_name']}\n红包金额：{data['total']}\n"
            f"红包包数：{data['total_count']}\n雷号：{data['mine']}\n━━━━━━━━━━━━━━\n"
            f"正在抢包：({grabbed_count}/{data['total_count']})\n"
        )
        for g in data["grabbers"]:
            h_name = f"{g['name']}*{g['name'][-1]}" if len(g['name'])>1 else g['name']+"*"
            new_text += f"\n{h_name} 已入场"

        if grabbed_count >= data["total_count"]:
            await self.finalize_packet(packet_id, query.message.message_id, context)
        else:
            await query.edit_message_text(text=new_text, reply_markup=query.message.reply_markup)

    async def finalize_packet(self, packet_id, msg_id, context):
        if packet_id not in active_packets: return
        data = active_packets[packet_id]
        data["status"] = "finished"
        
        result_lines = [f"🧧 红包结算结果 (雷:{data['mine']})", "━━━━━━━━━━━━━━"]
        
        for i, grabber in enumerate(data["grabbers"]):
            amt = data["amounts"][i]
            last_digit = int(str(amt)[-1])
            is_mine = (last_digit == data["mine"])
            
            h_name = f"{grabber['name']}*{grabber['name'][-1]}" if len(grabber['name'])>1 else grabber['name']+"*"
            line = f"{h_name} -> {amt:.2f}"
            
            if is_mine:
                line += " 💣"
                # 中雷赔付发包者95%，抽5%
                to_owner = round(data["total"] * (1 - COMMISSION), 2)
                self.db.add_balance(data["owner_id"], to_owner)
                self.db.add_balance(grabber['id'], -data["total"])
            else:
                # 未中雷不抽成
                self.db.add_balance(grabber['id'], amt)
            
            result_lines.append(line)

        await context.bot.edit_message_text(
            chat_id=data["chat_id"],
            message_id=msg_id,
            text="\n".join(result_lines)
        )
        del active_packets[packet_id]
