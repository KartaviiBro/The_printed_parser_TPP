# Playwright's official image ships Chromium + all OS deps preinstalled.
# (Tag matches the playwright version pinned in requirements.txt.)
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY . .

# Persisted SQLite lives on a mounted volume.
RUN mkdir -p /app/data
ENV TPP_DATABASE_URL=sqlite:////app/data/database.db

EXPOSE 8000

# Create tables, then serve the dashboard (headless scraping from the UI).
CMD ["sh", "-c", "python init_db.py && python webapp.py --host 0.0.0.0 --no-browser"]
