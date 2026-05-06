import os
import random
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

# --- 配置参数 ---
ADMIN_IDS = 7653037721  # 填入你的Telegram ID，多个用逗号隔开，例如 ["123", "456"]
MIN_AMOUNT = 20
MAX_AMOUNT = 1000
COMMISSION = 0.05 # 5% 抽成

class BotHandlers:
    def __init__(self, db):
        self.db = db # 假设你有基本的数据库实例

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        user = update.effective_user
        chat_id = update.effective_chat.id

        # 1. 管理员人工上下分指令 (格式: 回复某人发送 +100 或 -100)
        if (text.startswith('+') or text.startswith('-')) and str(user.id) == str(ADMIN_IDS):
            if update.message.reply_to_message:
                target_user = update.message.reply_to_message.from_user
                try:
                    amount = float(text)
                    # 执行上下分逻辑
                    self.db.add_balance(target_user.id, amount) 
                    await update.message.reply_text(f"✅ 操作成功！\n用户: {target_user.first_name}\n变动: {amount}\n当前余额: {self.db.get_balance(target_user.id)}")
                except: pass
            return

        # 2. 查询余额
        if text == "查询":
            balance = self.db.get_balance(user.id)
            await update.message.reply_text(f"👤 用户: {user.first_name}\n💰 余额: {balance}")
            return

        # 3. 发红包逻辑 (解析 100/5/7 或 100/7)
        pattern = r'^(\d+)(?:/(\d+))?/(\d)$' # 正则匹配金额/包数/雷号
        match = re.match(pattern, text)
        if match:
            amount = int(match.group(1))
            count = int(match.group(2)) if match.group(2) else 10 # 默认10个包
            mine = int(match.group(3))

            if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
                await update.message.reply_text(f"❌ 金额需在 {MIN_AMOUNT}-{MAX_AMOUNT} 之间")
                return
            
            if count > 10:
                await update.message.reply_text("❌ 最高只能发10个包")

            # 扣除分数
            if self.db.get_balance(user.id) < amount:
                await update.message.reply_text("❌ 余额不足，请联系管理员上分")
                return
            
            self.db.add_balance(user.id, -amount)
            
            # 发送红包按钮
            keyboard = [[InlineKeyboardButton("🧧 点击抢红包", callback_data=f"grab_{amount}_{count}_{mine}_{user.id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"🧧 【红包扫雷】\n"
                f"━━━━━━━━━━━━━━\n"
                f"发包老总：{user.first_name}\n"
                f"红包金额：{amount}\n"
                f"红包包数：{count}\n"
                f"雷号：{mine}\n"
                f"━━━━━━━━━━━━━━\n"
                f"等待抢包...",
                reply_markup=reply_markup
            )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = query.from_user
        # 格式: grab_金额_总数_雷号_发包者ID
        data = query.data.split('_')
        amount, total_count, mine, owner_id = int(data[1]), int(data[2]), int(data[3]), data[4]

        # 名字隐藏处理: 张三丰 -> 张*丰
        name = user.first_name
        hidden_name = f"{name[0]}*{name[-1]}" if len(name) > 1 else name + "*"

        # 模拟抢包逻辑 (这里应配合Redis记录谁抢过，防止重领)
        grab_amount = round(random.uniform(1.0, amount/total_count * 2), 2)
        actual_get = round(grab_amount * (1 - COMMISSION), 2)
        
        last_digit = int(str(grab_amount)[-1])
        is_mine = (last_digit == mine)

        # 拼接实时更新文字
        result_text = f"{hidden_name} 抢到了 {grab_amount}"
        if is_mine:
            result_text += " 💣"
            # 中雷逻辑：扣除红包总金额给发包者 (或庄家)
            self.db.add_balance(user.id, -amount)
            await query.answer(f"哎呀！中雷了，扣除 {amount} 分", show_alert=True)
        else:
            self.db.add_balance(user.id, actual_get)
            await query.answer(f"恭喜！抢到 {actual_get} 分 (已扣5%抽成)")

        # 更新原消息 (实时刷新列表)
        new_text = query.message.text + f"\n{result_text}"
        await query.edit_message_text(text=new_text, reply_markup=query.message.reply_markup)
