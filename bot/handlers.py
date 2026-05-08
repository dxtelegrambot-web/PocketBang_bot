import random, re, time, asyncio, sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
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
        n = str(name).replace('<', '&lt;').replace('>', '&gt;')
        return f"{n}*{n[-1]}" if len(str(name)) > 2 else n

    async def verify_owner(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        if not group_switch.get(cid): return False, "⚠️ 机器人未开启。"
        try:
            o = await context.bot.get_chat_member(cid, OWNER_ID)
            if o.status not in ['administrator', 'creator']: return False, "❌ 权限熔断。"
        except: return False, "❌ 权限熔断。"
        return True, ""

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = update.message.text if update.message else ""; user = update.effective_user
        if "all_logs_" in txt:
            cid = txt.split("all_logs_")[-1]; logs = self.db.get_user_logs(user.id, cid, 100)
            rep = f"<b>📊 全部明细</b>\n" + "\n".join([f"• {t[5:16]} {act} {amt:+.2f}" for t, act, amt in logs])
            return await update.message.reply_text(rep if logs else "暂无记录", parse_mode=ParseMode.HTML)
        if "total_assets_" in txt:
            cid = txt.split("total_assets_")[-1]; data = self.db.get_all_balances(cid)
            rep = f"<b>💰 持分总账</b>\n" + "\n".join([f"{i+1}. {self.mask_name(n)} -> {b:.2f}" for i, (n, b) in enumerate(data[:20])])
            return await update.message.reply_text(rep if data else "暂无数据", parse_mode=ParseMode.HTML)
        if "stats_" in txt:
            p = txt.split("_"); cid = p; page = int(p) if len(p)>2 else 0; offset = page*20
            with sqlite3.connect(self.db.db_path) as conn:
                all_m = conn.execute("SELECT timestamp, action, amount FROM logs WHERE action LIKE '中雷收入%' AND chat_id=? ORDER BY id DESC", (str(cid),)).fetchall()
            rep = f"<b>📊 详细账单 (页:{page+1})</b>\n━━━━━━━━━━━━━━\n"
            for i, (t, act, amt) in enumerate(all_m[offset:offset+20]):
                nm = re.sub(r'\[🔗\].*?\)', '', act).replace("中雷收入(来自", "").replace(")", "").strip()
                rep += f"{offset+i+1}. {t[5:16]}+{nm}+发{amt/0.95:.0f}+抽{amt/0.95*0.05:.2f}\n"
            btns = [InlineKeyboardButton("⬅️ 上一页", callback_data=f"page_{cid}_{page-1}")] if page > 0 else []
            if len(all_m) > offset+20: btns.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"page_{cid}_{page+1}"))
            m_f = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text
            return await m_f(rep, reply_markup=InlineKeyboardMarkup([btns]) if btns else None, parse_mode=ParseMode.HTML)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; chat = update.effective_chat
        if not msg or not msg.text: return
        txt = msg.text.strip(); cid = chat.id; gname = chat.title or "群组"
        if chat.type == "private": return

        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True; self.db.set_config(cid, 20, 1000, 1, 10, gname)
            return await msg.reply_text("✅ 机器人已启动！")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False; return await msg.reply_text("💤 已休眠。")

        if txt == "查询": return await msg.reply_text(f"💰 余额：{self.db.get_balance(user.id, cid):.2f}")
        if txt == "我的流水":
            logs = self.db.get_user_logs(user.id, cid, 20)
            rep = f"👤 <b>{user.first_name}</b> 流水\n" + "\n".join([f"• {t[11:16]} {act} {amt:+.2f}" for t, act, amt in logs])
            return await msg.reply_text(rep, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 全部明细", url=f"t.me/{context.bot.username}?start=all_logs_{cid}")]]), parse_mode=ParseMode.HTML)

        ready, alert = await self.verify_owner(update, context)
        if not ready:
            if re.match(r'^\d+/', txt) or txt in ["账单", "总账"]: return await msg.reply_text(alert)
            return

        m = await context.bot.get_chat_member(cid, user.id); is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID
        if txt == "账单" and is_adm: return await msg.reply_text("🔒 验证...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 查看账单", url=f"t.me/{context.bot.username}?start=stats_{cid}_0")]]))
        if txt == "总账" and is_adm: return await msg.reply_text("🔒 验证...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 查看总账", url=f"t.me/{context.bot.username}?start=total_assets_{cid}")]]))
        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user; num = float(txt.replace(' ', ''))
                self.db.add_balance(target.id, cid, num, target.first_name); self.db.log_action(target.id, "人工上分", num, cid)
                return await msg.reply_text(f"✅ {target.first_name} 余额：{self.db.get_balance(target.id, cid):.2f}")

        # 发包
        p_m = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if p_m:
            amt, cnt, mine = float(p_m.group(1)), int(p_m.group(2)) if p_m.group(2) else 10, int(p_m.group(3))
            conf = self.db.get_config(cid)
            if self.db.get_balance(user.id, cid) < amt: return await msg.reply_text("❌ 余额不足")
            self.db.add_balance(user.id, cid, -amt, user.first_name); self.db.log_action(user.id, "发包", -amt, cid)
            pid = f"pk_{int(time.time()*1000)}"
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, cnt), "count": cnt, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid, "status": "active"}
            cap = f"🧧 <b>【红包扫雷】</b>\n━━━━━━━━━━━━━━\n发包：{user.first_name}\n金额：{amt} | 雷：{mine} | 包：{cnt}\n━━━━━━━━━━━━━━\n等待入场..."
            kb = [[InlineKeyboardButton("🧧 立即抢包", callback_data=f"grab_{pid}")]]
            try:
                with open("cover.jpg", "rb") as f: sent = await context.bot.send_photo(chat_id=cid, photo=f, caption=cap, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
                active_packets[pid]["mid"] = sent.message_id
            except: 
                sent = await msg.reply_text(cap, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML); active_packets[pid]["mid"] = sent.message_id
            active_packets[pid]["task"] = asyncio.create_task(self.timer_refund(pid, cid, context))

    async def timer_refund(self, pid, cid, context):
        await asyncio.sleep(300)
        if pid in active_packets and active_packets[pid]["status"] == "active":
            d = active_packets[pid]; grabbed = len(d["grabbers"]); unclaimed = sum(d["amounts"][grabbed:])
            if unclaimed > 0:
                self.db.add_balance(d["owner_id"], cid, unclaimed, d["owner_name"]); self.db.log_action(d["owner_id"], "退还", unclaimed, cid)
                await context.bot.send_message(cid, f"⏰ <b>【{d['owner_name']}】</b>的红包到期退还：{unclaimed:.2f}", parse_mode=ParseMode.HTML)
            await self.finalize(pid, d["mid"], context)

    def gen_amts(self, total, count):
        amts = []; rem = total
        for i in range(count - 1):
            a = round(random.uniform(0.01, (rem / (count - i)) * 2), 2); amts.append(a); rem -= a
        amts.append(round(rem, 2)); random.shuffle(amts); return amts

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; u = q.from_user; d_raw = q.data
        if d_raw.startswith("page_"):
            q.message.text = f"/start stats_{d_raw.replace('page_','')}"; return await self.handle_start(update, context)

        pid = d_raw.replace("grab_", "")
        if pid not in active_packets: return await q.answer("❌ 红包失效", show_alert=True)
        d = active_packets[pid]
        if d["status"] != "active": return await q.answer("❌ 红包已抢完", show_alert=True)
        if self.db.get_balance(u.id, d["cid"]) < d["total"]: return await q.answer(f"余额不足{d['total']}", show_alert=True)
        if any(g['id'] == u.id for g in d["grabbers"]): return await q.answer("已抢过", show_alert=True)
        
        # 抢包成功，立即写入
        d["grabbers"].append({"id": u.id, "name": u.first_name})
        await q.answer("✅ 抢包成功！")
        
        grabbed_num = len(d["grabbers"])
        header = f"🧧 <b>【红包扫雷】</b>\n━━━━━━━━━━━━━━\n发包：{d['owner_name']}\n金额：{d['total']} | 雷：{d['mine']} | 包：{d['count']}\n━━━━━━━━━━━━━━\n"
        
        if grabbed_num >= d["count"]: 
            d["status"] = "settling"; d["task"].cancel(); await self.finalize(pid, q.message.message_id, context)
        else:
            name_list = []
            for i, g in enumerate(d["grabbers"]):
                m_n = self.mask_name(g['name'])
                if i == d["count"] - 2:
                    name_list.append(f"{i+1}. {m_n} -> 🔐 防计算中...")
                else:
                    amt = d["amounts"][i]; is_m = (int(str(amt)[-1]) == d["mine"])
                    icon = "💣" if is_m else "🧧"
                    name_list.append(f"{i+1}. {m_n} -> {amt:.2f} {icon}")
            
            try:
                await context.bot.edit_message_caption(
                    chat_id=d["cid"], 
                    message_id=q.message.message_id, 
                    caption=header + "\n".join(name_list), 
                    reply_markup=q.message.reply_markup,
                    parse_mode=ParseMode.HTML
                )
            except Exception: pass

    async def finalize(self, pid, mid, context):
        d = active_packets.pop(pid, None)
        if not d: return
        d["status"] = "finished"
        res = [f"🧧 <b>结算结果 (雷:{d['mine']})</b>", f"发包：{d['owner_name']} | 金额：{d['total']}", "━━━━━━━━━━━━━━"]
        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            self.db.add_balance(g['id'], d["cid"], amt, g['name']); self.db.log_action(g['id'], "抢包", amt, d["cid"])
            if is_mine:
                self.db.add_balance(g['id'], d["cid"], -d["total"], g['name']); self.db.log_action(g['id'], "中雷", -d["total"], d["cid"])
                inc = round(d["total"] * 0.95, 2); self.db.add_balance(d["owner_id"], d["cid"], inc, d["owner_name"])
                self.db.log_action(d["owner_id"], f"中雷收入(来自{g['name']})", inc, d["cid"])
            res.append(f"{i+1}. {self.mask_name(g['name'])} -> {amt:.2f} {'💣' if is_mine else '🧧'}")
        if len(d["grabbers"]) < d["count"]: res.append(f"━━━━━━━━━━━━━━\n⚠️ 剩余包数已过期退还。")
        
        try:
            await context.bot.edit_message_caption(chat_id=d["cid"], message_id=mid, caption="\n".join(res), parse_mode=ParseMode.HTML)
        except: pass
