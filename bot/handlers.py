import sqlite3
import random
import re
import pytz
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ChatMemberStatus, ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

OWNER_ID = 7653037721
DB_PATH = "mining_bot.db"
BEIJING_TZ = pytz.timezone('Asia/Shanghai')
scheduler = AsyncIOScheduler(timezone=BEIJING_TZ)

# --- 数据库初始化 ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS chat_status (chat_id INTEGER PRIMARY KEY, active INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS settings (chat_id INTEGER PRIMARY KEY, min_amt REAL, max_amt REAL, min_cnt INTEGER, max_cnt INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS users (chat_id INTEGER, user_id INTEGER, name TEXT, balance REAL, PRIMARY KEY (chat_id, user_id))')
    c.execute('CREATE TABLE IF NOT EXISTS logs (chat_id INTEGER, user_id INTEGER, msg TEXT, amount REAL, time TIMESTAMP)')
    conn.commit()
    conn.close()

# --- 核心辅助 ---
async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """熔断与权限校验 🛡️"""
    chat_id = update.effective_chat.id
    try:
        m = await context.bot.get_chat_member(chat_id, OWNER_ID)
        return m.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except: return False

def mask_name(n):
    return f"{n[0]}*{n[-1]}" if len(n) > 2 else n

# --- 全局内存任务 ---
active_games = {} 

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    text = update.message.text.strip()
    if chat.type == "private" or not await auth(update, context): return

    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT active FROM chat_status WHERE chat_id=?", (chat.id,))
    is_active = c.fetchone()

    # 1. 开启权限 🔑
    if text == "开启" and user.id == OWNER_ID:
        c.execute("INSERT OR REPLACE INTO chat_status VALUES (?, 1)", (chat.id,))
        c.execute("INSERT OR IGNORE INTO settings VALUES (?, 50, 1000, 7, 10)", (chat.id,))
        conn.commit()
        return await update.message.reply_text("✨ **系统已激活** ✨\n💸 祝各位老板日进斗金！💸")

    if not is_active: return

    # 2. 上分逻辑 💳 (上分@用户 100)
    if text.startswith("上分") and (user.id == OWNER_ID or (await context.bot.get_chat_member(chat.id, user.id)).status == ChatMemberStatus.ADMINISTRATOR):
        val = re.findall(r"\d+", text)
        target = update.message.reply_to_message.from_user if update.message.reply_to_message else user
        if val:
            amt = float(val[0])
            c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, 0)", (chat.id, target.id, target.full_name))
            c.execute("UPDATE users SET balance = balance + ? WHERE chat_id=? AND user_id=?", (amt, chat_id, target.id))
            conn.commit()
            await update.message.reply_text(f"✅ **充值成功**\n👤 账户：{target.full_name}\n💰 金额：+{amt}\n🏦 当前余额：查看“我的”")

    # 3. 发包逻辑 🧨 (金额/包数/雷号)
    elif re.match(r"^\d+/(\d+)/(\d)$", text):
        amt, cnt, mine = map(int, text.split('/'))
        c.execute("SELECT balance FROM users WHERE chat_id=? AND user_id=?", (chat.id, user.id))
        row = c.fetchone()
        if not row or row[0] < amt:
            return await update.message.reply_text("❌ **余额不足**\n请联系管理进行充值再战！🏦")

        # 预扣款
        c.execute("UPDATE users SET balance = balance - ? WHERE chat_id=? AND user_id=?", (amt, chat.id, user.id))
        conn.commit()

        # 生成红包金额
        parts = []
        remain = amt
        for i in range(cnt-1):
            p = round(random.uniform(0.01, (remain/ (cnt-i))*2), 2)
            parts.append(p); remain = round(remain - p, 2)
        parts.append(remain); random.shuffle(parts)

        # 发图
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🧧 戳我开抢 🧧", callback_data=f"grab_{update.message.message_id}")]])
        msg = await context.bot.send_photo(
            chat.id, photo=open("cover.jpg", "rb"),
            caption=f"🎁 **{user.full_name} 的大红包**\n━━━━━━━━━━━━\n🧧 红包金额：{amt} USDT\n📦 红包个数：{cnt} 个\n💣 埋雷数字：{mine}\n━━━━━━━━━━━━\n📢 请点击下方按钮抢包",
            reply_markup=btn, parse_mode=ParseMode.MARKDOWN
        )
        
        active_games[msg.message_id] = {
            "owner": user.id, "owner_name": user.full_name, "total": amt, 
            "count": cnt, "mine": mine, "parts": parts, "grabs": []
        }

    conn.close()

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    msg_id = query.message.message_id
    chat_id = query.message.chat_id

    if msg_id not in active_games:
        return await query.answer("⏰ 该红包已失效或已结清", show_alert=True)
    
    game = active_games[msg_id]
    if user.id in [g['user_id'] for g in game['grabs']]:
        return await query.answer("🚫 贪心哦，你已经抢过啦！", show_alert=True)

    # 领取逻辑
    idx = len(game['grabs'])
    amt = game['parts'][idx]
    game['grabs'].append({"user_id": user.id, "name": user.full_name, "amt": amt})
    
    await query.answer(f"🎉 你抢到了 {amt} USDT！")

    # 延迟开奖美化
    if len(game['grabs']) == game['count'] - 1:
        await query.edit_message_caption(caption=query.message.caption + f"\n\n⚡️ **最后一位拼手气中...**\n🔐 防计算加密锁已开启 ⚙️")
    
    elif len(game['grabs']) == game['count']:
        # 瞬间结清逻辑
        await finalize_game(query, context, msg_id)

async def finalize_game(query, context, msg_id):
    game = active_games.pop(msg_id)
    chat_id = query.message.chat_id
    mine = game['mine']
    owner_id = game['owner']
    
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    res_text = f"🏁 **{game['owner_name']} 的红包结算单**\n"
    res_text += f"💰 总额：{game['total']} | 💣 雷号：{mine}\n━━━━━━━━━━━━\n"
    
    hit_count = 0
    for g in game['grabs']:
        is_hit = str(g['amt']).endswith(str(mine))
        symbol = "💥" if is_hit else "💰"
        res_text += f"{symbol} {mask_name(g['name'])}：{g['amt']} {'(中雷)' if is_hit else ''}\n"
        
        # 先加抢到的钱
        c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, 0)", (chat_id, g['user_id'], g['name']))
        c.execute("UPDATE users SET balance = balance + ? WHERE chat_id=? AND user_id=?", (g['amt'], chat_id, g['user_id']))
        
        if is_hit:
            hit_count += 1
            # 扣除赔付
            c.execute("UPDATE users SET balance = balance - ? WHERE chat_id=? AND user_id=?", (game['total'], chat_id, g['user_id']))
            # 发包者收入 (95%)
            c.execute("UPDATE users SET balance = balance + ? WHERE chat_id=? AND user_id=?", (game['total']*0.95, chat_id, owner_id))

    conn.commit(); conn.close()
    res_text += f"━━━━━━━━━━━━\n📈 战果：{hit_count} 人中雷，庄家回血！"
    await query.edit_message_caption(caption=res_text, parse_mode=ParseMode.MARKDOWN)

async def start_command(update, context):
    await update.message.reply_text("🎰 **欢迎使用红中扫雷系统**\n\n🔹 群内回复：`开启` 激活\n🔹 发包格式：`金额/包数/雷号` (例: 100/10/5)\n🔹 查询流水：`我的流水`")
