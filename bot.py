import os
import asyncio
import io
import re
import json
from aiohttp import web
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PollAnswerHandler,
    ContextTypes,
    filters,
)

TOKEN = "8736461994:AAFv1d3bIVRGYwB6LgLH4pSLaAXhffmpSHE"
DATA_FILE = "quizzes_data.json"

# Persistent Storage helpers
def load_quizzes():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception:
            return {}
    return {}

def save_quizzes_to_file():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_quizzes, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving data: {e}")

user_quizzes = load_quizzes()
creation_state = {}
active_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 **Quiz Maker Bot Ready!**\n\n"
        "📌 **Available Commands:**\n"
        "🔹 `/create_quiz` - Naya quiz banayein\n"
        "🔹 `/done` - Quiz finalize karke save karein\n"
        "🔹 `/cancel_quiz` - Quiz creation cancel karein\n"
        "🔹 `/my_quizzes` - Saved quizzes dekhein aur start karein\n"
        "🔹 `/stop` - Running quiz ko rokein"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ **Settings:**\nDefault Poll Timer: 15s\nAnonymous: OFF\nStatus: Online 24/7")

async def create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    creation_state[user_id] = {"title": "", "timer": 15, "questions": [], "step": "TITLE"}
    await update.message.reply_text("📝 Apne Quiz ka **Title / Naam** likh kar bhejein:")

