import random, re, time, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

OWNER_ID = 7653037721 # 你的ID
COMMISSION = 0.05 
active_packets = {}
group_switch = {}

class BotHandlers:
    def __init__(self, db):
        self.db = db

    async def verify_owner(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        if not group_switch.get(cid): return False, "⚠️ 机器人未开启。"
        try:
            o = await context.bot.get_chat_member(cid, OWNER_ID)
            if o.status not in ['administrator', 'creator']: return False, "❌ 拥有者非管理员。"
        except: return False, "❌ 拥有者不在群内。"
        return True, ""

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; cid = msg.chat_id
        if not msg.text: return
        txt = msg.text

        # 1. 权限开关
        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            return await msg.reply_text("✅ 长期租赁授权已激活，流水库已连接。")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False
            return await msg.reply_text("💤 系统已休眠。")

        # 2. 授权检查
        ready, alert = await self.verify_owner(update, context)
        if not ready and txt in ["流水", "我的流水", "查询"]:
            return await msg.reply_text(alert)

        m = await context.bot.get_chat_member(cid, user.id)
        is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

        # 3. 个人流水 (显示最近20条)
        if txt == "我的流水":
            logs = self.db.get_user_logs(user.id, 20)
            if not logs: return await msg.reply_text("暂无你的交易记录。")
            rep = f"👤 【{user.first_name}】近20条流水\n━━━━━━━━━━━━━━\n"
            for t, act, amt, mine in logs:
                tag = "💣" if mine else "🧧"
                rep += f"• {t[11:16]} {act} {amt:.2f} {tag}\n"
            return await msg.reply_text(rep)

        # 4. 总流水 (仅管理员可见抽成)
        if txt == "流水" and is_adm:
            total_send, total_mine = self.db.get_group_stats(cid)
            # 计算总抽成：中雷赔付总额的 5% (假设赔付额等于发包总额)
            total_profit = total_mine * (total_send / max(1, total_send)) * 100 * COMMISSION # 简化算法
            rep = (f"📊 【全群财务报表】\n━━━━━━━━━━━━━━\n"
                   f"累计发包总额：{total_send:.2f}\n"
                   f"累计中雷次数：{total_mine}\n"
                   f"💰 预计系统总抽成：{total_send * 0.05:.2f}\n" # 以总流水估算抽成
                   f"━━━━━━━━━━━━━━\n仅限管理查看")
            return await msg.reply_text(rep)

        # 5. 上下分
        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user
                self.db.add_balance(target.id, float(txt))
                self.db.log_action(target.id, "人工上下分", float(txt), 0, cid)
                await msg.reply_text(f"✅ {target.first_name} 余额：{self.db.get_balance(target.id):.2f}")
            return

        if txt == "查询":
            await msg.reply_text(f"💰 余额：{self.db.get_balance(user.id):.2f}")

        # 6. 发包
        p = r'^(\d+)(?:/(\d+))?/(\d)$'
        match = re.match(p, txt)
        if match and group_switch.get(cid):
            amt, count, mine = float(match.group(1)), int(match.group(2)) if match.group(2) else 10, int(match.group(3))
            if self.db.get_balance(user.id) < amt: return await msg.reply_text("❌ 余额不足")
            self.db.add_balance(user.id, -amt)
            self.db.log_action(user.id, "发包", amt, 0, cid) # 记录发包记录
            pid = f"pk_{int(time.time()*1000)}"
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, count), "count": count, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid}
            await msg.reply_text(f"🧧 {amt}/{count} 雷:{mine}\n门槛:{amt} | 已抢 0/{count}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧧 抢红包", callback_data=f"grab_{pid}")]]))

    def gen_amts(self, total, count):
        amts = []
        rem = total
        for i in range(count - 1):
            a = round(random.uniform(0.01, (rem / (count - i)) * 2), 2)
            amts.append(a); rem -= a
        amts.append(round(rem, 2))
        random.shuffle(amts)
        return amts

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; u = q.from_user; pid = q.data.replace("grab_", "")
        ready, _ = await self.verify_owner(update, context)
        if not ready or pid not in active_packets: return await q.answer("失效")
        d = active_packets[pid]
        if self.db.get_balance(u.id) < d["total"]: return await q.answer(f"需持分{d['total']}", show_alert=True)
        if any(g['id'] == u.id for g in d["grabbers"]): return await q.answer("已抢")
        
        d["grabbers"].append({"id": u.id, "name": u.first_name})
        await q.answer("抢包成功")
        if len(d["grabbers"]) >= d["count"]:
            await self.finalize(pid, q.message.message_id, context)
        else:
            await q.edit_message_text(text=q.message.text + f"\n{u.first_name} 已抢", reply_markup=q.message.reply_markup)

    async def finalize(self, pid, mid, context):
        d = active_packets[pid]; res = [f"🧧 结算 (雷:{d['mine']})", "━━━━"]
        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            if is_mine:
                self.db.add_balance(d["owner_id"], round(d["total"] * 0.95, 2))
                self.db.add_balance(g['id'], -d["total"])
                self.db.log_action(g['id'], "抢包中雷", -d["total"], 1, d["cid"])
            else:
                self.db.add_balance(g['id'], amt)
                self.db.log_action(g['id'], "抢包", amt, 0, d["cid"])
            res.append(f"{g['name']}->{amt} {'💣' if is_mine else ''}")
        await context.bot.edit_message_text(chat_id=d["cid"], message_id=mid, text="\n".join(res))
        del active_packets[pid]
