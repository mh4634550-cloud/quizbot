import os, asyncio, io, re, json, random, time, urllib.request, urllib.parse, base64
from aiohttp import web, ClientSession
from PIL import Image, ImageOps, ImageFilter
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, PollAnswerHandler, ContextTypes, filters
from gtts import gTTS

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

TOKEN = os.environ.get("BOT_TOKEN")
UPI_ID = os.environ.get("UPI_ID", "marufhussain318-2@oksbi")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("GroqCloud")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
API_KEY = GROQ_API_KEY or GEMINI_KEY

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
search_state = {}

AI_OCR_PROMPT = """You are an expert OCR + exam question generator.
Read the ENTIRE uploaded page carefully. This can be NCERT, Science, Social Science, History, Geography, Civics, Biology, Chemistry, Physics, Maths, GK, or any study material.

Rules:
- Read all visible printed text, headings, bullets, tables, labels, captions and important diagram text.
- Do NOT invent facts that are not present on the page.
- Preserve names, numbers, dates, scientific terms and spellings as accurately as possible.
- Generate 8-15 objective MCQs from the information actually visible on this page. If the page has less information, generate fewer questions rather than inventing.
- Use Hindi in Devanagari script. English technical/scientific terms may remain in English when appropriate. Do NOT use Roman Hindi/Hinglish.
- Every question must have exactly 4 options and one correct answer.

Strict format:
Q1. प्रश्न?
A) विकल्प 1
B) विकल्प 2
C) विकल्प 3
D) विकल्प 4
Answer: A

Output ONLY MCQs. No introduction, no explanation."""

AI_TEXT_TO_QUIZ_PROMPT = """You are an expert Indian exam question generator.
Create objective MCQs ONLY from the source text below. The source may be NCERT Class 6-12 Hindi-medium Science, Social Science, History, Geography, Political Science, Economics, Biology, Chemistry, Physics, or another study chapter.

Rules:
- Do not add facts that are not supported by the source text.
- Use Hindi Devanagari script. Do NOT write Roman Hindi/Hinglish.
- Create 10-20 high-quality MCQs depending on source length.
- Exactly 4 options per question.
- Exactly one correct answer.

Strict format:
Q1. प्रश्न?
A) विकल्प 1
B) विकल्प 2
C) विकल्प 3
D) विकल्प 4
Answer: A

SOURCE TEXT:
"""


