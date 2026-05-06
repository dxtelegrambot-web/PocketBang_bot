import os
import random
import re
import time
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

# --- 核心配置 ---
OWNER_ID = 7653037721  # 你的数字ID
MIN_AMOUNT = 20
MAX_AMOUNT = 1000
COMMISSION = 0.05 

# 全局变量：存储红包状态和群组手动开关状态
active_packets = {}
group_switch = {} # {chat_id: True/False} 手动开启/关闭状态

class BotHandlers:
    def __init__(self, db):
        self.db = db

    async def verify_owner_presence(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """核心安全锁：检查拥有者是否在群、是否为管理员、是否已发送开启"""
        chat_id = update.effective_chat.id
        
        # 1. 检查手动开关
        if not group_switch.get(chat_id, False):
            return False, "⚠️ 机器人处于关闭状态，需拥有者发送“开启”激活。"

        try:
            # 2. 实时检查拥有者在不在群里，以及其权限
            owner_member = await context.bot.get_chat_member(chat_id, OWNER_ID)
            # member, administrator, creator 均视为在群，但你要求必须是管理员
            if owner_member.status not in ['administrator', 'creator']:
                return False, "❌ 拥有者已不是群管理员，机器人自动停用。"
        except Exception:
            # 报错说明拥有者已退群
            return False, "❌ 拥有者不在群聊中，机器人自动停 eyes。"

        return True, ""

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        user = update.effective_user
        chat_id = update.effective_chat.id
        if not text: return

        # --- 权限控制逻辑 ---
        
        # 1. 只有拥有者可以操作【开启/关闭】
        if text == "开启" and user.id == OWNER_ID:
            # 开启时顺便检查一下自己是不是管理员
            try:
                me = await context.bot.get_chat_member(chat_id, OWNER_ID)
                if me.status in ['administrator', 'creator']:
                    group_switch[chat_id] = True
                    return await update.message.reply_text("✅ 身份验证成功！机器人已启动。")
                else:
                    return await update.message.reply_text("❌ 开启失败：请先将拥有者设为群管理员。")
            except:
                return

        if text == "关闭" and user.id == OWNER_ID:
            group_switch[chat_id] = False
            return await update.message.reply_text("💤 机器人已进入休眠状态。")

        # 2. 所有功能（查询、发包、加分）执行前的【三重锁】检查
        is_ready, alert_msg = await self.verify_owner_presence(update, context)
        if not is_ready:
            # 只有发包或查询时才弹窗提醒，避免群聊刷屏
            if "/" in text or text == "查询":
                await update.message.reply_text(alert_msg)
            return

        # 3. 管理员上下分逻辑（支持拥有者和群管理员）
        chat_member = await context.bot.get_chat_member(chat_id, user.id)
        is_group_admin = chat_member.status in ['administrator', 'creator']
        
        if (text.startswith('+') or text.startswith('-')) and is_group_admin:
            if update.message.reply_to_message:
                target_user = update.message.reply_to_message.from_user
                try:
                    change = float(text)
                    self.db.add_balance(target_user.id, change)
                    await update.message.reply_text(f"✅ 操作成功！\n当前余额: {self.db.get_balance(target_user.id):.2f}")
                except: pass
            return

        # 4. 查询
        if text == "查询":
            balance = self.db.get_balance(user.id)
            await update.message.reply_text(f"👤 {user.first_name}\n💰 余额: {balance:.2f}")

        # 5. 发红包逻辑
        pattern = r'^(\d+)(?:/(\d+))?/(\d)$'
        match = re.match(pattern, text)
        if match:
            amount = float(match.group(1))
            count = int(match.group(2)) if match.group(2) else 10
            mine = int(match.group(3))
            
            if self.db.get_balance(user.id) < amount:
                return await update.message.reply_text("❌ 余额不足")

            self.db.add_balance(user.id, -amount)
            packet_id = f"pk_{int(time.time()*1000)}"
            active_packets[packet_id] = {
                "total": amount, "amounts": self.generate_amounts(amount, count),
                "total_count": count, "mine": mine, "owner_id": user.id,
                "owner_name": user.first_name, "grabbers": [], "chat_id": chat_id
            }

            keyboard = [[InlineKeyboardButton("🧧 立即抢红包", callback_data=f"grab_{packet_id}")]]
            await update.message.reply_text(
                f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{user.first_name}\n金额：{amount} | 雷号：{mine}\n状态：等待抢包 (0/{count})",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    # 这里的结算逻辑(finalize)和金额分配(generate)保持不变
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
        
        # 抢包时也要检查拥有者是否还在
        is_ready, _ = await self.verify_owner_presence(update, context)
        if not is_ready:
            return await query.answer("❌ 授权失效，无法抢包", show_alert=True)

        if packet_id not in active_packets: return
        data = active_packets[packet_id]
        
        if self.db.get_balance(user.id) < data["total"]:
            return await query.answer(f"⚠️ 余额不足以赔付！", show_alert=True)
        if any(g['id'] == user.id for g in data["grabbers"]):
            return await query.answer("❌ 请勿重复抢包", show_alert=True)

        data["grabbers"].append({"id": user.id, "name": user.first_name})
        grabbed_count = len(data["grabbers"])
        await query.answer("✅ 抢包成功...")

        if grabbed_count >= data["total_count"]:
            await self.finalize_packet(packet_id, query.message.message_id, context)
        else:
            # 实时更新入场名单
            h_name = f"{user.first_name}*{user.first_name[-1]}" if len(user.first_name)>1 else user.first_name+"*"
            new_text = query.message.text + f"\n{h_name} 已入场"
            await query.edit_message_text(text=new_text, reply_markup=query.message.reply_markup)

    async def finalize_packet(self, packet_id, msg_id, context):
        data = active_packets[packet_id]
        result_lines = [f"🧧 红包结算结果 (雷:{data['mine']})", "━━━━━━━━━━━━━━"]
        for i, grabber in enumerate(data["grabbers"]):
            amt = data["amounts"][i]
            is_mine = (int(str(amt)[-1]) == data["mine"])
            h_name = f"{grabber['name']}*{grabber['name'][-1]}" if len(grabber['name'])>1 else grabber['name']+"*"
            line = f"{h_name} -> {amt:.2f}"
            if is_mine:
                line += " 💣"
                self.db.add_balance(data["owner_id"], round(data["total"] * 0.95, 2))
                self.db.add_balance(grabber['id'], -data["total"])
            else:
                self.db.add_balance(grabber['id'], amt)
            result_lines.append(line)
        await context.bot.edit_message_text(chat_id=data["chat_id"], message_id=msg_id, text="\n".join(result_lines))
        del active_packets[packet_id]
