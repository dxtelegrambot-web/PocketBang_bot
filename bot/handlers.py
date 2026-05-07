import random, re, time, asyncio
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

    async def verify_owner(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        if not group_switch.get(cid): return False, "⚠️ 机器人未开启。"
        try:
            o = await context.bot.get_chat_member(cid, OWNER_ID)
            if o.status not in ['administrator', 'creator']: return False, "❌ 权限熔断。"
        except: return False, "❌ 拥有者不在群内。"
        return True, ""

    def mask_name(self, name):
        """改进的名字脱敏逻辑：张三丰 -> 张*丰，短名原样显示"""
        if not name: return "*"
        n_str = str(name)
        if len(n_str) <= 2: return n_str 
        return f"{n_str[0]}*{n_str[-1]}"

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user
        if not msg or not msg.text: return
        cid = update.effective_chat.id
        txt = msg.text.strip()

        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            return await msg.reply_text("✅ 机器人已启动！")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False
            return await msg.reply_text("💤 机器人已休眠。")

        ready, alert = await self.verify_owner(update, context)
        if not ready:
            if any(x in txt for x in ["/", "查询", "流水", "总计"]) or re.match(r'^\d+/', txt):
                await msg.reply_text(alert)
            return

        m = await context.bot.get_chat_member(cid, user.id)
        is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

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

        # 发包
        packet_match = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if packet_match:
            amt = float(packet_match.group(1)); count = int(packet_match.group(2)) if packet_match.group(2) else 10; mine = int(packet_match.group(3))
            if self.db.get_balance(user.id) < amt: return await msg.reply_text("❌ 余额不足")
            self.db.add_balance(user.id, -amt)
            self.db.log_action(user.id, "发包", -amt, 0, cid)
            pid = f"pk_{int(time.time()*1000)}"
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, count), "count": count, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid}
            kb = [[InlineKeyboardButton("🧧 立即抢包", callback_data=f"grab_{pid}")]]
            return await msg.reply_text(f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{user.first_name}\n金额：{amt} | 雷：{mine} | 包：{count}\n━━━━━━━━━━━━━━\n等待入场... (0/{count})", reply_markup=InlineKeyboardMarkup(kb))

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
        grabbed_num = len(d["grabbers"])
        await q.answer("✅ 抢包成功！")

        if grabbed_num >= d["count"]: 
            await self.finalize(pid, q.message.message_id, context)
        else:
            name_list = []
            for i, g in enumerate(d["grabbers"]):
                m_name = self.mask_name(g['name'])
                if i == d["count"] - 2:
                    name_list.append(f"{i+1}. {m_name} -> 🔐 防计算中...")
                else:
                    amt = d["amounts"][i]
                    is_m = (int(str(amt)[-1]) == d["mine"])
                    name_list.append(f"{i+1}. {m_name} -> {amt:.2f} {'💣' if is_m else ''}")
            
            new_text = (f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n"
                        f"发包：{d['owner_name']}\n金额：{d['total']} | 雷：{d['mine']} | 包：{d['count']}\n"
                        f"━━━━━━━━━━━━━━\n" + "\n".join(name_list) + f"\n\n正在抢包... ({grabbed_num}/{d['count']})")
            await q.edit_message_text(text=new_text, reply_markup=q.message.reply_markup)

    async def finalize(self, pid, mid, context):
        d = active_packets.pop(pid, None)
        if not d or not d["grabbers"]: return
        res = [f"🧧 结算结果 (雷:{d['mine']})", "━━━━━━━━━━━━━━"]
        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            self.db.add_balance(g['id'], amt)
            self.db.log_action(g['id'], "抢到红包", amt, 0, d["cid"])
            if is_mine:
                self.db.add_balance(g['id'], -d["total"])
                inc = round(d["total"] * (1 - COMMISSION), 2)
                self.db.add_balance(d["owner_id"], inc)
                self.db.log_action(g['id'], "抢包中雷", -d["total"], 1, d["cid"])
                self.db.log_action(d["owner_id"], f"中雷收入({g['name']})", inc, 0, d["cid"])
            res.append(f"{i+1}. {self.mask_name(g['name'])} -> {amt:.2f} {'💣' if is_mine else ''}")
        await context.bot.edit_message_text(chat_id=d["cid"], message_id=mid, text="\n".join(res))
