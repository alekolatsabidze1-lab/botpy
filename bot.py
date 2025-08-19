import asyncio
import aiohttp
import ssl
import certifi
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from bs4 import BeautifulSoup
import re
import json
from urllib.parse import urljoin, urlparse
import os
import signal
import sys
from threading import Thread
import gc
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

# ლოგინგის კონფიგურაცია - ნაკლები verbose
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Simple HTTP Server for Render.com port binding
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {"status": "Bot is running!", "service": "Product Search Bot"}
            self.wfile.write(json.dumps(response).encode())
        elif self.path == '/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {"bot_status": "active", "version": "1.0"}
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress server logs
        pass

class ProductBot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.session = None
        self.insecure_session = None
        
    async def init_session(self):
        """HTTP სესიების ინიციალიზაცია SSL და non-SSL-ისთვის"""
        
        # SSL კონტექსტის შექმნა უსაფრთხო კავშირებისთვის
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        
        # არაუსაფრთხო SSL კონტექსტი (self-signed ან expired certificates)
        insecure_ssl_context = ssl.create_default_context()
        insecure_ssl_context.check_hostname = False
        insecure_ssl_context.verify_mode = ssl.CERT_NONE
        
        # Secure connector
        secure_connector = aiohttp.TCPConnector(
            ssl=ssl_context,
            limit=50,
            limit_per_host=5,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=30,
            enable_cleanup_closed=True
        )
        
        # Insecure connector (for sites with SSL issues)
        insecure_connector = aiohttp.TCPConnector(
            ssl=insecure_ssl_context,
            limit=50,
            limit_per_host=5,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=30,
            enable_cleanup_closed=True
        )
        
        # Standard headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ka-GE,ka;q=0.9,en;q=0.8,ru;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        }
        
        # Secure session
        self.session = aiohttp.ClientSession(
            connector=secure_connector,
            timeout=aiohttp.ClientTimeout(total=20, connect=5),
            headers=headers
        )
        
        # Insecure session
        self.insecure_session = aiohttp.ClientSession(
            connector=insecure_connector,
            timeout=aiohttp.ClientTimeout(total=20, connect=5),
            headers=headers
        )
    
    async def close_session(self):
        """სესიების დახურვა"""
        if self.session and not self.session.closed:
            await self.session.close()
        
        if self.insecure_session and not self.insecure_session.closed:
            await self.insecure_session.close()
            
        # Memory cleanup
        gc.collect()
    
    async def fetch_website_content(self, url):
        """საიტიდან კონტენტის მოპოვება ყველა შემთხვევისთვის"""
        try:
            if not self.session:
                await self.init_session()
            
            # URL-ის ნორმალიზაცია
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            
            # მცდელობა 1: HTTPS secure სესიით
            if url.startswith('https://'):
                content = await self._try_fetch_with_session(url, self.session, "HTTPS (secure)")
                if content:
                    return content
            
            # მცდელობა 2: HTTPS insecure სესიით (self-signed certificates)
            if url.startswith('https://'):
                content = await self._try_fetch_with_session(url, self.insecure_session, "HTTPS (insecure)")
                if content:
                    return content
            
            # მცდელობა 3: HTTP fallback
            http_url = url.replace('https://', 'http://', 1)
            content = await self._try_fetch_with_session(http_url, self.session, "HTTP fallback")
            if content:
                return content
                
            # მცდელობა 4: HTTP with different headers
            simplified_headers = {
                'User-Agent': 'Mozilla/5.0 (compatible)',
                'Accept': 'text/html,*/*',
                'Accept-Encoding': 'gzip, deflate'
            }
            
            content = await self._try_fetch_with_session(
                http_url, 
                self.session, 
                "HTTP simplified",
                override_headers=simplified_headers
            )
            if content:
                return content
                
            logger.error(f"All attempts failed for URL: {url}")
            return None
                        
        except Exception as e:
            logger.error(f"Unexpected error in fetch_website_content: {str(e)}")
            return None
    
    async def _try_fetch_with_session(self, url, session, method_name, override_headers=None):
        """კონკრეტული სესიით URL-ის ჩატვირთვის მცდელობა"""
        try:
            headers = override_headers if override_headers else {}
            
            logger.info(f"Trying {method_name} for: {url}")
            
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    content = await response.text(encoding='utf-8', errors='ignore')
                    logger.info(f"Success with {method_name} for: {url}")
                    return content
                elif response.status in [301, 302, 303, 307, 308]:
                    redirect_url = str(response.url)
                    logger.info(f"Redirected from {url} to: {redirect_url} via {method_name}")
                    
                    # Follow redirect with same session
                    async with session.get(redirect_url, headers=headers) as redirect_response:
                        if redirect_response.status == 200:
                            content = await redirect_response.text(encoding='utf-8', errors='ignore')
                            logger.info(f"Success after redirect with {method_name}")
                            return content
                else:
                    logger.warning(f"{method_name} returned status {response.status} for: {url}")
                    return None
                        
        except aiohttp.ClientSSLError as ssl_error:
            logger.warning(f"SSL Error with {method_name} for {url}: {ssl_error}")
            return None
            
        except aiohttp.ClientConnectorError as conn_error:
            logger.warning(f"Connection Error with {method_name} for {url}: {conn_error}")
            return None
            
        except asyncio.TimeoutError:
            logger.warning(f"Timeout with {method_name} for URL: {url}")
            return None
            
        except Exception as e:
            logger.warning(f"Error with {method_name} for {url}: {str(e)}")
            return None
    
    def parse_products(self, html_content, base_url):
        """HTML-ის პარსინგი პროდუქციის მოსაპოვებლად"""
        soup = BeautifulSoup(html_content, 'html.parser')
        products = []
        
        # სხვადასხვა სელექტორები
        product_selectors = [
            '.product-item', '.product-card', '.item-product', '.product-container',
            '.card', '[data-product-id]', '.product-list-item',
            '.product', '.item', '[class*="product"]', '.item-card'
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
        
        # თუ პროდუქტები ვერ მოიძებნა, ვეცადოთ ფართო ძებნით
        if not products:
            fallback_selectors = [
                'div[class*="item"]', 'div[class*="card"]', 
                'div[class*="product"]', 'article', 'li[class*="item"]'
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
            # სახელის ძებნა
            name_selectors = [
                'h1', 'h2', 'h3', 'h4',
                '.title', '.name', '.product-name', '.product-title',
                '.item-title', '.card-title',
                'a[title]',
                '.product-info h3', '.product-info h2'
            ]
            
            name = None
            for selector in name_selectors:
                name_elem = item.select_one(selector)
                if name_elem:
                    name_text = name_elem.get_text(strip=True)
                    if len(name_text) > 5:
                        name = name_text
                        break
                
                if not name and selector == 'a[title]':
                    title_attr = name_elem.get('title', '').strip() if name_elem else ''
                    if len(title_attr) > 5:
                        name = title_attr
                        break
            
            # ფასის ძებნა
            price_selectors = [
                '.price', '.cost', '[class*="price"]', '[class*="cost"]',
                '.amount', '.value', '.product-price', '.item-price'
            ]
            
            price = None
            for selector in price_selectors:
                price_elem = item.select_one(selector)
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    price_patterns = [
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:₾|ლარი|GEL)',
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:\$|USD)',
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:€|EUR)',
                        r'(\d+(?:,\d{3})*(?:\.\d{2})?)'
                    ]
                    
                    for pattern in price_patterns:
                        price_match = re.search(pattern, price_text.replace(' ', ''))
                        if price_match:
                            price = price_match.group(1).replace(',', '')
                            if not any(symbol in price_text for symbol in ['₾', 'ლარი', 'GEL']):
                                price = f"{price}₾"
                            break
                    if price:
                        break
            
            # სურათის ძებნა
            image_url = None
            img_selectors = [
                'img', '.product-image img', '.image img', 
                '.photo img', '.thumbnail img', '.item-image img', '.card-img img'
            ]
            
            for selector in img_selectors:
                img_elem = item.select_one(selector)
                if img_elem:
                    img_src = (img_elem.get('src') or 
                              img_elem.get('data-src') or 
                              img_elem.get('data-lazy-src') or
                              img_elem.get('data-original') or
                              img_elem.get('data-srcset') or
                              img_elem.get('srcset'))
                    
                    if img_src:
                        if ',' in img_src:
                            img_src = img_src.split(',')[0].split(' ')[0]
                        
                        if img_src.startswith('//'):
                            img_src = 'https:' + img_src
                        elif img_src.startswith('/'):
                            img_src = urljoin(base_url, img_src)
                        elif not img_src.startswith('http'):
                            img_src = urljoin(base_url, img_src)
                        
                        image_url = img_src
                        if self.is_valid_image_url(image_url):
                            break
            
            # background-image ძებნა თუ img ვერ მოიძებნა
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
                    'name': name[:150],
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
        
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg']
        url_lower = url.lower()
        
        if any(url_lower.endswith(ext) for ext in image_extensions):
            return True
        
        image_indicators = ['image', 'img', 'photo', 'picture', 'pic', 'thumb']
        if any(indicator in url_lower for indicator in image_indicators):
            return True
        
        if url.startswith('data:image'):
            return True
            
        return False

    async def send_products_with_images(self, update, products, website_name="საიტი"):
        """პროდუქციის გაგზავნა სურათებით"""
        if not products:
            await update.message.reply_text("🚫 პროდუქცია არ მოიძებნა.")
            return
        
        limited_products = products[:6]
        
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
                        await update.message.reply_text(
                            f"📦 {caption}\n\n❌ სურათი ვერ ჩაიტვირთა",
                            parse_mode='Markdown',
                            disable_web_page_preview=True
                        )
                else:
                    await update.message.reply_text(
                        f"📦 {caption}",
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    
                await asyncio.sleep(0.3)  # Rate limiting
                
            except Exception as e:
                logger.error(f"Error sending product {i}: {str(e)}")
                continue

    async def get_ssl_status_display(self, url):
        """SSL სტატუსის ვიზუალური ინდიკატორი"""
        try:
            parsed_url = urlparse(url)
            
            if parsed_url.scheme == 'http':
                return "🔓 HTTP"
            
            if parsed_url.scheme == 'https':
                # ვცადოთ secure connection
                try:
                    import socket
                    import ssl as ssl_module
                    
                    context = ssl_module.create_default_context()
                    with socket.create_connection((parsed_url.hostname, 443), timeout=5) as sock:
                        with context.wrap_socket(sock, server_hostname=parsed_url.hostname) as ssock:
                            cert = ssock.getpeercert()
                            if cert:
                                return "🔒 HTTPS (valid SSL)"
                    return "⚠️ HTTPS (SSL issues)"
                    
                except Exception:
                    return "⚠️ HTTPS (SSL issues)"
            
            return "❓ Unknown"
            
        except Exception:
            return "❓ Unknown"

class TelegramBot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.product_bot = ProductBot(bot_token)
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start კომანდა"""
        keyboard = [
            [InlineKeyboardButton("🛒 პროდუქციის ძებნა", callback_data='search_products')],
            [InlineKeyboardButton("ℹ️ დახმარება", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = (
            "🤖 *მოგესალმებით!*\n\n"
            "ეს ბოტი დაგეხმარებათ საიტებიდან პროდუქციის ინფორმაციის მოძებნაში.\n\n"
            "📍 *გამოყენება:*\n"
            "• გამოაგზავნეთ საიტის URL\n"
            "• ან გამოიყენეთ `/search <URL>` კომანდა\n\n"
            "🔧 *მხარდაჭერა:*\n"
            "• HTTP/HTTPS საიტები\n"
            "• SSL სერთიფიკატის გარეშე საიტები\n"
            "• Self-signed certificates\n\n"
            "🚀 *Render.com-ზე მუშაობს*\n\n"
            "დაწყებისთვის აირჩიეთ ღილაკი:"
        )
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Search კომანდა"""
        if not context.args:
            await update.message.reply_text(
                "❗ გთხოვთ მიუთითოთ საიტის URL\n\nმაგალითი: `/search https://example.com`", 
                parse_mode='Markdown'
            )
            return
        
        url = context.args[0]
        await self.process_website(update, url)
    
    async def handle_url_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """URL შეტყობინების დამუშავება"""
        text = update.message.text
        
        url_pattern = r'https?://[^\s]+'
        urls = re.findall(url_pattern, text)
        
        if urls:
            await self.process_website(update, urls[0])
        else:
            # ვეცადოთ URL-ის ამოცნობა www. ან domain.com ფორმატით
            domain_pattern = r'(?:www\.)?[a-zA-Z0-9][a-zA-Z0-9-]{1,61}[a-zA-Z0-9]\.[a-zA-Z]{2,}'
            domains = re.findall(domain_pattern, text)
            
            if domains:
                url = 'https://' + domains[0]
                await self.process_website(update, url)
            else:
                await update.message.reply_text("❗ გთხოვთ გამოაგზავნოთ ვალიდური URL ან domain")
    
    async def process_website(self, update, url):
        """საიტის დამუშავება"""
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
        
        search_message = await update.message.reply_text("🔍 ვძებნი პროდუქციას...")
        
        try:
            if not self.product_bot.session:
                await self.product_bot.init_session()
            
            # SSL სტატუსის მიღება
            ssl_status = await self.product_bot.get_ssl_status_display(url)
            
            await search_message.edit_text(f"🔍 ვძებნი პროდუქციას... {ssl_status}")
            
            html_content = await self.product_bot.fetch_website_content(url)
            
            if not html_content:
                await search_message.edit_text(f"❌ საიტის ჩატვირთვა ვერ მოხერხდა\n{ssl_status}")
                return
            
            await search_message.edit_text(f"🔍 ვანალიზებ პროდუქციას... {ssl_status}")
            
            products = self.product_bot.parse_products(html_content, url)
            
            await search_message.delete()
            
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
                "🔍 გთხოვთ გამოაგზავნოთ საიტის URL რომლიდანაც გსურთ პროდუქციის ძებნა:\n\n"
                "მაგალითად:\n"
                "• `https://example.com`\n"
                "• `http://shop.example.com`\n"
                "• `example.com` (ავტომატურად დაემატება https://)\n\n"
                "🔧 *მხარდაჭერილი ტიპები:*\n"
                "✅ HTTPS (ვალიდური SSL)\n"
                "✅ HTTPS (არავალიდური SSL)\n"
                "✅ HTTP საიტები\n"
                "✅ Self-signed certificates",
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
                "1. გამოაგზავნეთ საიტის URL\n"
                "2. ბოტი გადავა საიტზე\n"
                "3. მოიძიებს პროდუქციასა და ფასებს\n"
                "4. გამოაგზავნის ინფორმაციას ჩატში\n\n"
                "🔹 *SSL მხარდაჭერა:*\n"
                "• 🔒 - ვალიდური SSL სერთიფიკატი\n"
                "• ⚠️ - SSL შეცდომები (მაგრამ მუშაობს)\n"
                "• 🔓 - HTTP (არაუსაფრთხო)\n\n"
                "🔹 *მხარდაჭერილი საიტების ტიპები:*\n"
                "✅ HTTPS საიტები ვალიდური SSL-ით\n"
                "✅ HTTPS საიტები არავალიდური SSL-ით\n"
                "✅ Self-signed certificates\n"
                "✅ Expired certificates\n"
                "✅ HTTP საიტები\n"
                "✅ ყველა სტანდარტული ecommerce სტრუქტურა\n\n"
                "🚀 *Hosted on Render.com*"
                "დაწყებისთვის აირჩიეთ ღილაკი:"
        )
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Search კომანდა"""
        if not context.args:
            await update.message.reply_text(
                "❗ გთხოვთ მიუთითოთ საიტის URL\n\nმაგალითი: `/search https://example.com`", 
                parse_mode='Markdown'
            )
            return
        
        url = context.args[0]
        await self.process_website(update, url)
    
    async def handle_url_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """URL შეტყობინების დამუშავება"""
        text = update.message.text
        
        url_pattern = r'https?://[^\s]+'
        urls = re.findall(url_pattern, text)
        
        if urls:
            await self.process_website(update, urls[0])
        else:
            # ვეცადოთ URL-ის ამოცნობა www. ან domain.com ფორმატით
            domain_pattern = r'(?:www\.)?[a-zA-Z0-9][a-zA-Z0-9-]{1,61}[a-zA-Z0-9]\.[a-zA-Z]{2,}'
            domains = re.findall(domain_pattern, text)
            
            if domains:
                url = 'https://' + domains[0]
                await self.process_website(update, url)
            else:
                await update.message.reply_text("❗ გთხოვთ გამოაგზავნოთ ვალიდური URL ან domain")
    
    async def process_website(self, update, url):
        """საიტის დამუშავება"""
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
        
        search_message = await update.message.reply_text("🔍 ვძებნი პროდუქციას...")
        
        try:
            if not self.product_bot.session:
                await self.product_bot.init_session()
            
            # SSL სტატუსის მიღება
            ssl_status = await self.product_bot.get_ssl_status_display(url)
            
            await search_message.edit_text(f"🔍 ვძებნი პროდუქციას... {ssl_status}")
            
            html_content = await self.product_bot.fetch_website_content(url)
            
            if not html_content:
                await search_message.edit_text(f"❌ საიტის ჩატვირთვა ვერ მოხერხდა\n{ssl_status}")
                return
            
            await search_message.edit_text(f"🔍 ვანალიზებ პროდუქციას... {ssl_status}")
            
            products = self.product_bot.parse_products(html_content, url)
            
            await search_message.delete()
            
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
                "🔍 გთხოვთ გამოაგზავნოთ საიტის URL რომლიდანაც გსურთ პროდუქციის ძებნა:\n\n"
                "მაგალითად:\n"
                "• `https://example.com`\n"
                "• `http://shop.example.com`\n"
                "• `example.com` (ავტომატურად დაემატება https://)\n\n"
                "🔧 *მხარდაჭერილი ტიპები:*\n"
                "✅ HTTPS (ვალიდური SSL)\n"
                "✅ HTTPS (არავალიდური SSL)\n"
                "✅ HTTP საიტები\n"
                "✅ Self-signed certificates",
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
                "1. გამოაგზავნეთ საიტის URL\n"
                "2. ბოტი გადავა საიტზე\n"
                "3. მოიძიებს პროდუქციასა და ფასებს\n"
                "4. გამოაგზავნის ინფორმაციას ჩატში\n\n"
                "🔹 *SSL მხარდაჭერა:*\n"
                "• 🔒 - ვალიდური SSL სერთიფიკატი\n"
                "• ⚠️ - SSL შეცდომები (მაგრამ მუშაობს)\n"
                "• 🔓 - HTTP (არაუსაფრთხო)\n\n"
                "🔹 *მხარდაჭერილი საიტების ტიპები:*\n"
                "✅ HTTPS საიტები ვალიდური SSL-ით\n"
                "✅ HTTPS საიტები არავალიდური SSL-ით\n"
                "✅ Self-signed certificates\n"
                "✅ Expired certificates\n"
                "✅ HTTP საიტები\n"
                "✅ ყველა სტანდარტული ecommerce სტრუქტურა\n\n"
                "🚀 *Hosted on Render.com*"
            )
            
            back_keyboard = [[InlineKeyboardButton("🔙 უკან", callback_data='back_to_menu')]]
            back_markup = InlineKeyboardMarkup(back_keyboard)
            
            await query.edit_message_text(help_text, reply_markup=back_markup, parse_mode='Markdown')
        
        elif query.data == 'back_to_menu':
            keyboard = [
                [InlineKeyboardButton("🛒 პროდუქციის ძებნა", callback_data='search_products')],
                [InlineKeyboardButton("ℹ️ დახმარება", callback_data='help')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            welcome_text = (
                "🤖 *მოგესალმებით!*\n\n"
                "ეს ბოტი დაგეხმარებათ საიტებიდან პროდუქციის ინფორმაციის მოძებნაში.\n\n"
                "🔧 *Render.com-ზე მუშაობს*\n\n"
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
            "1. გამოაგზავნეთ საიტის URL\n"
            "2. ბოტი გადავა საიტზე\n"
            "3. მოიძიებს პროდუქციასა და ფასებს\n"
            "4. გამოაგზავნის ინფორმაციას ჩატში\n\n"
            "🔧 *მაგალითები:*\n"
            "• `https://shop.example.com`\n"
            "• `/search https://store.example.com`\n\n"
            "🚀 *Hosted on Render.com*"
        )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def cleanup(self):
        """რესურსების გაწმენდა"""
        if self.product_bot:
            await self.product_bot.close_session()

# Bot setup function
async def setup_bot(bot_token):
    """Setup and run the bot"""
    try:
        # Better request configuration for Render.com
        request = HTTPXRequest(
            connection_pool_size=4,
            read_timeout=20,
            write_timeout=20,
            connect_timeout=10,
            pool_timeout=20
        )
        
        # Clear any existing webhooks first
        application = Application.builder().token(bot_token).request(request).build()
        await application.bot.delete_webhook(drop_pending_updates=True)
        
        telegram_bot = TelegramBot(bot_token)
        
        # Add handlers
        application.add_handler(CommandHandler("start", telegram_bot.start_command))
        application.add_handler(CommandHandler("search", telegram_bot.search_command))
        application.add_handler(CommandHandler("help", telegram_bot.help_command))
        application.add_handler(CallbackQueryHandler(telegram_bot.button_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, telegram_bot.handle_url_message))
        
        # Error handler
        async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
            logger.error(f'Update {update} caused error {context.error}')
        
        application.add_error_handler(error_handler)
        
        # Start polling with better configuration
        await application.initialize()
        await application.start()
        await application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            poll_interval=1.0,
            timeout=10
        )
        
        logger.warning("🤖 Bot started successfully!")
        
        # Keep running
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"Bot setup error: {e}")
        await asyncio.sleep(5)
        # Retry mechanism
        await setup_bot(bot_token)

def run_bot(bot_token):
    """Run bot in asyncio loop"""
    asyncio.run(setup_bot(bot_token))

def run_http_server():
    """Run simple HTTP server for Render.com port binding"""
    port = int(os.environ.get('PORT', 5000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.warning(f"🌐 Starting HTTP server on port {port}")
    server.serve_forever()

def main():
    """ბოტის გაშვება Render.com-ისთვის ოპტიმიზებული"""
    # BOT_TOKEN გარემოს ცვლადიდან
    BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN ან TELEGRAM_TOKEN გარემოს ცვლადი არ არის დაყენებული!")
        sys.exit(1)
    
    print("🚀 Starting bot on Render.com...")
    
    # Start HTTP server in separate thread for Render.com port detection
    server_thread = Thread(target=run_http_server, daemon=True)
    server_thread.start()
    
    # Give server time to start
    import time
    time.sleep(2)
    
    # Graceful shutdown handler
    def signal_handler(signum, frame):
        print("🛑 ბოტის გაჩერება...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start bot
        run_bot(BOT_TOKEN)
    except KeyboardInterrupt:
        print("📴 ბოტი გაჩერდა მომხმარებლის მიერ")
    except Exception as e:
        logger.error(f"Application error: {e}")
        print(f"❌ ბოტის შეცდომა: {e}")
    finally:
        print("📴 ბოტი დასრულდა")

if __name__ == '__main__':
    main()


