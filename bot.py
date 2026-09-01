import os, asyncio, io, re, json, random, time, urllib.request, urllib.parse
from aiohttp import web
from PIL import Image
import google.generativeai as genai
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, PollAnswerHandler, ContextTypes, filters

TOKEN = "8736461994:AAHl06AxkYQmRudfV3r2AgLYQVlUV8mMoHU"
UPI_ID = "marufhussain318-2@oksbi"
GEMINI_KEY = "AQ.Ab8RN6JacArVio7NBYlubfksQK8a9q9G2u4UyCzvJKqT56nF0Q"
DATA_FILE = "quiz_db.json"

genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel("gemini-1.5-flash")

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"quizzes": {}, "purchases": {}}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

db = load_data()
creation_state = {}
active_sessions = {}

AI_PROMPT = """Analyze this image thoroughly. Extract and generate MAXIMUM possible multiple-choice questions from EVERY single fact, sentence, bullet point, table, or line present on this book page.

Format strictly as:
Q1. Question text
A) Option 1
B) Option 2
C) Option 3
D) Option 4
Answer: Correct Option Letter (A/B/C/D)

Leave a blank line between each question. Do not include introductory or closing remarks."""

def parse_questions(text):
    out = []
    for b in re.split(r"\n\s*\n|(?=\n\s*(?:Q\s*\d+[\.\)]|\d+[\.\)]))", text):
        lines = [l.strip() for l in b.split("\n") if l.strip()]
        if len(lines) < 3:
            continue
        q_txt = re.sub(r"^(Q\s*\d+[\.\)]|\d+[\.\)])\s*", "", lines[0]).strip()
        opts = []
        c_idx = 0
        for l in lines[1:]:
            if re.match(r"^[\(\[]?[A-Da-d1-4][\)\]\.]", l):
                opts.append(re.sub(r"^[\(\[]?[A-Da-d1-4][\)\]\.]\s*", "", l).strip()[:100])
            elif any(k in l for k in ["उत्तर", "Answer", "Ans", "ans"]):
                m = re.search(r"[\(\[]?([A-Da-d1-4])[\)\]]?", l.split(":")[-1])
                if m:
                    c_idx = {"A": 0, "1": 0, "B": 1, "2": 1, "C": 2, "3": 2, "D": 3, "4": 3}.get(m.group(1).upper(), 0)
        if len(opts) >= 2:
            out.append({"question": q_txt[:280], "options": opts[:4], "correct_id": min(c_idx, len(opts) - 1)})
    return out

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Create Quiz", callback_data="menu_create")],
        [InlineKeyboardButton("📚 Quiz Store", callback_data="menu_store")],
        [InlineKeyboardButton("🛑 Stop Running Quiz", callback_data="menu_stop")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        arg = context.args[0]
        if arg.startswith("quiz_"):
            qid = arg.replace("quiz_", "")
            await show_quiz_card(update.effective_chat.id, qid, update.effective_user.id, context)
            return

    text = "👋 **Welcome to Dulhin Bazar Quiz Bot!**\n\nNeeche diye gaye buttons par click karke bot use karein:"
    if update.message:
        await update.message.reply_text(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def show_quiz_card(chat_id, qid, uid, context: ContextTypes.DEFAULT_TYPE):
    qz = db.get("quizzes", {}).get(qid)
    if not qz:
        await context.bot.send_message(chat_id, "⚠️ Yeh Quiz mojood nahi hai.")
        return
    bme = await context.bot.get_me()
    has = qz["price"] == 0 or qid in db.get("purchases", {}).get(str(uid), []) or qz.get("owner") == uid
    price_tag = "FREE" if qz["price"] == 0 else f"₹{qz['price']}"
    card = f"🎲 '*{qz['title']}*'\n\n📁 Subject: *{qz['subject']}*\n✒️ Total: *{len(qz['questions'])} Qs*\n⏱ Timer: *{qz['timer']}s*\n💰 Price: *{price_tag}*"
    
    share_url = f"https://t.me/share/url?url=https://t.me/{bme.username}?start=quiz_{qid}&text={urllib.parse.quote('Take this Quiz: ' + qz['title'])}"
    group_url = f"https://t.me/{bme.username}?startgroup=quiz_{qid}"
    
    kb = []
    if has:
        kb.append([InlineKeyboardButton("🚀 Start Quiz (I am ready!)", callback_data=f"startready_{qid}")])
    else:
        kb.append([InlineKeyboardButton(f"💳 Buy Now (₹{qz['price']})", callback_data=f"buy_{qid}")])
    kb.append([InlineKeyboardButton("👥 Start in Group", url=group_url)])
    kb.append([InlineKeyboardButton("↗️ Share Quiz Link", url=share_url)])
    kb.append([InlineKeyboardButton("⬅️ Back to Store", callback_data="menu_store")])

    await context.bot.send_message(chat_id, card, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def create_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    creation_state[uid] = {"title": "", "subject": "General", "price": 0, "timer": 15, "questions": [], "step": "TITLE"}
    msg = "📝 Quiz ka **Title / Naam** likh kar bhejein:"
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.reply_text(msg, parse_mode="Markdown")

async def finalize_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or not creation_state[uid]["questions"]:
        await update.message.reply_text("⚠️ Pehle book page ki photo ya text bhejein!")
        return
    st = creation_state[uid]
    qid = f"q_{int(time.time())}"
    db["quizzes"][qid] = {
        "id": qid,
        "title": st["title"],
        "subject": st["subject"],
        "price": st["price"],
        "timer": st["timer"],
        "owner": uid,
        "questions": st["questions"]
    }
    db["purchases"].setdefault(str(uid), []).append(qid)
    save_data()
    del creation_state[uid]

    bme = await context.bot.get_me()
    share_url = f"https://t.me/share/url?url=https://t.me/{bme.username}?start=quiz_{qid}&text={urllib.parse.quote('Solve this Quiz: ' + db['quizzes'][qid]['title'])}"
    group_url = f"https://t.me/{bme.username}?startgroup=quiz_{qid}"
    
    kb = [
        [InlineKeyboardButton("🚀 Start Quiz", callback_data=f"startready_{qid}")],
        [InlineKeyboardButton("👥 Add to Group", url=group_url)],
        [InlineKeyboardButton("↗️ Share with Friends", url=share_url)]
    ]
    
    direct_cmd = f"/start_quiz_{qid}"
    await update.message.reply_text(
        f"🎉 **Quiz Successfully Created & Saved!**\n\n🎲 **Title:** {db['quizzes'][qid]['title']}\n📊 **Total Qs:** {len(db['quizzes'][qid]['questions'])}\n\n👉 Direct Start Command: `{direct_cmd}`\n👉 Start link: `https://t.me/{bme.username}?start=quiz_{qid}`",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

async def process_image_bytes(p_bytes, msg, uid):
    try:
        res = ai_model.generate_content([AI_PROMPT, Image.open(io.BytesIO(p_bytes))]).text
        qs = parse_questions(res)
        if qs:
            creation_state[uid]["questions"].extend(qs)
            await msg.edit_text(f"✅ AI ne is page se **{len(qs)} Questions** banaye!\nTotal Questions: **{len(creation_state[uid]['questions'])}**\n\nAur photo bhejein ya complete karne ke liye `/done` bhejein.")
        else:
            await msg.edit_text("⚠️ Image saaf nahi aayi ya questions generate nahi ho sake. Dusri clear photo bhejein.")
    except Exception:
        await msg.edit_text("⚠️ OCR Scan error. Kripya saaf photo upload karein.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or creation_state[uid]["step"] != "QUESTIONS":
        return
    msg = await update.message.reply_text("🔍 AI Deep Scan chal raha hai...")
    f = await (await context.bot.get_file(update.message.photo[-1].file_id)).download_as_bytearray()
    await process_image_bytes(f, msg, uid)

async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or creation_state[uid]["step"] != "QUESTIONS":
        return
    doc = update.message.document
    f = await (await context.bot.get_file(doc.file_id)).download_as_bytearray()
    if (doc.mime_type and doc.mime_type.startswith("image/")) or doc.file_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        msg = await update.message.reply_text("🔍 AI Deep Scanning image file...")
        await process_image_bytes(f, msg, uid)
    else:
        qs = parse_questions(f.decode("utf-8", errors="ignore"))
        if qs:
            creation_state[uid]["questions"].extend(qs)
            await update.message.reply_text(f"📁 **{len(qs)} Qs** add hue! Total: {len(creation_state[uid]['questions'])}\nComplete karne ke liye `/done` bhejein.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    
    if text.startswith("/start_quiz_"):
        qid = text.replace("/start_quiz_", "")
        await show_quiz_card(update.effective_chat.id, qid, uid, context)
        return

    if uid not in creation_state:
        return

    st = creation_state[uid]
    if st["step"] == "TITLE":
        st["title"] = text
        st["step"] = "SUBJECT"
        await update.message.reply_text(f"✅ Title: *{text}*\nAb **Subject** likhein (e.g. History, GK, Science):", parse_mode="Markdown")
    elif st["step"] == "SUBJECT":
        st["subject"] = text
        st["step"] = "PRICE"
        kb = [[InlineKeyboardButton("Free (₹0)", callback_data="p_0"), InlineKeyboardButton("₹21", callback_data="p_21")], [InlineKeyboardButton("₹49", callback_data="p_49")]]
        await update.message.reply_text("💰 Price chunein:", reply_markup=InlineKeyboardMarkup(kb))
    elif st["step"] == "QUESTIONS":
        if text.lower() in ["/done", "done"]:
            await finalize_quiz(update, context)
            return
        qs = parse_questions(text)
        if qs:
            st["questions"].extend(qs)
            await update.message.reply_text(f"✅ {len(qs)} Qs add hue! Total: {len(st['questions'])}\nSave karne ke liye `/done` bhejein.")

async def store_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.get("quizzes"):
        msg = "📂 Store khali hai. Pehle `/create_quiz` karein."
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.edit_message_text(msg)
        return
    kb = []
    for qid, q in db["quizzes"].items():
        price_tag = "FREE" if q["price"] == 0 else f"₹{q['price']}"
        kb.append([InlineKeyboardButton(f"🎲 {q['title']} ({len(q['questions'])} Qs) - {price_tag}", callback_data=f"view_{qid}")])
    kb.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="menu_main")])
    
    text = "📚 **Quiz Store - Apna Quiz Select Karein:**"
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = update.effective_user.id
    cid = update.effective_chat.id

    if d == "menu_main":
        await q.edit_message_text("👋 **Dulhin Bazar Quiz Bot Menu:**", reply_markup=get_main_keyboard(), parse_mode="Markdown")
    elif d == "menu_create":
        creation_state[uid] = {"title": "", "subject": "General", "price": 0, "timer": 15, "questions": [], "step": "TITLE"}
        await q.edit_message_text("📝 Quiz ka **Title / Naam** likh kar chat mein bhejein:")
    elif d == "menu_store":
        await store_cmd(update, context)
    elif d == "menu_stop":
        await stop_quiz_cmd(update, context)
    elif d.startswith("p_"):
        creation_state[uid]["price"] = int(d.split("_")[1])
        creation_state[uid]["step"] = "QUESTIONS"
        await q.edit_message_text("📸 **Ab direct book page ki PHOTO bhejein** (AI saare points extract karega) ya text bhej kar `/done` karein.")
    elif d.startswith("view_"):
        qid = d.split("_", 1)[1]
        await show_quiz_card(cid, qid, uid, context)
    elif d.startswith("buy_"):
        qz = db["quizzes"].get(d.split("_", 1)[1])
        await q.edit_message_text(f"💳 **Pay ₹{qz['price']} to UPI:** `{UPI_ID}`\n\nPayment screenshot ke sath Admin ko details bhejein:\n🆔 UID: `{uid}`\n🆔 QID: `{qz['id']}`", parse_mode="Markdown")
    elif d.startswith("startready_"):
        qid = d.split("_", 1)[1]
        active_sessions[cid] = {"quiz": db["quizzes"][qid], "stats": {}, "map": {}, "run": True}
        
        # Dramatic Countdown Sequence
        cd_msg = await q.edit_message_text("🔥 **Ready...**", parse_mode="Markdown")
        await asyncio.sleep(1)
        await cd_msg.edit_text("⏳ **3...**", parse_mode="Markdown")
        await asyncio.sleep(1)
        await cd_msg.edit_text("⏳ **2...**", parse_mode="Markdown")
        await asyncio.sleep(1)
        await cd_msg.edit_text("⏳ **1...**", parse_mode="Markdown")
        await asyncio.sleep(1)
        await cd_msg.edit_text("🚀 **Set... GO!**", parse_mode="Markdown")
        await asyncio.sleep(0.5)
        
        asyncio.create_task(run_quiz(cid, context))

async def run_quiz(chat_id, context: ContextTypes.DEFAULT_TYPE):
    s = active_sessions.get(chat_id)
    if not s:
        return
    for idx, qd in enumerate(s["quiz"]["questions"]):
        if not s.get("run"):
            break
        try:
            m = await context.bot.send_poll(
                chat_id=chat_id,
                question=f"Q{idx+1}. {qd['question']}",
                options=qd["options"],
                type=Poll.QUIZ,
                correct_option_id=qd["correct_id"],
                open_period=s["quiz"]["timer"],
                is_anonymous=False
            )
            s["map"][m.poll.id] = qd["correct_id"]
        except Exception:
            continue
        for _ in range(s["quiz"]["timer"] + 2):
            if not s.get("run"):
                break
            await asyncio.sleep(1)
    if s.get("run"):
        res = f"🏁 '*{s['quiz']['title']}*' Finished!\n\n🏆 **Leaderboard:**\n"
        for r, u in enumerate(sorted(s["stats"].values(), key=lambda x: x["c"], reverse=True), 1):
            res += f"{r}. 👤 *{u['n']}*: ✅ {u['c']} | ❌ {u['w']}\n"
        await context.bot.send_message(chat_id, res if s["stats"] else "🏁 Quiz Finished!", parse_mode="Markdown")
        if chat_id in active_sessions:
            del active_sessions[chat_id]

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = update.poll_answer
    for s in active_sessions.values():
        if ans.poll_id in s["map"]:
            u = s["stats"].setdefault(ans.user.id, {"n": ans.user.first_name, "c": 0, "w": 0})
            if ans.option_ids and ans.option_ids[0] == s["map"][ans.poll_id]:
                u["c"] += 1
            else:
                u["w"] += 1

async def stop_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if cid in active_sessions:
        active_sessions[cid]["run"] = False
        msg = "🛑 Quiz stopped successfully."
    else:
        msg = "⚠️ Koi active quiz nahi chal raha hai."
        
    if update.message:
        await update.message.reply_text(msg)
    elif update.callback_query:
        await update.callback_query.edit_message_text(msg)

async def handle_http(request):
    return web.Response(text="Bot Online 24/7!")

async def keep_alive():
    await asyncio.sleep(30)
    while True:
        try:
            urllib.request.urlopen("https://quizbot-1vsr.onrender.com", timeout=10)
        except Exception:
            pass
        await asyncio.sleep(300)

async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("create_quiz", create_quiz_cmd))
    app.add_handler(CommandHandler("done", finalize_quiz))
    app.add_handler(CommandHandler("store", store_cmd))
    app.add_handler(CommandHandler("stop", stop_quiz_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PollAnswerHandler(handle_answer))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    server = web.Application()
    server.router.add_get("/", handle_http)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()
    asyncio.create_task(keep_alive())
    print("Bot Live...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
