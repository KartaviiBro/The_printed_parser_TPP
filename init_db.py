from db.database import init_db

if __name__ == "__main__":
    print("Инициализация таблиц базы данных...")
    init_db()
    print("Готово! Теперь можно запускать парсер.")