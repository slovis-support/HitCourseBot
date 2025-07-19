from database import Base, engine

# Удалить все таблицы (если есть)
Base.metadata.drop_all(bind=engine)

# Создать заново с актуальной структурой
Base.metadata.create_all(bind=engine)

print("✅ База данных сброшена и пересоздана.")
