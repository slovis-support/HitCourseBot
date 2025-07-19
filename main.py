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
from database import Base, engine, SessionLocal, User, Message  # 👈 импорт всех моделей
from datetime import datetime

# Переменные окружения
openai_api_key = os.getenv("OPENAI_API_KEY")
assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
telegram_token = os.getenv("TELEGRAM_TOKEN")
webhook_url = os.getenv("WEBHOOK_URL")
webhook_path = "/webhook"

# Создание таблиц
Base.metadata.create_all(bind=engine)

# Flask-приложение
flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "https://hitcourse.ru"}})
client = OpenAI(api_key=openai_api_key)
telegram_app = ApplicationBuilder().token(telegram_token).build()
threads = {}

# Обработчики Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    name = update.effective_user.first_name
    db = SessionLocal()

    user = db.query(User).filter_by(user_id=user_id).first()
    if not user:
        user = User(user_id=user_id, name=name, greeted="yes")
        db.add(user)
    else:
        user.name = name
        user.greeted = "yes"
    db.commit()
    db.close()

    await update.message.reply_text(
        f"Привет, {name}! Я — Словис, помощник платформы Хиткурс.\n"
        "Здесь, чтобы помочь тебе ориентироваться в мире онлайн-обучения.\n"
        "Спроси — и получи честный, понятный ответ 🧠"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_input = update.message.text
    lowered = user_input.lower()
    db = SessionLocal()

    user = db.query(User).filter_by(user_id=user_id).first()
    name = user.name if user else None

    # Обработка имени
    if "меня зовут" in lowered:
        name = user_input.split("меня зовут", 1)[-1].strip().strip(".! ")
        if user:
            user.name = name
        else:
            user = User(user_id=user_id, name=name)
            db.add(user)
        db.commit()
        db.close()
        await update.message.reply_text(f"Приятно познакомиться, {name}! Запомнил 😊")
        return

    if "как меня зовут" in lowered:
        if name:
            await update.message.reply_text(f"Тебя зовут {name}! 😊")
        else:
            await update.message.reply_text("Пока не знаю твоего имени. Напиши: «меня зовут ...»")
        db.close()
        return

    # Создание потока в OpenAI
    if user_id not in threads:
        thread = client.beta.threads.create()
        threads[user_id] = thread.id

    # Получение последних 10 сообщений
    history = db.query(Message).filter_by(user_id=user_id).order_by(Message.timestamp.desc()).limit(10).all()
    for m in reversed(history):
        client.beta.threads.messages.create(
            thread_id=threads[user_id],
            role=m.role,
            content=m.content
        )

    # Отправка нового сообщения пользователя
    client.beta.threads.messages.create(
        thread_id=threads[user_id],
        role="user",
        content=user_input
    )
    db.add(Message(user_id=user_id, role="user", content=user_input))
    db.commit()

    # Получение ответа OpenAI
    client.beta.threads.runs.create_and_poll(
        thread_id=threads[user_id],
        assistant_id=assistant_id
    )
    messages = client.beta.threads.messages.list(thread_id=threads[user_id])
    answer = messages.data[0].content[0].text.value
    
    db.add(Message(user_id=user_id, role="assistant", content=answer))
    db.commit()
    db.close()

    if name and not ("меня зовут" in lowered or "как меня зовут" in lowered):
        answer = f"{name}, {answer}"

    await update.message.reply_text(answer)

# Регистрируем обработчики
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Webhook обработка
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

# Keep-alive ping
def keep_alive_ping():
    while True:
        try:
            requests.get(webhook_url)
        except Exception as e:
            print("Keep-alive error:", e)
        time.sleep(60)

threading.Thread(target=keep_alive_ping, daemon=True).start()

# Flask WebApp (если хочешь оставить)
@flask_app.route("/message", methods=["POST"])
def web_chat():
    try:
        data = request.get_json()
        user_message = data.get("message", "")
        if not user_message.strip():
            return {"reply": "Пустое сообщение."}, 400

        thread_id = threads.get("web")
        if not thread_id:
            thread = client.beta.threads.create()
            threads["web"] = thread.id
            thread_id = thread.id

        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_message
        )
        client.beta.threads.runs.create_and_poll(
            thread_id=thread_id,
            assistant_id=assistant_id
        )
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        reply = messages.data[0].content[0].text.value
        return {"reply": reply}

    except Exception as e:
        print("Ошибка в /message:", e)
        return {"reply": "Произошла ошибка на сервере."}, 500

# Запуск Flask
if __name__ == "__main__":
    print("🤖 Бот с памятью запущен на Railway")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
