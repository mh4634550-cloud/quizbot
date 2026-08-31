import os
import asyncio
import io
import re
import json
import random
import time
import urllib.request
import urllib.parse
from aiohttp import web
from PIL import Image
import google.generativeai as genai
from telegram import (
    Update,
    Poll,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PollAnswerHandler,
    InlineQueryHandler,
    ContextTypes,
    filters,
)

# ================= CONFIGURATION =================
TOKEN = "8832779613:AAETBqawjr3YwH8Su6c_Qz5OuD-IuHiOsqc"
UPI_ID = "marufhussain318-2@oksbi"
GEMINI_API_KEY = "AQ.Ab8RN6JacArVio7NBYlubfksQK8a9q9G2u4UyCzvJKqT56nF0Q"
DATA_FILE = "store_quiz_data.json"

genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel("gemini-1.5-flash")

# ================= PERSISTENT STORAGE =================
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
    except Exception as e:
        print(f"Storage Error: {e}")

db = load_data()
creation_state = {}
active_sessions = {}

# ================= GEMINI OCR PROMPT =================
AI_PROMPT = """
You are an expert exam quiz parser. 
Read the text and questions in this book page image accurately.
Extract all possible objective/multiple-choice questions from the content in Hindi/English.
Format each question strictly as:

Q1. Question text here
A) Option 1
B) Option 2
C) Option 3
D) Option 4
Answer: Correct Option Letter (A/B/C/D)

Ensure there is a blank line between two questions. Do not write any conversational intro or extra text.
"""

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args and args[0].startswith("quiz_"):
        q_id = args[0].replace("quiz_", "")
        q = db.get("quizzes", {}).get(q_id)
        if q:
            await show_prequiz_card(update.effective_chat.id, q_id, context)
            return

    text = (
        "👋 **Welcome to Dulhin Bazar Quiz Bot!**\n\n"
        "📌 **Quick Actions:**\n"
        "📸 **Photo to Quiz:** `/create_quiz` karke book ke page ki seedhi photo bhejein!\n"
        "📁 **Bulk Questions:** 100-500 questions ki `.txt` file upload karein.\n\n"
        "🔹 `/store` - Subject-wise Store (Lucent/NCERT)\n"
        "🔹 `/mystore` - Aapka Store Dashboard\n"
        "🔹 `/create_quiz` - Naya Quiz Banayein\n"
        "🔹 `/my_quizzes` - Unlocked Quizzes List\n"
        "🔹 `/stop` - Running Quiz Stop Karein\n\n"
        "👑 **Admin Unlock:** `/approve <user_id> <quiz_id>`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def mystore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    u_str = str(user_id)
    purchased = db.get("purchases", {}).get(u_str, [])
    my_created = [q for q in db.get("quizzes", {}).values() if q.get("owner") == user_id]

    text = f"🏪 **Aapka Personal Quiz Store Dashboard**\n\n"
    text += f"🆔 **Your User ID:** `{user_id}`\n"
    text += f"📦 **Purchased Quizzes:** {len(purchased)}\n"
    text += f"🛠 **Created Quizzes:** {len(my_created)}"

    keyboard = [
        [InlineKeyboardButton("📂 My Purchases", callback_data="mystore_purchases")],
        [InlineKeyboardButton("🛠 My Created Quizzes", callback_data="mystore_created")],
        [InlineKeyboardButton("➕ Create Quiz", callback_data="mystore_create_new")],
        [InlineKeyboardButton("🛒 Browse Main Store", callback_data="back_store")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_prequiz_card(chat_id, q_id, context: ContextTypes.DEFAULT_TYPE):
    q = db["quizzes"].get(q_id)
    if not q:
        await context.bot.send_message(chat_id, "⚠️ Quiz not found.")
        return

    card = (
        f"🎲 Get ready for the quiz '*{q['title']}*'\n\n"
        f"📁 Subject: *{q['subject']}*\n"
        f"✒️ {len(q['questions'])} questions\n"
        f"⏱ {q['timer']} seconds per question\n"
        f"💰 Price: *{'FREE' if q['price'] == 0 else f'₹{q['price']}'}*\n"
        f"📰 Votes are *visible* to the quiz owner\n\n"
        f"🏁 Press the button below when you are ready.\n"
        f"Send /stop to stop it."
    )
    keyboard = [[InlineKeyboardButton("I am ready!", callback_data=f"startready_{q_id}")]]
    await context.bot.send_message(chat_id=chat_id, text=card, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quizzes = db.get("quizzes", {})
    if not quizzes:
        await update.message.reply_text("📂 Abhi store mein koi quiz nahi hai. Pehle `/create_quiz` karein.")
        return

    subjects = {}
    for q_id, q in quizzes.items():
        sub = q.get("subject", "General")
        if sub not in subjects:
            subjects[sub] = []
        subjects[sub].append((q_id, q))

    keyboard = []
    for sub in subjects.keys():
        keyboard.append([InlineKeyboardButton(f"📁 {sub} ({len(subjects[sub])} Sets)", callback_data=f"sub_{sub}")])

    keyboard.append([InlineKeyboardButton("🏪 My Personal Store", callback_data="open_mystore")])
    await update.message.reply_text("📚 **Subject-wise Quiz Store:**\nApna Subject select karein:", reply_markup=InlineKeyboardMarkup(keyboard))

async def create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    creation_state[user_id] = {
        "title": "",
        "subject": "General",
        "price": 0,
        "timer": 15,
        "shuffle": False,
        "questions": [],
        "step": "TITLE"
    }
    await update.message.reply_text("📝 Apne Quiz ka **Title / Naam** likh kar bhejein:")

async def cancel_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in creation_state:
        del creation_state[user_id]
        await update.message.reply_text("❌ Quiz creation cancel ho gaya.")
    else:
        await update.message.reply_text("⚠️ Koi active quiz process nahi chal raha hai.")

# ================= PARSER =================
def parse_single_question(block):
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    if len(lines) < 3:
        return None
    
    q_text = re.sub(r"^(Q\s*\d+[\.\)]|\d+[\.\)])\s*", "", lines[0]).strip()
    options = []
    correct_idx = 0

    for line in lines[1:]:
        if re.match(r"^[\(\[]?[A-Da-d1-4][\)\].]", line):
            clean_opt = re.sub(r"^[\(\[]?[A-Da-d1-4][\)\].]\s*", "", line).strip()
            options.append(clean_opt[:100])
        elif any(k in line for k in ["उत्तर", "Answer", "Ans", "ans", "ANSWER"]):
            ans_part = line.split(":")[-1].strip() if ":" in line else line
            match = re.search(r"[\(\[]?([A-Da-d1-4])[\)\]]?", ans_part)
            if match:
                val = match.group(1).upper()
                if val in ["A", "1"]: correct_idx = 0
                elif val in ["B", "2"]: correct_idx = 1
                elif val in ["C", "3"]: correct_idx = 2
                elif val in ["D", "4"]: correct_idx = 3

    if len(options) >= 2:
        return {"question": q_text[:280], "options": options[:4], "correct_id": min(correct_idx, len(options)-1)}
    return None

def parse_bulk_questions(text):
    blocks = re.split(r"\n\s*\n|(?=\n\s*(?:Q\s*\d+[\.\)]|\d+[\.\)]))", text)
    parsed_list = []
    for b in blocks:
        if b.strip():
            q_data = parse_single_question(b.strip())
            if q_data:
                parsed_list.append(q_data)
    return parsed_list

async def finalize_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in creation_state:
        await update.message.reply_text("⚠️ Pehle `/create_quiz` karein.")
        return

    state = creation_state[user_id]
    if not state["questions"]:
        await update.message.reply_text("⚠️ Kam se kam 1 question add karein!")
        return

    quiz_id = f"q_{int(time.time())}"
    quiz_data = {
        "id": quiz_id,
        "title": state["title"],
        "subject": state["subject"],
        "price": state["price"],
        "timer": state["timer"],
        "shuffle": state.get("shuffle", False),
        "owner": user_id,
        "questions": state["questions"]
    }
    
    db["quizzes"][quiz_id] = quiz_data
    
    u_str = str(user_id)
    if u_str not in db["purchases"]:
        db["purchases"][u_str] = []
    db["purchases"][u_str].append(quiz_id)
    save_data()
    del creation_state[user_id]

    price_tag = "FREE" if quiz_data['price'] == 0 else f"₹{quiz_data['price']}"
    shuffle_text = "✅ ON" if quiz_data['shuffle'] else "❌ OFF"

    bot_me = await context.bot.get_me()
    share_text = f"🎲 Quiz \"{quiz_data['title']}\"\n🖊 {len(quiz_data['questions'])} questions · ⏱ {quiz_data['timer']} sec"
    share_url = f"https://t.me/share/url?url=https://t.me/{bot_me.username}?start=quiz_{quiz_id}&text={urllib.parse.quote(share_text)}"

    keyboard = [
        [InlineKeyboardButton("Start this quiz ↗️", url=f"https://t.me/{bot_me.username}?start=quiz_{quiz_id}")],
        [InlineKeyboardButton("Start quiz in group ↗️", url=f"https://t.me/{bot_me.username}?startgroup=quiz_{quiz_id}")],
        [InlineKeyboardButton("Share quiz ↗️", url=share_url)]
    ]

    await update.message.reply_text(
        f"🎉 **Quiz Created Successfully!**\n\n"
        f"🎲 Quiz \"*{quiz_data['title']}*\"\n"
        f"🖊 {len(quiz_data['questions'])} questions · ⏱ {quiz_data['timer']} sec\n"
        f"📁 Subject: {quiz_data['subject']} | 💰 Price: {price_tag} | 🔀 Shuffle: {shuffle_text}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ================= PHOTO & FILE HANDLERS =================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in creation_state or creation_state[user_id]["step"] != "QUESTIONS":
        return

    msg = await update.message.reply_text("🔍 **AI Image Scanning...** Book ke page se questions read ho rahe hain...")
    
    try:
        photo = update.message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        photo_bytes = await photo_file.download_as_bytearray()
        
        image = Image.open(io.BytesIO(photo_bytes))
        response = ai_model.generate_content([AI_PROMPT, image])
        extracted_text = response.text

        bulk_parsed = parse_bulk_questions(extracted_text)
        if bulk_parsed:
            creation_state[user_id]["questions"].extend(bulk_parsed)
            await msg.edit_text(
                f"✅ **AI ne Photo se {len(bulk_parsed)} Questions bana diye!**\n"
                f"Total abhi tak: **{len(creation_state[user_id]['questions'])}**\n\n"
                f"📸 Aur photos bhejein ya save karne ke liye `/done` bhejein."
            )
        else:
            await msg.edit_text("⚠️ Image saaf nahi aayi ya questions detect nahi ho paye. Dubara clear photo bhejein.")
    except Exception as e:
        print(f"AI Vision Error: {e}")
        await msg.edit_text("⚠️ AI Processing error. Kripya check karein image clear ho.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in creation_state or creation_state[user_id]["step"] != "QUESTIONS":
        return

    doc = update.message.document
    if not doc.file_name.endswith(('.txt', '.text')):
        await update.message.reply_text("⚠️ Kripya `.txt` text file bhejein.")
        return

    file = await context.bot.get_file(doc.file_id)
    file_bytes = await file.download_as_bytearray()
    content = file_bytes.decode('utf-8', errors='ignore')

    bulk_parsed = parse_bulk_questions(content)
    if bulk_parsed:
        creation_state[user_id]["questions"].extend(bulk_parsed)
        await update.message.reply_text(
            f"📁 **{len(bulk_parsed)} Questions** add ho gaye!\nTotal: **{len(creation_state[user_id]['questions'])}**\n\nSave karne ke liye `/done` bhejein."
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id not in creation_state:
        return

    state = creation_state[user_id]

    if state["step"] == "TITLE":
        state["title"] = text
        state["step"] = "SUBJECT"
        await update.message.reply_text(f"✅ Title: *{text}*\n\nAb iska **Subject / Category** likhein (e.g. *Lucent History, NCERT Science*):", parse_mode="Markdown")
        return

    if state["step"] == "SUBJECT":
        state["subject"] = text
        state["step"] = "PRICE"
        keyboard = [
            [InlineKeyboardButton("🆓 Free (₹0)", callback_data="setprice_0")],
            [InlineKeyboardButton("₹21 (Chapter)", callback_data="setprice_21"), InlineKeyboardButton("₹49", callback_data="setprice_49")],
            [InlineKeyboardButton("₹99", callback_data="setprice_99"), InlineKeyboardButton("₹149", callback_data="setprice_149")]
        ]
        await update.message.reply_text(f"📁 Subject: *{text}*\n\nPrice select karein ya khud type karein (e.g. 21):", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if state["step"] == "PRICE":
        if text.isdigit():
            state["price"] = int(text)
            state["step"] = "TIMER"
            keyboard = [
                [InlineKeyboardButton("10s", callback_data="time_10"), InlineKeyboardButton("15s", callback_data="time_15")],
                [InlineKeyboardButton("30s", callback_data="time_30"), InlineKeyboardButton("60s", callback_data="time_60")]
            ]
            await update.message.reply_text(f"💰 Price: *₹{text}*\n\nTimer choose karein:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if state["step"] == "QUESTIONS":
        if text.lower() in ["/done", "done"]:
            await finalize_quiz(update, context)
            return

        bulk_parsed = parse_bulk_questions(text)
        if bulk_parsed:
            state["questions"].extend(bulk_parsed)
            await update.message.reply_text(
                f"✅ **{len(bulk_parsed)} Questions** add ho gaye!\nTotal: **{len(state['questions'])}**\n\nSave karne ke liye `/done` bhejein."
            )

# ================= BUTTON CALLBACKS =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if data == "open_mystore":
        u_str = str(user_id)
        purchased = db.get("purchases", {}).get(u_str, [])
        my_created = [q for q in db.get("quizzes", {}).values() if q.get("owner") == user_id]
        
        text = f"🏪 **Aapka Personal Quiz Store Dashboard**\n\n"
        text += f"🆔 **User ID:** `{user_id}`\n"
        text += f"📦 **Purchased Quizzes:** {len(purchased)}\n"
        text += f"🛠 **Created Quizzes:** {len(my_created)}"

        keyboard = [
            [InlineKeyboardButton("📂 My Purchases", callback_data="mystore_purchases")],
            [InlineKeyboardButton("🛠 My Created Quizzes", callback_data="mystore_created")],
            [InlineKeyboardButton("➕ Create Quiz", callback_data="mystore_create_new")],
            [InlineKeyboardButton("⬅️ Back to Store", callback_data="back_store")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "mystore_purchases":
        purchased = db.get("purchases", {}).get(str(user_id), [])
        keyboard = []
        for q_id in purchased:
            q = db.get("quizzes", {}).get(q_id)
            if q:
                keyboard.append([InlineKeyboardButton(f"▶️ [{q['subject']}] {q['title']}", callback_data=f"view_{q_id}")])
        keyboard.append([InlineKeyboardButton("⬅️ Back to MyStore", callback_data="open_mystore")])
        await query.edit_message_text("📂 **Purchased / Unlocked Quizzes:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "mystore_created":
        my_created = [(q_id, q) for q_id, q in db.get("quizzes", {}).items() if q.get("owner") == user_id]
        keyboard = []
        for q_id, q in my_created:
            p_str = "FREE" if q['price'] == 0 else f"₹{q['price']}"
            keyboard.append([InlineKeyboardButton(f"⚙️ [{p_str}] {q['title']}", callback_data=f"view_{q_id}")])
        keyboard.append([InlineKeyboardButton("⬅️ Back to MyStore", callback_data="open_mystore")])
        await query.edit_message_text("🛠 **Aapke Banaye Hue Quizzes:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "mystore_create_new":
        await query.message.reply_text("Naya quiz shuru karne ke liye `/create_quiz` likhein.")

    elif data.startswith("setprice_"):
        price_val = int(data.split("_")[1])
        if user_id in creation_state:
            creation_state[user_id]["price"] = price_val
            creation_state[user_id]["step"] = "TIMER"
            keyboard = [
                [InlineKeyboardButton("10s", callback_data="time_10"), InlineKeyboardButton("15s", callback_data="time_15")],
                [InlineKeyboardButton("30s", callback_data="time_30"), InlineKeyboardButton("60s", callback_data="time_60")]
            ]
            await query.edit_message_text(f"💰 Price: **₹{price_val}**\n\nTimer choose karein:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("time_"):
        timer_val = int(data.split("_")[1])
        if user_id in creation_state:
            creation_state[user_id]["timer"] = timer_val
            creation_state[user_id]["step"] = "SHUFFLE"
            keyboard = [
                [InlineKeyboardButton("🔀 Shuffle: ON", callback_data="shuf_on"), InlineKeyboardButton("➡️ Shuffle: OFF", callback_data="shuf_off")]
            ]
            await query.edit_message_text(f"⏱ Timer: **{timer_val}s**\n\nKya sawalo ka order **Shuffle** karna hai?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("shuf_"):
        shuf_choice = data.split("_")[1] == "on"
        if user_id in creation_state:
            creation_state[user_id]["shuffle"] = shuf_choice
            creation_state[user_id]["step"] = "QUESTIONS"
            status_txt = "ON" if shuf_choice else "OFF"
            await query.edit_message_text(f"🔀 Shuffle Mode: **{status_txt}**\n\n📸 **Ab direct book ke page ki PHOTO kheench kar bhejein** ya 100-500 questions ki `.txt` file upload karein.\nPoora hone ke baad `/done` bhejein.")

    elif data.startswith("sub_"):
        selected_sub = data.split("_", 1)[1]
        quizzes = db.get("quizzes", {})
        keyboard = []
        for q_id, q in quizzes.items():
            if q.get("subject") == selected_sub:
                price_str = "FREE" if q["price"] == 0 else f"₹{q['price']}"
                keyboard.append([InlineKeyboardButton(f"{q['title']} - {price_str}", callback_data=f"view_{q_id}")])
        keyboard.append([InlineKeyboardButton("⬅️ Back to Subjects", callback_data="back_store")])
        await query.edit_message_text(f"📚 **{selected_sub} Quizzes:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "back_stor
