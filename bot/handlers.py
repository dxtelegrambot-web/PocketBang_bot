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
            if o.status not in ['administrator', 'creator']: 
                return False, "❌ 权限熔断：拥有者失去管理权，机器人已宕机。请恢复权限后发送“开启”。"
        except: return False, "❌ 权限熔断：拥有者不在群内，机器人已停机。"
        return True, ""

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = update.message.text; user = update.effective_user
        if "all_logs_" in txt:
            cid = txt.split("all_logs_")[-1]
            # 修正：只取群名
            conf = self.db.get_config(cid)[-1]
            logs = self.db.get_user_logs(user.id, cid, 100)
            rep = f"📊 【全部明细】群:{conf}\n━━━━━━━━━━━━━━\n"
            # 修正：去除链接显示
            for t, act, amt in logs:
                clean_act = re.sub(r'\[🔗\].*?\)', '', act).strip()
                rep += f"• {t[5:16]} {clean_act} {amt:+.2f}\n"
            return await update.message.reply_text(rep if logs else "暂无记录")
        
        if "total_assets_" in txt:
            cid = txt.split("total_assets_")[-1]
            conf = self.db.get_config(cid)[-1]
            data = self.db.get_all_balances(cid)
            rep = f"💰 【{conf}】总账单\n总持分：{sum(b for n,b in data):.2f}\n━━━━━━━━━━━━━━\n"
            rep += "\n".join([f"{i+1}. {self.mask_name(n)} -> {b:.2f}" for i, (n, b) in enumerate(data[:20])])
            return await update.message.reply_text(rep if data else "暂无数据")
        
        if "stats_" in txt:
            parts = txt.split("_")
            cid = parts[1]; page = int(parts[2]) if len(parts) > 2 else 0; offset = page * 20
            conf = self.db.get_config(cid)[-1]
            with sqlite3.connect(self.db.db_path) as conn:
                ts = conn.execute("SELECT SUM(ABS(amount)) FROM logs WHERE action='发包' AND chat_id=?", (str(cid),)).fetchone()[0] or 0
                all_mines = conn.execute("SELECT timestamp, action, amount FROM logs WHERE action LIKE '中雷收入%' AND chat_id=? ORDER BY id DESC", (str(cid),)).fetchall()
            pay_total = sum(item[2] for item in all_mines)
            real_profit = (pay_total / 0.95) * 0.05 if pay_total > 0 else 0
            rep = (f"📊 【{conf}】详细账单\n━━━━━━━━━━━━━━\n发包总额：{ts:.2f}\n中雷总赔付：{pay_total / 0.95:.2f}\n系统净抽成：{real_profit:.2f}\n\n📜 中雷明细(每页20条)：\n")
            page_data = all_mines[offset:offset+20]
            for i, (t, act, amt) in enumerate(page_data):
                from_name = re.sub(r'\[🔗\].*?\)', '', act).replace("中雷收入(来自", "").replace(")", "").strip()
                hb_amt = amt / 0.95; fee = hb_amt * 0.05
                rep += f"{offset+i+1}. {t[5:16]}+{from_name}+发{hb_amt:.0f}+抽{fee:.2f}\n"
            btns = []
            if page > 0: btns.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"page_{cid}_{page-1}"))
            if len(all_mines) > offset + 20: btns.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"page_{cid}_{page+1}"))
            kb = [btns] if btns else None
            return await update.message.reply_text(rep, reply_markup=InlineKeyboardMarkup(kb) if kb else None)
        await update.message.reply_text("🤖 扫雷系统就绪。")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; chat = update.effective_chat
        if not msg or not msg.text: return
        txt = msg.text.strip(); cid = chat.id; gname = chat.title or "未知群组"
        if chat.type == "private" and not txt.startswith("/start"): return

        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            old = self.db.get_config(cid)
            self.db.set_config(cid, old[0], old[1], old[2], old[3], gname)
            return await msg.reply_text("✅ 机器人已启动！")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False
            return await msg.reply_text("💤 机器人已休眠。")

        conf_match = re.match(r'^金额(\d+)~(\d+)/包数(\d+)~(\d+)$', txt)
        if conf_match:
            m = await context.bot.get_chat_member(cid, user.id)
            if m.status in ['administrator', 'creator'] or user.id == OWNER_ID:
                mina, maxa, minc, maxc = map(float, conf_match.groups())
                self.db.set_config(cid, mina, maxa, int(minc), int(maxc), gname)
                return await msg.reply_text(f"⚙️ 规则设置成功！")

        if txt == "查询":
            return await msg.reply_text(f"💰 余额：{self.db.get_balance(user.id, cid):.2f}")

        if txt == "我的流水":
            logs = self.db.get_user_logs(user.id, cid, 20)
            rep = f"👤 【{user.first_name}】本群流水\n"
            for t, act, amt in logs:
                clean_act = re.sub(r'\[🔗\].*?\)', '', act).strip()
                rep += f"• {t[11:16]} {clean_act} {amt:+.2f}\n"
            kb = [[InlineKeyboardButton("📩 查看全部", url=f"t.me/{context.bot.username}?start=all_logs_{cid}")]]
            return await msg.reply_text(rep, reply_markup=InlineKeyboardMarkup(kb))

        ready, alert = await self.verify_owner(update, context)
        if not ready:
            if re.match(r'^\d+/', txt) or txt in ["账单", "总账"]: return await msg.reply_text(alert)
            return

        m = await context.bot.get_chat_member(cid, user.id); is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

        if txt == "账单" and is_adm:
            kb = [[InlineKeyboardButton("📩 私聊查看账单", url=f"t.me/{context.bot.username}?start=stats_{cid}_0")]]
            return await msg.reply_text("🔒 安全校验：点击查看详细账单。", reply_markup=InlineKeyboardMarkup(kb))
        if txt == "总账" and is_adm:
            kb = [[InlineKeyboardButton("📩 私聊查看总账", url=f"t.me/{context.bot.username}?start=total_assets_{cid}")]]; return await msg.reply_text("🔒 点击私聊查看。", reply_markup=InlineKeyboardMarkup(kb))

        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user; num = float(txt.replace(' ', ''))
                self.db.add_balance(target.id, cid, num, target.first_name); self.db.log_action(target.id, "人工上下分", num, cid)
                return await msg.reply_text(f"✅ {target.first_name} 余额：{self.db.get_balance(target.id, cid):.2f}")

        p_match = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if p_match:
            amt, count, mine = float(p_match.group(1)), int(p_match.group(2)) if p_match.group(2) else 10, int(p_match.group(3))
            conf = self.db.get_config(cid)
            if not (conf[0] <= amt <= conf[1]) or not (conf[2] <= count <= conf[3]): return await msg.reply_text("❌ 规则不符")
            if self.db.get_balance(user.id, cid) < amt: return await msg.reply_text("❌ 余额不足")
            self.db.add_balance(user.id, cid, -amt, user.first_name); self.db.log_action(user.id, "发包", -amt, cid)
            pid = f"pk_{int(time.time()*1000)}"
            task = asyncio.create_task(self.timer_refund(pid, cid, context))
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, count), "count": count, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid, "task": task}
            cap = f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{user.first_name}\n金额：{amt} | 雷：{mine} | 包：{count}\n━━━━━━━━━━━━━━\n等待抢包..."
            kb = [[InlineKeyboardButton("🧧 立即抢包", callback_data=f"grab_{pid}")]]
            try:
                with open("cover.jpg", "rb") as f: sent = await context.bot.send_photo(chat_id=cid, photo=f, caption=cap, reply_markup=InlineKeyboardMarkup(kb))
                active_packets[pid]["mid"] = sent.message_id
            except: sent = await msg.reply_text(cap, reply_markup=InlineKeyboardMarkup(kb)); active_packets[pid]["mid"] = sent.message_id

    async def timer_refund(self, pid, cid, context):
        await asyncio.sleep(300)
        if pid in active_packets:
            d = active_packets[pid]; grabbed = len(d["grabbers"]); unclaimed = sum(d["amounts"][grabbed:])
            if unclaimed > 0:
                self.db.add_balance(d["owner_id"], cid, unclaimed, d["owner_name"]); self.db.log_action(d["owner_id"], "过期退还", unclaimed, cid)
                await context.bot.send_message(cid, f"⏰ 【{d['owner_name']}】的红包到期，已退还：{unclaimed:.2f}")
            await self.finalize(pid, d["mid"], context)

    def gen_amts(self, total, count):
        amts = []; rem = total
        for i in range(count - 1):
            a = round(random.uniform(0.01, (rem / (count - i)) * 2), 2); amts.append(a); rem -= a
        amts.append(round(rem, 2)); random.shuffle(amts); return amts

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; u = q.from_user; pid = q.data.replace("grab_", "")
        if pid.startswith("page_"):
            p_parts = pid.split("_")
            q.message.text = f"/start stats_{p_parts[1]}_{p_parts[2]}"
            return await self.handle_start(update, context)
        if pid not in active_packets: return await q.answer("失效")
        d = active_packets[pid]
        if self.db.get_balance(u.id, d["cid"]) < d["total"]: return await q.answer(f"持分不足{d['total']}", show_alert=True)
        if any(g['id'] == u.id for g in d["grabbers"]): return await q.answer("已抢过")
        d["grabbers"].append({"id": u.id, "name": u.first_name}); grabbed_num = len(d["grabbers"])
        await q.answer("✅ 抢包成功！")
        header = f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{d['owner_name']}\n金额：{d['total']} | 雷：{d['mine']} | 包：{d['count']}\n━━━━━━━━━━━━━━\n"
        if grabbed_num >= d["count"]: 
            d["task"].cancel(); await self.finalize(pid, q.message.message_id, context)
        else:
            name_list = []
            for i, g in enumerate(d["grabbers"]):
                m_n = self.mask_name(g['name'])
                if i == d["count"] - 2: name_list.append(f"{i+1}. {m_n} 已抢，等待开奖")
                else:
                    amt = d["amounts"][i]; is_m = (int(str(amt)[-1]) == d["mine"])
                    name_list.append(f"{i+1}. {m_n} -> {amt:.2f} {'💣' if is_m else '🧧'}")
            await context.bot.edit_message_caption(chat_id=d["cid"], message_id=q.message.message_id, caption=header + "\n".join(name_list), reply_markup=q.message.reply_markup)

    async def finalize(self, pid, mid, context):
        d = active_packets.pop(pid, None)
        if not d: return
        res = [f"🧧 结算结果 (雷:{d['mine']})", f"发包：{d['owner_name']} | 金额：{d['total']}", "━━━━━━━━━━━━━━"]
        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            self.db.add_balance(g['id'], d["cid"], amt, g['name']); self.db.log_action(g['id'], "抢包", amt, d["cid"])
            if is_mine:
                self.db.add_balance(g['id'], d["cid"], -d["total"], g['name']); self.db.log_action(g['id'], "抢包中雷", -d["total"], d["cid"])
                inc = round(d["total"] * 0.95, 2); self.db.add_balance(d["owner_id"], d["cid"], inc, d["owner_name"]); 
                self.db.log_action(d["owner_id"], f"中雷收入(来自{g['name']})", inc, d["cid"])
            res.append(f"{i+1}. {self.mask_name(g['name'])} -> {amt:.2f} {'💣' if is_mine else '🧧'}")
        if len(d["grabbers"]) < d["count"]: res.append(f"━━━━━━━━━━━━━━\n⚠️ 剩余包数已退还。")
        await context.bot.edit_message_caption(chat_id=d["cid"], message_id=mid, caption="\n".join(res))
