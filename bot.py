import asyncio
import re
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

user_quizzes = {}
creation_state = {}
active_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 **Quiz Maker Bot Mein Aapka Swagat Hai!**\n\n"
        "📌 **Commands:**\n"
        "🔹 `/create_quiz` - Naya custom quiz banayein\n"
        "🔹 `/my_quizzes` - Apne banaye huye quizzes dekhein\n"
        "🔹 `/stop` - Chal rahe quiz ko rokein"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    creation_state[user_id] = {"title": "", "timer": 15, "questions": [], "step": "TITLE"}
    await update.message.reply_text("📝 Pehle apne Quiz ka **Title / Naam** likh kar bhejein:")

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
        if text.lower() == "/done":
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
            del creation_state[user_id]

            await update.message.reply_text(
                f"🎉 **Quiz Ban Gaya!**\n\n"
                f"Title: *{quiz_data['title']}*\n"
                f"Questions: *{len(quiz_data['questions'])}*\n\n"
                f"Quiz chalane ke liye `/my_quizzes` likhein.",
                parse_mode="Markdown"
            )
            return

        parsed_q = parse_question_block(text)
        if parsed_q:
            state["questions"].append(parsed_q)
            await update.message.reply_text(f"✅ Question #{len(state['questions'])} add ho gaya! Agla bhejein ya `/done` likhein.")
        else:
            await update.message.reply_text("⚠️ Format sahi nahi hai. Question, 4 options aur answer bhejein.")

def parse_question_block(text):
    try:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        q_text = lines[0]
        options = []
        correct_idx = 0

        for line in lines[1:]:
            if re.match(r"^[\(\[]?[A-Da-d1-4][\)\].]", line):
                clean_opt = re.sub(r"^[\(\[]?[A-Da-d1-4][\)\].]\s*", "", line)
                options.append(clean_opt)
            elif "उत्तर" in line or "Answer" in line or "Ans" in line:
                if "A" in line or "1" in line: correct_idx = 0
                elif "B" in line or "2" in line: correct_idx = 1
                elif "C" in line or "3" in line: correct_idx = 2
                elif "D" in line or "4" in line: correct_idx = 3

        if len(options) >= 2:
            return {"question": q_text, "options": options[:4], "correct_id": correct_idx}
        return None
    except Exception:
        return None

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
            await query.edit_message_text(f"⏱ Timer: **{timer_val}s** set ho gaya.\n\nAb sawal bhejein, complete hone par `/done` likhein.")

    elif data.startswith("startquiz_"):
        _, q_owner, q_idx = data.split("_")
        q_owner = int(q_owner)
        q_idx = int(q_idx)
        
        quiz_data = user_quizzes[q_owner][q_idx]
        chat_id = update.effective_chat.id
        
        active_sessions[chat_id] = {
            "quiz": quiz_data,
            "index": 0,
            "scores": {},
            "poll_map": {}
        }
        
        await query.message.reply_text(f"🚀 **Quiz Shuru:** {quiz_data['title']}")
        await send_quiz_poll(chat_id, context)

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

async def send_quiz_poll(chat_id, context: ContextTypes.DEFAULT_TYPE):
    session = active_sessions.get(chat_id)
    if not session:
        return

    idx = session["index"]
    quiz = session["quiz"]

    if idx >= len(quiz["questions"]):
        scores = session["scores"]
        leaderboard = "🏁 **Quiz Leaderboard:**\n\n"
        if not scores:
            leaderboard += "Kisi ne sahi jawab nahi diya."
        else:
            sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            for rank, (name, score) in enumerate(sorted_scores, 1):
                leaderboard += f"{rank}. {name} — {score} Marks\n"
        
        await context.bot.send_message(chat_id=chat_id, text=leaderboard, parse_mode="Markdown")
        del active_sessions[chat_id]
        return

    q_data = quiz["questions"][idx]
    
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
    
    await asyncio.sleep(quiz["timer"] + 2)
    if chat_id in active_sessions and active_sessions[chat_id]["index"] == idx:
        active_sessions[chat_id]["index"] += 1
        await send_quiz_poll(chat_id, context)

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_name = answer.user.first_name

    for chat_id, session in active_sessions.items():
        if poll_id in session["poll_map"]:
            correct_id = session["poll_map"][poll_id]
            if answer.option_ids and answer.option_ids[0] == correct_id:
                session["scores"][user_name] = session["scores"].get(user_name, 0) + 1

async def stop_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in active_sessions:
        del active_sessions[chat_id]
        await update.message.reply_text("🛑 Quiz rokk diya gaya hai.")
    else:
        await update.message.reply_text("⚠️ Abhi koi quiz nahi chal raha hai.")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("create_quiz", create_quiz))
    app.add_handler(CommandHandler("my_quizzes", my_quizzes))
    app.add_handler(CommandHandler("stop", stop_quiz))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Exam Quiz Maker Bot 24/7 Started...")
    app.run_polling()
  
