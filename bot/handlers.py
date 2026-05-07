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

    def mask_name(self, name):
        if not name: return "*"
        n = str(name)
        return f"{n}*{n[-1]}" if len(n) > 2 else n

    async def verify_owner(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        if not group_switch.get(cid): return False, "⚠️ 机器人未开启。"
        try:
            o = await context.bot.get_chat_member(cid, OWNER_ID)
            if o.status not in ['administrator', 'creator']: return False, "❌ 权限熔断：拥有者非管理。"
        except: return False, "❌ 权限熔断：拥有者不在群内。"
        return True, ""

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = update.message.text
        if "all_logs_" in txt:
            # 解析私聊指令中的 chat_id
            try:
                target_cid = txt.split("all_logs_")[1]
                logs = self.db.get_user_logs(update.effective_user.id, target_cid, 50)
                if not logs: return await update.message.reply_text("该群暂无你的流水记录。")
                rep = "📊 全量流水明细\n━━━━━━━━━━━━━━\n"
                for t, act, amt, mine in logs:
                    symbol = "+" if amt > 0 else ""
                    rep += f"• {t[5:16]} {act} {symbol}{amt:.2f}\n"
                return await update.message.reply_text(rep)
            except: pass
        await update.message.reply_text("🤖 扫雷系统已就绪。")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; cid = update.effective_chat.id
        if not msg or not msg.text: return
        txt = msg.text.strip()

        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            return await msg.reply_text("✅ 机器人已启动！[本群数据独立]")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False
            return await msg.reply_text("💤 机器人已休眠。")

        ready, alert = await self.verify_owner(update, context)
        if not ready:
            if any(x in txt for x in ["查询", "流水", "总计"]) or re.match(r'^\d+/', txt):
                await msg.reply_text(alert)
            return

        m = await context.bot.get_chat_member(cid, user.id)
        is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user
                try:
                    num = float(txt.replace(' ', ''))
                    self.db.add_balance(target.id, cid, num)
                    self.db.log_action(target.id, "人工上下分", num, 0, cid)
                    cur_bal = self.db.get_balance(target.id, cid)
                    return await msg.reply_text(f"✅ {target.first_name} 本群余额：{cur_bal:.2f}")
                except: pass

        if txt == "查询":
            bal = self.db.get_balance(user.id, cid)
            return await msg.reply_text(f"💰 本群余额：{bal:.2f}")

        if txt == "我的流水":
            logs = self.db.get_user_logs(user.id, cid, 20)
            rep = f"👤 【{user.first_name}】本群流水\n" + "\n".join([f"• {t[11:16]} {act} {'+' if amt>0 else ''}{amt:.2f}" for t, act, amt, mine in logs])
            kb = [[InlineKeyboardButton("📩 查看全部流水", url=f"t.me/{context.bot.username}?start=all_logs_{cid}")]]
            return await msg.reply_text(rep, reply_markup=InlineKeyboardMarkup(kb))

        if txt == "流水" and is_adm:
            ts, tm = self.db.get_group_stats(cid)
            try:
                await context.bot.send_message(user.id, f"📊 【{msg.chat.title}】流水\n总发包: {ts:.2f}\n中雷数: {tm}\n盈利: {ts*COMMISSION:.2f}")
                await msg.reply_text("✅ 详细流水已私信。")
            except: await msg.reply_text("❌ 请先私聊机器人点 /start")
            return

        if txt == "总计" and is_adm:
            data = self.db.get_all_balances(cid)
            rep = f"💰 【{msg.chat.title}】持分排行\n" + "\n".join([f"{i+1}. ID:{u} -> {b:.2f}" for i, (u, b) in enumerate(data[:10])])
            return await msg.reply_text(rep)

        packet_match = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if packet_match:
            amt = float(packet_match.group(1)); count = int(packet_match.group(2)) if packet_match.group(2) else 10; mine = int(packet_match.group(3))
            if self.db.get_balance(user.id, cid) < amt: return await msg.reply_text("❌ 余额不足")
            self.db.add_balance(user.id, cid, -amt)
            self.db.log_action(user.id, "发包", -amt, 0, cid)
            pid = f"pk_{int(time.time()*1000)}"
            task = asyncio.create_task(self.timer_refund(pid, cid, context))
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, count), "count": count, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid, "task": task}
            kb = [[InlineKeyboardButton("🧧 立即抢包", callback_data=f"grab_{pid}")]]
            sent_msg = await msg.reply_text(f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{user.first_name}\n金额：{amt} | 雷：{mine} | 包：{count}\n━━━━━━━━━━━━━━\n等待入场... (0/{count})", reply_markup=InlineKeyboardMarkup(kb))
            active_packets[pid]["mid"] = sent_msg.message_id

    async def timer_refund(self, pid, cid, context):
        await asyncio.sleep(600)
        if pid in active_packets:
            d = active_packets[pid]
            grabbed_count = len(d["grabbers"])
            unclaimed_amount = sum(d["amounts"][grabbed_count:])
            if unclaimed_amount > 0:
                self.db.add_balance(d["owner_id"], cid, unclaimed_amount)
                self.db.log_action(d["owner_id"], "过期退还", unclaimed_amount, 0, cid)
                await context.bot.send_message(cid, f"⏰ 红包已到期！退回给 {d['owner_name']}：{unclaimed_amount:.2f}")
            await self.finalize(pid, d["mid"], context)

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
        if self.db.get_balance(u.id, d["cid"]) < d["total"]: return await q.answer(f"需持分{d['total']}", show_alert=True)
        if any(g['id'] == u.id for g in d["grabbers"]): return await q.answer("已抢过")
        
        d["grabbers"].append({"id": u.id, "name": u.first_name})
        grabbed_num = len(d["grabbers"])
        await q.answer("✅ 抢包成功！")

        if grabbed_num >= d["count"]: 
            d["task"].cancel()
            await self.finalize(pid, q.message.message_id, context)
        else:
            name_list = []
            for i, g in enumerate(d["grabbers"]):
                m_name = self.mask_name(g['name'])
                if i == d["count"] - 2: name_list.append(f"{i+1}. {m_name} 已抢，等待开奖")
                else:
                    amt = d["amounts"][i]; is_m = (int(str(amt)[-1]) == d["mine"])
                    name_list.append(f"{i+1}. {m_name} -> {amt:.2f} {'💣' if is_m else ''}")
            list_str = "\n".join(name_list)
            new_text = f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{d['owner_name']}\n金额：{d['total']} | 雷：{d['mine']} | 包：{d['count']}\n━━━━━━━━━━━━━━\n" + "\n".join(name_list) + f"\n\n正在抢包... ({grabbed_num}/{d['count']})"
            await q.edit_message_text(text=new_text, reply_markup=q.message.reply_markup)

    async def finalize(self, pid, mid, context):
        d = active_packets.pop(pid, None)
        if not d: return
        res = [f"🧧 结算结果 (雷:{d['mine']})", "━━━━━━━━━━━━━━"]
        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            self.db.add_balance(g['id'], d["cid"], amt)
            self.db.log_action(g['id'], "抢到红包", amt, 0, d["cid"])
            if is_mine:
                self.db.add_balance(g['id'], d["cid"], -d["total"])
                self.db.log_action(g['id'], "抢包中雷", -d["total"], 1, d["cid"])
                inc = round(d["total"] * (1 - COMMISSION), 2)
                self.db.add_balance(d["owner_id"], d["cid"], inc)
                self.db.log_action(d["owner_id"], f"中雷收入({g['name']})", inc, 0, d["cid"])
            res.append(f"{i+1}. {self.mask_name(g['name'])} -> {amt:.2f} {'💣' if is_mine else ''}")
        if len(d["grabbers"]) < d["count"]: res.append(f"━━━━━━━━━━━━━━\n⚠️ 剩余 {d['count']-len(d['grabbers'])} 个包已退还。")
        await context.bot.edit_message_text(chat_id=d["cid"], message_id=mid, text="\n".join(res))
