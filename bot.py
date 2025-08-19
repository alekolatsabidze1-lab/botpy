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
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        
        connector = aiohttp.TCPConnector(
            ssl=ssl_context,
            limit=100,
            limit_per_host=10,
            ttl_dns_cache=300,
            keepalive_timeout=60,
            enable_cleanup_closed=True
        )
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ka-GE,ka;q=0.9,en;q=0.8,ru;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive'
            }
        )
    
    async def close_session(self):
        if self.session:
            await self.session.close()
    
    async def fetch_website_content(self, url):
        try:
            if not self.session:
                await self.init_session()
            
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.text(encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            return None
    
    def parse_products(self, html_content, base_url):
        soup = BeautifulSoup(html_content, 'html.parser')
        products = []
        
        selectors = ['.product', '.item', '.product-item', '.product-card', '[class*="product"]']
        
        for selector in selectors:
            items = soup.select(selector)
            if items:
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
            name = None
            for sel in ['h1','h2','h3','.title','.name','.product-name']:
                el = item.select_one(sel)
                if el:
                    txt = el.get_text(strip=True)
                    if len(txt) > 3:
                        name = txt
                        break
            
            # ფასი
            price = None
            for sel in ['.price','.cost','[class*="price"]']:
                el = item.select_one(sel)
                if el:
                    text = el.get_text(strip=True)
                    for pat in [
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:₾|ლარი|GEL)',
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:\$|USD)',
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:€|EUR)',
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)'
                    ]:
                        m = re.search(pat, text.replace(" ", ""))
                        if m:
                            price = m.group(1).replace(",","") + "₾"
                            break
                if price:
                    break
            
            # სურათი
            image_url = None
            img = item.select_one("img")
            if img and img.get("src"):
                image_url = urljoin(base_url, img.get("src"))
            
            # ლინკი
            link_url = None
            a = item.select_one("a")
            if a and a.get("href"):
                link_url = urljoin(base_url, a.get("href"))
            
            if name and price:
                return {
                    "name": name,
                    "price": price,
                    "image_url": image_url,
                    "link_url": link_url
                }
        except Exception as e:
            logger.error(f"Product parse error: {e}")
        return None
    
    def is_valid_image_url(self, url):
        if not url:
            return False
        return any(url.lower().endswith(ext) for ext in ['.jpg','.jpeg','.png','.gif','.webp'])


class TelegramBot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.product_bot = ProductBot(bot_token)
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[InlineKeyboardButton("🛍️ პროდუქციის ძებნა", callback_data='search_products')]]
        await update.message.reply_text("🤖 მოგესალმებით!", reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❗ მიუთითე URL")
            return
        url = context.args[0]
        await self.process_website(update, url)
    
    async def handle_url_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        urls = re.findall(r'https?://[^\s]+', text)
        if urls:
            await self.process_website(update, urls[0])
        else:
            await update.message.reply_text("❗ მიუთითე ვალიდური URL")
    
    async def process_website(self, update, url):
        if not self.product_bot.session:
            await self.product_bot.init_session()
        
        html = await self.product_bot.fetch_website_content(url)
        if not html:
            await update.message.reply_text("❌ საიტი ვერ ჩაიტვირთა")
            return
        
        products = self.product_bot.parse_products(html, url)
        if not products:
            await update.message.reply_text("🚫 ვერ მოიძებნა პროდუქტი")
            return
        
        for p in products[:5]:
            msg = f"*{p['name']}*\nფასი: {p['price']}"
            if p['link_url']:
                msg += f"\n🔗 {p['link_url']}"
            await update.message.reply_text(msg, parse_mode='Markdown')


def main():
    import os, signal, sys
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        print("BOT_TOKEN not set")
        return
    
    bot = TelegramBot(BOT_TOKEN)
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("search", bot.search_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_url_message))
    
    def shutdown(sig, frame):
        print("🛑 გაჩერება...")
        if bot.product_bot.session:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(bot.product_bot.close_session())
            else:
                loop.run_until_complete(bot.product_bot.close_session())
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    
    print("🤖 ბოტი გაშვებულია...")
    app.run_polling()

if __name__ == "__main__":
    main()
