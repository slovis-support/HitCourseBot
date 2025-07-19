from database import SessionLocal, User, init_db

init_db()
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

# Переменные окружения
openai_api_key = os.getenv("OPENAI_API_KEY")
assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
telegram_token = os.getenv("TELEGRAM_TOKEN")
webhook_url = os.getenv("WEBHOOK_URL")
webhook_path = "/webhook"

# Flask-приложение
flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "https://hitcourse.ru"}})
client = OpenAI(api_key=openai_api_key)
telegram_app = ApplicationBuilder().token(telegram_token).build()
threads = {}

# ======== Новая память пользователя =========
def handle_user_message(user_id, text):
    db = SessionLocal()
    user = db.query(User).filter(User.telegram_id == str(user_id)).first()

    if text.lower().startswith("я "):
        name = text[2:].strip()
        if user:
            user.name = name
        else:
            user = User(telegram_id=str(user_id), name=name)
            db.add(user)
        db.commit()
        return f"Приятно познакомиться, {name}!"

    if "как меня зовут" in text.lower():
        if user and user.name:
            return f"Тебя зовут {user.name}!"
        else:
            return "Я пока не знаю, как тебя зовут. Напиши: 'я [твоё имя]' 😊"

    return None
# ============================================

# Обработчики Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я — Словис, помощник платформы Хиткурс.\n"
        "Спроси — и получи честный, понятный ответ 🧠"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_input = update.message.text

    # Проверка на имя или "как меня зовут"
    reply = handle_user_message(user_id, user_input)
    if reply:
        await update.message.reply_text(reply)
        return

    # Работа с Assistant API
    if user_id not in threads:
        thread = client.beta.threads.create()
        threads[user_id] = thread.id

    try:
        client.beta.threads.messages.create(
            thread_id=threads[user_id],
            role="user",
            content=user_input
        )

        client.beta.threads.runs.create_and_poll(
            thread_id=threads[user_id],
            assistant_id=assistant_id
        )

        messages = client.beta.threads.messages.list(thread_id=threads[user_id])
        answer = messages.data[0].content[0].text.value
        await update.message.reply_text(answer)

    except Exception as e:
        print("Ошибка OpenAI:", e)
        await update.message.reply_text("Произошла ошибка. Попробуй позже.")

# Регистрируем обработчики
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Обработка Webhook
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

# Keep Alive Ping
def keep_alive_ping():
    while True:
        try:
            requests.get(webhook_url)
        except Exception as e:
            print("Keep-alive error:", e)
        time.sleep(60)

threading.Thread(target=keep_alive_ping, daemon=True).start()

# WebApp Route
@flask_app.route("/message", methods=["POST"])
def web_chat():
    try:
        data = request.get_json()
        user_message = data.get("message", "")
        if not user_message.strip():
            return {"reply": "Пустое сообщение."}, 400

        if "web" not in threads:
            thread = client.beta.threads.create()
            threads["web"] = thread.id

        client.beta.threads.messages.create(
            thread_id=threads["web"],
            role="user",
            content=user_message
        )
        run = client.beta.threads.runs.create_and_poll(
            thread_id=threads["web"],
            assistant_id=assistant_id
        )
        messages = client.beta.threads.messages.list(thread_id=threads["web"])
        reply = messages.data[0].content[0].text.value
        return {"reply": reply}

    except Exception as e:
        print("Ошибка в /message:", e)
        return {"reply": "Произошла ошибка на сервере."}, 500

# Запуск Flask
if __name__ == "__main__":
    print("🤖 Бот HitCourse (Webhook + Assistant API + PostgreSQL) запущен на Railway")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
