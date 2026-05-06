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
COMMISSION = 0.05 # 5% 抽成 (中雷赔付给发包者时扣除)

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
                    await update.message.reply_text(f"✅ 上分成功！\n用户: {target_user.first_name}\n变动: {change}\n当前余额: {round(self.db.get_balance(target_user.id), 2)}")
                except: pass
            return

        # 2. 查询
        if text == "查询":
            balance = self.db.get_balance(user.id)
            await update.message.reply_text(f"👤 用户: {user.first_name}\n💰 当前余额: {round(balance, 2)}")
            return

        # 3. 发红包逻辑
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
            
            user_balance = self.db.get_balance(user.id)
            if user_balance < amount:
                return await update.message.reply_text(f"❌ 余额不足以发包\n当前余额: {round(user_balance, 2)}")

            # 发包扣费
            self.db.add_balance(user.id, -amount)
            packet_id = f"pk_{int(time.time())}_{user.id}"
            
            # 生成固定金额列表
            amounts = self.generate_amounts(amount, count)
            
            active_packets[packet_id] = {
                "total": amount,
                "amounts": amounts,
                "total_count": count,
                "mine": mine,
                "owner_id": user.id,
                "owner_name": user.first_name,
                "grabbers": [], # 仅记录符合条件的抢包者
                "status": "active",
                "chat_id": update.effective_chat.id
            }

            keyboard = [[InlineKeyboardButton("🧧 立即抢红包", callback_data=f"grab_{packet_id}")]]
            await update.message.reply_text(
                f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n"
                f"发包老总：{user.first_name}\n红包金额：{amount}\n"
                f"红包包数：{count}\n雷号：{mine}\n━━━━━━━━━━━━━━\n"
                f"抢包门槛：需持分 {amount} 以上\n"
                f"进度：(0/{count})",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            # 10分钟过期自动开奖任务
            asyncio.create_task(self.expire_packet(packet_id, context))

    def generate_amounts(self, total, count):
        amounts = []
        remaining = total
        for i in range(count - 1):
            # 保证金额随机且末尾数字分布均匀
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
            return await query.answer("❌ 该红包已过期结算", show_alert=True)

        data = active_packets[packet_id]
        if data["status"] != "active": return

        # --- 核心修复：余额门槛检查 ---
        user_balance = self.db.get_balance(user.id)
        if user_balance < data["total"]:
            return await query.answer(f"❌ 抢包失败！\n你的余额 ({round(user_balance, 2)}) 低于中雷赔付额 ({data['total']})，请联系管理员上分。", show_alert=True)

        if any(g['id'] == user.id for g in data["grabbers"]):
            return await query.answer("❌ 你已经抢过此包，请等待开奖", show_alert=True)
        
        # 记录抢包者
        data["grabbers"].append({"id": user.id, "name": user.first_name})
        grabbed_count = len(data["grabbers"])
        
        await query.answer("✅ 抢包成功，等待结算...")

        # 实时更新抢包人数
        new_text = (
            f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n"
            f"发包老总：{data['owner_name']}\n红包金额：{data['total']}\n"
            f"红包包数：{data['total_count']}\n雷号：{data['mine']}\n━━━━━━━━━━━━━━\n"
            f"正在抢包：({grabbed_count}/{data['total_count']})\n"
        )
        for g in data["grabbers"]:
            h_name = f"{g['name']}*{g['name'][-1]}" if len(g['name'])>1 else g['name']+"*"
            new_text += f"\n{h_name} 已抢入"

        if grabbed_count >= data["total_count"]:
            # 这里的 message_id 是按钮所在的消息 ID
            await self.finalize_packet(packet_id, query.message.message_id, context)
        else:
            await query.edit_message_text(text=new_text, reply_markup=query.message.reply_markup)

    async def expire_packet(self, packet_id, context):
        await asyncio.sleep(600) # 10分钟
        if packet_id in active_packets and active_packets[packet_id]["status"] == "active":
            # 这里需要寻找到消息 ID 才能更新
            pass # 实际运行中 finalize 会处理，此处为防止意外

    async def finalize_packet(self, packet_id, msg_id, context):
        if packet_id not in active_packets: return
        data = active_packets[packet_id]
        data["status"] = "finished"
        
        result_lines = [f"🧧 红包结算结果 (雷:{data['mine']})", "━━━━━━━━━━━━━━"]
        
        for i, grabber in enumerate(data["grabbers"]):
            grab_amount = data["amounts"][i]
            # 取金额的最后一位数字
            last_digit = int(str(grab_amount)[-1])
            is_mine = (last_digit == data["mine"])
            
            h_name = f"{grabber['name']}*{grabber['name'][-1]}" if len(grabber['name'])>1 else grabber['name']+"*"
            line = f"{h_name} -> {grab_amount:.2f}"
            
            if is_mine:
                line += " 💣"
                # 中雷赔付逻辑：发包者收 95%，机器人抽 5%
                pay_to_owner = round(data["total"] * (1 - COMMISSION), 2)
                self.db.add_balance(data["owner_id"], pay_to_owner)
                self.db.add_balance(grabber['id'], -data["total"]) # 中雷者扣全额
            else:
                # 未中雷：抢多少加多少，不抽成
                self.db.add_balance(grabber['id'], grab_amount)
            
            result_lines.append(line)

        # 退还未领取的红包份额
        grabbed_num = len(data["grabbers"])
        if grabbed_num < data["total_count"]:
            refund = sum(data["amounts"][grabbed_num:])
            self.db.add_balance(data["owner_id"], round(refund, 2))
            result_lines.append(f"━━━━━━━━━━━━━━\n未领完退还：{round(refund, 2)}")

        await context.bot.edit_message_text(
            chat_id=data["chat_id"],
            message_id=msg_id,
            text="\n".join(result_lines)
        )
        del active_packets[packet_id]
