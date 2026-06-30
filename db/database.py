import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.models import Base

# Путь к файлу базы данных SQLite в корне проекта
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Создаем движок базы данных
# connect_args={"check_same_thread": False} нужен только для SQLite при асинхронной работе
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# Создаем фабрику сессий для работы с БД
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Создает таблицы в базе данных, если они еще не созданы"""
    Base.metadata.create_all(bind=engine)
    print(f"[БД] База данных успешно инициализирована по пути: {DB_PATH}")

def get_db():
    """Контекстный менеджер для получения сессии БД"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()