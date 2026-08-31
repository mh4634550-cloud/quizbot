import os, asyncio, io, re, json, random, time, urllib.request, urllib.parse
from aiohttp import web
from PIL import Image
import google.generativeai as genai
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, PollAnswerHandler, InlineQueryHandler, ContextTypes, filters

TOKEN = "8832779613:AAETBqawjr3YwH8Su6c_Qz5OuD-IuHiOsqc"
UPI_ID = "marufhussain318-2@oksbi"
GEMINI_API_KEY = "AQ.Ab8RN6JacArVio7NBYlubfksQK8a9q9G2u4UyCzvJKqT56nF0Q"
DATA_FILE = "store_quiz_data.json"

genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel("gemini-1.5-flash")

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    return {"quizzes": {}, "purchases": {}}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception: pass

db = load_data()
creation_state, active_sessions = {}, {}

AI_PROMPT = """Extract all multiple-choice questions from this book page. Format strictly as:
Q1. Question
A) Option 1
B) Option 2
C) Option 3
D) Option 4
Answer: Correct Option Letter (A/B/C/D)
Leave a blank line between questions. No extra talk."""

def parse_single_question(block):
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    if len(lines) < 3: return None
    q_text = re.sub(r"^(Q\s*\d+[\.\)]|\d+[\.\)])\s*", "", lines[0]).strip()
    options, correct_idx = [], 0
    for line in lines[1:]:
        if re.match(r"^[\(\[]?[A-Da-d1-4][\)\].]", line):
            options.append(re.sub(r"^[\(\[]?[A-Da-d1-4][\)\].]\s*", "", line).strip()[:100])
        elif any(k in line for k in ["उत्तर", "Answer", "Ans", "ans"]):
            m = re.search(r"[\(\[]?([A-Da-d1-4])[\)\]]?", line.split(":")[-1])
            if m: correct_idx = {"A":0,"1":0,"B":1,"2":1,"C":2,"3":2,"D":3,"4":3}.get(m.group(1).upper(), 0)
    if len(options) >= 2:
        return {"question": q_text[:280], "options": options[:4], "correct_id": min(correct_idx, len(options)-1)}
    return None

def parse_bulk_questions(text):
    return [q for b in re.split(r"\n\s*\n|(?=\n\s*(?:Q\s*\d+[\.\)]|\d+[\.\)]))", text) if (q := parse_single_question(b.strip()))]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].startswith("quiz_"):
        await show_prequiz_card(update.effective_chat.id, context.args[0].replace("quiz_", ""), context)
        return
    await update.message.reply_text("👋 **Dulhin Bazar Quiz Bot!**\n\n📸 **Photo to Quiz:** `/create_quiz` karke book ki photo bhejein.\n📁 **Bulk:** `.txt` file upload karein.\n\n🔹 `/store` | `/mystore` | `/create_quiz` | `/my_quizzes` | `/stop`", parse_mode="Markdown")

