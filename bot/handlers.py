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
        """名字脱敏逻辑：2字内原样显示，长名万***手"""
        if not name: return "*"
        n = str(name)
        if len(n) <= 2: return n 
        return f"{n[0]}*{n[-1]}"

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
            if not logs: return await update.message.reply_text("该群暂无你的账单记录。")
            rep = f"📊 【全量明细】\n" + "\n".join([f"• {t[5:16]} {act} {'+' if amt>0 else ''}{amt:.2f}" for t, act, amt, mine in logs])
            return await update.message.reply_text(rep)
        await update.message.reply_text("🤖 扫雷系统已就绪。")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; cid = update.effective_chat.id
        if not msg or not msg.text: return
        txt = msg.text.strip()

        # 1. 开启/关闭指令
        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            return await msg.reply_text("✅ 机器人已启动！\n请设置本群规则，例如发送：\n金额50~1000/包数10~20")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False
            return await msg.reply_text("💤 系统已休眠。")

        # 2. 规则设置指令
        conf_match = re.match(r'^金额(\d+)~(\d+)/包数(\d+)~(\d+)$', txt)
        if conf_match:
            m = await context.bot.get_chat_member(cid, user.id)
            if m.status in ['administrator', 'creator'] or user.id == OWNER_ID:
                mina, maxa, minc, maxc = map(int, conf_match.groups())
                self.db.set_config(cid, mina, maxa, minc, maxc)
                return await msg.reply_text(f"⚙️ 规则设置成功！\n💰 金额：{mina}~{maxa}\n📦 包数：{minc}~{maxc}")

        # 3. 基础指令（查询、流水）- 放在熔断检查前，保证用户体验
        if txt == "查询":
            bal = self.db.get_balance(user.id, cid)
            return await msg.reply_text(f"💰 本群余额：{bal:.2f}")

        if txt == "我的流水":
            logs = self.db.get_user_logs(user.id, cid, 20)
            if not logs: return await msg.reply_text("暂无账单记录。")
            rep = f"👤 【{user.first_name}】本群流水\n" + "\n".join([f"• {t[11:16]} {act} {'+' if amt>0 else ''}{amt:.2f}" for t, act, amt, mine in logs])
            kb = [[InlineKeyboardButton("📩 查看全部流水", url=f"t.me/{context.bot.username}?start=all_logs_{cid}")]]
            return await msg.reply_text(rep, reply_markup=InlineKeyboardMarkup(kb))

        # 4. 权限熔断检查
        ready, alert = await self.verify_owner(update, context)
        if not ready:
            if re.match(r'^\d+/', txt) or txt in ["账单", "总计"]: return await msg.reply_text(alert)
            return

        m = await context.bot.get_chat_member(cid, user.id)
        is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

        # 5. 管理员财务指令
        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user
                try:
                    num = float(txt.replace(' ', ''))
                    self.db.add_balance(target.id, cid, num)
                    self.db.log_action(target.id, "人工上下分", num, 0, cid)
                    return await msg.reply_text(f"✅ {target.first_name} 余额：{self.db.get_balance(target.id, cid):.2f}")
                except: pass

        if txt == "账单" and is_adm:
            ts, tm = self.db.get_group_stats(cid)
            conn = sqlite3.connect("bot_data.db")
            pay_total = conn.execute("SELECT SUM(amount) FROM logs WHERE action LIKE '中雷收入%' AND chat_id=?", (str(cid),)).fetchone()[0] or 0
            conn.close()
            real_profit = (pay_total / 0.95) * 0.05 if pay_total > 0 else 0
            rep = (f"📊 【{msg.chat.title}】账单\n━━━━━━━━━━━━━━\n发包总额：{ts:.2f}\n中雷次数：{tm}\n中雷总赔付：{pay_total / 0.95:.2f}\n系统净利润：{real_profit:.2f}")
            try: await context.bot.send_message(user.id, rep); await msg.reply_text("✅ 详细账单已私信。")
            except: await msg.reply_text("❌ 请先私聊机器人点 /start")
            return

        if txt == "总计" and is_adm:
            data = self.db.get_all_balances(cid)
            rep = "💰 【资产排行】\n" + "\n".join([f"{i+1}. ID:{u} -> {b:.2f}" for i, (u, b) in enumerate(data[:10])])
            return await msg.reply_text(rep)

        # 6. 发红包逻辑
        packet_match = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if packet_match:
            amt = float(packet_match.group(1)); count = int(packet_match.group(2)) if packet_match.group(2) else 10; mine = int(packet_match.group(3))
            mina, maxa, minc, maxc = self.db.get_config(cid)
            if not (mina <= amt <= maxa) or not (minc <= count <= maxc):
                return await msg.reply_text(f"❌ 不符合群规则！\n限制：金额{mina}~{maxa}/包数{minc}~{maxc}")
            if self.db.get_balance(user.id, cid) < amt: return await msg.reply_text("❌ 余额不足")
            
            self.db.add_balance(user.id, cid, -amt)
            self.db.log_action(user.id, "发包", -amt, 0, cid)
            pid = f"pk_{int(time.time()*1000)}"
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, count), "count": count, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid}
            return await msg.reply_text(f"🧧 {amt}/{count} 雷:{mine}\n等待抢包...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧧 立即抢包", callback_data=f"grab_{pid}")]]))

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
            await self.finalize(pid, q.message.message_id, context)
        else:
            name_list = []
            for i, g in enumerate(d["grabbers"]):
                m_name = self.mask_name(g['name'])
                # 倒数第二个包隐藏金额逻辑
                if i == d["count"] - 2: name_list.append(f"{i+1}. {m_name} -> 🔐 防计算中...")
                else:
                    amt = d["amounts"][i]; is_m = (int(str(amt)[-1]) == d["mine"])
                    name_list.append(f"{i+1}. {m_name} -> {amt:.2f} {'💣' if is_m else ''}")
            await q.edit_message_text(text=f"🧧 扫雷中 ({grabbed_num}/{d['count']})\n━━━━━━━━━━━━━━\n" + "\n".join(name_list), reply_markup=q.message.reply_markup)

    async def finalize(self, pid, mid, context):
        d = active_packets.pop(pid, None)
        if not d: return
        res = [f"🧧 结算 (雷:{d['mine']})", "━━━━━━━━━━━━━━"]
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
        await context.bot.edit_message_text(chat_id=d["cid"], message_id=mid, text="\n".join(res))
