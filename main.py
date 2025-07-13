import os
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, ContextTypes, filters
)
from openai import OpenAI

# Переменные окружения
openai_api_key = os.getenv("OPENAI_API_KEY")
assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
telegram_token = os.getenv("TELEGRAM_TOKEN")
webhook_url = os.getenv("WEBHOOK_URL")  # без /webhook на конце
webhook_path = "/webhook"

# OpenAI клиент
client = OpenAI(api_key=openai_api_key)
threads = {}

# Flask сервер
flask_app = Flask(__name__)

# Telegram приложение
app = ApplicationBuilder().token(telegram_token).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я — Словис, помощник платформы Хиткурс. Готов помочь тебе с обучением и вопросами! 🧠"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_input = update.message.text

    if user_id not in threads:
        thread = client.beta.threads.create()
        threads[user_id] = thread.id

    try:
        client.beta.threads.messages.create(
            thread_id=threads[user_id],
            role="user",
            content=user_input
        )

        run = client.beta.threads.runs.create_and_poll(
            thread_id=threads[user_id],
            assistant_id=assistant_id
        )

        messages = client.beta.threads.messages.list(thread_id=threads[user_id])
        answer = messages.data[0].content[0].text.value
        await update.message.reply_text(answer)

    except Exception as e:
        print("Ошибка OpenAI:", e)
        await update.message.reply_text("Произошла ошибка. Попробуй позже.")

# Регистрируем хендлеры
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Обработка Telegram Webhook во Flask
@flask_app.route(webhook_path, methods=["POST"])
def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), app.bot)
        asyncio.get_event_loop().create_task(app.process_update(update))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.create_task(app.process_update(update))
    return "OK", 200

# Запуск Flask
if __name__ == "__main__":
    print("🤖 Бот HitCourse (Webhook + Assistant API) запущен на Railway")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
