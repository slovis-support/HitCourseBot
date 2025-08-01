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
from models import Base, User, Message

# Переменные окружения
DATABASE_URL = os.environ['DATABASE_URL']
openai_api_key = os.getenv("OPENAI_API_KEY")
assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
telegram_token = os.getenv("TELEGRAM_TOKEN")
webhook_url = os.getenv("WEBHOOK_URL")
webhook_path = "/webhook"

# SQLAlchemy с проверкой подключения
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

# Telegram и Flask
telegram_app = ApplicationBuilder().token(telegram_token).build()
flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "https://hitcourse.ru"}})
client = OpenAI(api_key=openai_api_key)
threads = {}

def save_message(user_id, role, content):
    db = SessionLocal()
    try:
        db.add(Message(user_id=user_id, role=role, content=content))
        db.commit()
    except Exception as e:
        print("Ошибка при сохранении сообщения:", e)
    finally:
        db.close()

def get_last_messages(user_id, limit=10):
    db = SessionLocal()
    try:
        messages = (
            db.query(Message)
            .filter(Message.user_id == user_id)
            .order_by(Message.timestamp.desc())
            .limit(limit)
            .all()
        )
        return reversed(messages)
    except Exception as e:
        print("Ошибка при получении истории:", e)
        return []
    finally:
        db.close()

def clear_messages(user_id):
    db = SessionLocal()
    try:
        db.query(Message).filter(Message.user_id == user_id).delete()
        db.commit()
    except Exception as e:
        print("Ошибка при очистке истории:", e)
    finally:
        db.close()

# Создание таблицы users
with psycopg2.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
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
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users (user_id, name, greeted)
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (user_id) DO UPDATE SET name = EXCLUDED.name, greeted = TRUE
                """, (user_id, name))
                conn.commit()

        await update.message.reply_text(
            f"Привет, {name}! Я — Словис, помощник платформы Хиткурс.\n"
            "Спроси — и получи честный, понятный ответ 🧠"
        )
    except Exception as e:
        print("Ошибка в start:", e)

from telegram.constants import ChatAction

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Получено сообщение от Telegram")
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
        # Показываем «Словис печатает...»
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # Грузим имя и статус приветствия
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name, greeted FROM users WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                name, greeted = row if row else (None, False)
                if name and not greeted:
                    await update.message.reply_text(f"Рад снова видеть, {name}! 😊")
                    cur.execute("UPDATE users SET greeted = TRUE WHERE user_id = %s", (user_id,))
                    conn.commit()

        # Загружаем историю
        history = get_last_messages(user_id, limit=10)
        for msg in history:
            client.beta.threads.messages.create(
                thread_id=threads[user_id], role=msg.role, content=msg.content
            )

        # Отправляем новое сообщение
        client.beta.threads.messages.create(
            thread_id=threads[user_id], role="user", content=user_input
        )

        # Запуск и получение ответа
        client.beta.threads.runs.create_and_poll(
            thread_id=threads[user_id], assistant_id=assistant_id
        )
        messages = client.beta.threads.messages.list(thread_id=threads[user_id])
        answer = messages.data[0].content[0].text.value

        save_message(user_id, "user", user_input)
        save_message(user_id, "assistant", answer)

        await update.message.reply_text(answer)

    except Exception as e:
        print("Ошибка OpenAI:", e)
        await update.message.reply_text("Произошла ошибка. Попробуй позже.")

    except Exception as e:
        print("Ошибка OpenAI:", e)
        await update.message.reply_text("Произошла ошибка. Попробуй позже.")

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

@flask_app.route(webhook_path, methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    async def process():
        await telegram_app.initialize()
        await telegram_app.process_update(update)
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(process())
    except Exception as e:
        print("Webhook error:", e)
    return "OK", 200

def keep_alive_ping():
    while True:
        try:
            requests.get(webhook_url)
        except Exception as e:
            print("Keep-alive error:", e)
        time.sleep(60)

threading.Thread(target=keep_alive_ping, daemon=True).start()

@flask_app.route("/message", methods=["POST"])
def web_chat():
    try:
        data = request.get_json()
        user_message = data.get("message", "")
        user_id = data.get("user_id", "web_user")
        user_name = data.get("name", "Гость")

        if not user_message.strip():
            return {"reply": "Пустое сообщение."}, 400

        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
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
                thread_id=threads[user_id], role=msg.role, content=msg.content
            )

        client.beta.threads.messages.create(
            thread_id=threads[user_id], role="user", content=user_message
        )

        run = client.beta.threads.runs.create_and_poll(
            thread_id=threads[user_id], assistant_id=assistant_id
        )
        messages = client.beta.threads.messages.list(thread_id=threads[user_id])
        reply = messages.data[0].content[0].text.value

        save_message(user_id, "user", user_message)
        save_message(user_id, "assistant", reply)

        return {"reply": reply}

    except Exception as e:
        print("Ошибка в /message:", e)
        return {"reply": "Произошла ошибка на сервере."}, 500

if __name__ == "__main__":
    print("\U0001F9D0 Бот HitCourse запущен")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
