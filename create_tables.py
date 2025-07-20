import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from models import User, Message  # если User и Message у тебя в другом файле — поправь импорт

# Если переменные заданы через Railway (как DATABASE_URL):
DATABASE_URL = os.environ['DATABASE_URL']

engine = create_engine(DATABASE_URL)
Base = declarative_base()

# Зарегистрируй модели
User.__table__.create(bind=engine, checkfirst=True)
Message.__table__.create(bind=engine, checkfirst=True)

print("✅ Таблицы созданы (если их не было)")
