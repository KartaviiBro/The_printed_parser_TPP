import asyncio
from playwright.async_api import async_playwright
from db.database import SessionLocal
from db.models import Model3D

class PrintablesScraper:
    async def fetch_popular_models(self, limit: int = 20) -> list:
        print("[Printables] Запуск браузера...")
        async with async_playwright() as p:
            # Запускаем НЕ headless (будем видеть окно браузера)
            # Это лучший способ пройти проверку Cloudflare
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # Переходим на страницу
            await page.goto("https://www.printables.com/model?o=popular")
            
            print("[Printables] Ждем 15 секунд... пройдите капчу вручную, если появится!")
            await page.wait_for_timeout(15000)
            
            # Теперь, когда мы "живые" люди, попробуем забрать данные
            models = await page.evaluate('''() => {
                const cards = document.querySelectorAll('.print-card');
                return Array.from(cards).map(card => ({
                    title: card.querySelector('.print-card__title')?.innerText.trim(),
                    url: "https://www.printables.com" + card.querySelector('a')?.getAttribute('href'),
                    img_url: card.querySelector('img')?.getAttribute('src')
                }));
            }''')
            
            await browser.close()
            return [m for m in models if m['title']]

    async def run(self):
        models = await self.fetch_popular_models()
        print(f"[Printables] Найдено моделей: {len(models)}")
        db = SessionLocal()
        try:
            for model_data in models:
                if not db.query(Model3D).filter(Model3D.url == model_data["url"]).first():
                    db.add(Model3D(**model_data))
            db.commit()
            print("[Printables] Данные успешно сохранены!")
        finally:
            db.close()

if __name__ == "__main__":
    asyncio.run(PrintablesScraper().run())