# reset_db.py
from database import Base, engine

# Удаление всех таблиц
Base.metadata.drop_all(bind=engine)
print("❌ Все таблицы удалены.")