async def mystore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = f"🏪 **Quiz Dashboard**\n\n🆔 `{uid}`\n📦 Purchases: {len(db.get('purchases',{}).get(str(uid),[]))}"
    kb = [[InlineKeyboardButton("📂 My Purchases", callback_data="mystore_purchases")], [InlineKeyboardButton("🛠 My Created", callback_data="mystore_created")], [InlineKeyboardButton("🛒 Store", callback_data="back_store")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def show_prequiz_card(chat_id, q_id, context: ContextTypes.DEFAULT_TYPE):
    q = db["quizzes"].get(q_id)
    if not q: return
    card = f"🎲 '*{q['title']}*'\n📁 {q['subject']} | ✒️ {len(q['questions'])} Qs | ⏱ {q['timer']}s\n💰 Price: *{'FREE' if q['price']==0 else f'₹{q[\"price\"]}'}*"
    kb = [[InlineKeyboardButton("I am ready!", callback_data=f"startready_{q_id}")]]
    await context.bot.send_message(chat_id, card, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = {q.get("subject", "General"): 1 for q in db.get("quizzes", {}).values()}
    if not subs: return await update.message.reply_text("📂 Store khali hai. `/create_quiz` karein.")
    kb = [[InlineKeyboardButton(f"📁 {s}", callback_data=f"sub_{s}")] for s in subs]
    kb.append([InlineKeyboardButton("🏪 My Store", callback_data="open_mystore")])
    await update.message.reply_text("📚 **Quiz Store:**", reply_markup=InlineKeyboardMarkup(kb))

async def create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    creation_state[update.effective_user.id] = {"title": "", "subject": "General", "price": 0, "timer": 15, "shuffle": False, "questions": [], "step": "TITLE"}
    await update.message.reply_text("📝 Quiz ka **Title/Naam** likhein:")

async def finalize_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or not creation_state[uid]["questions"]:
        return await update.message.reply_text("⚠️ Questions add karein!")
    s = creation_state[uid]
    qid = f"q_{int(time.time())}"
    qdata = {"id": qid, "title": s["title"], "subject": s["subject"], "price": s["price"], "timer": s["timer"], "shuffle": s["shuffle"], "owner": uid, "questions": s["questions"]}
    db["quizzes"][qid] = qdata
    db["purchases"].setdefault(str(uid), []).append(qid)
    save_data()
    del creation_state[uid]
    bme = await context.bot.get_me()
    surl = f"https://t.me/share/url?url=https://t.me/{bme.username}?start=quiz_{qid}&text={urllib.parse.quote(qdata['title'])}"
    kb = [[InlineKeyboardButton("Start Quiz ↗️", url=f"https://t.me/{bme.username}?start=quiz_{qid}")], [InlineKeyboardButton("Group Mein Start ↗️", url=f"https://t.me/{bme.username}?startgroup=quiz_{qid}")], [InlineKeyboardButton("Share ↗️", url=surl)]]
    await update.message.reply_text(f"🎉 **Quiz Saved!**\n🎲 *{qdata['title']}* ({len(qdata['questions'])} Qs)", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or creation_state[uid]["step"] != "QUESTIONS": return
    msg = await update.message.reply_text("🔍 AI Image Scanning...")
    try:
        p = await (await context.bot.get_file(update.message.photo[-1].file_id)).download_as_bytearray()
        res = ai_model.generate_content([AI_PROMPT, Image.open(io.BytesIO(p))]).text
        qs = parse_bulk_questions(res)
        if qs:
            creation_state[uid]["questions"].extend(qs)
            await msg.edit_text(f"✅ AI ne {len(qs)} Questions add kiye!\nTotal: {len(creation_state[uid]['questions'])}\n\nSave ke liye `/done` bhejein.")
        else: await msg.edit_text("⚠️ Image saaf nahi thi. Dubara bhejein.")
    except Exception: await msg.edit_text("⚠️ AI OCR Error.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or creation_state[uid]["step"] != "QUESTIONS": return
    f = await (await context.bot.get_file(update.message.document.file_id)).download_as_bytearray()
    qs = parse_bulk_questions(f.decode('utf-8', errors='ignore'))
    if qs:
        creation_state[uid]["questions"].extend(qs)
        await update.message.reply_text(f"📁 {len(qs)} Questions add ho gaye! Total: {len(creation_state[uid]['questions'])}\nSave: `/done`")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, text = update.effective_user.id, update.message.text.strip()
    if uid not in creation_state: return
    s = creation_state[uid]
    if s["step"] == "TITLE":
        s["title"], s["step"] = text, "SUBJECT"
        await update.message.reply_text(f"✅ Title: *{text}*\nAb **Subject** likhein (e.g. Lucent History):", parse_mode="Markdown")
    elif s["step"] == "SUBJECT":
        s["subject"], s["step"] = text, "PRICE"
        kb = [[InlineKeyboardButton("Free (₹0)", callback_data="setp_0"), InlineKeyboardButton("₹21", callback_data="setp_21")], [InlineKeyboardButton("₹49", callback_data="setp_49"), InlineKeyboardButton("₹99", callback_data="setp_99")]]
        await update.message.reply_text("💰 Price choose karein ya number type karein:", reply_markup=InlineKeyboardMarkup(kb))
    elif s["step"] == "PRICE" and text.isdigit():
        s["price"], s["step"] = int(text), "TIMER"
        kb = [[InlineKeyboardButton("10s", callback_data="tm_10"), InlineKeyboardButton("15s", callback_data="tm_15")], [InlineKeyboardButton("30s", callback_data="tm_30")]]
        await update.message.reply_text("⏱ Timer chunein:", reply_markup=InlineKeyboardMarkup(kb))
    elif s["step"] == "QUESTIONS":
        if text.lower() in ["/done", "done"]: return await finalize_quiz(update, context)
        qs = parse_bulk_questions(text)
        if qs:
            s["questions"].extend(qs)
            await update.message.reply_text(f"✅ {len(qs)} Questions add hue! Total: {len(s['questions'])}\nSave ke liye `/done` bhejein.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d, uid, cid = q.data, update.effective_user.id, update.effective_chat.id
    if d == "open_mystore": await mystore(update, context)
    elif d == "back_store": await store(update, context)
    elif d == "mystore_purchases":
        kb = [[InlineKeyboardButton(f"▶️ {db['quizzes'][x]['title']}", callback_data=f"view_{x}")] for x in db.get("purchases",{}).get(str(uid),[]) if x in db.get("quizzes",{})]
        await q.edit_message_text("📂 Purchases:", reply_markup=InlineKeyboardMarkup(kb) if kb else None)
    elif d == "mystore_created":
        kb = [[InlineKeyboardButton(f"⚙️ {v['title']}", callback_data=f"view_{k}")] for k,v in db.get("quizzes",{}).items() if v.get("owner")==uid]
        await q.edit_message_text("🛠 Created:", reply_markup=InlineKeyboardMarkup(kb) if kb else None)
    elif d.startswith("setp_"):
        creation_state[uid]["price"], creation_state[uid]["step"] = int(d.split("_")[1]), "TIMER"
        kb = [[InlineKeyboardButton("10s", callback_data="tm_10"), InlineKeyboardButton("15s", callback_data="tm_15")], [InlineKeyboardButton("30s", callback_data="tm_30")]]
        await q.edit_message_text("⏱ Timer chunein:", reply_markup=InlineKeyboardMarkup(kb))
    elif d.startswith("tm_"):
        creation_state[uid]["timer"], creation_state[uid]["step"] = int(d.split("_")[1]), "SHUF"
        kb = [[InlineKeyboardButton("🔀 Shuffle ON", callback_data="sh_1"), InlineKeyboardButton("Shuffle OFF", callback_data="sh_0")]]
        await q.edit_message_text("Shuffle order?", reply_markup=InlineKeyboardMarkup(kb))
    elif d.startswith("sh_"):
        creation_state[uid]["shuffle"], creation_state[uid]["step"] = (d == "sh_1"), "QUESTIONS"
        await q.edit_message_text("📸 **Ab direct book page ki PHOTO bhejein** ya `.txt` file bhej kar `/done` karein.")
    elif d.startswith("sub_"):
        sub = d.split("_", 1)[1]
        kb = [[InlineKeyboardButton(f"{v['title']} - {'FREE' if v['price']==0 else f'₹{v[\"price\"]}'}", callback_data=f"view_{k}")] for k,v in db.get("quizzes",{}).items() if v.get("subject")==sub]
        await q.edit_message_text(f"📚 {sub}:", reply_markup=InlineKeyboardMarkup(kb))
    elif d.startswith("view_"):
        qid = d.split("_", 1)[1]
        qz = db["quizzes"].get(qid)
        if not qz: return
        has = qz["price"] == 0 or qid in db.get("purchases",{}).get(str(uid),[]) or qz.get("owner") == uid
        card = f"🎲 '*{qz['title']}*'\n📁 {qz['subject']} | ✒️ {len(qz['questions'])} Qs | ⏱ {qz['timer']}s"
        kb = [[InlineKeyboardButton("I am ready!", callback_data=f"startready_{qid}")]] if has else [[InlineKeyboardButton(f"💳 Buy Now (₹{qz['price']})", callback_data=f"buy_{qid}")]]
        await q.edit_message_text(card, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    elif d.startswith("buy_"):
        qz = db["quizzes"].get(d.split("_", 1)[1])
        await q.edit_message_text(f"💳 **Pay ₹{qz['price']} via UPI:** `{UPI_ID}`\nAdmin ko screenshot bhejein sath me:\nUID: `{uid}`\nQID: `{qz['id']}`", parse_mode="Markdown")
    elif d.startswith("startready_"):
        qid = d.split("_", 1)[1]
        active_sessions[cid] = {"quiz": db["quizzes"][qid], "quiz_id": qid, "start_time": time.time(), "user_stats": {}, "poll_map": {}, "is_running": True}
        await q.edit_message_text("🚀 Starting quiz...")
        asyncio.create_task(run_quiz_loop(cid, context))

async def approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        t_uid, qid = str(context.args[0]), context.args[1]
        db["purchases"].setdefault(t_uid, []).append(qid)
        save_data()
        await update.message.reply_text(f"✅ Unlocked for `{t_uid}`!")
        await context.bot.send_message(int(t_uid), f"🎉 Payment Verified! *{db['quizzes'][qid]['title']}* unlocked.", parse_mode="Markdown")
    except Exception: await update.message.reply_text("/approve <user_id> <quiz_id>")

async def run_quiz_loop(chat_id, context: ContextTypes.DEFAULT_TYPE):
    s = active_sessions.get(chat_id)
    if not s: return
    qs = list(s["quiz"]["questions"])
    if s["quiz"].get("shuffle"): random.shuffle(qs)
    for idx, qd in enumerate(qs):
        if not s.get("is_running"): break
        try:
            m = await context.bot.send_poll(chat_id, f"Q{idx+1}. {qd['question']}", qd["options"], type=Poll.QUIZ, correct_option_id=qd["correct_id"], open_period=s["quiz"]["timer"], is_anonymous=False)
            s["poll_map"][m.poll.id] = qd["correct_id"]
        except Exception: continue
        for _ in range(s["quiz"]["timer"] + 2):
            if not s.get("is_running"): break
            await asyncio.sleep(1)
    if s.get("is_running"): await finish_quiz(chat_id, context)

async def finish_quiz(chat_id, context: ContextTypes.DEFAULT_TYPE):
    s = active_sessions.pop(chat_id, None)
    if not s: return
    tq = len(s["quiz"]["questions"])
    res = f"🏁 '*{s['quiz']['title']}*' finished!\n\n"
    for r, u in enumerate(sorted(s["user_stats"].values(), key=lambda x: (x["correct"], -x["time"]), reverse=True), 1):
        res += f"👤 *{u['name']}*: ✅ {u['correct']} | ❌ {u['wrong']} | ⏱ {round(u['time'],1)}s\n"
    await context.bot.send_message(chat_id, res if s["user_stats"] else "🏁 Quiz Finished! Koi score record nahi hua.", parse_mode="Markdown")

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = update.poll_answer
    for s in active_sessions.values():
        if ans.poll_id in s["poll_map"]:
            u = s["user_stats"].setdefault(ans.user.id, {"name": ans.user.first_name, "correct": 0, "wrong": 0, "time": 0.0})
            if ans.option_ids and ans.option_ids[0] == s["poll_map"][ans.poll_id]: u["correct"] += 1
            else: u["wrong"] += 1
            u["time"] = time.time() - s["start_time"]

async def stop_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id in active_sessions:
        active_sessions[update.effective_chat.id]["is_running"] = False
        await update.message.reply_text("🛑 Stopping...")
        await finish_quiz(update.effective_chat.id, context)
    else: await update.message.reply_text("⚠️ Koi quiz running nahi hai.")

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.inline_query.query.strip().lower()
    bme = await context.bot.get_me()
    res = []
    for k, v in db.get("quizzes", {}).items():
        if q in v["title"].lower() or q in v.get("subject", "").lower():
            txt = f"🎲 \"{v['title']}\"\n🖊 {len(v['questions'])} Qs · ⏱ {v['timer']}s"
            kb = [[InlineKeyboardButton("Start Quiz ↗️", url=f"https://t.me/{bme.username}?start=quiz_{k}")]]
            res.append(InlineQueryResultArticle(id=k, title=v["title"], description=f"{len(v['questions'])} Qs", input_message_content=InputTextMessageContent(txt), reply_markup=InlineKeyboardMarkup(kb)))
    await update.inline_query.answer(res[:10], cache_time=1)

async def handle_http(request): return web.Response(text="Bot Online 24/7!")

async def keep_alive():
    await asyncio.sleep(30)
    while True:
        try: urllib.request.urlopen("https://quizbot-1vsr.onrender.com", timeout=10)
        except Exception: pass
        await asyncio.sleep(300)

async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    for cmd, h in [("start",start),("store",store),("mystore",mystore),("create_quiz",create_quiz),("done",finalize_quiz),("approve",approve_payment),("stop",stop_quiz)]:
        app.add_handler(CommandHandler(cmd, h))
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    server = web.Application()
    server.router.add_get("/", handle_http)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()
    asyncio.create_task(keep_alive())
    print("Bot Started...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    while True: await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
