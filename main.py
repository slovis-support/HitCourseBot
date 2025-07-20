import os
import asyncio
import threading
import time
import requests
import psycopg2
from flask import Flask, request
from flask_cors import CORS
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# URL БД из Railway
DATABASE_URL = os.environ['DATABASE_URL']

from models import Base, User, Message  # Импортируем из models.py

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

def save_message(user_id, role, content):
    db = SessionLocal()
    message = Message(user_id=user_id, role=role, content=content)
    db.add(message)
    db.commit()
    db.close()

def get_last_messages(user_id, limit=10):
    db = SessionLocal()
    messages = (
        db.query(Message)
        .filter(Message.user_id == user_id)
        .order_by(Message.timestamp.desc())
        .limit(limit)
        .all()
    )
    db.close()
    return reversed(messages)

def clear_messages(user_id):
    db = SessionLocal()
    db.query(Message).filter(Message.user_id == user_id).delete()
    db.commit()
    db.close()

# Создаём таблицы при первом запуске
Base.metadata.create_all(bind=engine)

# Переменные окружения
openai_api_key = os.getenv("OPENAI_API_KEY")
assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
telegram_token = os.getenv("TELEGRAM_TOKEN")
webhook_url = os.getenv("WEBHOOK_URL")
database_url = os.getenv("DATABASE_URL")
webhook_path = "/webhook"

# Flask-приложение
flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "https://hitcourse.ru"}})
client = OpenAI(api_key=openai_api_key)
telegram_app = ApplicationBuilder().token(telegram_token).build()
threads = {}

# Подключение PostgreSQL
conn = psycopg2.connect(database_url)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    greeted BOOLEAN DEFAULT FALSE
)
""")
conn.commit()

# Обработчики Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    name = update.effective_user.first_name
    cur.execute("""
        INSERT INTO users (user_id, name, greeted)
        VALUES (%s, %s, TRUE)
        ON CONFLICT (user_id) DO UPDATE SET name = EXCLUDED.name, greeted = TRUE
    """, (user_id, name))
    conn.commit()

    await update.message.reply_text(
        f"Привет, {name}! Я — Словис, помощник платформы Хиткурс.\n"
        "Здесь, чтобы помочь тебе ориентироваться в мире онлайн-обучения.\n"
        "Спроси — и получи честный, понятный ответ 🧠"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_input = update.message.text

    if user_input.strip().lower() == "/clear":
        clear_messages(user_id)
        await update.message.reply_text("История очищена 🗑️")
        return

    if user_id not in threads:
        thread = client.beta.threads.create()
        threads[user_id] = thread.id

    try:
        cur.execute("SELECT name, greeted FROM users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        name, greeted = row if row else (None, False)

        if name and not greeted:
            await update.message.reply_text(f"Рад снова видеть, {name}! 😊")
            cur.execute("UPDATE users SET greeted = TRUE WHERE user_id = %s", (user_id,))
            conn.commit()

        history = get_last_messages(user_id, limit=10)
        for msg in history:
            client.beta.threads.messages.create(
                thread_id=threads[user_id],
                role=msg.role,
                content=msg.content
            )

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

        save_message(user_id, "user", user_input)
        save_message(user_id, "assistant", answer)

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
        user_id = data.get("user_id", "web_user")
        user_name = data.get("name", "Гость")

        if not user_message.strip():
            return {"reply": "Пустое сообщение."}, 400

        cur.execute("""
            INSERT INTO users (user_id, name, greeted)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (user_id) DO NOTHING
        """, (user_id, user_name))
        conn.commit()

        if user_id not in threads:
            thread = client.beta.threads.create()
            threads[user_id] = thread.id

        history = get_last_messages(user_id, limit=10)
        for msg in history:
            client.beta.threads.messages.create(
                thread_id=threads[user_id],
                role=msg.role,
                content=msg.content
            )

        client.beta.threads.messages.create(
            thread_id=threads[user_id],
            role="user",
            content=user_message
        )

        run = client.beta.threads.runs.create_and_poll(
            thread_id=threads[user_id],
            assistant_id=assistant_id
        )
        messages = client.beta.threads.messages.list(thread_id=threads[user_id])
        reply = messages.data[0].content[0].text.value

        save_message(user_id, "user", user_message)
        save_message(user_id, "assistant", reply)

        return {"reply": reply}

    except Exception as e:
        print("Ошибка в /message:", e)
        return {"reply": "Произошла ошибка на сервере."}, 500

# Запуск Flask
if __name__ == "__main__":
    print("🤖 Бот HitCourse (Webhook + Assistant API + PostgreSQL) запущен на Railway")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
