import os, asyncio, io, re, json, random, time, urllib.request
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

AI_PROMPT = """Extract all multiple-choice questions from this book image. Format strictly:
Q1. Question text
A) Opt 1
B) Opt 2
C) Opt 3
D) Opt 4
Answer: Correct Letter (A/B/C/D)
Leave blank line between questions."""

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **Dulhin Bazar Quiz Bot**\n\n🔹 `/create_quiz` - Create Quiz\n🔹 `/store` - Quiz Store\n🔹 `/stop` - Stop Quiz", parse_mode="Markdown")

async def create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    creation_state[update.effective_user.id] = {"title": "", "subject": "General", "price": 0, "timer": 15, "questions": [], "step": "TITLE"}
    await update.message.reply_text("📝 Quiz ka **Title / Naam** bhejein:")

async def finalize_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or not creation_state[uid]["questions"]:
        await update.message.reply_text("⚠️ Pehle questions add karein!")
        return
    st = creation_state[uid]
    qid = f"q_{int(time.time())}"
    db["quizzes"][qid] = {"id": qid, "title": st["title"], "subject": st["subject"], "price": st["price"], "timer": st["timer"], "owner": uid, "questions": st["questions"]}
    db["purchases"].setdefault(str(uid), []).append(qid)
    save_data()
    del creation_state[uid]
    bme = await context.bot.get_me()
    kb = [[InlineKeyboardButton("Start Quiz ↗️", url=f"https://t.me/{bme.username}?start=quiz_{qid}")]]
    await update.message.reply_text(f"🎉 **Quiz Saved!**\n🎲 *{db['quizzes'][qid]['title']}* ({len(db['quizzes'][qid]['questions'])} Qs)", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or creation_state[uid]["step"] != "QUESTIONS":
        return
    msg = await update.message.reply_text("🔍 AI Scanning photo...")
    try:
        f = await (await context.bot.get_file(update.message.photo[-1].file_id)).download_as_bytearray()
        qs = parse_questions(ai_model.generate_content([AI_PROMPT, Image.open(io.BytesIO(f))]).text)
        if qs:
            creation_state[uid]["questions"].extend(qs)
            await msg.edit_text(f"✅ AI ne {len(qs)} Qs add kiye! Total: {len(creation_state[uid]['questions'])}\nAur photos bhejein ya save ke liye `/done` likhein.")
        else:
            await msg.edit_text("⚠️ Image saaf nahi aayi.")
    except Exception:
        await msg.edit_text("⚠️ OCR Error.")

async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or creation_state[uid]["step"] != "QUESTIONS":
        return
    f = await (await context.bot.get_file(update.message.document.file_id)).download_as_bytearray()
    qs = parse_questions(f.decode("utf-8", errors="ignore"))
    if qs:
        creation_state[uid]["questions"].extend(qs)
        await update.message.reply_text(f"📁 {len(qs)} Qs add hue! Total: {len(creation_state[uid]['questions'])}\nSave ke liye `/done` bhejein.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if uid not in creation_state:
        return
    st = creation_state[uid]
    if st["step"] == "TITLE":
        st["title"] = text
        st["step"] = "SUBJECT"
        await update.message.reply_text(f"✅ Title: *{text}*\nAb **Subject** likhein:", parse_mode="Markdown")
    elif st["step"] == "SUBJECT":
        st["subject"] = text
        st["step"] = "PRICE"
        kb = [[InlineKeyboardButton("Free (₹0)", callback_data="p_0"), InlineKeyboardButton("₹21", callback_data="p_21")], [InlineKeyboardButton("₹49", callback_data="p_49")]]
        await update.message.reply_text("💰 Price select karein:", reply_markup=InlineKeyboardMarkup(kb))
    elif st["step"] == "QUESTIONS":
        if text.lower() in ["/done", "done"]:
            await finalize_quiz(update, context)
            return
        qs = parse_questions(text)
        if qs:
            st["questions"].extend(qs)
            await update.message.reply_text(f"✅ {len(qs)} Qs add hue! Total: {len(st['questions'])}\nSave ke liye `/done` likhein.")

async def store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.get("quizzes"):
        await update.message.reply_text("Store khali hai. `/create_quiz` karein.")
        return
    kb = []
    for qid, q in db["quizzes"].items():
        price_tag = "FREE" if q["price"] == 0 else f"₹{q['price']}"
        btn_title = f"{q['title']} - {price_tag}"
        kb.append([InlineKeyboardButton(btn_title, callback_data=f"view_{qid}")])
    await update.message.reply_text("📚 **Quiz Store:**", reply_markup=InlineKeyboardMarkup(kb))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = update.effective_user.id
    cid = update.effective_chat.id

    if d.startswith("p_"):
        creation_state[uid]["price"] = int(d.split("_")[1])
        creation_state[uid]["step"] = "QUESTIONS"
        await q.edit_message_text("📸 **Ab direct book page ki PHOTO bhejein** ya `.txt` file bhej kar `/done` karein.")
    elif d.startswith("view_"):
        qid = d.split("_", 1)[1]
        qz = db["quizzes"].get(qid)
        if not qz:
            return
        has = qz["price"] == 0 or qid in db.get("purchases", {}).get(str(uid), []) or qz.get("owner") == uid
        price_tag = "FREE" if qz["price"] == 0 else f"₹{qz['price']}"
        card = f"🎲 '*{qz['title']}*'\n📁 {qz['subject']} | ✒️ {len(qz['questions'])} Qs | ⏱ {qz['timer']}s\n💰 Price: *{price_tag}*"
        if has:
            kb = [[InlineKeyboardButton("I am ready!", callback_data=f"start_{qid}")]]
        else:
            kb = [[InlineKeyboardButton(f"💳 Buy Now (₹{qz['price']})", callback_data=f"buy_{qid}")]]
        await q.edit_message_text(card, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    elif d.startswith("buy_"):
        qz = db["quizzes"].get(d.split("_", 1)[1])
        await q.edit_message_text(f"💳 Pay ₹{qz['price']} to UPI: `{UPI_ID}`\nBhejein details Admin ko:\nUID: `{uid}` | QID: `{qz['id']}`", parse_mode="Markdown")
    elif d.startswith("start_"):
        qid = d.split("_", 1)[1]
        active_sessions[cid] = {"quiz": db["quizzes"][qid], "stats": {}, "map": {}, "run": True}
        await q.edit_message_text("🚀 Starting quiz...")
        asyncio.create_task(run_quiz(cid, context))

async def run_quiz(chat_id, context: ContextTypes.DEFAULT_TYPE):
    s = active_sessions.get(chat_id)
    if not s:
        return
    for idx, qd in enumerate(s["quiz"]["questions"]):
        if not s.get("run"):
            break
        try:
            m = await context.bot.send_poll(chat_id, f"Q{idx+1}. {qd['question']}", qd["options"], type=Poll.QUIZ, correct_option_id=qd["correct_id"], open_period=s["quiz"]["timer"], is_anonymous=False)
            s["map"][m.poll.id] = qd["correct_id"]
        except Exception:
            continue
        for _ in range(s["quiz"]["timer"] + 2):
            if not s.get("run"):
                break
            await asyncio.sleep(1)
    if s.get("run"):
        res = f"🏁 '*{s['quiz']['title']}*' finished!\n\n"
        for r, u in enumerate(sorted(s["stats"].values(), key=lambda x: x["c"], reverse=True), 1):
            res += f"👤 *{u['n']}*: ✅ {u['c']} | ❌ {u['w']}\n"
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

async def stop_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id in active_sessions:
        active_sessions[update.effective_chat.id]["run"] = False
        await update.message.reply_text("🛑 Quiz Stopped.")
    else:
        await update.message.reply_text("Koi active quiz nahi hai.")

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
    for c, h in [("start", start), ("create_quiz", create_quiz), ("done", finalize_quiz), ("store", store), ("stop", stop_quiz)]:
        app.add_handler(CommandHandler(c, h))
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
