from sqlalchemy import create_engine
from models import Base  # если у тебя есть models.py
# или если всё в main.py, замени на:
# from main import Base, engine

# 👇 Вставь сюда ту же строку, что у тебя в main.py
DATABASE_URL = "postgresql://postgres:HMNwRXohqjAKGPpRLjaXGZToShilJUCc@mainline.proxy.rlwy.net:12203/railway"
engine = create_engine(DATABASE_URL)

# ❌ Удаляем старые таблицы
Base.metadata.drop_all(bind=engine)

# ✅ Создаём заново
Base.metadata.create_all(bind=engine)

print("📦 Таблицы пересозданы!")
