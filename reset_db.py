
from database import Base, engine
from sqlalchemy import inspect, text

# Получаем соединение
with engine.connect() as connection:
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    for table in tables:
        connection.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
        print(f"Удалена таблица: {table}")
