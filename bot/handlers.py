import random, re, time, asyncio, sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

# --- 核心配置 ---
OWNER_ID = 7653037721
COMMISSION = 0.05 
active_packets = {}
group_switch = {}

class BotHandlers:
    def __init__(self, db):
        self.db = db

    def mask_name(self, name):
        """名字脱敏：2字内原样显示，长名 万*手"""
        if not name: return "*"
        n = str(name)
        if len(n) <= 2: return n 
        return f"{n}*{n[-1]}"

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
            target_cid = txt.split("all_logs_")[-1]
            logs = self.db.get_user_logs(update.effective_user.id, target_cid, 100)
            if not logs: return await update.message.reply_text("该群暂无账单记录。")
            rep = "📊 【全量明细】\n" + "\n".join([f"• {t[5:16]} {act} {'+' if amt>0 else ''}{amt:.2f}" for t, act, amt, mine in logs])
            return await update.message.reply_text(rep)
        if "total_assets_" in txt:
            target_cid = txt.split("total_assets_")[-1]
            data = self.db.get_all_balances(target_cid)
            if not data: return await update.message.reply_text("该群暂无数据。")
            total_sum = sum(b for u, b in data)
            rep = f"💰 【总账】总持分：{total_sum:.2f}\n" + "\n".join([f"{i+1}. ID:{u}->{b:.2f}" for i, (u, b) in enumerate(data[:20])])
            return await update.message.reply_text(rep)
        await update.message.reply_text("🤖 扫雷系统已就绪。")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; chat = update.effective_chat
        if not msg or not msg.text: return
        txt = msg.text.strip(); cid = chat.id
        if chat.type == "private" and not txt.startswith("/start"): return

        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            return await msg.reply_text("✅ 机器人已启动！")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False
            return await msg.reply_text("💤 机器人已休眠。")

        conf_match = re.match(r'^金额(\d+)~(\d+)/包数(\d+)~(\d+)$', txt)
        if conf_match:
            m = await context.bot.get_chat_member(cid, user.id)
            if m.status in ['administrator', 'creator'] or user.id == OWNER_ID:
                mina, maxa, minc, maxc = map(int, conf_match.groups())
                self.db.set_config(cid, mina, maxa, minc, maxc)
                return await msg.reply_text(f"⚙️ 规则设置成功！\n💰 金额：{mina}~{maxa}\n📦 包数：{minc}~{maxc}")

        if txt == "查询":
            return await msg.reply_text(f"💰 余额：{self.db.get_balance(user.id, cid):.2f}")

        if txt == "我的流水":
            logs = self.db.get_user_logs(user.id, cid, 20)
            rep = f"👤 【{user.first_name}】近20条流水\n" + "\n".join([f"• {t[11:16]} {act} {'+' if amt>0 else ''}{amt:.2f}" for t, act, amt, mine in logs])
            kb = [[InlineKeyboardButton("📩 全部流水", url=f"t.me/{context.bot.username}?start=all_logs_{cid}")]]
            return await msg.reply_text(rep, reply_markup=InlineKeyboardMarkup(kb))

        ready, alert = await self.verify_owner(update, context)
        if not ready:
            if re.match(r'^\d+/', txt) or txt in ["账单", "总账"]: return await msg.reply_text(alert)
            return

        m = await context.bot.get_chat_member(cid, user.id); is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

        if txt == "账单" and is_adm:
            ts, tm = self.db.get_group_stats(cid)
            conn = sqlite3.connect("bot_data.db")
            pay_t = conn.execute("SELECT SUM(amount) FROM logs WHERE action LIKE '中雷收入%' AND chat_id=?", (str(cid),)).fetchone()[0] or 0
            conn.close()
            rep = f"📊 【账单】总发包:{ts:.2f}\n系统净利:{(pay_t/0.95)*0.05:.2f}"
            try: await context.bot.send_message(user.id, rep); await msg.reply_text("✅ 账单已私信。")
            except: await msg.reply_text("❌ 请先私聊机器人。")
            return

        if txt == "总账" and is_adm:
            kb = [[InlineKeyboardButton("📩 私聊查看", url=f"t.me/{context.bot.username}?start=total_assets_{cid}")]]
            return await msg.reply_text("🔒 点击下方查看总账。", reply_markup=InlineKeyboardMarkup(kb))

        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user; num = float(txt.replace(' ', ''))
                self.db.add_balance(target.id, cid, num); self.db.log_action(target.id, "上分", num, 0, cid)
                return await msg.reply_text(f"✅ {target.first_name} 余额：{self.db.get_balance(target.id, cid):.2f}")

        # 发包逻辑 (带封面图)
        p_match = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if p_match:
            amt, count, mine = float(p_match.group(1)), int(p_match.group(2)) if p_match.group(2) else 10, int(p_match.group(3))
            mina, maxa, minc, maxc = self.db.get_config(cid)
            if not (mina <= amt <= maxa) or not (minc <= count <= maxc): return await msg.reply_text("❌ 规则不符")
            if self.db.get_balance(user.id, cid) < amt: return await msg.reply_text("❌ 余额不足")
            
            self.db.add_balance(user.id, cid, -amt); self.db.log_action(user.id, "发包", -amt, 0, cid)
            pid = f"pk_{int(time.time()*1000)}"
            task = asyncio.create_task(self.timer_refund(pid, cid, context))
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, count), "count": count, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid, "task": task}
            
            caption = f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{user.first_name}\n金额：{amt} | 雷：{mine} | 包：{count}\n━━━━━━━━━━━━━━\n等待入场... (0/{count})"
            kb = [[InlineKeyboardButton("🧧 立即抢包", callback_data=f"grab_{pid}")]]
            
            try:
                with open("cover.jpg", "rb") as f:
                    sent = await context.bot.send_photo(chat_id=cid, photo=f, caption=caption, reply_markup=InlineKeyboardMarkup(kb))
                active_packets[pid]["mid"] = sent.message_id
            except:
                sent = await msg.reply_text(caption, reply_markup=InlineKeyboardMarkup(kb))
                active_packets[pid]["mid"] = sent.message_id

    async def timer_refund(self, pid, cid, context):
        await asyncio.sleep(600)
        if pid in active_packets:
            d = active_packets[pid]; grabbed = len(d["grabbers"]); unclaimed = sum(d["amounts"][grabbed:])
            if unclaimed > 0:
                self.db.add_balance(d["owner_id"], cid, unclaimed); self.db.log_action(d["owner_id"], "过期退还", unclaimed, 0, cid)
                await context.bot.send_message(cid, f"⏰ 红包到期，已退还发包者：{unclaimed:.2f}")
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
        
        d["grabbers"].append({"id": u.id, "name": u.first_name}); grabbed_num = len(d["grabbers"])
        await q.answer("✅ 抢包成功！")

        if grabbed_num >= d["count"]: 
            d["task"].cancel(); await self.finalize(pid, q.message.message_id, context)
        else:
            name_list = []
            for i, g in enumerate(d["grabbers"]):
                m_name = self.mask_name(g['name'])
                if i == d["count"] - 2: name_list.append(f"{i+1}. {m_name} 已抢，等待开奖")
                else:
                    amt = d["amounts"][i]; is_m = (int(str(amt)[-1]) == d["mine"])
                    name_list.append(f"{i+1}. {m_name} -> {amt:.2f} {'💣' if is_m else ''}")
            await context.bot.edit_message_caption(chat_id=d["cid"], message_id=q.message.message_id, caption=f"🧧 扫雷中 ({grabbed_num}/{d['count']})\n━━━━━━━━━━━━━━\n" + "\n".join(name_list), reply_markup=q.message.reply_markup)

    async def finalize(self, pid, mid, context):
        d = active_packets.pop(pid, None)
        if not d: return
        res = [f"🧧 结算结果 (雷:{d['mine']})", "━━━━━━━━━━━━━━"]
        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            self.db.add_balance(g['id'], d["cid"], amt); self.db.log_action(g['id'], "抢包", amt, 0, d["cid"])
            if is_mine:
                self.db.add_balance(g['id'], d["cid"], -d["total"]); self.db.log_action(g['id'], "中雷", -d["total"], 1, d["cid"])
                inc = round(d["total"] * (1 - COMMISSION), 2)
                self.db.add_balance(d["owner_id"], d["cid"], inc); self.db.log_action(d["owner_id"], f"中雷收入({g['name']})", inc, 0, d["cid"])
            res.append(f"{i+1}. {self.mask_name(g['name'])} -> {amt:.2f} {'💣' if is_mine else ''}")
        if len(d["grabbers"]) < d["count"]: res.append(f"━━━━━━━━━━━━━━\n⚠️ 剩余 {d['count']-len(d['grabbers'])} 个包已退还。")
        await context.bot.edit_message_caption(chat_id=d["cid"], message_id=mid, caption="\n".join(res))
