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
        """HTTP სესიის ინიციალიზაცია SSL სერტიფიკატებით"""
        # SSL კონტექსტის შექმნა
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.check_hostname = False  # ზოგიერთი საიტისთვის
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        
        # HTTP კონექტორი SSL-ით
        connector = aiohttp.TCPConnector(
            ssl=ssl_context,
            limit=100,
            limit_per_host=10,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=60,
            enable_cleanup_closed=True
        )
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'ka-GE,ka;q=0.9,en;q=0.8,ru;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',  # brotli მხარდაჭერა
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'max-age=0'
            }
        )
    
    async def close_session(self):
        """სესიის დახურვა"""
        if self.session:
            await self.session.close()
    
    async def fetch_website_content(self, url):
        """საიტიდან კონტენტის მოპოვება SSL მხარდაჭერით"""
        try:
            if not self.session:
                await self.init_session()
            
            # URL-ის ნორმალიზაცია
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            
            # Brotli compression მხარდაჭერისთვის
            headers = {
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache'
            }
            
            # პირველ რიგში HTTPS-ის მცდელობა
            if url.startswith('http://'):
                https_url = url.replace('http://', 'https://', 1)
                try:
                    async with self.session.get(https_url, headers=headers) as response:
                        if response.status == 200:
                            content = await response.text(encoding='utf-8', errors='ignore')
                            return content
                except Exception as e:
                    logger.warning(f"HTTPS failed for {https_url}: {e}, trying HTTP...")
            
            # ძირითადი მცდელობა
            try:
                async with self.session.get(url, headers=headers) as response:
                    if response.status == 200:
                        content = await response.text(encoding='utf-8', errors='ignore')
                        return content
                    elif response.status in [301, 302, 303, 307, 308]:
                        # Redirect handling
                        redirect_url = str(response.url)
                        logger.info(f"Redirected to: {redirect_url}")
                        async with self.session.get(redirect_url, headers=headers) as redirect_response:
                            if redirect_response.status == 200:
                                content = await redirect_response.text(encoding='utf-8', errors='ignore')
                                return content
                    else:
                        logger.error(f"HTTP Error {response.status} for URL: {url}")
                        return None
                        
            except aiohttp.ContentTypeError as cte_error:
                logger.warning(f"Content-Type error for {url}, trying without encoding: {cte_error}")
                # Content-Type error-ის შემთხვევაში encoding-ის გარეშე
                async with self.session.get(url, headers={'Accept-Encoding': 'identity'}) as response:
                    if response.status == 200:
                        content = await response.read()
                        return content.decode('utf-8', errors='ignore')
                    
        except aiohttp.ClientSSLError as ssl_error:
            logger.error(f"SSL Error for {url}: {ssl_error}")
            # SSL შეცდომის შემთხვევაში HTTP-ის მცდელობა
            if url.startswith('https://'):
                http_url = url.replace('https://', 'http://', 1)
                try:
                    async with self.session.get(http_url, headers={'Accept-Encoding': 'gzip, deflate'}) as response:
                        if response.status == 200:
                            content = await response.text(encoding='utf-8', errors='ignore')
                            logger.warning(f"Fallback to HTTP successful for {http_url}")
                            return content
                except Exception as e:
                    logger.error(f"HTTP fallback also failed: {e}")
            return None
            
        except aiohttp.ClientConnectorError as conn_error:
            logger.error(f"Connection Error for {url}: {conn_error}")
            return None
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout Error for URL: {url}")
            return None
            
        except Exception as e:
            logger.error(f"Unexpected error fetching {url}: {str(e)}")
            # ბოლო მცდელობა - ყველაზე მარტივი მიდგომა
            try:
                simple_headers = {'Accept-Encoding': 'identity', 'User-Agent': 'TelegramBot/1.0'}
                async with self.session.get(url, headers=simple_headers) as response:
                    if response.status == 200:
                        content = await response.read()
                        return content.decode('utf-8', errors='ignore')
            except Exception as final_error:
                logger.error(f"Final fallback failed for {url}: {final_error}")
            return None
    
    def parse_products(self, html_content, base_url):
        """HTML-ის პარსინგი პროდუქციის მოსაპოვებლად"""
        soup = BeautifulSoup(html_content, 'html.parser')
        products = []
        
        # raiders.ge-სთვის სპეციალური სელექტორები
        if 'raiders.ge' in base_url:
            product_selectors = [
                '.product-item',
                '.product-card',
                '.item-product',
                '.product-container',
                '.card',
                '[data-product-id]',
                '.product-list-item'
            ]
        else:
            # ზოგადი სელექტორები
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
            if items and len(items) > 1:  # მინიმუმ 2 პროდუქტი უნდა მოიძებნოს
                for item in items[:10]:  # მაქსიმუმ 10 პროდუქტი
                    product = self.extract_product_info(item, base_url)
                    if product:
                        products.append(product)
                if products:  # თუ პროდუქტები მოიძებნა, შევწყვიტოთ
                    break
        
        # თუ პროდუქტები ვერ მოიძებნა, ვეცადოთ ყველაზე ფართო ძებნა
        if not products:
            fallback_selectors = [
                'div[class*="item"]',
                'div[class*="card"]', 
                'div[class*="product"]',
                'article',
                'li[class*="item"]'
            ]
            
            for selector in fallback_selectors:
                items = soup.select(selector)
                if items:
                    for item in items[:15]:
                        product = self.extract_product_info(item, base_url)
                        if product:
                            products.append(product)
                    if products:
                        break
        
        return products
    
    def extract_product_info(self, item, base_url):
        """ცალკეული პროდუქტის ინფორმაციის ამოღება"""
        try:
            # სახელის ძებნა (raiders.ge-ისთვის გაფართოებული)
            name_selectors = [
                'h1', 'h2', 'h3', 'h4',
                '.title', '.name', '.product-name', '.product-title',
                '.item-title', '.card-title',
                'a[title]',  # ლინკის title ატრიბუტი
                '.product-info h3', '.product-info h2'
            ]
            name = None
            for selector in name_selectors:
                name_elem = item.select_one(selector)
                if name_elem:
                    name_text = name_elem.get_text(strip=True)
                    if len(name_text) > 5:  # მინიმუმ 5 სიმბოლო
                        name = name_text
                        break
                
                # title ატრიბუტის შემოწმება
                if not name and selector == 'a[title]':
                    title_attr = name_elem.get('title', '').strip()
                    if len(title_attr) > 5:
                        name = title_attr
                        break
            
            # ფასის ძებნა (georgia-ური ვალუტისთვის გაფართოებული)
            price_selectors = [
                '.price', '.cost', 
                '[class*="price"]', '[class*="cost"]',
                '.amount', '.value',
                '.product-price', '.item-price'
            ]
            price = None
            for selector in price_selectors:
                price_elem = item.select_one(selector)
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    # ფასის ამოღება რეგექსით - ლარი, დოლარი, ევრო
                    price_patterns = [
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:₾|ლარი|GEL)',
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:\$|USD)',
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:€|EUR)',
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)'  # მხოლოდ რიცხვი
                    ]
                    
                    for pattern in price_patterns:
                        price_match = re.search(pattern, price_text.replace(' ', ''))
                        if price_match:
                            price = price_match.group(1).replace(',', '')
                            # ლარის სიმბოლო დავამატოთ თუ არ არის
                            if not any(symbol in price_text for symbol in ['₾', 'ლარი']):
                                price = f"{price}₾"
                            break
                    if price:
                        break
            
            # სურათის ძებნა (გაუმჯობესებული)
            image_url = None
            
            # პირველი - img ელემენტის ძებნა
            img_selectors = [
                'img',
                '.product-image img',
                '.image img', 
                '.photo img',
                '.thumbnail img',
                '.item-image img',
                '.card-img img'
            ]
            
            for selector in img_selectors:
                img_elem = item.select_one(selector)
                if img_elem:
                    # სხვადასხვა src ატრიბუტების შემოწმება
                    img_src = (img_elem.get('src') or 
                              img_elem.get('data-src') or 
                              img_elem.get('data-lazy-src') or
                              img_elem.get('data-original') or
                              img_elem.get('data-srcset') or
                              img_elem.get('srcset'))
                    
                    if img_src:
                        # srcset-ის შემთხვევაში პირველი URL-ის აღება
                        if ',' in img_src:
                            img_src = img_src.split(',')[0].split(' ')[0]
                        
                        # სრული URL-ის შექმნა
                        if img_src.startswith('//'):
                            img_src = 'https:' + img_src
                        elif img_src.startswith('/'):
                            img_src = urljoin(base_url, img_src)
                        elif not img_src.startswith('http'):
                            img_src = urljoin(base_url, img_src)
                        
                        image_url = img_src
                        
                        # სურათის ვალიდაცია
                        if self.is_valid_image_url(image_url):
                            break
            
            # თუ img ელემენტი ვერ მოიძებნა, background-image-ის ძებნა
            if not image_url:
                bg_selectors = ['.product-image', '.image', '.photo', '.thumbnail', '.item-image']
                for selector in bg_selectors:
                    bg_elem = item.select_one(selector)
                    if bg_elem:
                        style = bg_elem.get('style', '')
                        bg_match = re.search(r'background-image:\s*url\(["\']?(.*?)["\']?\)', style)
                        if bg_match:
                            bg_url = bg_match.group(1)
                            if bg_url.startswith('//'):
                                bg_url = 'https:' + bg_url
                            image_url = urljoin(base_url, bg_url)
                            if self.is_valid_image_url(image_url):
                                break
            
            # ლინკის ძებნა
            link_url = None
            link_elem = item.select_one('a')
            if link_elem:
                href = link_elem.get('href')
                if href:
                    if href.startswith('//'):
                        href = 'https:' + href
                    elif href.startswith('/'):
                        href = urljoin(base_url, href)
                    elif not href.startswith('http'):
                        href = urljoin(base_url, href)
                    link_url = href
            
            # შედეგის დაბრუნება
            if name and price and len(name) > 3:
                return {
                    'name': name[:150],  # სახელის შეზღუდვა
                    'price': price,
                    'image_url': image_url,
                    'link_url': link_url
                }
                
        except Exception as e:
            logger.error(f"Error extracting product info: {str(e)}")
        
        return None
    
    def is_valid_image_url(self, url):
        """სურათის URL-ის ვალიდაცია"""
        if not url or len(url) < 10:
            return False
        
        # ფაილის ექსტენშენის შემოწმება
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg']
        url_lower = url.lower()
        
        # URL-ში სურათის ექსტენშენია
        if any(url_lower.endswith(ext) for ext in image_extensions):
            return True
        
        # ან სურათის ქვეჯგუფი URL-ში
        image_indicators = ['image', 'img', 'photo', 'picture', 'pic', 'thumb']
        if any(indicator in url_lower for indicator in image_indicators):
            return True
        
        # data: URL (base64 სურათები)
        if url.startswith('data:image'):
            return True
            
        return False
    
    async def send_products_with_images(self, update, products, website_name="საიტი"):
        """პროდუქციის გაგზავნა სურათებით"""
        if not products:
            await update.message.reply_text("🚫 პროდუქცია არ მოიძებნა.")
            return
        
        header_message = f"🛍️ *{website_name}*-ის პროდუქცია:\n\n"
        
        # თუ 3-ზე მეტი პროდუქტია, გავაგზავნოთ Media Group
        limited_products = products[:6]  # მაქსიმუმ 6 პროდუქტი
        
        for i, product in enumerate(limited_products, 1):
            try:
                caption = f"*{i}. {product['name']}*\n\n"
                caption += f"💰 *ფასი:* `{product['price']}`\n"
                
                if product.get('link_url'):
                    caption += f"🔗 [მეტის ნახვა]({product['link_url']})\n"
                
                caption += f"\n📊 *საიტი:* {website_name}"
                
                # სურათის გაგზავნა
                if product.get('image_url') and self.is_valid_image_url(product['image_url']):
                    try:
                        await update.message.reply_photo(
                            photo=product['image_url'],
                            caption=caption,
                            parse_mode='Markdown'
                        )
                    except Exception as img_error:
                        logger.warning(f"Failed to send image for {product['name']}: {img_error}")
                        # სურათის გარეშე გაგზავნა
                        await update.message.reply_text(
                            f"📦 {caption}\n\n❌ სურათი ვერ ჩაიტვირთა",
                            parse_mode='Markdown',
                            disable_web_page_preview=True
                        )
                else:
                    # სურათის გარეშე
                    await update.message.reply_text(
                        f"📦 {caption}",
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    
                # ძალიან სწრაფი გაგზავნის თავიდან ასაცილებლად
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error sending product {i}: {str(e)}")
                continue
    
    async def check_ssl_certificate(self, url):
        """SSL სერტიფიკატის შემოწმება"""
        try:
            parsed_url = urlparse(url)
            if parsed_url.scheme != 'https':
                return True  # HTTP საიტებისთვის SSL შემოწმება არ ჭირდება
            
            import socket
            import ssl as ssl_module
            
            context = ssl_module.create_default_context()
            
            with socket.create_connection((parsed_url.hostname, 443), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=parsed_url.hostname) as ssock:
                    cert = ssock.getpeercert()
                    if cert:
                        logger.info(f"SSL Certificate valid for {parsed_url.hostname}")
                        return True
            return False
            
        except Exception as e:
            logger.warning(f"SSL check failed for {url}: {e}")
            return False  # SSL შემოწმება ვერ მოხერხდა, მაგრამ გავაგრძელოთ


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
            
            # SSL შემოწმება (HTTPS საიტებისთვის)
            ssl_status = "🔐" if url.startswith('https://') else "🔓"
            if url.startswith('https://'):
                await search_message.edit_text(f"🔍 ვძებნი პროდუქციას... {ssl_status} SSL შემოწმება")
                ssl_valid = await self.product_bot.check_ssl_certificate(url)
                ssl_status = "✅🔐" if ssl_valid else "⚠️🔐"
            
            await search_message.edit_text(f"🔍 ვძებნი პროდუქციას... {ssl_status}")
            
            # საიტის კონტენტის მოპოვება
            html_content = await self.product_bot.fetch_website_content(url)
            
            if not html_content:
                await search_message.edit_text("❌ საიტის ჩატვირთვა ვერ მოხერხდა")
                return
            
            await search_message.edit_text(f"🔍 ვანალიზებ პროდუქციას... {ssl_status}")
            
            # პროდუქციის პარსინგი
            products = self.product_bot.parse_products(html_content, url)
            
            # ძებნის შეტყობინების წაშლა
            await search_message.delete()
            
            # შედეგების ფორმატირება და გაგზავნა სურათებით
            website_name = f"{ssl_status} {urlparse(url).netloc}"
            await self.product_bot.send_products_with_images(update, products, website_name)
            
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
                "🤖 *მოგესალმებით!*\n\