async def generate_ai_text(prompt, image_bytes=None, use_web=False):
    """Generate AI text. Groq is primary; Gemini can be used independently as fallback."""
    timeout = 120

    async def groq_call():
        if not GROQ_API_KEY:
            raise Exception("GROQ_API_KEY not configured")
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        if image_bytes:
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            payload = {
                "model": "qwen/qwen3.6-27b",
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                ]}],
                "temperature": 0.1, "max_completion_tokens": 5000
            }
        else:
            payload = {
                "model": "groq/compound-mini" if use_web else "openai/gpt-oss-120b",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2, "max_completion_tokens": 4000
            }
            if use_web:
                payload["search_settings"] = {"country": "IN"}
        timeout_obj = __import__("aiohttp").ClientTimeout(total=timeout)
        async with ClientSession(timeout=timeout_obj) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                raw = await resp.text()
                try: data = json.loads(raw)
                except Exception: data = {"raw": raw}
                if resp.status != 200:
                    raise Exception(f"Groq HTTP {resp.status}: {str(data)[:400]}")
                return data["choices"][0]["message"]["content"]

    async def gemini_call():
        if not GEMINI_KEY:
            raise Exception("GEMINI_KEY not configured")
        model = "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        parts = []
        if image_bytes:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode("utf-8")}})
        parts.append({"text": prompt})
        timeout_obj = __import__("aiohttp").ClientTimeout(total=timeout)
        async with ClientSession(timeout=timeout_obj) as session:
            async with session.post(url, json={"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.1}}, headers={"Content-Type": "application/json"}) as resp:
                raw = await resp.text()
                try: data = json.loads(raw)
                except Exception: data = {"raw": raw}
                if resp.status != 200:
                    raise Exception(f"Gemini HTTP {resp.status}: {str(data)[:400]}")
                return data["candidates"][0]["content"]["parts"][0]["text"]

    errors = []
    if GROQ_API_KEY:
        try:
            return await groq_call()
        except Exception as e:
            errors.append(str(e))
    if GEMINI_KEY:
        try:
            return await gemini_call()
        except Exception as e:
            errors.append(str(e))
    raise Exception("AI failed: " + " | ".join(errors)[:700])

def create_welcome_audio():
    buf = io.BytesIO()
    speech_text = "Hii! My name is Maruf. Kaise ho aap log? Aap logon ka swagat hai mere bot mein. Welcome to my quiz bot!"
    tts = gTTS(text=speech_text, lang='hi', slow=False)
    tts.write_to_fp(buf)
    buf.seek(0)
    buf.name = "welcome.mp3"
    return buf

def create_winner_audio(winner_name):
    buf = io.BytesIO()
    clean_name = re.sub(r'[^a-zA-Z0-9 ]', '', winner_name).strip() or "Champion"
    tts_text = f"Congratulations {clean_name}! You are the top winner of this quiz!"
    tts = gTTS(text=tts_text, lang='en', tld='co.in', slow=False)
    tts.write_to_fp(buf)
    buf.seek(0)
    buf.name = "winner.mp3"
    return buf

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

def _ensure_hindi_fonts():
    if FPDF is None:
        raise RuntimeError("fpdf2 install nahi hai. requirements.txt me fpdf2 add karein.")
    font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    os.makedirs(font_dir, exist_ok=True)
    files = {
        "regular": ("NotoSansDevanagari-Regular.ttf", "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Regular.ttf"),
        "bold": ("NotoSansDevanagari-Bold.ttf", "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf"),
    }
    paths = {}
    for key, (name, url) in files.items():
        path = os.path.join(font_dir, name)
        if not os.path.exists(path) or os.path.getsize(path) < 10000:
            urllib.request.urlretrieve(url, path)
        paths[key] = path
    return paths["regular"], paths["bold"]

def generate_pdf_buffer(title, content_text):
    regular, bold = _ensure_hindi_fonts()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.add_font("NotoDeva", "", regular)
    pdf.add_font("NotoDeva", "B", bold)
    pdf.set_text_shaping(True, script="deva", language="hin", direction="ltr")
    pdf.set_font("NotoDeva", "B", 16)
    pdf.multi_cell(0, 10, f"Dulhin Bazar Study Notes: {title}")
    pdf.ln(2)
    pdf.set_font("NotoDeva", "", 11)
    clean = content_text.replace("**", "").replace("###", "").replace("##", "")
    for line in clean.splitlines():
        line = line.strip()
        if not line:
            pdf.ln(3)
            continue
        pdf.multi_cell(0, 7, line)
    out = io.BytesIO()
    data = pdf.output(dest="S")
    out.write(bytes(data))
    out.seek(0)
    return out

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Photo → Objective Quiz", callback_data="menu_photo")],
        [InlineKeyboardButton("➕ Create Custom Quiz", callback_data="menu_create")],
        [InlineKeyboardButton("🔍 Search / Current Affairs", callback_data="menu_search")],
        [InlineKeyboardButton("📘 Static GK", callback_data="topic_static_gk"), InlineKeyboardButton("📰 Current Affairs", callback_data="topic_current_affairs")],
        [InlineKeyboardButton("📚 NCERT Class 6–12 Quiz", callback_data="menu_ncert")],
        [InlineKeyboardButton("📚 Lucent / Saar Sangrah Scan", callback_data="menu_bookscan")],
        [InlineKeyboardButton("🛒 Quiz Store", callback_data="menu_store")],
        [InlineKeyboardButton("🛑 Stop Running Quiz", callback_data="menu_stop")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.args:
        arg = context.args[0]
        if arg.startswith("quiz_"):
            qid = arg.replace("quiz_", "")
            await show_quiz_card(chat_id, qid, update.effective_user.id, context)
            return

    try:
        voice_fp = await asyncio.to_thread(create_welcome_audio)
        await context.bot.send_voice(chat_id=chat_id, voice=voice_fp, caption="🤖 *Maruf Hussain - Quiz Assistant*")
    except Exception:
        pass

    text = "👋 **Welcome to Dulhin Bazar Study & Quiz Bot**\n\n📄 **Topic Notes/PDF:** kisi bhi subject ya topic ka naam bhejein.\n📸 **Photo to Quiz:** direct clear photo bhej sakte hain.\n🔍 **Search/Current Affairs:** button ya `/search <question>` use karein.\n📚 **Lucent/Saar Sangrah:** apne pages scan karke objective MCQs bana sakte hain."
    if update.message:
        await update.message.reply_text(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    query_text = " ".join(context.args).strip() if context.args else ""
    
    if query_text:
        await execute_search(query_text, update, context)
    else:
        search_state[uid] = True
        msg = "🔍 **Question Fact-Check / AI Search**\n\nApna question ya doubt yahan chat me paste karein. AI uska 100% accurate answer aur reasoning dega:"
        if update.message:
            await update.message.reply_text(msg, parse_mode="Markdown")
        elif update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown")

async def execute_search(question_query, update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message if update.message else update.callback_query.message
    status_msg = await target.reply_text("🔍 Fact-checking and finding accurate answer...", parse_mode="Markdown")
    
    prompt = f"""You are a strict, highly accurate exam fact-checker and knowledge engine.
Question/Query: {question_query}

Provide a crisp, 100% correct answer:
1. Direct Correct Answer (Bold)
2. Brief 2-3 line verified explanation/fact logic.
Language: Clear English and Hindi readable."""

    try:
        ai_reply = await generate_ai_text(prompt, use_web=True)
        await status_msg.edit_text(f"🎯 **Verified Answer:**\n\n{ai_reply}", parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Search Error: {str(e)[:120]}")

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
        kb.append([InlineKeyboardButton("🚀 Start Quiz", callback_data=f"startready_{qid}")])
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
Language: पूरी तरह हिंदी (Devanagari) में लिखो। Roman Hindi/Hinglish बिल्कुल मत लिखो। केवल जरूरी English technical terms, abbreviations और exam terms को English में रख सकते हो."""

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
        if uid not in creation_state:
            raise Exception("Pehle Photo → Quiz ya NCERT mode select karein")
        pil_img = Image.open(io.BytesIO(bytes(p_bytes))).convert("RGB")
        # OCR-friendly preprocessing: upscale small pages, contrast and light sharpening.
        w, h = pil_img.size
        max_side = max(w, h)
        if max_side < 1800:
            scale = min(2.0, 2200 / max_side)
            pil_img = pil_img.resize((int(w*scale), int(h*scale)), Image.Resampling.LANCZOS)
        pil_img = ImageOps.autocontrast(pil_img)
        pil_img = pil_img.filter(ImageFilter.SHARPEN)
        pil_img.thumbnail((3000, 3000), Image.Resampling.LANCZOS)
        img_byte_arr = io.BytesIO()
        pil_img.save(img_byte_arr, format="JPEG", quality=95, optimize=True)
        raw_bytes = img_byte_arr.getvalue()

        # Two-pass approach: image -> exact visible text -> MCQs. This is much more reliable than asking OCR + MCQ in one pass.
        ocr_prompt = """Read this page exactly. Transcribe all readable educational text, headings, bullets, tables, labels and captions. Preserve Hindi Devanagari, names, numbers, dates and scientific terms. Do not invent or summarize. Output only the transcription."""
        source_text = await generate_ai_text(ocr_prompt, image_bytes=raw_bytes)
        if not source_text or len(source_text.strip()) < 30:
            raise Exception("Page ka text detect nahi hua. Clear, straight, high-resolution photo bhejein.")

        quiz_prompt = AI_TEXT_TO_QUIZ_PROMPT + "\n\n" + source_text[:18000]
        result = await generate_ai_text(quiz_prompt)
        qs = parse_questions(result)
        if not qs:
            # Retry once with the original vision prompt if parsing failed.
            result = await generate_ai_text(AI_OCR_PROMPT, image_bytes=raw_bytes)
            qs = parse_questions(result)
        if qs:
            creation_state[uid]["questions"].extend(qs)
            await msg.edit_text(f"✅ Photo scan successful! {len(qs)} MCQs banaye.\nTotal Questions: {len(creation_state[uid]['questions'])}\n\nAur photos/text bhejo ya /done se save karo.")
        else:
            await msg.edit_text("⚠️ Text read hua, lekin MCQ format nahi bana. Photo ko seedha, bright aur high-resolution rakho; phir dobara bhejo.")
    except Exception as e:
        await msg.edit_text(f"⚠️ Scan error: {str(e)[:350]}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # Direct photo -> quiz mode. User ko create flow start karna zaroori nahi.
    if uid not in creation_state:
        creation_state[uid] = {
            "title": "Photo Scan Quiz",
            "subject": "Scanned Notes",
            "price": 0,
            "timer": 15,
            "questions": [],
            "step": "QUESTIONS",
            "auto_photo": True,
        }
    elif creation_state[uid].get("step") != "QUESTIONS":
        await update.message.reply_text("⚠️ Abhi quiz setup chal raha hai. Pehle current step complete karein ya /cancel bhejein.")
        return

    msg = await update.message.reply_text("🔍 AI photo scan karke objective MCQs bana raha hai...")
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

    if search_state.get(uid):
        del search_state[uid]
        await execute_search(text, update, context)
        return

    if uid not in creation_state:
        if text.startswith("/"):
            return
        await handle_pdf_request(text, update, context)
        return

    st = creation_state[uid]
    if st.get("step") == "NCERT_TEXT":
        if text.lower() in ["/done", "done"]:
            await finalize_quiz(update, context)
            return
        status = await update.message.reply_text("🤖 Chapter text se MCQs bana raha hoon...")
        try:
            st["source_text"] = (st.get("source_text", "") + "\n" + text).strip()
            source = st["source_text"]
            # Generate in bounded chunks to avoid oversized API requests.
            await make_quiz_from_source_text(uid, source[-24000:], status, st.get("subject", "NCERT Chapter"))
            st["source_text"] = ""
        except Exception as e:
            await status.edit_text(f"⚠️ NCERT Quiz Error: {str(e)[:250]}")
        return

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
        elif st.get("subject", "").startswith("NCERT"):
            status = await update.message.reply_text("🤖 NCERT chapter text se MCQs bana raha hoon...")
            try:
                await make_quiz_from_source_text(uid, text, status, st.get("subject", "NCERT"))
            except Exception as e:
                await status.edit_text(f"⚠️ NCERT Quiz Error: {str(e)[:250]}")


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    creation_state.pop(uid, None)
    search_state.pop(uid, None)
    await update.message.reply_text("✅ Current setup cancel ho gaya.", reply_markup=get_main_keyboard())

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


async def send_topic_notes(topic_name, target_message, context: ContextTypes.DEFAULT_TYPE, use_web=False):
    status = await target_message.reply_text(f"🤖 {topic_name} ke exam notes bana raha hoon...")
    prompt = f"""You are an exam-preparation assistant for Indian competitive exams.
Topic: {topic_name}
Give high-yield notes in Roman Hindi + simple English.
Include: key facts, dates if relevant, one-line revision points, and 10 objective MCQs with answers.
If this is current affairs, use only fresh verifiable information and mention exact dates.
Do not claim that this is verbatim content from any copyrighted commercial book."""
    try:
        text = await generate_ai_text(prompt, use_web=use_web)
        # Telegram message limit handling
        if len(text) <= 3900:
            await status.edit_text(text)
        else:
            await status.edit_text(text[:3900])
            for i in range(3900, len(text), 3900):
                await target_message.reply_text(text[i:i+3900])
    except Exception as e:
        await status.edit_text(f"⚠️ AI Error: {str(e)[:250]}")

async def show_ncert_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🎓 Class 6", callback_data="ncert_class_6"), InlineKeyboardButton("🎓 Class 7", callback_data="ncert_class_7")],
        [InlineKeyboardButton("🎓 Class 8", callback_data="ncert_class_8"), InlineKeyboardButton("🎓 Class 9", callback_data="ncert_class_9")],
        [InlineKeyboardButton("🎓 Class 10", callback_data="ncert_class_10"), InlineKeyboardButton("🎓 Class 11", callback_data="ncert_class_11")],
        [InlineKeyboardButton("🎓 Class 12", callback_data="ncert_class_12")],
        [InlineKeyboardButton("📸 Scan Chapter Page", callback_data="menu_photo")],
        [InlineKeyboardButton("📝 Send Chapter Text", callback_data="ncert_text")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="menu_main")]
    ]
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(
        "📚 NCERT Class 6–12 Quiz\n\nClass choose karo. Uske baad chapter ka text ya pages bhejo; bot source ke basis par objective MCQs banayega.",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def ncert_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    creation_state[uid] = {
        "title": "NCERT Chapter Quiz", "subject": "NCERT", "price": 0,
        "timer": 15, "questions": [], "step": "NCERT_TEXT", "source_text": ""
    }
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(
        "📝 NCERT chapter ka text paste karo. Main isi text se Hindi objective MCQs banaunga.\n\n"
        "Bada chapter ho to parts mein bhejo; end mein /done bhejo."
    )

async def make_quiz_from_source_text(uid, text, msg, subject="NCERT Chapter"):
    prompt = AI_TEXT_TO_QUIZ_PROMPT + "\n\n" + text[:24000]
    result = await generate_ai_text(prompt)
    qs = parse_questions(result)
    if not qs:
        raise Exception("AI ne valid MCQ format return nahi kiya")
    creation_state[uid]["questions"].extend(qs)
    await msg.edit_text(
        f"✅ {len(qs)} MCQs ban gaye. Total: {len(creation_state[uid]['questions'])}\n"
        "Aur chapter text/page bhej sakte ho. Save/start ke liye /done bhejo."
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = update.effective_user.id
    cid = update.effective_chat.id

    if d == "menu_main":
        await q.edit_message_text("👋 **Dulhin Bazar Quiz & Study Menu:**", reply_markup=get_main_keyboard(), parse_mode="Markdown")
    elif d == "menu_photo":
        creation_state[uid] = {"title": "Photo Scan Quiz", "subject": "Scanned Notes", "price": 0, "timer": 15, "questions": [], "step": "QUESTIONS", "auto_photo": True}
        await q.edit_message_text("📸 Ab book/notes ka clear PHOTO bhejo. Main usse objective MCQs bana dunga. Multiple photos bhej sakte ho; end me /done bhejo.")
    elif d == "menu_ncert":
        await show_ncert_menu(update, context)
    elif d.startswith("ncert_class_"):
        cls = d.rsplit("_", 1)[1]
        creation_state[uid] = {"title": f"NCERT Class {cls} Chapter Quiz", "subject": f"NCERT Class {cls}", "price": 0, "timer": 15, "questions": [], "step": "NCERT_TEXT", "source_text": "", "auto_photo": True}
        await q.edit_message_text(f"🎓 NCERT Class {cls} selected.\n\n📸 Chapter page ki clear photo bhejo, ya text paste karo. Main usi source se objective MCQs banaunga. End me /done.")
    elif d == "ncert_text":
        await ncert_text_start(update, context)
    elif d == "menu_bookscan":
        creation_state[uid] = {"title": "Book Scan Quiz", "subject": "Book Scan", "price": 0, "timer": 15, "questions": [], "step": "QUESTIONS", "auto_photo": True}
        await q.edit_message_text("📚 Lucent 2026, Saar Sangrah ya kisi bhi apni book/notes ke pages ka PHOTO bhejo. Main sirf tumhare bheje hue pages ko scan karke MCQs banaunga. End me /done bhejo.")
    elif d == "topic_static_gk":
        await q.edit_message_text("📘 Static GK generate ho raha hai...")
        await send_topic_notes("Static GK for SSC/Railway/Police exams", q.message, context, use_web=False)
    elif d == "topic_current_affairs":
        await q.edit_message_text("📰 Latest Current Affairs web se verify ho raha hai...")
        await send_topic_notes("Latest India and World Current Affairs", q.message, context, use_web=True)
    elif d == "menu_search":
        search_state[uid] = True
        await q.edit_message_text("🔍 **Question Doubt / Fact-Check:**\n\nAb apna question/doubt next message me paste karein. Current affairs ho to AI web se latest info check karega.", parse_mode="Markdown")
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
        qid = d[len("startready_"):]
        if qid not in db.get("quizzes", {}):
            await q.edit_message_text("⚠️ Quiz nahi mila.")
            return
        # Shuffle is always ON; no ON/OFF option is shown to the user.
        active_sessions[cid] = {"quiz": db["quizzes"][qid], "stats": {}, "map": {}, "run": True, "shuffle": True}
        
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
    questions = list(s["quiz"]["questions"])
    if s.get("shuffle"):
        random.shuffle(questions)

    for idx, qd in enumerate(questions):
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
        sorted_users = sorted(s["stats"].values(), key=lambda x: x["c"], reverse=True)
        res = f"🏁 '*{s['quiz']['title']}*' Finished!\n\n🏆 **Leaderboard:**\n"
        for r, u in enumerate(sorted_users, 1):
            res += f"{r}. 👤 *{u['n']}*: ✅ {u['c']} | ❌ {u['w']}\n"
            
        await context.bot.send_message(chat_id, res if s["stats"] else "🏁 Quiz Finished!", parse_mode="Markdown")
        
        if sorted_users:
            winner = sorted_users[0]
            try:
                winner_voice = await asyncio.to_thread(create_winner_audio, winner['n'])
                await context.bot.send_voice(
                    chat_id=chat_id, 
                    voice=winner_voice, 
                    caption=f"🎉 *Winner Voice Note: Congratulations {winner['n']}!*"
                )
            except Exception:
                pass
                
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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("Telegram error:", repr(context.error))
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(update.effective_chat.id, f"⚠️ Internal error: {type(context.error).__name__}. Render logs check karein.")
    except Exception:
        pass

async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("ncert", show_ncert_menu))
    app.add_handler(CommandHandler("create_quiz", create_quiz_cmd))
    app.add_handler(CommandHandler("done", finalize_quiz))
    app.add_handler(CommandHandler("store", store_cmd))
    app.add_handler(CommandHandler("stop", stop_quiz_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PollAnswerHandler(handle_answer))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

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
