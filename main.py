import os
import asyncio
import threading
import time
import requests
from flask import Flask, request
from flask_cors import CORS
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI
from database import SessionLocal, User, init_db

# Инициализация базы данных
init_db()

# Переменные окружения
openai_api_key = os.getenv("OPENAI_API_KEY")
assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
telegram_token = os.getenv("TELEGRAM_TOKEN")
webhook_url = os.getenv("WEBHOOK_URL")
webhook_path = "/webhook"

# Flask и Telegram
flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "https://hitcourse.ru"}})
client = OpenAI(api_key=openai_api_key)
telegram_app = ApplicationBuilder().token(telegram_token).build()

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    name = update.effective_user.first_name
    db = SessionLocal()

    user = db.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, name=name)
        db.add(user)
    else:
        user.name = name
    db.commit()
    db.close()

    await update.message.reply_text(
        f"Привет, {name}! Я — Словис, помощник платформы Хиткурс.\n"
        "Спроси — и получи честный, понятный ответ 🧠"
    )

# Обработка всех текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_input = update.message.text
    lowered = user_input.lower().strip()

    db = SessionLocal()
    user = db.query(User).filter_by(telegram_id=user_id).first()

    if not user:
        user = User(telegram_id=user_id)
        db.add(user)

    # Обработка: "меня зовут Алексей"
    if "меня зовут" in lowered:
        name = user_input.split("меня зовут", 1)[-1].strip().strip(".!")
        if name:
            user.name = name
            db.commit()
            await update.message.reply_text(f"Приятно познакомиться, {name}! Запомнил 😊")
            db.close()
            return

    # Если нет thread_id — создаём новый поток
    if not user.thread_id:
        thread = client.beta.threads.create()
        user.thread_id = thread.id
        db.commit()

    try:
        client.beta.threads.messages.create(
            thread_id=user.thread_id,
            role="user",
            content=user_input
        )

        client.beta.threads.runs.create_and_poll(
            thread_id=user.thread_id,
            assistant_id=assistant_id
        )

        messages = client.beta.threads.messages.list(thread_id=user.thread_id)
        answer = messages.data[0].content[0].text.value

        # 👇 Добавим имя в начало, если оно есть и это не запрос "как меня зовут"
        if user.name and not ("как меня зовут" in lowered or "меня зовут" in lowered):
            answer = f"{user.name}, {answer}"

        await update.message.reply_text(answer)

    except Exception as e:
        print("Ошибка:", e)
        await update.message.reply_text("Произошла ошибка. Попробуй позже.")
    finally:
        db.close()

# Регистрация обработчиков
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Webhook для Telegram
@flask_app.route(webhook_path, methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)

    async def process():
        await telegram_app.initialize()
        await telegram_app.process_update(update)

    try:
        asyncio.get_event_loop().create_task(process())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(process())

    return "OK", 200

# Пинг для Railway
def keep_alive_ping():
    while True:
        try:
            requests.get(webhook_url)
        except Exception as e:
            print("Keep-alive error:", e)
        time.sleep(60)

threading.Thread(target=keep_alive_ping, daemon=True).start()

# Запуск Flask
if __name__ == "__main__":
    print("🤖 Бот HitCourse запущен на Railway")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
