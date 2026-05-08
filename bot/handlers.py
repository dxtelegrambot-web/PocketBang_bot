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
                return False, "❌ 权限熔断：拥有者失去管理权。"
        except: return False, "❌ 权限熔断。"
        return True, ""

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = update.message.text; user = update.effective_user
        if "all_logs_" in txt:
            cid = txt.split("all_logs_")[-1]
            conf = self.db.get_config(cid)[-1]; logs = self.db.get_user_logs(user.id, cid, 100)
            rep = f"📊 【全部流水】群:{conf}\n━━━━━━━━━━━━━━\n"
            rep += "\n".join([f"• {t[5:16]} {act} {amt:+.2f}" for t, act, amt in logs])
            return await update.message.reply_text(rep if logs else "暂无记录")
        
        if "total_assets_" in txt:
            cid = txt.split("total_assets_")[-1]; conf = self.db.get_config(cid)[-1]; data = self.db.get_all_balances(cid)
            rep = f"💰 【{conf}】总账单\n总持分：{sum(b for n,b in data):.2f}\n━━━━━━━━━━━━━━\n"
            rep += "\n".join([f"{i+1}. {self.mask_name(n)} -> {b:.2f}" for i, (n, b) in enumerate(data[:20])])
            return await update.message.reply_text(rep if data else "暂无数据")
        
        if "stats_" in txt:
            parts = txt.split("_"); cid = parts[1]; page = int(parts[2]) if len(parts) > 2 else 0
            offset = page * 20; conf = self.db.get_config(cid)[-1]
            with sqlite3.connect(self.db.db_path) as conn:
                ts = conn.execute("SELECT SUM(ABS(amount)) FROM logs WHERE action='发包' AND chat_id=?", (str(cid),)).fetchone()[0] or 0
                all_m = conn.execute("SELECT timestamp, action, amount FROM logs WHERE action LIKE '中雷收入%' AND chat_id=? ORDER BY id DESC", (str(cid),)).fetchall()
            profit = sum(x[2] for x in all_m) / 0.95 * 0.05 if all_m else 0
            rep = f"📊 【{conf}】详细账单\n━━━━━━━━━━━━━━\n总发包金额：{ts:.2f}\n系统净抽成：{profit:.2f}\n\n📜 中雷明细：\n"
            for i, (t, act, amt) in enumerate(all_m[offset:offset+20]):
                link = re.search(r'\[🔗\]\((.*?)\)', act)
                l_str = f" [跳转]({link.group(1)})" if link else ""
                nm = re.sub(r'\[🔗\].*?\)', '', act).replace("中雷收入(来自", "").replace(")", "").strip()
                rep += f"{offset+i+1}. {t[5:16]}+{nm}+发{amt/0.95:.0f}+抽{amt/0.95*0.05:.2f}{l_str}\n"
            btns = [InlineKeyboardButton("⬅️ 上一页", callback_data=f"page_{cid}_{page-1}")] if page > 0 else []
            if len(all_m) > offset + 20: btns.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"page_{cid}_{page+1}"))
            m_f = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text
            return await m_f(rep, reply_markup=InlineKeyboardMarkup([btns]) if btns else None, parse_mode="Markdown", disable_web_page_preview=True)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; chat = update.effective_chat
        if not msg or not msg.text: return
        txt = msg.text.strip(); cid = chat.id; gname = chat.title or "群组"
        if chat.type == "private" and not txt.startswith("/start"): return

        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            old = self.db.get_config(cid)
            self.db.set_config(cid, old[0], old[1], old[2], old[3], gname)
            return await msg.reply_text("✅ 机器人已启动！")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False; return await msg.reply_text("💤 机器人已休眠。")

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
            rep = f"👤 【{user.first_name}】本群流水\n" + "\n".join([f"• {t[11:16]} {act} {amt:+.2f}" for t, act, amt in logs])
            return await msg.reply_text(rep, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 全部明细", url=f"t.me/{context.bot.username}?start=all_logs_{cid}")]]))

        ready, alert = await self.verify_owner(update, context)
        if not ready:
            if re.match(r'^\d+/', txt) or txt in ["账单", "总账"]: return await msg.reply_text(alert)
            return

        m = await context.bot.get_chat_member(cid, user.id); is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID
        if txt == "账单" and is_adm:
            return await msg.reply_text("🔒 验证中...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 查看账单", url=f"t.me/{context.bot.username}?start=stats_{cid}_0")]]))
        if txt == "总账" and is_adm:
            return await msg.reply_text("🔒 验证中...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 查看总账", url=f"t.me/{context.bot.username}?start=total_assets_{cid}")]]))

        if (txt.startswith('+') or txt.startswith('-')) and is_adm:
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user; num = float(txt.replace(' ', ''))
                self.db.add_balance(target.id, cid, num, target.first_name); self.db.log_action(target.id, "人工上下分", num, cid)
                return await msg.reply_text(f"✅ {target.first_name} 余额：{self.db.get_balance(target.id, cid):.2f}")

        p_m = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if p_m:
            amt, cnt, mine = float(p_m.group(1)), int(p_m.group(2)) if p_m.group(2) else 10, int(p_m.group(3))
            conf = self.db.get_config(cid)
            if not (conf[0] <= amt <= conf[1]) or not (conf[2] <= cnt <= conf[3]): return await msg.reply_text("❌ 规则不符")
            if self.db.get_balance(user.id, cid) < amt: return await msg.reply_text("❌ 余额不足")
            
            self.db.add_balance(user.id, cid, -amt, user.first_name); self.db.log_action(user.id, "发包", -amt, cid)
            pid = f"pk_{int(time.time()*1000)}"
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, cnt), "count": cnt, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid, "status": "active"}
            cap = f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{user.first_name}\n金额：{amt} | 雷：{mine} | 包：{cnt}\n━━━━━━━━━━━━━━\n等待入场..."
            kb = [[InlineKeyboardButton("🧧 立即抢包", callback_data=f"grab_{pid}")]]
            try:
                with open("cover.jpg", "rb") as f: sent = await context.bot.send_photo(chat_id=cid, photo=f, caption=cap, reply_markup=InlineKeyboardMarkup(kb))
                active_packets[pid]["mid"] = sent.message_id
            except: 
                sent = await msg.reply_text(cap, reply_markup=InlineKeyboardMarkup(kb)); active_packets[pid]["mid"] = sent.message_id
            active_packets[pid]["task"] = asyncio.create_task(self.timer_refund(pid, cid, context))

    async def timer_refund(self, pid, cid, context):
        await asyncio.sleep(300)
        if pid in active_packets and active_packets[pid]["status"] == "active":
            d = active_packets[pid]; grabbed = len(d["grabbers"]); unclaimed = sum(d["amounts"][grabbed:])
            if unclaimed > 0:
                self.db.add_balance(d["owner_id"], cid, unclaimed, d["owner_name"]); self.db.log_action(d["owner_id"], "过期退还", unclaimed, cid)
                await context.bot.send_message(cid, f"⏰ 【{d['owner_name']}】的红包到期，未领金额已退还。")
            await self.finalize(pid, d["mid"], context)

    def gen_amts(self, total, count):
        amts = []; rem = total
        for i in range(count - 1):
            a = round(random.uniform(0.01, (rem / (count - i)) * 2), 2); amts.append(a); rem -= a
        amts.append(round(rem, 2)); random.shuffle(amts); return amts

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; u = q.from_user; pid = q.data.replace("grab_", "")
        if pid.startswith("page_"):
            q.message.text = f"/start stats_{pid.split('_')[1]}_{pid.split('_')[2]}"
            return await self.handle_start(update, context)
            
        if pid not in active_packets: return await q.answer("❌ 红包失效", show_alert=True)
        d = active_packets[pid]
        if d["status"] != "active": return await q.answer("❌ 红包已抢完", show_alert=True)
        if self.db.get_balance(u.id, d["cid"]) < d["total"]: return await q.answer(f"持分不足{d['total']}", show_alert=True)
        if any(g['id'] == u.id for g in d["grabbers"]): return await q.answer("已抢过", show_alert=True)
        
        d["grabbers"].append({"id": u.id, "name": u.first_name}); grabbed_num = len(d["grabbers"])
        await q.answer("✅ 抢包成功！")
        
        header = f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{d['owner_name']}\n金额：{d['total']} | 雷：{d['mine']} | 包：{d['count']}\n━━━━━━━━━━━━━━\n"
        if grabbed_num >= d["count"]: 
            d["status"] = "settling"; d["task"].cancel(); await self.finalize(pid, q.message.message_id, context)
        else:
            name_list = []
            for i, g in enumerate(d["grabbers"]):
                m_n = self.mask_name(g['name'])
                if i == d["count"] - 2: name_list.append(f"{i+1}. {m_n} 已抢，等待开奖")
                else:
                    amt = d["amounts"][i]; is_m = (int(str(amt)[-1]) == d["mine"])
                    name_list.append(f"{i+1}. {m_n} -> {amt:.2f} {'💣' if is_m else '🧧'}")
            try: await context.bot.edit_message_caption(chat_id=d["cid"], message_id=q.message.message_id, caption=header + "\n".join(name_list), reply_markup=q.message.reply_markup)
            except: pass

    async def finalize(self, pid, mid, context):
        d = active_packets.pop(pid, None)
        if not d: return
        d["status"] = "finished"
        res = [f"🧧 结算结果 (雷:{d['mine']})", f"发包：{d['owner_name']} | 金额：{d['total']}", "━━━━━━━━━━━━━━"]
        link = f"https://t.me{str(d['cid'])[4:]}/{mid}" if str(d['cid']).startswith("-100") else ""
        link_tag = f" [🔗]({link})" if link else ""

        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            self.db.add_balance(g['id'], d["cid"], amt, g['name']); self.db.log_action(g['id'], "抢包", amt, d["cid"])
            if is_mine:
                self.db.add_balance(g['id'], d["cid"], -d["total"], g['name']); self.db.log_action(g['id'], "抢包中雷", -d["total"], d["cid"])
                inc = round(d["total"] * 0.95, 2); self.db.add_balance(d["owner_id"], d["cid"], inc, d["owner_name"]); 
                self.db.log_action(d["owner_id"], f"中雷收入(来自{g['name']}){link_tag}", inc, d["cid"])
            res.append(f"{i+1}. {self.mask_name(g['name'])} -> {amt:.2f} {'💣' if is_mine else '🧧'}")
        
        if len(d["grabbers"]) < d["count"]:
            res.append(f"━━━━━━━━━━━━━━\n⚠️ 剩余包数已退还。")
            
        # 强制仅修改原图说明，绝不发送新消息
        try:
            await context.bot.edit_message_caption(
                chat_id=d["cid"], 
                message_id=mid, 
                caption="\n".join(res), 
                parse_mode="Markdown"
            )
        except Exception:
            pass # 如果因为图片被删导致无法修改，则不产生任何输出，杜绝弹出新文本消息
