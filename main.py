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
from database import SessionLocal, User, Message, init_db

init_db()

openai_api_key = os.getenv("OPENAI_API_KEY")
assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
telegram_token = os.getenv("TELEGRAM_TOKEN")
webhook_url = os.getenv("WEBHOOK_URL")
webhook_path = "/webhook"

flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "https://hitcourse.ru"}})
client = OpenAI(api_key=openai_api_key)
telegram_app = ApplicationBuilder().token(telegram_token).build()
threads = {}

# Обработчик команды /start
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
    await update.message.reply_text(f"Привет, {name}! Я — Словис. Спроси что угодно по обучению!")

# Основной обработчик сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    lowered = text.lower()
    db = SessionLocal()

    # Найти или создать пользователя
    user = db.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id)
        db.add(user)
        db.commit()
    name = user.name

    # Обработка фразы "меня зовут"
    if "меня зовут" in lowered:
        name = text.split("меня зовут")[-1].strip().strip(".!")
        user.name = name
        db.commit()
        db.close()
        await update.message.reply_text(f"Приятно познакомиться, {name}! Запомнил 😊")
        return

    # Получаем последние 10 сообщений
    history = (
        db.query(Message)
        .filter_by(user_id=user.id)
        .order_by(Message.timestamp.desc())
        .limit(10)
        .all()
    )
    context_messages = []
    for msg in reversed(history):
        context_messages.append({"role": msg.role, "content": msg.content})

    context_messages.append({"role": "user", "content": text})

    # Создаем поток, если не существует
    if user_id not in threads:
        thread = client.beta.threads.create()
        threads[user_id] = thread.id

    try:
        for m in context_messages:
            client.beta.threads.messages.create(
                thread_id=threads[user_id],
                role=m["role"],
                content=m["content"]
            )

        client.beta.threads.runs.create_and_poll(
            thread_id=threads[user_id],
            assistant_id=assistant_id
        )

        messages = client.beta.threads.messages.list(thread_id=threads[user_id])
        answer = messages.data[0].content[0].text.value

        # Добавим имя в ответ, если оно есть и это не имя-вопрос
        if name and not ("как меня зовут" in lowered or "меня зовут" in lowered):
            answer = f"{name}, {answer}"

        # Сохраняем сообщения
        db.add(Message(user_id=user.id, role="user", content=text))
        db.add(Message(user_id=user.id, role="assistant", content=answer))
        db.commit()

        await update.message.reply_text(answer)

    except Exception as e:
        print("Ошибка OpenAI:", e)
        await update.message.reply_text("Произошла ошибка. Попробуй позже.")
    finally:
        db.close()

# Обработка Telegram Webhook
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

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

# Запуск Flask
if __name__ == "__main__":
    print("🤖 Бот Словис запущен на Railway")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
