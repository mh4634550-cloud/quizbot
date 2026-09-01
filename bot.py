import os, asyncio, io, re, json, random, time, urllib.request, urllib.parse, base64
from aiohttp import web, ClientSession
from PIL import Image
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, PollAnswerHandler, ContextTypes, filters

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors

TOKEN = os.environ.get("BOT_TOKEN")
UPI_ID = os.environ.get("UPI_ID", "marufhussain318-2@oksbi")

API_KEY = (
    os.environ.get("GroqCloud") 
    or os.environ.get("GEMINI_KEY") 
    or os.environ.get("GROQ_API_KEY")
)
DATA_FILE = "quiz_db.json"

if not TOKEN or not API_KEY:
    raise ValueError("BOT_TOKEN ya API Key environment variable me set nahi hai!")

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

AI_OCR_PROMPT = """Read this book page very carefully. Extract EVERY single historical fact, date, king name, event, battle, book, reform, and list item.
Convert all information into Multiple Choice Questions (MCQs) in Hindi/Hinglish.

Strict Format for each question:
Q1. Question text here?
A) Option 1
B) Option 2
C) Option 3
D) Option 4
Answer: Correct Option Letter (A/B/C/D)

Leave a blank line between each question. Output ONLY questions and answers."""

async def generate_ai_text(prompt, image_bytes=None):
    if API_KEY.startswith("gsk_"):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        if image_bytes:
            b64_img = base64.b64encode(image_bytes).decode('utf-8')
            payload = {
                "model": "llama-3.2-11b-vision-preview",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                        ]
                    }
                ],
                "temperature": 0.3
            }
        else:
            payload = {
                "model": "llama3-70b-8192",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4
            }

        async with ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data["choices"][0]["message"]["content"]
                else:
                    raise Exception(f"Groq API Error: {str(data)[:100]}")
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={API_KEY}"
        parts = []
        if image_bytes:
            b64_data = base64.b64encode(image_bytes).decode('utf-8')
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64_data}})
        parts.append({"text": prompt})
        
        async with ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": parts}]}, headers={"Content-Type": "application/json"}) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                else:
                    raise Exception(f"Gemini API Error: {str(data)[:100]}")

def parse_questions(text):
    out = []
    blocks = re.split(r"\n\s*\n|(?=\n\s*(?:Q\s*\d+[\.\)]|\d+[\.\)]))", text)
    for b in blocks:
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

