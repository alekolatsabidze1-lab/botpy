import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright
from telegram import Bot

# --- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN")   # Render env variable
CHAT_ID = os.getenv("CHAT_ID")       # ვის გავუგზავნოთ შეტყობინება
DB_PATH = Path("db.json")
SCAN_INTERVAL = 5 * 60   # 5 წუთი

CATEGORIES = [
    "https://yversy.com/e-store/?SECTION_ID=16617",  # computing parts
    "https://yversy.com/e-store/?SECTION_ID=16618",  # laptops
]

# --- HELPERS ---
async def load_db():
    if DB_PATH.exists():
        return json.loads(DB_PATH.read_text())
    return {}

async def save_db(db):
    DB_PATH.write_text(json.dumps(db, indent=2))

async def scrape_category(page, url):
    await page.goto(url)
    await page.wait_for_selector(".product-item")
    items = await page.query_selector_all(".product-item")

    products = []
    for item in items:
        title = await item.query_selector(".product-item-title")
        price = await item.query_selector(".product-item-price-current")

        title_text = await title.inner_text() if title else "Unknown"
        price_text = await price.inner_text() if price else "No price"

        products.append({"title": title_text, "price": price_text})
    return products

async def scan_and_notify():
    bot = Bot(BOT_TOKEN)
    db = await load_db()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        for url in CATEGORIES:
            try:
                products = await scrape_category(page, url)
                old_products = db.get(url, [])

                # ახალი პროდუქტების პოვნა
                new_items = [p for p in products if p not in old_products]

                if new_items:
                    msg = f"🆕 ახალი პროდუქტები ({url}):\n"
                    for p in new_items:
                        msg += f"- {p['title']} — {p['price']}\n"

                    await bot.send_message(chat_id=CHAT_ID, text=msg)

                db[url] = products
            except Exception as e:
                print(f"Scrape error on {url}: {e}")

        await browser.close()
        await save_db(db)

async def main():
    while True:
        await scan_and_notify()
        await asyncio.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
