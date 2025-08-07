
import os
import re
import asyncio
import threading
import time
import requests
import psycopg2
from flask import Flask, request
from flask_cors import CORS
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.constants import ChatAction
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

# SQLAlchemy
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

# Telegram и Flask
telegram_app = ApplicationBuilder().token(telegram_token).build()
flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "https://hitcourse.ru"}})
client = OpenAI(api_key=openai_api_key)
threads = {}

# 🔧 Улучшенное форматирование ссылок

    
def format_links(text, platform):
    # 🔹 Специальные текстовые фразы → кликабельные ссылки
    replacements = {
        "Перейти на страницу контактов": "https://hitcourse.ru/contacts",
        "Написать на почту": "mailto:support@hitcourse.ru",
        "Связаться в Telegram": "https://t.me/operatorhitcourse",
        "Наш бот": "https://t.me/hitcourse_bot",
    }

    for phrase, url in replacements.items():
        if phrase in text:
            if platform == "telegram":
                text = text.replace(phrase, f"[{phrase}]({url})")
            elif platform == "site":
                text = text.replace(phrase, f'<a href="{url}" target="_blank">{phrase}</a>')

    # 🔹 Исправим дубли типа: [Текст](Текст (URL)) → [Текст](URL)
    def fix_nested_links(match):
        label = match.group(1)
        url = match.group(2)
        return f"[{label}]({url})" if platform == "telegram" else f'<a href="{url}" target="_blank">{label}</a>'

    text = re.sub(r"\[([^\]]+)\]\s*\(.*?\(?\s*(https?://[^\s\)]+)\s*\)?\)", fix_nested_links, text)

    # 🔹 Оборачиваем URL-адреса вида https://... (если остались)
    def wrap_generic_url(match):
        url = match.group(0)
        return f"[Перейти по ссылке]({url})" if platform == "telegram" else f'<a href="{url}" target="_blank">Перейти по ссылке</a>'

    text = re.sub(r"(?<!\]\()(?<!href=\")https?://[^\s\)\]]+", wrap_generic_url, text)

    # 🔹 Удалим служебные фразы
    text = re.sub(r"(Подробнее\s*:|Смотрите\s*:|Узнать\s+подробнее\s*:)", "", text, flags=re.IGNORECASE)

    # 🔹 Удалим JSON-функции типа notify_operator
    text = re.sub(r"{\s*\"name\"\s*:\s*\"notify_operator\".*?}", "", text, flags=re.DOTALL)

    return text.strip()


   

# 🔧 Проверка запроса оператору
def check_operator_request(text):
    operator_phrases = [
        "хочу оператора", 
        "свяжите с оператором",
        "можно поговорить с человеком",
        "живой оператор"
    ]
    return any(phrase in text.lower() for phrase in operator_phrases)

# 🔧 Уведомление оператора
def notify_operator(user_id, platform, username=None):
    message = f"⚠️ Пользователь {username or user_id} ({platform}) хочет связаться с оператором!"
    if platform == "telegram":
        message += f"\nСсылка: tg://resolve?domain={username or user_id}"
    
    # Здесь должна быть ваша реализация отправки уведомления
    print(f"[OPERATOR NOTIFY] {message}")
    # send_to_admin_chat(message)

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

# Telegram: /start
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

# Telegram: обработка сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Получено сообщение от Telegram")
    user_id = str(update.effective_user.id)
    clean_input = update.message.text
    user_input = f"[telegram] {clean_input}"

    if clean_input.strip().lower() == "/clear":
        clear_messages(user_id)
        await update.message.reply_text("История очищена 🗑️")
        return

    # Проверка запроса оператора
    if check_operator_request(clean_input):
        notify_operator(user_id, "telegram", update.effective_user.username)
        await update.message.reply_text(
            "Сейчас свяжу вас с оператором. Ожидайте...\n"
            "Или напишите напрямую: @operatorhitcourse"
        )
        return

    if user_id not in threads:
        thread = client.beta.threads.create()
        threads[user_id] = thread.id

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
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
                thread_id=threads[user_id], role=msg.role, content=msg.content
            )

        client.beta.threads.messages.create(
            thread_id=threads[user_id], role="user", content=user_input
        )

        client.beta.threads.runs.create_and_poll(
            thread_id=threads[user_id], assistant_id=assistant_id
        )
        messages = client.beta.threads.messages.list(thread_id=threads[user_id])
        answer = messages.data[0].content[0].text.value
        # Удаляем хвосты вида  ,   и подобные
        answer = re.sub(r"【\d+(?::\d+)?†[^\】]+】", "", answer)

        formatted_answer = format_links(answer, platform="telegram")

        save_message(user_id, "user", clean_input)
        save_message(user_id, "assistant", answer)

        await update.message.reply_text(formatted_answer, parse_mode="Markdown")

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
        clean_message = data.get("message", "")
        user_message = f"[site] {clean_message}"

        user_id = data.get("user_id", "web_user")
        user_name = data.get("name", "Гость")

        if not user_message.strip():
            return {"reply": "Пустое сообщение."}, 400

        # Проверка запроса оператора
        if check_operator_request(clean_message):
            notify_operator(user_id, "site", user_name)
            return {
                "reply": (
                    "Наш оператор скоро с вами свяжется. "
                    "Или вы можете написать напрямую: "
                    '<a href="https://t.me/operatorhitcourse" target="_blank">@operatorhitcourse</a>'
                ),
                "html": True
            }, 200

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

        formatted_reply = format_links(reply, platform="site")

        save_message(user_id, "user", clean_message)
        save_message(user_id, "assistant", reply)

        return {"reply": formatted_reply, "html": True}

    except Exception as e:
        print("Ошибка в /message:", e)
        return {"reply": "Произошла ошибка на сервере."}, 500

if __name__ == "__main__":
    print("🧠 Бот HitCourse запущен")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
