import random, re, time, asyncio, sqlite3
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
            target_cid = txt.split("all_logs_")[-1]
            logs = self.db.get_user_logs(update.effective_user.id, target_cid, 50)
            if not logs: return await update.message.reply_text("暂无账单记录。")
            rep = f"📊 【个人账单】\n" + "\n".join([f"• {t[5:16]} {act} {'+' if amt>0 else ''}{amt:.2f}" for t, act, amt, mine in logs])
            return await update.message.reply_text(rep)
        await update.message.reply_text("🤖 扫雷系统已就绪。")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; cid = update.effective_chat.id
        if not msg or not msg.text: return
        txt = msg.text.strip()

        # 1. 开启/关闭
        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            return await msg.reply_text("✅ 机器人已启动！\n请设置规则，格式如：\n金额50~1000/包数10~20")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False
            return await msg.reply_text("💤 系统已休眠。")

        # 2. 设置规则指令 (仅管理员)
        conf_match = re.match(r'^金额(\d+)~(\d+)/包数(\d+)~(\d+)$', txt)
        if conf_match:
            m = await context.bot.get_chat_member(cid, user.id)
            if m.status in ['administrator', 'creator'] or user.id == OWNER_ID:
                mina, maxa, minc, maxc = map(int, conf_match.groups())
                self.db.set_config(cid, mina, maxa, minc, maxc)
                return await msg.reply_text(f"⚙️ 规则设置成功！\n💰 金额：{mina}~{maxa}\n📦 包数：{minc}~{maxc}")

        ready, alert = await self.verify_owner(update, context)
        if not ready and (re.match(r'^\d+/', txt) or txt in ["查询", "账单"]):
            return await msg.reply_text(alert)

        m = await context.bot.get_chat_member(cid, user.id)
        is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

        # 3. 基础指令
        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user
                num = float(txt.replace(' ', ''))
                self.db.add_balance(target.id, cid, num)
                self.db.log_action(target.id, "人工上下分", num, 0, cid)
                return await msg.reply_text(f"✅ {target.first_name} 余额：{self.db.get_balance(target.id, cid):.2f}")

        if txt == "查询":
            return await msg.reply_text(f"💰 余额：{self.db.get_balance(user.id, cid):.2f}")

        if txt == "账单" and is_adm:
            ts, tm = self.db.get_group_stats(cid)
            # 这里的数据库路径需要根据 main.py 里的位置确定，默认为主目录
            conn = sqlite3.connect("bot_data.db")
            pay_total = conn.execute("SELECT SUM(amount) FROM logs WHERE action LIKE '中雷收入%' AND chat_id=?", (str(cid),)).fetchone()[0] or 0
            conn.close()
            real_profit = (pay_total / 0.95) * 0.05 if pay_total > 0 else 0
            rep = (f"📊 【{msg.chat.title}】账单\n━━━━━━━━━━━━━━\n发包总额：{ts:.2f}\n中雷次数：{tm}\n中雷总赔付：{pay_total / 0.95:.2f}\n系统净利润：{real_profit:.2f}")
            await context.bot.send_message(user.id, rep)
            return await msg.reply_text("✅ 详细账单已私信。")

        # 4. 发红包逻辑 (核心：校验自定义规则)
        packet_match = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if packet_match:
            amt = float(packet_match.group(1)); count = int(packet_match.group(2)) if packet_match.group(2) else 10; mine = int(packet_match.group(3))
            mina, maxa, minc, maxc = self.db.get_config(cid)
            
            if not (mina <= amt <= maxa):
                return await msg.reply_text(f"❌ 金额不符！本群限制：{mina}~{maxa}")
            if not (minc <= count <= maxc):
                return await msg.reply_text(f"❌ 包数不符！本群限制：{minc}~{maxc}")
            if self.db.get_balance(user.id, cid) < amt:
                return await msg.reply_text("❌ 余额不足")

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
        if self.db.get_balance(u.id, d["cid"]) < d["total"]: return await q.answer(f"持分不足{d['total']}", show_alert=True)
        if any(g['id'] == u.id for g in d["grabbers"]): return await q.answer("已抢过")
        
        d["grabbers"].append({"id": u.id, "name": u.first_name})
        if len(d["grabbers"]) >= d["count"]: await self.finalize(pid, q.message.message_id, context)
        else:
            name_list = []
            for i, g in enumerate(d["grabbers"]):
                m_name = self.mask_name(g['name'])
                if i == d["count"] - 2: name_list.append(f"{i+1}. {m_name} 已抢，等待开奖")
                else:
                    amt = d["amounts"][i]; is_m = (int(str(amt)[-1]) == d["mine"])
                    name_list.append(f"{i+1}. {m_name} -> {amt:.2f} {'💣' if is_m else ''}")
            await q.edit_message_text(text=f"🧧 扫雷\n━━━━━━━━━━━━━━\n发包：{d['owner_name']}\n金额：{d['total']} | 雷：{d['mine']} | 包：{d['count']}\n━━━━━━━━━━━━━━\n" + "\n".join(name_list), reply_markup=q.message.reply_markup)

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
        await context.bot.edit_message_text(chat_id=d["cid"], message_id=mid, text="\n".join(res))
