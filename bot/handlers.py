import random, re, time, asyncio, sqlite3, html
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
        safe_name = html.escape(str(name))
        return f"{safe_name}*{safe_name[-1]}" if len(str(name)) > 2 else safe_name

    async def verify_owner(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        if not group_switch.get(cid): return False, "⚠️ 机器人未开启。"
        try:
            o = await context.bot.get_chat_member(cid, OWNER_ID)
            if o.status not in ['administrator', 'creator']: 
                return False, "❌ 权限熔断：拥有者失去管理权。"
        except: return False, "❌ 权限熔断。"
        return True, ""

    async def get_stats_text(self, cid, page=0):
        """统一的账单文本生成器，支持分页"""
        offset = page * 20
        conf = self.db.get_config(cid)[-1]
        with sqlite3.connect(self.db.db_path) as conn:
            ts = conn.execute("SELECT SUM(ABS(amount)) FROM logs WHERE action='发包' AND chat_id=?", (str(cid),)).fetchone()[0] or 0
            all_m = conn.execute("SELECT timestamp, action, amount FROM logs WHERE action LIKE '中雷收入%' AND chat_id=? ORDER BY id DESC", (str(cid),)).fetchall()
        
        pay_t = sum(x[2] for x in all_m) / 0.95 if all_m else 0
        profit = pay_t * 0.05
        rep = f"<b>📊 【{conf}】详细账单</b>\n━━━━━━━━━━━━━━\n总发包：{ts:.2f}\n中雷总赔付：{pay_t:.2f}\n系统净利润：{profit:.2f}\n\n📜 中雷明细(页:{page+1})：\n"
        
        for i, (t, act, amt) in enumerate(all_m[offset:offset+20]):
            nm = act.replace("中雷收入(来自", "").replace(")", "").strip()
            rep += f"{offset+i+1}. {t[5:16]}+{html.escape(nm)}+发{amt/0.95:.0f}+抽{amt/0.95*0.05:.2f}\n"
        
        btns = []
        if page > 0: btns.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"pg_{cid}_{page-1}"))
        if len(all_m) > offset + 20: btns.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"pg_{cid}_{page+1}"))
        return rep, InlineKeyboardMarkup([btns]) if btns else None

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = update.message.text if update.message else ""; user = update.effective_user
        if "all_logs_" in txt:
            cid = txt.split("all_logs_")[-1]
            gn = self.db.get_config(cid)[-1]; logs = self.db.get_user_logs(user.id, cid, 100)
            rep = f"<b>📊 全部流水</b> 群:{gn}\n━━━━━━━━━━━━━━\n"
            rep += "\n".join([f"• {t[5:16]} {html.escape(act)} {amt:+.2f}" for t, act, amt in logs])
            return await update.message.reply_text(rep if logs else "暂无记录", parse_mode=ParseMode.HTML)
        await update.message.reply_text("🤖 扫雷系统已就绪。")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; chat = update.effective_chat
        if not msg or not msg.text: return
        txt = msg.text.strip(); cid = chat.id; gname = chat.title or "群组"
        if chat.type == "private" and not txt.startswith("/start"): return

        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            c = self.db.get_config(cid); self.db.set_config(cid, c[0], c[1], c[2], c[3], gname)
            return await msg.reply_text("✅ 机器人已启动！")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False; return await msg.reply_text("💤 系统已休眠。")

        conf_m = re.match(r'^金额(\d+)~(\d+)/包数(\d+)~(\d+)$', txt)
        if conf_m:
            m = await context.bot.get_chat_member(cid, user.id)
            if m.status in ['administrator', 'creator'] or user.id == OWNER_ID:
                mina, maxa, minc, maxc = map(float, conf_m.groups())
                self.db.set_config(cid, mina, maxa, int(minc), int(maxc), gname)
                return await msg.reply_text(f"⚙️ 规则设置成功！")

        if txt == "查询":
            return await msg.reply_text(f"💰 余额：{self.db.get_balance(user.id, cid):.2f}")
        if txt == "我的流水":
            logs = self.db.get_user_logs(user.id, cid, 20)
            rep = f"👤 <b>{html.escape(user.first_name)}</b> 流水\n" + "\n".join([f"• {t[11:16]} {html.escape(act)} {amt:+.2f}" for t, act, amt in logs])
            kb = [[InlineKeyboardButton("📩 全部流水", url=f"t.me/{context.bot.username}?start=all_logs_{cid}")]]
            return await msg.reply_text(rep, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

        ready, alert = await self.verify_owner(update, context)
        if not ready:
            if re.match(r'^\d+/', txt) or txt in ["账单", "总账"]: return await msg.reply_text(alert)
            return

        m = await context.bot.get_chat_member(cid, user.id); is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

        # --- 核心改进：管理员报表直接私发，不留群内按钮 ---
        if txt == "账单" and is_adm:
            try:
                rep, kb = await self.get_stats_text(cid, 0)
                await context.bot.send_message(user.id, rep, reply_markup=kb, parse_mode=ParseMode.HTML)
                return await msg.reply_text("✅ 详细账单已私信。")
            except: return await msg.reply_text("❌ 私信失败！请先点击关注机器人并点 /start")

        if txt == "总账" and is_adm:
            try:
                conf = self.db.get_config(cid)[-1]; data = self.db.get_all_balances(cid)
                rep = f"<b>💰 【{conf}】总账单</b>\n总持分：{sum(b for n,b in data):.2f}\n━━━━━━━━━━━━━━\n"
                rep += "\n".join([f"{i+1}. {self.mask_name(n)} -> {b:.2f}" for i, (n, b) in enumerate(data[:20])])
                await context.bot.send_message(user.id, rep, parse_mode=ParseMode.HTML)
                return await msg.reply_text("✅ 本群总账已私信。")
            except: return await msg.reply_text("❌ 私信失败！请先私聊机器人。")

        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user; num = float(txt.replace(' ', ''))
                self.db.add_balance(target.id, cid, num, target.first_name); self.db.log_action(target.id, "人工上分", num, cid)
                return await msg.reply_text(f"✅ {html.escape(target.first_name)} 余额：{self.db.get_balance(target.id, cid):.2f}")

        p_m = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if p_m:
            amt, cnt, mine = float(p_m.group(1)), int(p_m.group(2)) if p_m.group(2) else 10, int(p_m.group(3))
            conf = self.db.get_config(cid)
            if not (conf[0] <= amt <= conf[1]) or not (conf[2] <= cnt <= conf[3]): return await msg.reply_text("❌ 规则不符")
            if self.db.get_balance(user.id, cid) < amt: return await msg.reply_text("❌ 余额不足")
            self.db.add_balance(user.id, cid, -amt, user.first_name); self.db.log_action(user.id, "发包", -amt, cid)
            pid = f"pk_{int(time.time()*1000)}"
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, cnt), "count": cnt, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid, "status": "active"}
            cap = f"🧧 <b>【红包扫雷】</b>\n━━━━━━━━━━━━━━\n发包：{html.escape(user.first_name)}\n金额：{amt} | 雷：{mine} | 包：{cnt}\n━━━━━━━━━━━━━━\n等待入场..."
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
                await context.bot.send_message(cid, f"⏰ <b>【{html.escape(d['owner_name'])}】</b>的红包到期已退还。", parse_mode=ParseMode.HTML)
            await self.finalize(pid, d["mid"], context)

    def gen_amts(self, total, count):
        amts = []; rem = total
        for i in range(count - 1):
            a = round(random.uniform(0.01, (rem / (count - i)) * 2), 2); amts.append(a); rem -= a
        amts.append(round(rem, 2)); random.shuffle(amts); return amts

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; u = q.from_user; d_raw = q.data
        
        # --- 核心改进：秒回翻页 ---
        if d_raw.startswith("pg_"):
            _, target_cid, target_page = d_raw.split("_")
            rep, kb = await self.get_stats_text(target_cid, int(target_page))
            return await q.edit_message_text(rep, reply_markup=kb, parse_mode=ParseMode.HTML)

        pid = d_raw.replace("grab_", "")
        if pid not in active_packets: return await q.answer("❌ 红包失效", show_alert=True)
        d = active_packets[pid]
        if d["status"] != "active": return await q.answer("❌ 红包已领完", show_alert=True)
        if self.db.get_balance(u.id, d["cid"]) < d["total"]: return await q.answer(f"余额不足{d['total']}", show_alert=True)
        if any(g['id'] == u.id for g in d["grabbers"]): return await q.answer("已抢过", show_alert=True)
        
        d["grabbers"].append({"id": u.id, "name": u.first_name})
        await q.answer("✅ 抢包成功！")
        grabbed_num = len(d["grabbers"])
        header = f"🧧 <b>【红包扫雷】</b>\n━━━━━━━━━━━━━━\n发包：{html.escape(d['owner_name'])}\n金额：{d['total']} | 雷：{d['mine']} | 包：{d['count']}\n━━━━━━━━━━━━━━\n"
        
        if grabbed_num >= d["count"]: 
            d["status"] = "settling"; d["task"].cancel(); await self.finalize(pid, q.message.message_id, context)
        else:
            name_list = []
            for i, g in enumerate(d["grabbers"]):
                m_n = self.mask_name(g['name'])
                if i == d["count"] - 2: name_list.append(f"{i+1}. {m_n} -> 🔐 防计算中...")
                else:
                    amt = d["amounts"][i]; is_m = (int(str(amt)[-1]) == d["mine"])
                    name_list.append(f"{i+1}. {m_n} -> {amt:.2f} {'💣' if is_m else '🧧'}")
            try: await context.bot.edit_message_caption(chat_id=d["cid"], message_id=q.message.message_id, caption=header + "\n".join(name_list), reply_markup=q.message.reply_markup, parse_mode=ParseMode.HTML)
            except: pass

    async def finalize(self, pid, mid, context):
        d = active_packets.pop(pid, None)
        if not d: return
        d["status"] = "finished"
        res = [f"🧧 <b>结算结果 (雷:{d['mine']})</b>", f"发包：{html.escape(d['owner_name'])} | 金额：{d['total']}", "━━━━━━━━━━━━━━"]
        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            self.db.add_balance(g['id'], d["cid"], amt, g['name']); self.db.log_action(g['id'], "抢包", amt, d["cid"])
            if is_mine:
                self.db.add_balance(g['id'], d["cid"], -d["total"], g['name']); self.db.log_action(g['id'], "抢包中雷", -d["total"], d["cid"])
                inc = round(d["total"] * 0.95, 2); self.db.add_balance(d["owner_id"], d["cid"], inc, d["owner_name"])
                self.db.log_action(d["owner_id"], f"中雷收入(来自{g['name']})", inc, d["cid"])
            res.append(f"{i+1}. {self.mask_name(g['name'])} -> {amt:.2f} {'💣' if is_mine else '🧧'}")
        if len(d["grabbers"]) < d["count"]: res.append(f"━━━━━━━━━━━━━━\n⚠️ 剩余包数已过期退还。")
        try: await context.bot.edit_message_caption(chat_id=d["cid"], message_id=mid, caption="\n".join(res), parse_mode=ParseMode.HTML)
        except: pass
