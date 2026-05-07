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

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message; user = update.effective_user; cid = msg.chat_id if msg else None
        if not msg or not msg.text: return
        txt = msg.text

        if txt == "开启" and user.id == OWNER_ID:
            group_switch[cid] = True
            return await msg.reply_text("✅ 开启成功，自动退款逻辑已强化。")
        if txt == "关闭" and user.id == OWNER_ID:
            group_switch[cid] = False
            return await msg.reply_text("💤 系统已休眠。")

        ready, alert = await self.verify_owner(update, context)
        if not ready and txt in ["流水", "我的流水", "查询", "总计"]: return await msg.reply_text(alert)

        m = await context.bot.get_chat_member(cid, user.id)
        is_adm = m.status in ['administrator', 'creator'] or user.id == OWNER_ID

        # 1. 查询/流水指令
        if txt == "查询":
            return await msg.reply_text(f"💰 余额：{self.db.get_balance(user.id):.2f}")

        if txt == "我的流水":
            logs = self.db.get_user_logs(user.id, 20)
            rep = f"👤 【{user.first_name}】流水表\n" + "\n".join([f"• {t[11:16]} {act} {amt:+.2f}" for t, act, amt, mine in logs])
            return await msg.reply_text(rep, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 全部流水", url=f"t.me/{context.bot.username}?start=all")]]))

        # 2. 发红包逻辑
        p = r'^(\d+)(?:/(\d+))?/(\d)$'
        match = re.match(p, txt)
        if match and group_switch.get(cid):
            amt, count, mine = float(match.group(1)), int(match.group(2)) if match.group(2) else 10, int(match.group(3))
            if self.db.get_balance(user.id) < amt: return await msg.reply_text("❌ 余额不足")
            
            self.db.add_balance(user.id, -amt)
            self.db.log_action(user.id, "发包", -amt, 0, cid)
            
            pid = f"pk_{int(time.time()*1000)}"
            active_packets[pid] = {
                "total": amt, "amounts": self.gen_amts(amt, count), 
                "count": count, "mine": mine, "owner_id": user.id, 
                "owner_name": user.first_name, "grabbers": [], "cid": cid, "active": True
            }
            
            m_obj = await msg.reply_text(f"🧧 {amt}/{count} 雷:{mine}\n(10分钟未领完自动退回)", 
                                       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧧 抢红包", callback_data=f"grab_{pid}")]]))
            
            # 【核心修复】启动 10 分钟倒计时任务
            asyncio.create_task(self.auto_refund_task(pid, m_obj.message_id, context))

    # --- 修复后的自动退款任务 ---
    async def auto_refund_task(self, pid, mid, context):
        await asyncio.sleep(600) # 等待10分钟
        if pid in active_packets and active_packets[pid]["active"]:
            d = active_packets[pid]
            d["active"] = False # 标记为已失效，防止并发冲突
            
            grabbed_num = len(d["grabbers"])
            # 计算未领取的金额
            unclaimed = round(sum(d["amounts"][grabbed_num:]), 2)
            
            if unclaimed > 0:
                self.db.add_balance(d["owner_id"], unclaimed)
                self.db.log_action(d["owner_id"], "红包过期退回", unclaimed, 0, d["cid"])
                await context.bot.send_message(d["cid"], f"⏰ 红包过期通知\n━━━━━━━━━━━━━━\n发包者：{d['owner_name']}\n退回金额：{unclaimed:.2f}\n已自动入账。")
            
            # 无论领没领完，10分钟到期强制更新消息并结算已抢部分
            await self.finalize(pid, mid, context)

    def gen_amts(self, total, count):
        amts = []; rem = total
        for i in range(count - 1):
            a = round(random.uniform(0.01, (rem / (count - i)) * 2), 2)
            amts.append(a); rem -= a
        amts.append(round(rem, 2))
        random.shuffle(amts)
        return amts

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; u = q.from_user; pid = q.data.replace("grab_", "")
        if pid not in active_packets or not active_packets[pid]["active"]: 
            return await q.answer("❌ 红包已过期或领完", show_alert=True)
        
        d = active_packets[pid]
        if self.db.get_balance(u.id) < d["total"]: return await q.answer(f"余额需≥{d['total']}", show_alert=True)
        if any(g['id'] == u.id for g in d["grabbers"]): return await q.answer("已抢过")
        
        d["grabbers"].append({"id": u.id, "name": u.first_name})
        await q.answer("抢包成功")
        
        if len(d["grabbers"]) >= d["count"]:
            d["active"] = False
            await self.finalize(pid, q.message.message_id, context)
        else:
            await q.edit_message_text(text=f"🧧 抢包中... ({len(d['grabbers'])}/{d['count']})", reply_markup=q.message.reply_markup)

    async def finalize(self, pid, mid, context):
        if pid not in active_packets: return
        d = active_packets[pid]
        res = [f"🧧 结算 (雷:{d['mine']})", "━━━━━━━━━━━━━━"]
        
        for i, g in enumerate(d["grabbers"]):
            amt = d["amounts"][i]; is_mine = (int(str(amt)[-1]) == d["mine"])
            self.db.add_balance(g['id'], amt) 
            self.db.log_action(g['id'], "抢到红包", amt, 0, d["cid"])
            
            if is_mine:
                self.db.add_balance(g['id'], -d["total"])
                self.db.log_action(g['id'], "抢包中雷", -d["total"], 1, d["cid"])
                # 发包者赔付流水
                income = round(d["total"] * (1 - COMMISSION), 2)
                self.db.add_balance(d["owner_id"], income)
                self.db.log_action(d["owner_id"], f"中雷收入({g['name']})", income, 0, d["cid"])
            
            res.append(f"{g['name']}->{amt} {'💣' if is_mine else ''}")
        
        try:
            await context.bot.edit_message_text(chat_id=d["cid"], message_id=mid, text="\n".join(res))
        except: pass
        if pid in active_packets: del active_packets[pid]
