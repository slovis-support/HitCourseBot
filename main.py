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

# Flask-приложение
flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "https://hitcourse.ru"}})
client = OpenAI(api_key=openai_api_key)
telegram_app = ApplicationBuilder().token(telegram_token).build()
threads = {}

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
        "Здесь, чтобы помочь тебе ориентироваться в мире онлайн-обучения.\n"
        "Спроси — и получи честный, понятный ответ 🧠"
    )

# Сообщения Telegram
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_input = update.message.text.strip()
    db = SessionLocal()

    user = db.query(User).filter_by(telegram_id=user_id).first()
    name = user.name if user else None
    lowered = user_input.lower()

    # Обработка фразы "как меня зовут"
    if "как меня зовут" in lowered:
        if name:
            await update.message.reply_text(f"Тебя зовут {name} 😊")
        else:
            await update.message.reply_text("Я пока не знаю, как тебя зовут. Напиши: «Меня зовут ...»")
        db.close()
        return

    # Обработка фразы "меня зовут ..."
    if "меня зовут" in lowered:
        try:
            new_name = user_input.lower().split("меня зовут", 1)[1].strip().split(" ")[0].capitalize()
            if new_name:
                if user:
                    user.name = new_name
                else:
                    user = User(telegram_id=user_id, name=new_name)
                    db.add(user)
                db.commit()
                await update.message.reply_text(f"Приятно познакомиться, {new_name}! Запомнил 😊")
            else:
                await update.message.reply_text("Я не понял имя. Напиши, например: Меня зовут Алексей.")
        except:
            await update.message.reply_text("Я не смог распознать имя. Попробуй ещё раз.")
        db.close()
        return

    # GPT-ответ
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
        reply = messages.data[0].content[0].text.value

        if name:
            reply = f"{name}, {reply}"
        await update.message.reply_text(reply)

    except Exception as e:
        print("Ошибка OpenAI:", e)
        await update.message.reply_text("Произошла ошибка. Попробуй позже.")
    finally:
        db.close()

# Регистрируем обработчики
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Webhook от Telegram
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

# Keep-alive пинг
def keep_alive_ping():
    while True:
        try:
            requests.get(webhook_url)
        except Exception as e:
            print("Keep-alive error:", e)
        time.sleep(60)

threading.Thread(target=keep_alive_ping, daemon=True).start()

# WebApp маршрут
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
        client.beta.threads.runs.create_and_poll(
            thread_id=threads["web"],
            assistant_id=assistant_id
        )
        messages = client.beta.threads.messages.list(thread_id=threads["web"])
        reply = messages.data[0].content[0].text.value
        return {"reply": reply}

    except Exception as e:
        print("Ошибка в /message:", e)
        return {"reply": "Произошла ошибка на сервере."}, 500

# Запуск
if __name__ == "__main__":
    print("🤖 Бот HitCourse (Webhook + Assistant API + PostgreSQL ORM) запущен на Railway")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
