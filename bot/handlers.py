import random, re, time, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

OWNER_ID = 7653037721
COMMISSION = 0.05 
active_packets = {}
group_switch = {}

class BotHandlers:
    def __init__(self, db):
        self.db = db

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = update.message.text
        if "all_logs" in txt:
            logs = self.db.get_user_logs(update.effective_user.id, 50)
            if not logs: return await update.message.reply_text("暂无流水记录。")
            rep = "📊 全量流水明细\n" + "\n".join([f"• {t[5:16]} {act} {'+' if amt>0 else ''}{amt:.2f}" for t, act, amt, mine in logs])
            return await update.message.reply_text(rep)
        await update.message.reply_text("🤖 系统就绪，请在群内使用。")

    async def verify_owner(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        if not group_switch.get(cid): return False, "⚠️ 机器人未开启。"
        try:
            o = await context.bot.get_chat_member(cid, OWNER_ID)
            if o.status not in ['administrator', 'creator']: return False, "❌ 拥有者非管理，熔断停用。"
        except: return False, "❌ 拥有者不在群内。"
        return True, ""

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; cid = update.effective_chat.id
        if not msg or not msg.text: return
        txt = msg.text.strip()

        # 开启/关闭
        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            return await msg.reply_text("✅ 系统已启动，所有指令已对齐。")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False
            return await msg.reply_text("💤 系统已休眠。")

        ready, alert = await self.verify_owner(update, context)
        if not ready and txt in ["查询", "流水", "总计", "我的流水"] or "/" in txt:
            return await msg.reply_text(alert)

        # 检查是否是管理员
        m = await context.bot.get_chat_member(cid, user.id)
        is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

        # 上下分逻辑 (优化点：先判断上下分，再判断其他)
        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user
                try:
                    num = float(txt.replace(' ', ''))
                    self.db.add_balance(target.id, num)
                    self.db.log_action(target.id, "人工上下分", num, 0, cid)
                    return await msg.reply_text(f"✅ {target.first_name} 余额：{self.db.get_balance(target.id):.2f}")
                except: pass

        if txt == "查询":
            return await msg.reply_text(f"💰 余额：{self.db.get_balance(user.id):.2f}")

        if txt == "我的流水":
            logs = self.db.get_user_logs(user.id, 20)
            rep = f"👤 【{user.first_name}】近20条流水\n" + "\n".join([f"• {t[11:16]} {act} {'+' if amt>0 else ''}{amt:.2f}" for t, act, amt, mine in logs])
            kb = [[InlineKeyboardButton("📩 查看全部流水", url=f"t.me/{context.bot.username}?start=all_logs")]]
            return await msg.reply_text(rep, reply_markup=InlineKeyboardMarkup(kb))

        if txt == "流水" and is_adm:
            ts, tm = self.db.get_group_stats(cid)
            try:
                await context.bot.send_message(user.id, f"📊 【财务报表】\n群: {msg.chat.title}\n总发包: {ts:.2f}\n中雷数: {tm}\n盈利: {ts*COMMISSION:.2f}")
                await msg.reply_text("✅ 详细流水已私信。")
            except: await msg.reply_text("❌ 请先私聊机器人点 /start")
            return

        if txt == "总计" and is_adm:
            data = self.db.get_all_balances()
            rep = "💰 【资产排行】\n" + "\n".join([f"{i+1}. ID:{u} -> {b:.2f}" for i, (u, b) in enumerate(data[:10])])
            return await msg.reply_text(rep)

        # 发包
        match = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if match:
            amt, count, mine = float(match.group(1)), int(match.group(2)) if match.group(2) else 10, int(match.group(3))
            if self.db.get_balance(user.id) < amt: return await msg.reply_text("❌ 余额不足")
            self.db.add_balance(user.id, -amt)
            self.db.log_action(user.id, "发包", -amt, 0, cid)
            pid = f"pk_{int(time.time()*1000)}"
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, count), "count": count, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid}
            return await msg.reply_text(f"🧧 {amt}/{count} 雷:{mine}\n等待抢包...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧧 抢红包", callback_data=f"grab_{pid}")]]))

    def gen_amts(self, total, count):
        amts = []; rem = total
        for i in range(count - 1):
            a = round(random.uniform(0.01, (rem / (count - i)) * 2), 2)
            amts.append(a); rem -= a
        amts.append(round(rem, 2)); random.shuffle(amts)
        return amts

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; u = q.from_user; pid = q.data.replace("grab_", "")
        ready, _ = await self.verify_owner(update, context)
        if not ready or pid not in active_packets: return await q.answer("失效")
        d = active_packets[pid]
        if self.db.get_balance(u.id) < d["total"]: return await q.answer(f"需持分{d['total']}", show_alert=True)
        if any(g['id'] == u.id for g in d["grabbers"]): return await q.answer("已抢过")
        d["grabbers"].append({"id": u.id, "name": u.first_name})
        await q.answer("抢包成功")
        if len(d["grabbers"]) >= d["count"]: await self.finalize(pid, q.message.message_id, context)
        else: await q.edit_message_text(text=f"🧧 抢包中 ({len(d['grabbers'])}/{d['count']})", reply_markup=q.message.reply_markup)

    async def finalize(self, pid, mid, context):
        d = active_packets.pop(pid, None)
        if not d: return
        res = [f"🧧 结算 (雷:{d['mine']})", "━━━━"]
        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            self.db.add_balance(g['id'], amt)
            self.db.log_action(g['id'], "抢到红包", amt, 0, d["cid"])
            if is_mine:
                self.db.add_balance(g['id'], -d["total"])
                self.db.log_action(g['id'], "抢包中雷", -d["total"], 1, d["cid"])
                inc = round(d["total"] * (1 - COMMISSION), 2)
                self.db.add_balance(d["owner_id"], inc)
                self.db.log_action(d["owner_id"], f"中雷收入({g['name']})", inc, 0, d["cid"])
            res.append(f"{g['name']}->{amt} {'💣' if is_mine else ''}")
        await context.bot.edit_message_text(chat_id=d["cid"], message_id=mid, text="\n".join(res))
