import asyncio
import aiohttp
import ssl
import certifi
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from bs4 import BeautifulSoup
import re
import json
from urllib.parse import urljoin, urlparse

# ლოგინგის კონფიგურაცია
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

class ProductBot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.session = None
        
    async def init_session(self):
        """HTTP სესიის ინიციალიზაცია Render-friendly"""
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False),  # Render-ზე ბევრ საიტს SSL პრობლემა აქვს
            timeout=aiohttp.ClientTimeout(total=40),
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
        """სესიის დახურვა"""
        if self.session:
            await self.session.close()
    
    async def fetch_website_content(self, url: str):
        """Fetch site HTML with aiohttp, fallback to requests if needed"""
        import requests
        try:
            if not self.session:
                await self.init_session()

            if not url.startswith(("http://", "https://")):
                url = "https://" + url

            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.text(errors="ignore")
                else:
                    logger.warning(f"Aiohttp bad status {response.status} for {url}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"Aiohttp error for {url}: {e}")

        # --- fallback to requests ---
        try:
            logger.info(f"Trying requests fallback for {url}")
            r = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0 Safari/537.36"
                },
                timeout=20,
                verify=False
            )
            if r.status_code == 200:
                return r.text
            else:
                logger.error(f"Requests bad status {r.status_code} for {url}")
        except Exception as e:
            logger.error(f"Requests fallback failed for {url}: {e}")

        return None
    
    def parse_products(self, html_content, base_url):
        """HTML-ის პარსინგი პროდუქციის მოსაპოვებლად"""
        soup = BeautifulSoup(html_content, 'html.parser')
        products = []
        
        product_selectors = [
            '.product',
            '.item',
            '.product-item',
            '.product-card',
            '[class*="product"]',
            '.card',
            '.item-card'
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
        """ცალკეული პროდუქტის ინფორმაციის ამოღება"""
        try:
            name_elem = item.select_one("h2, h3, .title, .product-name")
            price_elem = item.select_one(".price, .cost, [class*='price']")
            img_elem = item.select_one("img")
            
            name = name_elem.get_text(strip=True) if name_elem else None
            price = price_elem.get_text(strip=True) if price_elem else None
            image_url = None
            if img_elem and img_elem.get("src"):
                image_url = urljoin(base_url, img_elem.get("src"))
            
            link_elem = item.select_one("a")
            link_url = None
            if link_elem and link_elem.get("href"):
                link_url = urljoin(base_url, link_elem.get("href"))
            
            if name and price:
                return {
                    "name": name,
                    "price": price,
                    "image_url": image_url,
                    "link_url": link_url
                }
        except Exception as e:
            logger.error(f"Error extracting product info: {e}")
        return None
    
    def is_valid_image_url(self, url):
        if not url:
            return False
        return any(url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"])

    async def send_products_with_images(self, update, products, website_name="საიტი"):
        if not products:
            await update.message.reply_text("🚫 პროდუქცია არ მოიძებნა.")
            return
        
        for i, product in enumerate(products[:6], 1):
            caption = f"*{i}. {product['name']}*\n💰 {product['price']}\n"
            if product.get("link_url"):
                caption += f"🔗 [ლინკი]({product['link_url']})"
            
            if product.get("image_url") and self.is_valid_image_url(product["image_url"]):
                try:
                    await update.message.reply_photo(
                        photo=product["image_url"],
                        caption=caption,
                        parse_mode="Markdown"
                    )
                except:
                    await update.message.reply_text(caption, parse_mode="Markdown")
            else:
                await update.message.reply_text(caption, parse_mode="Markdown")
            await asyncio.sleep(0.5)

class TelegramBot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.product_bot = ProductBot(bot_token)
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[InlineKeyboardButton("🛍️ პროდუქციის ძებნა", callback_data='search_products')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🤖 მოგესალმებით! გამომიგზავნეთ საიტის ლინკი.", reply_markup=reply_markup)

    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("❗ გამოიყენეთ: `/search <url>`", parse_mode="Markdown")
            return
        url = context.args[0]
        await self.process_website(update, url)

    async def handle_url_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        urls = re.findall(r'https?://[^\s]+', update.message.text)
        if urls:
            await self.process_website(update, urls[0])
        else:
            await update.message.reply_text("❗ გთხოვთ გამოგზავნოთ ვალიდური URL")

    async def process_website(self, update, url):
        msg = await update.message.reply_text("🔍 ვტვირთავ საიტს...")
        try:
            if not self.product_bot.session:
                await self.product_bot.init_session()
            html_content = await self.product_bot.fetch_website_content(url)
            if not html_content:
                await msg.edit_text("❌ საიტის ჩატვირთვა ვერ მოხერხდა")
                return
            products = self.product_bot.parse_products(html_content, url)
            await msg.delete()
            await self.product_bot.send_products_with_images(update, products, urlparse(url).netloc)
        except Exception as e:
            logger.error(f"Error: {e}")
            await msg.edit_text("❌ მოხდა შეცდომა")

def main():
    import os, signal, sys
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN not set!")
        return
    telegram_bot = TelegramBot(BOT_TOKEN)
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", telegram_bot.start_command))
    application.add_handler(CommandHandler("search", telegram_bot.search_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, telegram_bot.handle_url_message))
    def stop_bot(signum, frame):
        print("🛑 Stop bot")
        sys.exit(0)
    signal.signal(signal.SIGINT, stop_bot)
    signal.signal(signal.SIGTERM, stop_bot)
    print("🤖 Bot running...")
    application.run_polling()

if __name__ == "__main__":
    main()
