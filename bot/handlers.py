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
        if not name: return "*"
        n = str(name)
        if len(n) <= 2: return n 
        return f"{n}*{n[-1]}"

    async def verify_owner(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cid = update.effective_chat.id
        if not group_switch.get(cid): return False, "⚠️ 机器人未开启。"
        try:
            o = await context.bot.get_chat_member(cid, OWNER_ID)
            if o.status not in ['administrator', 'creator']: return False, "❌ 权限熔断。"
        except: return False, "❌ 权限熔断。"
        return True, ""

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = update.message.text
        if "total_assets_" in txt:
            target_cid = txt.split("total_assets_")[-1]
            data = self.db.get_all_balances(target_cid)
            if not data: return await update.message.reply_text("暂无持分数据。")
            # 修正：总账显示名字
            rep = f"💰 【本群总账】\n总持分：{sum(b for n,b in data):.2f}\n"
            rep += "\n".join([f"{i+1}. {self.mask_name(n)} -> {b:.2f}" for i, (n, b) in enumerate(data[:20])])
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
        
        # 加减分逻辑：记录名字用于总账
        if (txt.startswith('+') or txt.startswith('-')) and user.id == OWNER_ID: # 简化示例
            if msg.reply_to_message:
                target = msg.reply_to_message.from_user
                num = float(txt.replace(' ', ''))
                self.db.add_balance(target.id, cid, num, target.first_name) # 存入名字
                return await msg.reply_text(f"✅ {target.first_name} 余额已更新")

        # 发包逻辑 (带封面图)
        p_match = re.match(r'^(\d+)(?:/(\d+))?/(\d)$', txt)
        if p_match:
            amt, count, mine = float(p_match.group(1)), int(p_match.group(2)) if p_match.group(2) else 10, int(p_match.group(3))
            if self.db.get_balance(user.id, cid) < amt: return await msg.reply_text("❌ 余额不足")
            self.db.add_balance(user.id, cid, -amt, user.first_name)
            pid = f"pk_{int(time.time()*1000)}"
            task = asyncio.create_task(self.timer_refund(pid, cid, context))
            active_packets[pid] = {"total": amt, "amounts": self.gen_amts(amt, count), "count": count, "mine": mine, "owner_id": user.id, "owner_name": user.first_name, "grabbers": [], "cid": cid, "task": task}
            cap = f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{user.first_name}\n金额：{amt} | 雷：{mine} | 包：{count}\n━━━━━━━━━━━━━━\n等待抢包..."
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
                # 修正：显示谁的红包到期
                await context.bot.send_message(cid, f"⏰ 【{d['owner_name']}】的红包已到期！\n未领金额 {unclaimed:.2f} 已退还余额。")
            await self.finalize(pid, d["mid"], context)

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; u = q.from_user; pid = q.data.replace("grab_", "")
        if pid not in active_packets: return await q.answer("失效")
        d = active_packets[pid]
        
        d["grabbers"].append({"id": u.id, "name": u.first_name})
        # 修正：抢包时保留发包者、金额等头信息
        header = f"🧧 【红包扫雷】\n━━━━━━━━━━━━━━\n发包：{d['owner_name']}\n金额：{d['total']} | 雷：{d['mine']} | 包：{d['count']}\n━━━━━━━━━━━━━━\n"
        name_list = [f"{i+1}. {self.mask_name(g['name'])} 已抢" for i, g in enumerate(d["grabbers"])]
        
        if len(d["grabbers"]) >= d["count"]:
            d["task"].cancel(); await self.finalize(pid, q.message.message_id, context)
        else:
            await context.bot.edit_message_caption(chat_id=d["cid"], message_id=q.message.message_id, caption=header + "\n".join(name_list), reply_markup=q.message.reply_markup)

    # gen_amts 和 finalize 逻辑保持不变...
