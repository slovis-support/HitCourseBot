import os
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI

# Переменные окружения
openai_api_key = os.getenv("OPENAI_API_KEY")
assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
telegram_token = os.getenv("TELEGRAM_TOKEN")
webhook_url = os.getenv("WEBHOOK_URL")

# Инициализация OpenAI
client = OpenAI(api_key=openai_api_key)
threads = {}

# Telegram-бот
telegram_app = ApplicationBuilder().token(telegram_token).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я — Словис, помощник платформы Хиткурс. Готов помочь! 🧠")

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

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Flask-приложение для Webhook
flask_app = Flask(__name__)

@import asyncio

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    from threading import Thread

    update = Update.de_json(request.get_json(force=True), telegram_app.bot)

    async def handle():
        await telegram_app.initialize()
        await telegram_app.process_update(update)

    def runner():
        asyncio.run(handle())

    Thread(target=runner).start()
    return "OK", 200



if __name__ == "__main__":
    print("🤖 Бот HitCourse (Webhook + Assistant API) запущен.")
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
