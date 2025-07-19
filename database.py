from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

# 🔗 Подключение к PostgreSQL на Railway
DATABASE_URL = "postgresql://postgres:HMNwRXohqjAKGPpRLjaXGZToShilJUCc@mainline.proxy.rlwy.net:12203/railway"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 👤 Таблица пользователей
class User(Base):
    __tablename__ = "users"
    user_id = Column(String, primary_key=True, index=True)
    name = Column(String)
    greeted = Column(String)  # может быть "yes" или None

# 💬 Таблица сообщений
class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    role = Column(String)  # "user" или "assistant"
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
