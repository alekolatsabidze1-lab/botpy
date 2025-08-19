import asyncio
import aiohttp
import ssl
import certifi
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin, urlparse

# ლოგინგის კონფიგურაცია
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


class ProductBot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.session = None

    async def init_session(self):
        """HTTP სესიის ინიციალიზაცია SSL სერტიფიკატებით"""
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_REQUIRED

        connector = aiohttp.TCPConnector(
            ssl=ssl_context,
            limit=100,
            limit_per_host=10,
            ttl_dns_cache=300,
            keepalive_timeout=60
        )

        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ka-GE,ka;q=0.9,en;q=0.8,ru;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
            }
        )

    async def close_session(self):
        if self.session:
            await self.session.close()

    async def fetch_website_content(self, url):
        """საიტიდან კონტენტის მოპოვება"""
        try:
            if not self.session:
                await self.init_session()

            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.text(encoding='utf-8', errors='ignore')
                return None
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            return None

    def parse_products(self, html_content, base_url):
        """HTML-ის პარსინგი პროდუქციის მოსაპოვებლად"""
        soup = BeautifulSoup(html_content, 'html.parser')
        products = []

        product_selectors = [
            '.product', '.item', '.product-item', '.product-card',
            '[class*="product"]', '.card', '.item-card'
        ]

        for selector in product_selectors:
            items = soup.select(selector)
            if items and len(items) > 1:
                for item in items[:10]:
                    product = self.extract_product_info(item, base_url)
                    if product:
                        products.append(product)
                if products:
                    break

        return products

    def extract_product_info(self, item, base_url):
        """პროდუქტის ინფორმაციის ამოღება"""
        try:
            name = None
            for selector in ['h1', 'h2', 'h3', '.title', '.name', '.product-name']:
                name_elem = item.select_one(selector)
                if name_elem:
                    name = name_elem.get_text(strip=True)
                    if len(name) > 3:
                        break

            price = None
            for selector in ['.price', '[class*="price"]', '.amount']:
                price_elem = item.select_one(selector)
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    match = re.search(r'(\d+(?:[.,]\d{1,2})?)', price_text)
                    if match:
                        price = match.group(1) + "₾"
                        break

            image_url = None
            img_elem = item.select_one("img")
            if img_elem:
                image_url = img_elem.get("src")
                if image_url and image_url.startswith("/"):
                    image_url = urljoin(base_url, image_url)

            link_url = None
            link_elem = item.select_one("a")
            if link_elem and link_elem.get("href"):
                href = link_elem.get("href")
                if href.startswith("/"):
                    link_url = urljoin(base_url, href)
                else:
                    link_url = href

            if name and price:
                return {
                    "name": name,
                    "price": price,
                    "image_url": image_url,
                    "link_url": link_url
                }
        except Exception as e:
            logger.error(f"Parse error: {e}")

        return None

    def is_valid_image_url(self, url):
        if not url:
            return False
        return url.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))


class TelegramBot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.product_bot = ProductBot(bot_token)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🛍️ პროდუქციის ძებნა", callback_data='search_products')],
            [InlineKeyboardButton("ℹ️ დახმარება", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🤖 მოგესალმებით! გამომიგზავნეთ URL.", reply_markup=reply_markup)

    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❗ URL მიუთითე `/search https://example.com`")
            return
        url = context.args[0]
        await self.process_website(update, url)

    async def handle_url_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        urls = re.findall(r'https?://[^\s]+', update.message.text)
        if urls:
            await self.process_website(update, urls[0])
        else:
            await update.message.reply_text("❗ მიუთითე ვალიდური URL")

    async def process_website(self, update, url):
        msg = await update.message.reply_text("🔍 ვძებნი პროდუქციას...")
        html = await self.product_bot.fetch_website_content(url)
        if not html:
            await msg.edit_text("❌ ვერ ჩაიტვირთა")
            return
        products = self.product_bot.parse_products(html, url)
        if not products:
            await msg.edit_text("❌ პროდუქცია ვერ მოიძებნა")
            return
        await msg.delete()
        for product in products[:6]:
            caption = f"*{product['name']}*\n💰 {product['price']}"
            if product.get("link_url"):
                caption += f"\n🔗 {product['link_url']}"
            if product.get("image_url") and self.product_bot.is_valid_image_url(product['image_url']):
                await update.message.reply_photo(photo=product['image_url'], caption=caption, parse_mode="Markdown")
            else:
                await update.message.reply_text(caption, parse_mode="Markdown")
            await asyncio.sleep(0.5)


def main():
    import os
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN არ არის დაყენებული")
        return
    telegram_bot = TelegramBot(BOT_TOKEN)
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", telegram_bot.start_command))
    application.add_handler(CommandHandler("search", telegram_bot.search_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, telegram_bot.handle_url_message))
    print("🤖 ბოტი გაშვებულია...")
    application.run_polling()


if __name__ == "__main__":
    main()