async def cancel_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in creation_state:
        del creation_state[user_id]
        await update.message.reply_text("❌ Quiz creation cancel kar diya gaya hai.")
    else:
        await update.message.reply_text("⚠️ Koi active quiz creation process nahi chal raha hai.")

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
            options.append(clean_opt[:100])  # Telegram Option limit 100 chars
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
        await update.message.reply_text("⚠️ Koi active quiz creation nahi chal raha hai.")
        return

    state = creation_state[user_id]
    if not state["questions"]:
        await update.message.reply_text("⚠️ Kam se kam 1 question add karein!")
        return

    if user_id not in user_quizzes:
        user_quizzes[user_id] = []

    quiz_id = f"q_{len(user_quizzes[user_id]) + 1}"
    quiz_data = {
        "id": quiz_id,
        "title": state["title"],
        "timer": state["timer"],
        "questions": state["questions"]
    }
    user_quizzes[user_id].append(quiz_data)
    save_quizzes_to_file()
    del creation_state[user_id]

    await update.message.reply_text(
        f"🎉 **Quiz Save Ho Gaya!**\n\n"
        f"📌 Title: *{quiz_data['title']}*\n"
        f"📊 Total Questions: *{len(quiz_data['questions'])}*\n"
        f"⏱ Timer: *{quiz_data['timer']}s per question*\n\n"
        f"Group mein test start karne ke liye `/my_quizzes` likhein.",
        parse_mode="Markdown"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in creation_state or creation_state[user_id]["step"] != "QUESTIONS":
        return

    doc = update.message.document
    if not doc.file_name.endswith(('.txt', '.text')):
        await update.message.reply_text("⚠️ Kripya `.txt` format ki text file bhejein.")
        return

    file = await context.bot.get_file(doc.file_id)
    file_bytes = await file.download_as_bytearray()
    content = file_bytes.decode('utf-8', errors='ignore')

    bulk_parsed = parse_bulk_questions(content)
    if bulk_parsed:
        creation_state[user_id]["questions"].extend(bulk_parsed)
        await update.message.reply_text(
            f"📁 **File se {len(bulk_parsed)} Questions** add ho gaye!\n"
            f"Total Questions: **{len(creation_state[user_id]['questions'])}**\n\n"
            f"Save karne ke liye `/done` bhejein."
        )
    else:
        await update.message.reply_text("⚠️ File ke questions ka format match nahi hua.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id not in creation_state:
        return

    state = creation_state[user_id]

    if state["step"] == "TITLE":
        state["title"] = text
        state["step"] = "TIMER"
        keyboard = [
            [InlineKeyboardButton("10 Sec", callback_data="time_10"), InlineKeyboardButton("15 Sec", callback_data="time_15")],
            [InlineKeyboardButton("30 Sec", callback_data="time_30"), InlineKeyboardButton("60 Sec", callback_data="time_60")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"✅ Title: *{text}*\n\nTimer select karein:", reply_markup=reply_markup, parse_mode="Markdown")
        return

    if state["step"] == "QUESTIONS":
        if text.lower() in ["/done", "done"]:
            await finalize_quiz(update, context)
            return

        bulk_parsed = parse_bulk_questions(text)
        if bulk_parsed:
            state["questions"].extend(bulk_parsed)
            await update.message.reply_text(
                f"✅ **{len(bulk_parsed)} Questions** add ho gaye!\n"
                f"Total abhi tak: **{len(state['questions'])}**\n\n"
                f"Aur bhejein ya save karne ke liye `/done` bhejein."
            )
        else:
            await update.message.reply_text("⚠️ Format check karein (Question, 4 options aur Answer hona zaroori hai).")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("time_"):
        timer_val = int(data.split("_")[1])
        if user_id in creation_state:
            creation_state[user_id]["timer"] = timer_val
            creation_state[user_id]["step"] = "QUESTIONS"
            await query.edit_message_text(f"⏱ Timer: **{timer_val}s** set ho gaya.\n\nAb sawal paste karein ya `.txt` file bhej dein. Save karne ke liye `/done` bhejein.")

    elif data.startswith("startquiz_"):
        _, q_owner, q_idx = data.split("_")
        q_owner = int(q_owner)
        q_idx = int(q_idx)
        
        quiz_data = user_quizzes[q_owner][q_idx]
        chat_id = update.effective_chat.id
        
        # Stop existing session if any
        if chat_id in active_sessions:
            active_sessions[chat_id]["is_running"] = False
        
        active_sessions[chat_id] = {
            "quiz": quiz_data,
            "index": 0,
            "scores": {},
            "poll_map": {},
            "is_running": True
        }
        
        await query.message.reply_text(f"🚀 **Quiz Shuru:** {quiz_data['title']}\nTotal Questions: {len(quiz_data['questions'])}")
        asyncio.create_task(run_quiz_loop(chat_id, context))

async def my_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    quizzes = user_quizzes.get(user_id, [])

    if not quizzes:
        await update.message.reply_text("📂 Koi saved quiz nahi hai. Pehle `/create_quiz` karein.")
        return

    keyboard = []
    for idx, q in enumerate(quizzes):
        keyboard.append([InlineKeyboardButton(f"▶️ {q['title']} ({len(q['questions'])} Qs)", callback_data=f"startquiz_{user_id}_{idx}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📚 **Saved Quizzes:**", reply_markup=reply_markup)

async def run_quiz_loop(chat_id, context: ContextTypes.DEFAULT_TYPE):
    session = active_sessions.get(chat_id)
    if not session:
        return

    quiz = session["quiz"]
    
    for idx, q_data in enumerate(quiz["questions"]):
        if not session.get("is_running"):
            break
        
        session["index"] = idx
        try:
            poll_msg = await context.bot.send_poll(
                chat_id=chat_id,
                question=f"Q{idx + 1}. {q_data['question']}",
                options=q_data["options"],
                type=Poll.QUIZ,
                correct_option_id=q_data["correct_id"],
                open_period=quiz["timer"],
                is_anonymous=False
            )
            session["poll_map"][poll_msg.poll.id] = q_data["correct_id"]
        except Exception as e:
            print(f"Error sending poll: {e}")
            continue

        # Wait for timer
        for _ in range(quiz["timer"] + 2):
            if not session.get("is_running"):
                break
            await asyncio.sleep(1)

    if session.get("is_running"):
        scores = session["scores"]
        leaderboard = f"🏁 **Quiz Finished: {quiz['title']}**\n\n🏆 **Leaderboard:**\n\n"
        if not scores:
            leaderboard += "Kisi ne sahi jawab nahi diya."
        else:
            sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            for rank, (name, score) in enumerate(sorted_scores, 1):
                medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "🔹"
                leaderboard += f"{medal} {rank}. {name} — {score} Marks\n"
        
        await context.bot.send_message(chat_id=chat_id, text=leaderboard, parse_mode="Markdown")
        if chat_id in active_sessions:
            del active_sessions[chat_id]

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_name = update.effective_user.first_name

    for chat_id, session in active_sessions.items():
        if poll_id in session["poll_map"]:
            correct_id = session["poll_map"][poll_id]
            if answer.option_ids and answer.option_ids[0] == correct_id:
                session["scores"][user_name] = session["scores"].get(user_name, 0) + 1

async def stop_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in active_sessions and active_sessions[chat_id].get("is_running"):
        active_sessions[chat_id]["is_running"] = False
        del active_sessions[chat_id]
        await update.message.reply_text("🛑 **Quiz safalta-purvak rokk diya gaya hai.**")
    else:
        await update.message.reply_text("⚠️ Abhi koi quiz running nahi hai.")

async def handle_http(request):
    return web.Response(text="Dulhin Bazar Quiz Bot is Active 24/7!")

async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CommandHandler("create_quiz", create_quiz))
    app.add_handler(CommandHandler("done", finalize_quiz))
    app.add_handler(CommandHandler("cancel_quiz", cancel_quiz))
    app.add_handler(CommandHandler("my_quizzes", my_quizzes))
    app.add_handler(CommandHandler("stop", stop_quiz))
    
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    server = web.Application()
    server.router.add_get("/", handle_http)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print("Quiz Bot System Online...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
        
