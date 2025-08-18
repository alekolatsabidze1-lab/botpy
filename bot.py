import asyncio
import json
import os
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

# ფაილები
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DATA_PATH = BASE_DIR / "data.json"

# config.json ჩატვირთვა
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

# მონაცემები (თუ არსებობს)
if DATA_PATH.exists():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        products = json.load(f)
else:
    products = {}

# subscribers
subscribers = set()

# პროდუქციის წამოღება ერთი კატეგორიიდან
async def fetch_category_products(url: str):
    items = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, timeout=60000)

        cards = await page.query_selector_all(".product-card")
        for card in cards:
            name_el = await card.query_selector(".product-card-title")
            price_el = await card.query_selector(".product-card-price")
            link_el = await card.query_selector("a")

            name = (await name_el.inner_text()).strip() if name_el else "No name"
            price = (await price_el.inner_text()).strip() if price_el else "No price"
            link = await link_el.get_attribute("href") if link_el else url

            items.append({"id": link, "name": name, "price": price, "url": link})

        await browser.close()
    return items

# ყველა კატეგორიის წამოღება
async def fetch_all_products():
    all_items = []
    for url in config["categories"]:
        try:
            items = await fetch_category_products(url)
            all_items.extend(items)
        except Exception as e:
            print("Scrape error:", e)
    return all_items

# შეტყობინება subscribers-ს
async def notify(app: Application, message: str):
    for chat_id in subscribers:
        try:
            await app.bot.send_message(chat_id, message)
        except Exception as e:
            print("Send error:", e)

# მთავარი ფუნქცია - სკანირება
async def scan_and_notify(app: Application):
    global products
    new_products = await fetch_all_products()

    for product in new_products:
        if product["id"] not in products:
            await notify(app, f"🆕 ახალი პროდუქტი: {product['name']} - {product['price']}\n{product['url']}")
            products[product["id"]] = product
        elif products[product["id"]]["price"] != product["price"]:
            await notify(app, f"💰 ფასის ცვლილება: {product['name']}\nძველი ფასი: {products[product['id']]['price']}\nახალი ფასი: {product['price']}\n{product['url']}")
            products[product["id"]] = product

    # შენახვა
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)

# Telegram command: /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers.add(chat_id)
    await update.message.reply_text("გამარჯობა! ✅ ახლა იღებ ყველა სიახლეს და ფასის ცვლილებას.")

# Background task
async def periodic_task(app: Application):
    while True:
        try:
            await scan_and_notify(app)
        except Exception as e:
            print("Scan error:", e)
        await asyncio.sleep(config["scanIntervalMinutes"] * 60)

# main
async def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("❌ BOT_TOKEN env variable not set")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))

    # run background loop
    asyncio.create_task(periodic_task(app))

    print("🤖 Bot started...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
