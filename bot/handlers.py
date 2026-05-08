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
        return f"{n[0]}*{n[-1]}" if len(n) > 2 else n

    async def verify_owner(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        if not group_switch.get(cid): return False, "⚠️ 机器人未开启。"
        try:
            o = await context.bot.get_chat_member(cid, OWNER_ID)
            if o.status not in ['administrator', 'creator']: return False, "❌ 权限熔断。"
        except: return False, "❌ 权限熔断。"
        return True, ""

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = update.message.text; user = update.effective_user
        if "total_assets_" in txt:
            cid = txt.split("total_assets_")[-1]
            data = self.db.get_all_balances(cid)
            if not data: return await update.message.reply_text("暂无数据。")
            rep = f"💰 【本群总账】\n总持分：{sum(b for n,b in data):.2f}\n" + "\n".join([f"{i+1}. {self.mask_name(n)} -> {b:.2f}" for i, (n, b) in enumerate(data[:20])])
            return await update.message.reply_text(rep)
        await update.message.reply_text("🤖 扫雷系统就绪。")

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
            return await msg.reply_text("💤 系统已休眠。")

        # 规则设置
        conf_match = re.match(r'^金额(\d+)~(\d+)/包数(\d+)~(\d+)$', txt)
        if conf_match:
            m = await context.bot.get_chat_member(cid, user.id)
            if m.status in ['administrator', 'creator'] or user.id == OWNER_ID:
                mina, maxa, minc, maxc = map(int, conf_match.groups())
                self.db.set_config(cid, mina, maxa, minc, maxc)
                return await msg.reply_text(f"⚙️ 规则成功：金额{mina}~{maxa}，包数{minc}~{maxc}")

        if txt == "查询":
            return await msg.reply_text(f"💰 余额：{self.db.get_balance(user.id, cid):.2f}")

        ready, alert = await self.verify_owner(update, context)
        if not ready:
            if re.match(r'^\d+/', txt) or txt in ["账单", "总账"]: return await msg.reply_text(alert)
            return

        m = await context.bot.get_chat_member(cid, user.id); is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

        if txt == "总账" and is_adm:
            kb = [[InlineKeyboardButton("📩 私聊查看", url=f"t.me/{context.bot.username}?start=total_assets_{cid}")]]
            return await msg.reply_text("🔒 点击下方查看总账。", reply_markup=InlineKeyboardMarkup(kb))

        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user; num = float(txt.replace(' ', ''))
                self.db.add_balance(target.id, cid, num, target.first_name)
                return await msg.reply_text(f"✅ {target.first_name} 余额已更新")

        # 发包逻辑
        p_match = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if p_match:
            amt, count, mine = float(p_match.group(1)), int(p_match.group(2)) if p_match.group(2) else 10, int(p_match.group(3))
            mina, maxa, minc, maxc = self.db.get_config(cid)
            if not (mina <= amt <= maxa) or not (minc <= count <= maxc): return await msg.reply_text("❌ 规则不符")
            if self.db.get_balance(user.id, cid) < amt: return await msg.reply_text("❌ 余额不足")
            self.db.add_balance(user.id, cid, -amt, user.first_name)
            pid = f"pk_{int(time.time()*1000)}"
            task = asyncio.create_task(self.timer_refund(pid, cid, context))
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, count), "count": count, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid, "task": task}
            cap = f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{user.first_name}\n金额：{amt} | 雷：{mine} | 包：{count}\n━━━━━━━━━━━━━━\n等待入场..."
            try:
                with open("cover.jpg", "rb") as f:
                    sent = await context.bot.send_photo(chat_id=cid, photo=f, caption=cap, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧧 立即抢包", callback_data=f"grab_{pid}")]]))
                active_packets[pid]["mid"] = sent.message_id
            except: pass

    async def timer_refund(self, pid, cid, context):
        await asyncio.sleep(600)
        if pid in active_packets:
            d = active_packets[pid]; grabbed = len(d["grabbers"]); unclaimed = sum(d["amounts"][grabbed:])
            if unclaimed > 0:
                self.db.add_balance(d["owner_id"], cid, unclaimed, d["owner_name"])
                await context.bot.send_message(cid, f"⏰ 【{d['owner_name']}】的红包到期，已退还：{unclaimed:.2f}")
            await self.finalize(pid, d["mid"], context)

    def gen_amts(self, total, count):
        amts = []; rem = total
        for i in range(count - 1):
            a = round(random.uniform(0.01, (rem / (count - i)) * 2), 2); amts.append(a); rem -= a
        amts.append(round(rem, 2)); random.shuffle(amts); return amts

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; u = q.from_user; pid = q.data.replace("grab_", "")
        if pid not in active_packets: return await q.answer("失效")
        d = active_packets[pid]
        if self.db.get_balance(u.id, d["cid"]) < d["total"]: return await q.answer(f"持分不足{d['total']}", show_alert=True)
        if any(g['id'] == u.id for g in d["grabbers"]): return await q.answer("已抢过")
        d["grabbers"].append({"id": u.id, "name": u.first_name})
        if len(d["grabbers"]) >= d["count"]:
            d["task"].cancel(); await self.finalize(pid, q.message.message_id, context)
        else:
            header = f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{d['owner_name']}\n金额：{d['total']} | 雷：{d['mine']} | 包：{d['count']}\n━━━━━━━━━━━━━━\n"
            name_list = []
            for i, g in enumerate(d["grabbers"]):
                m_name = self.mask_name(g['name'])
                if i == d["count"] - 2: name_list.append(f"{i+1}. {m_name} -> 🔐 防计算中...")
                else:
                    amt = d["amounts"][i]; is_m = (int(str(amt)[-1]) == d["mine"])
                    name_list.append(f"{i+1}. {m_name} -> {amt:.2f} {'💣' if is_m else ''}")
            await context.bot.edit_message_caption(chat_id=d["cid"], message_id=q.message.message_id, caption=header + "\n".join(name_list), reply_markup=q.message.reply_markup)

    async def finalize(self, pid, mid, context):
        d = active_packets.pop(pid, None)
        if not d: return
        res = [f"🧧 结算结果 (雷:{d['mine']})", f"发包：{d['owner_name']} | 金额：{d['total']}", "━━━━━━━━━━━━━━"]
        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            self.db.add_balance(g['id'], d["cid"], amt, g['name'])
            if is_mine:
                self.db.add_balance(g['id'], d["cid"], -d["total"], g['name'])
                self.db.add_balance(d["owner_id"], d["cid"], round(d["total"] * 0.95, 2), d["owner_name"])
            res.append(f"{i+1}. {self.mask_name(g['name'])} -> {amt:.2f} {'💣' if is_mine else ''}")
        await context.bot.edit_message_caption(chat_id=d["cid"], message_id=mid, caption="\n".join(res))
