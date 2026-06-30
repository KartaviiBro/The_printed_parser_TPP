# init_db.py
"""Create the database schema."""
from db.database import init_db
from scraper.logging_setup import setup_logging

if __name__ == "__main__":
    setup_logging()
    print("Initialising database tables...")
    init_db()
    print("Done. You can now run the scraper (see: python main.py --help).")
