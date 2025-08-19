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
        """HTTP სესიის ინიციალიზაცია"""
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_REQUIRED

        connector = aiohttp.TCPConnector(
            ssl=ssl_context,
            limit=20,
            limit_per_host=5,
            ttl_dns_cache=300,
            keepalive_timeout=30,
            enable_cleanup_closed=True
        )

        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30, connect=15),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ka;q=0.8",
                "Connection": "keep-alive",
            }
        )

    async def close_session(self):
        if self.session:
            await self.session.close()

    async def fetch_website_content(self, url: str):
        """საიტიდან კონტენტის მოპოვება"""
        try:
            if not self.session:
                await self.init_session()

            if not url.startswith(("http://", "https://")):
                url = "https://" + url

            async with self.session.get(url, ssl=False) as response:
                if response.status == 200:
                    text = await response.text(errors="ignore")
                    return text
                else:
                    logger.error(f"Bad status {response.status} for {url}")
                    return None
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Connection error for {url}: {e}")
        except asyncio.TimeoutError:
            logger.error(f"Timeout loading {url}")
        except Exception as e:
            logger.error(f"Unexpected fetch error for {url}: {e}")
        return None

    def parse_products(self, html_content, base_url):
        soup = BeautifulSoup(html_content, 'html.parser')
        products = []
        product_selectors = ['.product', '.item', '.product-item', '.product-card', '[class*="product"]', '.card']

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
        try:
            # სახელი
            name_elem = item.select_one("h1, h2, h3, h4, .title, .product-name, .item-title, .card-title")
            name = name_elem.get_text(strip=True) if name_elem else None

            # ფასი
            price_elem = item.select_one(".price, .cost, [class*='price'], [class*='cost']")
            price = None
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                match = re.search(r'(\d+(?:[.,]\d{2})?)', price_text.replace(" ", ""))
                if match:
                    price = match.group(1) + "₾"

            # სურათი
            img_elem = item.select_one("img")
            image_url = None
            if img_elem:
                src = img_elem.get("src") or img_elem.get("data-src")
                if src:
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = urljoin(base_url, src)
                    image_url = src

            # ლინკი
            link_elem = item.select_one("a")
            link_url = None
            if link_elem and link_elem.get("href"):
                href = link_elem["href"]
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    href = urljoin(base_url, href)
                link_url = href

            if name and price:
                return {"name": name[:150], "price": price, "image_url": image_url, "link_url": link_url}
        except Exception as e:
            logger.error(f"Extract error: {e}")
        return None

    def is_valid_image_url(self, url):
        if not url:
            return False
        image_extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp"]
        return any(url.lower().endswith(ext) for ext in image_extensions)

    async def send_products_with_images(self, update, products, website_name="საიტი"):
        if not products:
            await update.message.reply_text("🚫 პროდუქცია არ მოიძებნა.")
            return

        for i, product in enumerate(products[:6], 1):
            caption = f"*{i}. {product['name']}*\n💰 {product['price']}"
            if product.get("link_url"):
                caption += f"\n🔗 [ნახვა]({product['link_url']})"

            if product.get("image_url") and self.is_valid_image_url(product['image_url']):
                try:
                    await update.message.reply_photo(product["image_url"], caption=caption, parse_mode="Markdown")
                except:
                    await update.message.reply_text(caption, parse_mode="Markdown")
            else:
                await update.message.reply_text(caption, parse_mode="Markdown")
            await asyncio.sleep(0.3)


class TelegramBot:
    def __init__(self, bot_token):
        self.product_bot = ProductBot(bot_token)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[InlineKeyboardButton("🛍️ ძებნა", callback_data='search_products')]]
        await update.message.reply_text("🤖 მოგესალმებით! გამომიგზავნეთ საიტის URL პროდუქციის მოსაძებნად.", 
                                        reply_markup=InlineKeyboardMarkup(keyboard))

    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❗ გამოიყენეთ `/search <url>`")
            return
        url = context.args[0]
        await self.process_website(update, url)

    async def handle_url_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        urls = re.findall(r'https?://[^\s]+', text)
        if urls:
            await self.process_website(update, urls[0])
        else:
            await update.message.reply_text("❗ მიუთითეთ სწორი URL")

    async def process_website(self, update, url):
        msg = await update.message.reply_text("🔍 ვტვირთავ საიტს...")
        try:
            if not self.product_bot.session:
                await self.product_bot.init_session()

            html = await self.product_bot.fetch_website_content(url)
            if not html:
                await msg.edit_text("❌ საიტის ჩატვირთვა ვერ მოხერხდა")
                return

            products = self.product_bot.parse_products(html, url)
            await msg.delete()
            await self.product_bot.send_products_with_images(update, products, urlparse(url).netloc)
        except Exception as e:
            logger.error(f"Process error: {e}")
            await msg.edit_text("❌ შეცდომა მოხდა")

def main():
    import os, signal, sys
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN არ არის დაყენებული!")
        return

    tg = TelegramBot(BOT_TOKEN)
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", tg.start_command))
    application.add_handler(CommandHandler("search", tg.search_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tg.handle_url_message))

    def signal_handler(signum, frame):
        print("🛑 გაჩერება...")
        if tg.product_bot.session:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(tg.product_bot.close_session())
            else:
                loop.run_until_complete(tg.product_bot.close_session())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("🤖 ბოტი გაშვებულია...")
    application.run_polling()

if __name__ == "__main__":
    main()
