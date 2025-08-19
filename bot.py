import asyncio
import aiohttp
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
        """HTTP სესიის ინიციალიზაცია"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
        )
    
    async def close_session(self):
        """სესიის დახურვა"""
        if self.session:
            await self.session.close()
    
    async def fetch_website_content(self, url):
        """საიტიდან კონტენტის მოპოვება"""
        try:
            if not self.session:
                await self.init_session()
                
            async with self.session.get(url) as response:
                if response.status == 200:
                    content = await response.text()
                    return content
                else:
                    logger.error(f"HTTP Error {response.status} for URL: {url}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {str(e)}")
            return None
    
    def parse_products(self, html_content, base_url):
        """HTML-ის პარსინგი პროდუქციის მოსაპოვებლად"""
        soup = BeautifulSoup(html_content, 'html.parser')
        products = []
        
        # ესაა მაგალითი - შეცვალეთ თქვენი საიტის სტრუქტურის მიხედვით
        product_selectors = [
            '.product',
            '.item',
            '.product-item',
            '.product-card',
            '[class*="product"]'
        ]
        
        for selector in product_selectors:
            items = soup.select(selector)
            if items:
                for item in items[:10]:  # მაქსიმუმ 10 პროდუქტი
                    product = self.extract_product_info(item, base_url)
                    if product:
                        products.append(product)
                break
        
        return products
    
    def extract_product_info(self, item, base_url):
        """ცალკეული პროდუქტის ინფორმაციის ამოღება"""
        try:
            # სახელის ძებნა
            name_selectors = ['h1', 'h2', 'h3', '.title', '.name', '.product-name', '.product-title']
            name = None
            for selector in name_selectors:
                name_elem = item.select_one(selector)
                if name_elem:
                    name = name_elem.get_text(strip=True)
                    break
            
            # ფასის ძებნა
            price_selectors = ['.price', '.cost', '[class*="price"]', '[class*="cost"]']
            price = None
            for selector in price_selectors:
                price_elem = item.select_one(selector)
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    # ფასის ამოღება რეგექსით
                    price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                    if price_match:
                        price = price_match.group()
                    break
            
            # სურათის ძებნა
            image_url = None
            img_elem = item.select_one('img')
            if img_elem:
                img_src = img_elem.get('src') or img_elem.get('data-src')
                if img_src:
                    image_url = urljoin(base_url, img_src)
            
            # ლინკის ძებნა
            link_url = None
            link_elem = item.select_one('a')
            if link_elem:
                href = link_elem.get('href')
                if href:
                    link_url = urljoin(base_url, href)
            
            if name and price:
                return {
                    'name': name[:100],  # სახელის შეზღუდვა
                    'price': price,
                    'image_url': image_url,
                    'link_url': link_url
                }
        except Exception as e:
            logger.error(f"Error extracting product info: {str(e)}")
        
        return None
    
    async def format_products_message(self, products, website_name="საიტი"):
        """პროდუქციის შეტყობინების ფორმატირება"""
        if not products:
            return "🚫 პროდუქცია არ მოიძებნა."
        
        message = f"🛍️ *{website_name}*-ის პროდუქცია:\n\n"
        
        for i, product in enumerate(products[:5], 1):  # მაქსიმუმ 5 პროდუქტი
            message += f"{i}. *{product['name']}*\n"
            message += f"💰 ფასი: `{product['price']}` ₾\n"
            if product.get('link_url'):
                message += f"🔗 [მეტის ნახვა]({product['link_url']})\n"
            message += "\n"
        
        return message

# ბოტის კომანდები
class TelegramBot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.product_bot = ProductBot(bot_token)
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start კომანდა"""
        keyboard = [
            [InlineKeyboardButton("🛍️ პროდუქციის ძებნა", callback_data='search_products')],
            [InlineKeyboardButton("ℹ️ დახმარება", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = (
            "🤖 *მოგესალმებით!*\n\n"
            "ეს ბოტი დაგეხმარებათ საიტებიდან პროდუქციის ინფორმაციის მოძებნაში.\n\n"
            "📝 *გამოყენება:*\n"
            "• გამოგზავნეთ საიტის URL\n"
            "• ან გამოიყენეთ `/search <URL>` კომანდა\n\n"
            "დაწყებისთვის აირჩიეთ ღილაკი:"
        )
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Search კომანდა"""
        if not context.args:
            await update.message.reply_text("❗ გთხოვთ მიუთითოთ საიტის URL\n\nმაგალითი: `/search https://example.com`", parse_mode='Markdown')
            return
        
        url = context.args[0]
        await self.process_website(update, url)
    
    async def handle_url_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """URL შეტყობინების დამუშავება"""
        text = update.message.text
        
        # URL-ის შემოწმება
        url_pattern = r'https?://[^\s]+'
        urls = re.findall(url_pattern, text)
        
        if urls:
            await self.process_website(update, urls[0])
        else:
            await update.message.reply_text("❗ გთხოვთ გამოგზავნოთ ვალიდური URL")
    
    async def process_website(self, update, url):
        """საიტის დამუშავება"""
        # URL-ის ვალიდაცია
        try:
            parsed_url = urlparse(url)
            if not parsed_url.scheme:
                url = 'https://' + url
            elif parsed_url.scheme not in ['http', 'https']:
                await update.message.reply_text("❗ გთხოვთ გამოიყენოთ HTTP ან HTTPS URL")
                return
        except Exception:
            await update.message.reply_text("❗ არასწორი URL ფორმატი")
            return
        
        # ძებნის შეტყობინება
        search_message = await update.message.reply_text("🔍 ვძებნი პროდუქციას...")
        
        try:
            # HTTP სესიის ინიციალიზაცია (თუ არ არის)
            if not self.product_bot.session:
                await self.product_bot.init_session()
            
            # საიტის კონტენტის მოპოვება
            html_content = await self.product_bot.fetch_website_content(url)
            
            if not html_content:
                await search_message.edit_text("❌ საიტის ჩატვირთვა ვერ მოხერხდა")
                return
            
            # პროდუქციის პარსინგი
            products = self.product_bot.parse_products(html_content, url)
            
            # შედეგების ფორმატირება
            website_name = urlparse(url).netloc
            message = await self.product_bot.format_products_message(products, website_name)
            
            await search_message.edit_text(message, parse_mode='Markdown', disable_web_page_preview=True)
            
        except Exception as e:
            logger.error(f"Error processing website: {str(e)}")
            await search_message.edit_text("❌ პროდუქციის ძებნისას მოხდა შეცდომა")
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """ღილაკების callback"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'search_products':
            await query.edit_message_text(
                "📝 გთხოვთ გამოგზავნოთ საიტის URL რომლიდანაც გსურთ პროდუქციის ძებნა:\n\n"
                "მაგალითი: `https://example.com`",
                parse_mode='Markdown'
            )
        elif query.data == 'help':
            help_text = (
                "📖 *დახმარების სექცია*\n\n"
                "🔹 *კომანდები:*\n"
                "• `/start` - ბოტის გაშვება\n"
                "• `/search <URL>` - პროდუქციის ძებნა\n"
                "• `/help` - დახმარება\n\n"
                "🔹 *გამოყენება:*\n"
                "1. გამოგზავნეთ საიტის URL\n"
                "2. ბოტი გადავა საიტზე\n"
                "3. მოიძიებს პროდუქციასა და ფასებს\n"
                "4. გამოგზავნის ინფორმაციას ჩატში\n\n"
                "🔹 *მხარდაჭერილი საიტები:*\n"
                "• ყველა საიტი რომელიც HTML სტანდარტული სტრუქტურის აქვს"
            )
            
            back_keyboard = [[InlineKeyboardButton("🔙 უკან", callback_data='back_to_menu')]]
            back_markup = InlineKeyboardMarkup(back_keyboard)
            
            await query.edit_message_text(help_text, reply_markup=back_markup, parse_mode='Markdown')
        
        elif query.data == 'back_to_menu':
            keyboard = [
                [InlineKeyboardButton("🛍️ პროდუქციის ძებნა", callback_data='search_products')],
                [InlineKeyboardButton("ℹ️ დახმარება", callback_data='help')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            welcome_text = (
                "🤖 *მოგესალმებით!*\n\n"
                "ეს ბოტი დაგეხმარებათ საიტებიდან პროდუქციის ინფორმაციის მოძებნაში.\n\n"
                "დაწყებისთვის აირჩიეთ ღილაკი:"
            )
            
            await query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Help კომანდა"""
        help_text = (
            "📖 *დახმარების სექცია*\n\n"
            "🔹 *კომანდები:*\n"
            "• `/start` - ბოტის გაშვება\n"
            "• `/search <URL>` - პროდუქციის ძებნა\n"
            "• `/help` - დახმარება\n\n"
            "🔹 *გამოყენება:*\n"
            "1. გამოგზავნეთ საიტის URL\n"
            "2. ბოტი გადავა საიტზე\n"
            "3. მოიძიებს პროდუქციასა და ფასებს\n"
            "4. გამოგზავნის ინფორმაციას ჩატში\n\n"
            "📧 *მაგალითები:*\n"
            "• `https://shop.example.com`\n"
            "• `/search https://store.example.com`"
        )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

def main():
    """ბოტის გაშვება"""
    import signal
    import sys
    
    # BOT_TOKEN გარემოს ცვლადიდან
    import os
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN გარემოს ცვლადი არ არის დაყენებული!")
        return
    
    telegram_bot = TelegramBot(BOT_TOKEN)
    
    # Application-ის შექმნა
    application = Application.builder().token(BOT_TOKEN).build()
    
    # კომანდების დამატება
    application.add_handler(CommandHandler("start", telegram_bot.start_command))
    application.add_handler(CommandHandler("search", telegram_bot.search_command))
    application.add_handler(CommandHandler("help", telegram_bot.help_command))
    application.add_handler(CallbackQueryHandler(telegram_bot.button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, telegram_bot.handle_url_message))
    
    # Graceful shutdown handler
    def signal_handler(signum, frame):
        print("🛑 ბოტის გაჩერება...")
        if telegram_bot.product_bot.session:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(telegram_bot.product_bot.close_session())
                else:
                    loop.run_until_complete(telegram_bot.product_bot.close_session())
            except Exception as e:
                logger.error(f"Error closing session: {e}")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # ბოტის გაშვება (სინქრონული მეთოდი)
        print("🤖 ბოტი გაშვებულია...")
        application.run_polling(
            poll_interval=1.0,
            timeout=20,
            bootstrap_retries=-1,
            close_loop=False
        )
    except Exception as e:
        logger.error(f"Application error: {e}")
    finally:
        print("🔄 ბოტი გაჩერდა")

if __name__ == '__main__':
    main()