def generate_pdf_buffer(title, content_text):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    
    def draw_header():
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, height - 40, f"Dulhin Bazar Notes: {title[:40]}")
        c.setStrokeColor(colors.HexColor("#0088cc"))
        c.setLineWidth(1.5)
        c.line(40, height - 45, width - 40, height - 45)
        c.setFont("Helvetica", 9)

    draw_header()
    y = height - 65
    clean_text = content_text.replace("**", "").replace("##", "").replace("*", "•")
    lines = clean_text.split("\n")
    
    for line in lines:
        wrapped_chunks = [line[i:i+90] for i in range(0, len(line), 90)] if line else [""]
        for chunk in wrapped_chunks:
            if y < 55:
                c.showPage()
                draw_header()
                y = height - 65
            c.drawString(40, y, chunk)
            y -= 14
            
    c.save()
    buf.seek(0)
    return buf

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Create Quiz (Photo to Quiz)", callback_data="menu_create")],
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

    text = "👋 **Dulhin Bazar Study & Quiz Bot**\n\n📄 **Instant PDF Notes:** Chat me kisi bhi subject/chapter ka naam likhein.\n\n🎲 **Quiz Features:** Neeche diye buttons use karein."
    if update.message:
        await update.message.reply_text(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def show_quiz_card(chat_id, qid, uid, context: ContextTypes.DEFAULT_TYPE):
    qz = db.get("quizzes", {}).get(qid)
    if not qz:
        await context.bot.send_message(chat_id, "⚠️ Quiz nahi mila.")
        return
    bme = await context.bot.get_me()
    has = qz["price"] == 0 or qid in db.get("purchases", {}).get(str(uid), []) or qz.get("owner") == uid
    price_tag = "FREE" if qz["price"] == 0 else f"₹{qz['price']}"
    card = f"🎲 '*{qz['title']}*'\n\n📁 Subject: *{qz['subject']}*\n✒️ Total: *{len(qz['questions'])} Qs*\n⏱ Timer: *{qz['timer']}s*\n💰 Price: *{price_tag}*"
    
    share_url = f"https://t.me/share/url?url=https://t.me/{bme.username}?start=quiz_{qid}&text={urllib.parse.quote('Take Quiz: ' + qz['title'])}"
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

async def handle_pdf_request(topic_name, update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(f"⏳ **Generating PDF for:** `{topic_name}`...\n⏱ Time: ~3-5 seconds", parse_mode="Markdown")
    prompt = f"""Write detailed, comprehensive, high-yield exam revision notes and 10 top MCQs on the topic: '{topic_name}'.
Include:
1. Core Concepts & Chronology / Key Facts
2. Important Exam Points (Bullet format)
3. 10 Most Expected Multiple Choice Questions with Answers at the end.
Language: Clear English and Hindi readable format."""

    try:
        raw_text = await generate_ai_text(prompt)
        pdf_file = await asyncio.to_thread(generate_pdf_buffer, topic_name, raw_text)
        clean_name = re.sub(r'[^a-zA-Z0-9]', '_', topic_name)
        pdf_file.name = f"{clean_name}_Notes.pdf"
        
        await update.message.reply_document(
            document=pdf_file,
            filename=f"{clean_name}_Notes.pdf",
            caption=f"📚 **Complete PDF Notes:** {topic_name}\n🚀 Generated by Dulhin Bazar Bot",
            parse_mode="Markdown"
        )
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"⚠️ PDF Error: {str(e)[:120]}")

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
        await update.message.reply_text("⚠️ Pehle book page ki photo ya questions add karein!")
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
    share_url = f"https://t.me/share/url?url=https://t.me/{bme.username}?start=quiz_{qid}&text={urllib.parse.quote('Solve Quiz: ' + db['quizzes'][qid]['title'])}"
    group_url = f"https://t.me/{bme.username}?startgroup=quiz_{qid}"
    
    kb = [
        [InlineKeyboardButton("🚀 Start Quiz", callback_data=f"startready_{qid}")],
        [InlineKeyboardButton("👥 Add to Group", url=group_url)],
        [InlineKeyboardButton("↗️ Share Link", url=share_url)]
    ]
    
    await update.message.reply_text(
        f"🎉 **Quiz Created!**\n\n🎲 Title: *{db['quizzes'][qid]['title']}* ({len(db['quizzes'][qid]['questions'])} Qs)\n👉 Command: `/start_quiz_{qid}`",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

async def process_image_bytes(p_bytes, msg, uid):
    try:
        pil_img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
        pil_img.thumbnail((2048, 2048))
        
        img_byte_arr = io.BytesIO()
        pil_img.save(img_byte_arr, format='JPEG', quality=85)
        raw_bytes = img_byte_arr.getvalue()
        
        res_text = await generate_ai_text(AI_OCR_PROMPT, image_bytes=raw_bytes)
        qs = parse_questions(res_text)
        
        if qs:
            creation_state[uid]["questions"].extend(qs)
            await msg.edit_text(f"✅ AI ne is page se **{len(qs)} Questions** banaye!\nTotal Questions: **{len(creation_state[uid]['questions'])}**\n\nAur photos bhejein ya complete karne ke liye `/done` bhejein.")
        else:
            await msg.edit_text("⚠️ Questions extract nahi ho sake. Clear photo bhejein.")
    except Exception as e:
        await msg.edit_text(f"⚠️ Scan error: {str(e)[:120]}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or creation_state[uid]["step"] != "QUESTIONS":
        return
    msg = await update.message.reply_text("🔍 AI Deep Page Scan chal raha hai (3-5 sec)...")
    f = await (await context.bot.get_file(update.message.photo[-1].file_id)).download_as_bytearray()
    await process_image_bytes(f, msg, uid)

async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in creation_state or creation_state[uid]["step"] != "QUESTIONS":
        return
    doc = update.message.document
    f = await (await context.bot.get_file(doc.file_id)).download_as_bytearray()
    if (doc.mime_type and doc.mime_type.startswith("image/")) or doc.file_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        msg = await update.message.reply_text("🔍 AI Deep Page Scan chal raha hai...")
        await process_image_bytes(f, msg, uid)
    else:
        qs = parse_questions(f.decode("utf-8", errors="ignore"))
        if qs:
            creation_state[uid]["questions"].extend(qs)
            await update.message.reply_text(f"📁 **{len(qs)} Qs** add hue! Total: {len(creation_state[uid]['questions'])}\nSave ke liye `/done` likhein.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    
    if text.startswith("/start_quiz_"):
        qid = text.replace("/start_quiz_", "")
        await show_quiz_card(update.effective_chat.id, qid, uid, context)
        return

    if uid not in creation_state:
        if text.startswith("/"):
            return
        await handle_pdf_request(text, update, context)
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
            await update.message.reply_text(f"✅ {len(qs)} Qs add hue! Total: {len(st['questions'])}\nSave karne ke liye `/done` likhein.")

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
    
    text = "📚 **Quiz Store:**"
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
        await q.edit_message_text("👋 **Dulhin Bazar Quiz & Study Menu:**", reply_markup=get_main_keyboard(), parse_mode="Markdown")
    elif d == "menu_create":
        creation_state[uid] = {"title": "", "subject": "General", "price": 0, "timer": 15, "questions": [], "step": "TITLE"}
        await q.edit_message_text("📝 Quiz ka **Title / Naam** likh kar bhejein:")
    elif d == "menu_store":
        await store_cmd(update, context)
    elif d == "menu_stop":
        await stop_quiz_cmd(update, context)
    elif d.startswith("p_"):
        creation_state[uid]["price"] = int(d.split("_")[1])
        creation_state[uid]["step"] = "QUESTIONS"
        await q.edit_message_text("📸 **Ab direct book page ki PHOTO bhejein** (AI questions extract karega) ya text bhej kar `/done` karein.")
    elif d.startswith("view_"):
        qid = d.split("_", 1)[1]
        await show_quiz_card(cid, qid, uid, context)
    elif d.startswith("buy_"):
        qz = db["quizzes"].get(d.split("_", 1)[1])
        await q.edit_message_text(f"💳 **Pay ₹{qz['price']} to UPI:** `{UPI_ID}`\n\nPayment details Admin ko bhejein:\n🆔 UID: `{uid}` | QID: `{qz['id']}`", parse_mode="Markdown")
    elif d.startswith("startready_"):
        qid = d.split("_", 1)[1]
        active_sessions[cid] = {"quiz": db["quizzes"][qid], "stats": {}, "map": {}, "run": True}
        
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
    app.add_handler(MessageHandler(filters.TEXT, handle_text))

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
