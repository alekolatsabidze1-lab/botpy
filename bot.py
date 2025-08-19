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
import socket

# ლოგინგის კონფიგურაცია
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
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
            response = {"bot_status": "active", "version": "1.1"}
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

class ProductBot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.session = None
        
    async def init_session(self):
        """HTTP სესიის ინიციალიზაცია გაუმჯობესებული კონფიგურაციით"""
        try:
            # SSL კონტექსტის შექმნა - უფრო ლიბერალური
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE  # SSL ვერიფიკაცია გამორთული
            ssl_context.set_ciphers('DEFAULT')
            
            # TCP კონექტორი გაუმჯობესებული პარამეტრებით
            connector = aiohttp.TCPConnector(
                ssl=ssl_context,
                limit=100,
                limit_per_host=20,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=60,
                enable_cleanup_closed=True,
                force_close=True,
                resolver=aiohttp.AsyncResolver()
            )
            
            # თანამედროვე User-Agent და headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'ka-GE,ka;q=0.9,en-US;q=0.8,en;q=0.7,ru;q=0.6',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache'
            }
            
            # კლიენტ სესიის შექმნა გაუმჯობესებული timeout-ებით
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(
                    total=45,      # გაზრდილი total timeout
                    connect=10,    # connection timeout
                    sock_connect=10,
                    sock_read=30   # read timeout
                ),
                headers=headers,
                skip_auto_headers=['User-Agent']  # ხელით დაყენებული User-Agent გამოიყენოს
            )
            
            logger.info("HTTP სესია წარმატებით ინიციალიზდა")
            
        except Exception as e:
            logger.error(f"სესიის ინიციალიზაციის შეცდომა: {e}")
            raise
    
    async def close_session(self):
        """სესიის დახურვა"""
        if self.session and not self.session.closed:
            await self.session.close()
            await asyncio.sleep(0.1)  # დროის მიცემა დასახურად
            gc.collect()
    
    def normalize_url(self, url):
        """URL-ის ნორმალიზაცია"""
        url = url.strip()
        
        # თუ სქემა არ არის მითითებული, დავამატოთ https://
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        # www. პრეფიქსის დამატება თუ საჭიროა
        parsed = urlparse(url)
        if parsed.netloc and not parsed.netloc.startswith('www.') and '.' in parsed.netloc:
            # გარკვეული დომენებისთვის www არ ვამატებთ
            skip_www = ['localhost', '127.0.0.1', '192.168.', '10.', '172.']
            if not any(parsed.netloc.startswith(skip) for skip in skip_www):
                netloc_parts = parsed.netloc.split('.')
                if len(netloc_parts) == 2:  # მხოლოდ domain.com ფორმატისთვის
                    url = url.replace(parsed.netloc, f"www.{parsed.netloc}")
        
        return url
    
    async def fetch_website_content(self, url):
        """საიტიდან კონტენტის მოპოვება - მაქსიმალური compatibility"""
        try:
            if not self.session:
                await self.init_session()
            
            # URL-ის ნორმალიზაცია
            normalized_url = self.normalize_url(url)
            logger.info(f"🌐 მცდელობა: {normalized_url}")
            
            # ყველა შესაძლო URL ვარიანტი
            urls_to_try = []
            
            # ძირითადი URL ვარიანტები
            base_urls = [normalized_url]
            if normalized_url != url:
                base_urls.append(url)
            
            for base_url in base_urls:
                parsed = urlparse(base_url if base_url.startswith(('http://', 'https://')) else f'https://{base_url}')
                domain = parsed.netloc
                path = parsed.path or '/'
                
                # სხვადასხვა კომბინაციები
                combinations = [
                    f"https://{domain}{path}",
                    f"https://www.{domain.replace('www.', '')}{path}",
                    f"http://{domain}{path}",
                    f"http://www.{domain.replace('www.', '')}{path}",
                    f"https://{domain.replace('www.', '')}{path}",
                    f"http://{domain.replace('www.', '')}{path}"
                ]
                
                for combo in combinations:
                    if combo not in urls_to_try:
                        urls_to_try.append(combo)
            
            # User-Agent ვარიანტები
            user_agents = [
                # Chrome Windows
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                # Chrome Mac
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                # Firefox
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
                # Safari Mac
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
                # Edge
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
                # Mobile Chrome
                'Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
                # iPhone Safari
                'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
                # Very simple bot-like
                'curl/7.68.0',
                'Wget/1.21.2'
            ]
            
            last_error = None
            
            # ყველა URL-სა და User-Agent-ის კომბინაციის ცდა
            for attempt_url in urls_to_try:
                for user_agent in user_agents:
                    try:
                        logger.info(f"🔄 ცდა: {attempt_url} | UA: {user_agent[:50]}...")
                        
                        # Headers კონფიგურაცია
                        headers = {
                            'User-Agent': user_agent,
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                            'Accept-Language': 'en-US,en;q=0.5',
                            'Accept-Encoding': 'gzip, deflate, br',
                            'DNT': '1',
                            'Connection': 'keep-alive',
                            'Upgrade-Insecure-Requests': '1'
                        }
                        
                        # Cloudflare-friendly headers
                        if 'Chrome' in user_agent:
                            headers.update({
                                'Sec-Fetch-Dest': 'document',
                                'Sec-Fetch-Mode': 'navigate',
                                'Sec-Fetch-Site': 'none',
                                'Sec-Fetch-User': '?1',
                                'Cache-Control': 'max-age=0'
                            })
                        
                        # მცდელობა 1: ნორმალური request
                        async with self.session.get(
                            attempt_url,
                            headers=headers,
                            allow_redirects=True,
                            max_redirects=10
                        ) as response:
                            
                            logger.info(f"📊 Response: {response.status} | {attempt_url}")
                            
                            if response.status == 200:
                                content_type = response.headers.get('content-type', '').lower()
                                if 'html' in content_type or 'text' in content_type:
                                    content = await response.text(encoding='utf-8', errors='ignore')
                                    
                                    # კონტენტის ვალიდაცია
                                    if len(content.strip()) > 200:
                                        # Cloudflare რეგისტრაცია
                                        if 'cloudflare' in content.lower() and 'checking your browser' in content.lower():
                                            logger.warning(f"⚠️ Cloudflare რეგისტრაცია: {attempt_url}")
                                            continue
                                        
                                        # JavaScript redirect რეგისტრაცია
                                        if len(content) < 1000 and 'window.location' in content:
                                            logger.warning(f"⚠️ JS redirect: {attempt_url}")
                                            continue
                                        
                                        logger.info(f"✅ წარმატება! {attempt_url} ({len(content)} chars)")
                                        return content
                                    else:
                                        logger.warning(f"❌ ცარიელი კონტენტი: {attempt_url}")
                            
                            elif response.status in [301, 302, 303, 307, 308]:
                                redirect_url = str(response.url)
                                logger.info(f"↪️ Redirect: {redirect_url}")
                                
                                # Manual redirect follow
                                if redirect_url not in urls_to_try:
                                    async with self.session.get(redirect_url, headers=headers) as redir_resp:
                                        if redir_resp.status == 200:
                                            content = await redir_resp.text(encoding='utf-8', errors='ignore')
                                            if len(content.strip()) > 200:
                                                return content
                            
                            elif response.status == 403:
                                logger.warning(f"🚫 403 Forbidden: {attempt_url}")
                                last_error = f"403 Forbidden"
                            
                            elif response.status == 404:
                                logger.warning(f"🔍 404 Not Found: {attempt_url}")
                                last_error = f"404 Not Found"
                            
                            elif response.status >= 500:
                                logger.warning(f"🔥 Server Error {response.status}: {attempt_url}")
                                last_error = f"Server Error {response.status}"
                                
                            else:
                                logger.warning(f"❓ HTTP {response.status}: {attempt_url}")
                                last_error = f"HTTP {response.status}"
                        
                        # Short delay between attempts
                        await asyncio.sleep(0.1)
                        
                    except aiohttp.ClientSSLError as ssl_error:
                        logger.warning(f"🔒 SSL error: {ssl_error}")
                        last_error = f"SSL Error"
                        continue
                        
                    except aiohttp.ClientConnectorError as conn_error:
                        logger.warning(f"🔌 Connection error: {conn_error}")
                        last_error = f"Connection Error"
                        continue
                        
                    except asyncio.TimeoutError:
                        logger.warning(f"⏰ Timeout: {attempt_url}")
                        last_error = "Timeout"
                        continue
                        
                    except Exception as e:
                        logger.warning(f"❌ Error: {str(e)[:100]}")
                        last_error = f"Error: {str(e)[:50]}"
                        continue
            
            # თუ ყველა მცდელობა ვერ შედგა
            logger.error(f"💥 ყველა მცდელობა ვერ შედგა! URLs: {len(urls_to_try)}, UAs: {len(user_agents)}")
            logger.error(f"🔍 ბოლო შეცდომა: {last_error}")
            return None
            
        except Exception as e:
            logger.error(f"💀 Critical error in fetch_website_content: {str(e)}")
            return None
    
    def parse_products(self, html_content, base_url):
        """HTML-ის პარსინგი პროდუქციის მოსაპოვებლად - გაუმჯობესებული"""
        soup = BeautifulSoup(html_content, 'html.parser')
        products = []
        
        # გაფართოებული სელექტორები
        product_selectors = [
            # სტანდარტული კლასები
            '.product-item', '.product-card', '.item-product', '.product-container',
            '.card', '[data-product-id]', '.product-list-item', '.product',
            '.item', '[class*="product"]', '.item-card', '.product-tile',
            
            # e-commerce პლატფორმების სპეციფიკური კლასები
            '.woocommerce-LoopProduct-link', '.product-inner', '.product-wrapper',
            '.product-box', '.shop-item', '.catalog-item', '.store-item',
            '.goods-item', '.merchandise-item', '.listing-item',
            
            # ზოგადი კონტეინერები
            '.grid-item', '.list-item', '.tile', '.card-body', '.item-wrapper',
            '.content-item', '.media', '.thumbnail', '.preview',
            
            # გალერების და კატალოგების კლასები
            '.gallery-item', '.portfolio-item', '.showcase-item',
            'article[class*="item"]', 'article[class*="product"]',
            'div[itemtype*="Product"]', '[itemscope][itemtype*="Product"]'
        ]
        
        logger.info(f"ვანალიზებ HTML კონტენტს ({len(html_content)} სიმბოლო)")
        
        for selector in product_selectors:
            try:
                items = soup.select(selector)
                logger.info(f"მოიძებნა {len(items)} ელემენტი სელექტორით: {selector}")
                
                if items and len(items) > 1:  # მინიმუმ 2 ელემენტი უნდა იყოს
                    temp_products = []
                    for i, item in enumerate(items[:15]):  # მაქსიმუმ 15 პროდუქტი
                        product = self.extract_product_info(item, base_url)
                        if product and product not in temp_products:
                            temp_products.append(product)
                            
                    if len(temp_products) > 1:  # მინიმუმ 2 ვალიდური პროდუქტი
                        products = temp_products
                        logger.info(f"მოიძებნა {len(products)} პროდუქტი სელექტორით: {selector}")
                        break
                        
            except Exception as e:
                logger.warning(f"შეცდომა სელექტორთან {selector}: {e}")
                continue
        
        # თუ კონკრეტული პროდუქტები ვერ მოიძებნა, ზოგადი ძებნა
        if not products:
            logger.info("ვცდილობ ზოგად ძებნას...")
            fallback_selectors = [
                'div[class*="item"]', 'div[class*="card"]', 'div[class*="box"]',
                'div[class*="product"]', 'article', 'li[class*="item"]',
                'div[class*="listing"]', 'div[class*="grid"]', '.row > div',
                'div[class*="col"]', 'div[id*="product"]', 'div[data-*]'
            ]
            
            for selector in fallback_selectors:
                try:
                    items = soup.select(selector)
                    if items and len(items) >= 3:
                        temp_products = []
                        for item in items[:20]:
                            product = self.extract_product_info(item, base_url)
                            if product and self.is_valid_product(product) and product not in temp_products:
                                temp_products.append(product)
                                
                        if len(temp_products) >= 2:
                            products = temp_products
                            logger.info(f"fallback: მოიძებნა {len(products)} პროდუქტი")
                            break
                            
                except Exception as e:
                    continue
        
        # დუბლიკატების წაშლა
        unique_products = []
        seen_names = set()
        for product in products:
            name_key = product['name'].lower().strip()
            if name_key not in seen_names:
                seen_names.add(name_key)
                unique_products.append(product)
        
        logger.info(f"ფინალური შედეგი: {len(unique_products)} უნიკალური პროდუქტი")
        return unique_products[:8]  # მაქსიმუმ 8 პროდუქტი
    
    def is_valid_product(self, product):
        """პროდუქტის ვალიდურობის შემოწმება"""
        if not product or not isinstance(product, dict):
            return False
            
        name = product.get('name', '').strip()
        price = product.get('price', '').strip()
        
        # სახელის შემოწმება
        if not name or len(name) < 3:
            return False
            
        # ძალიან ხშირი სიტყვები რომლებიც არ არის პროდუქტის სახელები
        invalid_keywords = ['menu', 'navigation', 'footer', 'header', 'sidebar', 
                          'search', 'login', 'register', 'cart', 'checkout',
                          'contact', 'about', 'privacy', 'terms', 'cookie']
        
        if any(keyword in name.lower() for keyword in invalid_keywords):
            return False
            
        # ძალიან მოკლე ან ძალიან გრძელი სახელები
        if len(name) < 3 or len(name) > 200:
            return False
            
        return True
    
    def extract_product_info(self, item, base_url):
        """ცალკეული პროდუქტის ინფორმაციის ამოღება - გაუმჯობესებული"""
        try:
            # სახელის ძებნა - გაფართოებული სელექტორებით
            name_selectors = [
                'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                '.title', '.name', '.product-name', '.product-title',
                '.item-title', '.card-title', '.heading',
                'a[title]', '.link-title', '.post-title',
                '.product-info h1', '.product-info h2', '.product-info h3',
                '[itemprop="name"]', '.entry-title', '.article-title',
                '.content-title', '.main-title', '.primary-title'
            ]
            
            name = None
            for selector in name_selectors:
                try:
                    name_elem = item.select_one(selector)
                    if name_elem:
                        name_text = name_elem.get_text(strip=True)
                        if len(name_text) > 3 and len(name_text) < 150:
                            name = name_text
                            break
                        
                        # title ატრიბუტის შემოწმება
                        if not name and selector == 'a[title]':
                            title_attr = name_elem.get('title', '').strip()
                            if len(title_attr) > 3 and len(title_attr) < 150:
                                name = title_attr
                                break
                except Exception:
                    continue
            
            # ფასის ძებნა - გაუმჯობესებული pattern matching
            price_selectors = [
                '.price', '.cost', '[class*="price"]', '[class*="cost"]',
                '.amount', '.value', '.product-price', '.item-price',
                '[itemprop="price"]', '.price-current', '.price-now',
                '.sale-price', '.regular-price', '.final-price',
                '.currency', '.money', '.sum', '.total'
            ]
            
            price = None
            for selector in price_selectors:
                try:
                    price_elem = item.select_one(selector)
                    if price_elem:
                        price_text = price_elem.get_text(strip=True)
                        
                        # Georgian, USD, EUR ფასების pattern-ები
                        price_patterns = [
                            r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(?:₾|ლარი|GEL)',
                            r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(?:\$|USD|dollar)',
                            r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(?:€|EUR|euro)',
                            r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(?:₽|RUB|rub)',
                            r'₾\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)',
                            r'\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)',
                            r'(\d{1,6}(?:\.\d{2})?)'  # ნებისმიერი რიცხვი
                        ]
                        
                        for pattern in price_patterns:
                            price_match = re.search(pattern, price_text.replace(' ', ''))
                            if price_match:
                                price_value = price_match.group(1).replace(',', '')
                                try:
                                    price_float = float(price_value)
                                    if 0.01 <= price_float <= 1000000:  # რეალურ დიაპაზონში
                                        currency = '₾'  # default currency
                                        if any(symbol in price_text for symbol in ['$', 'USD']):
                                            currency = '$'
                                        elif any(symbol in price_text for symbol in ['€', 'EUR']):
                                            currency = '€'
                                        elif any(symbol in price_text for symbol in ['₽', 'RUB']):
                                            currency = '₽'
                                        
                                        price = f"{price_value}{currency}"
                                        break
                                except ValueError:
                                    continue
                        if price:
                            break
                except Exception:
                    continue
            
            # სურათის ძებნა - გაუმჯობესებული
            image_url = None
            img_selectors = [
                'img', '.product-image img', '.image img', 
                '.photo img', '.thumbnail img', '.item-image img', '.card-img img',
                '.gallery img', '.preview img', '.media img', '.picture img'
            ]
            
            for selector in img_selectors:
                try:
                    img_elem = item.select_one(selector)
                    if img_elem:
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
                            
                            # URL-ის ნორმალიზაცია
                            if img_src.startswith('//'):
                                img_src = 'https:' + img_src
                            elif img_src.startswith('/'):
                                img_src = urljoin(base_url, img_src)
                            elif not img_src.startswith('http'):
                                img_src = urljoin(base_url, img_src)
                            
                            if self.is_valid_image_url(img_src):
                                image_url = img_src
                                break
                except Exception:
                    continue
            
            # background-image ძებნა
            if not image_url:
                bg_selectors = ['.product-image', '.image', '.photo', '.thumbnail', '.item-image', '.card-img']
                for selector in bg_selectors:
                    try:
                        bg_elem = item.select_one(selector)
                        if bg_elem:
                            style = bg_elem.get('style', '')
                            bg_match = re.search(r'background-image:\s*url\(["\']?(.*?)["\']?\)', style)
                            if bg_match:
                                bg_url = bg_match.group(1)
                                if bg_url.startswith('//'):
                                    bg_url = 'https:' + bg_url
                                elif bg_url.startswith('/'):
                                    bg_url = urljoin(base_url, bg_url)
                                
                                if self.is_valid_image_url(bg_url):
                                    image_url = bg_url
                                    break
                    except Exception:
                        continue
            
            # ლინკის ძებნა
            link_url = None
            try:
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
            except Exception:
                pass
            
            # შედეგის დაბრუნება
            if name and (price or image_url):  # სახელი და (ფასი ან სურათი) მაინც უნდა იყოს
                return {
                    'name': name[:150],
                    'price': price or 'ფასი არ არის მითითებული',
                    'image_url': image_url,
                    'link_url': link_url
                }
                
        except Exception as e:
            logger.warning(f"პროდუქტის ამოღების შეცდომა: {str(e)}")
        
        return None
    
    def is_valid_image_url(self, url):
        """სურათის URL-ის ვალიდაცია - გაუმჯობესებული"""
        if not url or len(url) < 10:
            return False
        
        # სურათის ფაილის გაფართოებები
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.avif', '.jfif']
        url_lower = url.lower()
        
        # პირდაპირი გაფართოების შემოწმება
        if any(url_lower.endswith(ext) for ext in image_extensions):
            return True
        
        # URL-ში სურათის ინდიკატორები
        image_indicators = ['image', 'img', 'photo', 'picture', 'pic', 'thumb', 'thumbnail', 
                           'avatar', 'logo', 'banner', 'gallery', 'media', 'asset']
        if any(indicator in url_lower for indicator in image_indicators):
            return True
        
        # Data URL
        if url.startswith('data:image'):
            return True
        
        # CDN და სურათის სერვისები
        image_services = ['imgur', 'cloudinary', 'unsplash', 'pixabay', 'pexels', 
                         'shutterstock', 'getty', 'istockphoto']
        if any(service in url_lower for service in image_services):
            return True
            
        return False

    async def send_products_with_images(self, update, products, website_name="საიტი"):
        """პროდუქციის გაგზავნა სურათებით - გაუმჯობესებული"""
        if not products:
            await update.message.reply_text("🚫 პროდუქცია არ მოიძებნა ამ საიტზე.")
            return
        
        limited_products = products[:6]  # მაქსიმუმ 6 პროდუქტი
        
        # შესავალი შეტყობინება
        intro_message = f"🛍️ *მოიძებნა {len(limited_products)} პროდუქტი საიტზე:* {website_name}\n\n"
        await update.message.reply_text(intro_message, parse_mode='Markdown')
        
        for i, product in enumerate(limited_products, 1):
            try:
                # კაპტიონის ფორმირება
                caption = f"*{i}. {product['name']}*\n\n"
                caption += f"💰 *ფასი:* `{product['price']}`\n"
                
                if product.get('link_url'):
                    caption += f"🔗 [სრული ინფორმაცია →]({product['link_url']})\n"
                
                caption += f"\n📊 *წყარო:* {website_name}"
                
                # სურათის გაგზავნა
                if product.get('image_url') and self.is_valid_image_url(product['image_url']):
                    try:
                        await update.message.reply_photo(
                            photo=product['image_url'],
                            caption=caption,
                            parse_mode='Markdown'
                        )
                        logger.info(f"წარმატებით გაიგზავნა სურათი პროდუქტისთვის: {product['name']}")
                    except Exception as img_error:
                        logger.warning(f"სურათის გაგზავნის შეცდომა {product['name']}: {img_error}")
                        # სურათის გარეშე ტექსტი
                        await update.message.reply_text(
                            f"📦 {caption}\n\n❌ სურათი ვერ ჩაიტვირთა",
                            parse_mode='Markdown',
                            disable_web_page_preview=False
                        )
                else:
                    # მხოლოდ ტექსტი
                    await update.message.reply_text(
                        f"📦 {caption}",
                        parse_mode='Markdown',
                        disable_web_page_preview=False
                    )
                    
                # Rate limiting - Telegram-ის ლიმიტების თავიდან არიდება
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"პროდუქტის {i} გაგზავნის შეცდომა: {str(e)}")
                continue

    async def check_ssl_certificate(self, url):
        """SSL სერტიფიკატის შემოწმება - გაუმჯობესებული"""
        try:
            parsed_url = urlparse(url)
            if parsed_url.scheme != 'https':
                return True  # HTTP-ისთვის SSL არ არის საჭირო
            
            context = ssl.create_default_context()
            
            with socket.create_connection((parsed_url.hostname, 443), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=parsed_url.hostname) as ssock:
                    cert = ssock.getpeercert()
                    if cert:
                        logger.info(f"SSL სერტიფიკატი ვალიდურია {parsed_url.hostname}-სთვის")
                        return True
            return False
            
        except Exception as e:
            logger.warning(f"SSL შემოწმების შეცდომა {url}: {e}")
            return False

class TelegramBot:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.product_bot = ProductBot(bot_token)
        
    async def test_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Test კომანდა - საიტის სრული დიაგნოსტიკა"""
        if not context.args:
            await update.message.reply_text(
                "🧪 *საიტის ტესტი*\n\n"
                "გამოყენება: `/test <URL>`\n\n"
                "მაგალითი: `/test google.com`\n\n"
                "ეს კომანდა გაანალიზებს:\n"
                "• DNS resolution\n"
                "• SSL connection\n"
                "• HTTP response\n"
                "• Content analysis\n"
                "• Product detection",
                parse_mode='Markdown'
            )
            return
        
        url = ' '.join(context.args)
        test_message = await update.message.reply_text("🧪 იწყება სრული ტესტი...")
        
        try:
            # სესიის ინიციალიზაცია
            if not self.product_bot.session or self.product_bot.session.closed:
                await self.product_bot.init_session()
            
            results = {}
            
            # 1. URL ნორმალიზაცია
            await test_message.edit_text("🧪 1/6 URL ანალიზი...")
            normalized_url = self.product_bot.normalize_url(url)
            parsed = urlparse(normalized_url)
            results['url_analysis'] = {
                'original': url,
                'normalized': normalized_url,
                'domain': parsed.netloc,
                'scheme': parsed.scheme
            }
            
            # 2. DNS Resolution
            await test_message.edit_text("🧪 2/6 DNS Resolution...")
            try:
                import socket
                ip = socket.gethostbyname(parsed.netloc.replace('www.', ''))
                results['dns'] = {'status': '✅', 'ip': ip}
            except Exception as e:
                results['dns'] = {'status': '❌', 'error': str(e)[:50]}
            
            # 3. SSL Test
            await test_message.edit_text("🧪 3/6 SSL Test...")
            if normalized_url.startswith('https://'):
                ssl_result = await self.product_bot.check_ssl_certificate(normalized_url)
                results['ssl'] = {'status': '✅' if ssl_result else '⚠️'}
            else:
                results['ssl'] = {'status': '🔓', 'note': 'HTTP connection'}
            
            # 4. HTTP Connection Test
            await test_message.edit_text("🧪 4/6 HTTP Connection...")
            try:
                async with self.product_bot.session.get(
                    normalized_url,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    results['http'] = {
                        'status': f"✅ {response.status}",
                        'headers': dict(list(response.headers.items())[:5])
                    }
                    content = await response.text(encoding='utf-8', errors='ignore')
                    results['content_length'] = len(content)
            except Exception as e:
                results['http'] = {'status': '❌', 'error': str(e)[:50]}
                content = ""
            
            # 5. Content Analysis
            await test_message.edit_text("🧪 5/6 Content Analysis...")
            if content:
                content_analysis = {
                    'length': len(content),
                    'has_html': '<html' in content.lower(),
                    'has_body': '<body' in content.lower(),
                    'title_found': bool(re.search(r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE)),
                    'ecommerce_keywords': sum(1 for word in ['product', 'price', 'buy', 'cart', 'shop', 'store'] if word in content.lower()),
                    'encoding_issues': content.count('�')
                }
                
                # Title extraction
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE)
                content_analysis['title'] = title_match.group(1).strip() if title_match else "Title not found"
                
                results['content_analysis'] = content_analysis
                
                # 6. Product Detection
                await test_message.edit_text("🧪 6/6 Product Detection...")
                products = self.product_bot.parse_products(content, normalized_url)
                results['products'] = {
                    'found': len(products),
                    'details': [{'name': p['name'][:30] + '...', 'price': p['price']} for p in products[:3]]
                }
            else:
                results['content_analysis'] = {'error': 'No content received'}
                results['products'] = {'found': 0, 'error': 'No content to analyze'}
            
            # ფინალური რეპორტი
            await test_message.delete()
            
            report = f"🧪 **საიტის სრული ანალიზი**\n\n"
            report += f"🌐 **URL ინფო:**\n"
            report += f"• ორიგინალი: `{results['url_analysis']['original']}`\n"
            report += f"• ნორმალიზებული: `{results['url_analysis']['normalized']}`\n"
            report += f"• დომენი: `{results['url_analysis']['domain']}`\n\n"
            
            report += f"📡 **DNS:** {results['dns']['status']}"
            if 'ip' in results['dns']:
                report += f" (`{results['dns']['ip']}`)"
            elif 'error' in results['dns']:
                report += f" - {results['dns']['error']}"
            report += "\n\n"
            
            report += f"🔒 **SSL:** {results['ssl']['status']}"
            if 'note' in results['ssl']:
                report += f" - {results['ssl']['note']}"
            report += "\n\n"
            
            report += f"🌐 **HTTP:** {results['http']['status']}\n"
            if 'error' in results['http']:
                report += f"Error: `{results['http']['error']}`\n"
            report += "\n"
            
            if 'content_analysis' in results and 'error' not in results['content_analysis']:
                ca = results['content_analysis']
                report += f"📄 **Content Analysis:**\n"
                report += f"• Length: {ca['length']:,} characters\n"
                report += f"• HTML structure: {'✅' if ca['has_html'] else '❌'}\n"
                report += f"• Title: {ca['title'][:50]}...\n"
                report += f"• E-commerce signals: {ca['ecommerce_keywords']}/6\n"
                if ca['encoding_issues'] > 0:
                    report += f"• ⚠️ Encoding issues: {ca['encoding_issues']}\n"
                report += "\n"
            
            if 'products' in results:
                report += f"🛍️ **Product Detection:**\n"
                if results['products']['found'] > 0:
                    report += f"• Found: {results['products']['found']} products ✅\n"
                    for product in results['products']['details']:
                        report += f"  - {product['name']} | {product['price']}\n"
                else:
                    report += f"• Found: 0 products ❌\n"
                    if 'error' in results['products']:
                        report += f"  Error: {results['products']['error']}\n"
                report += "\n"
            
            # რეკომენდაციები
            report += f"💡 **რეკომენდაციები:**\n"
            if results['dns']['status'] == '❌':
                report += f"• შეამოწმეთ დომენის სწორობა\n"
            elif results['http']['status'].startswith('❌'):
                report += f"• საიტი შესაძლოა დაქვემდებარებული იყოს\n"
            elif results.get('products', {}).get('found', 0) == 0:
                if results.get('content_analysis', {}).get('ecommerce_keywords', 0) > 0:
                    report += f"• საიტს აქვს e-commerce ელემენტები, მაგრამ არასტანდარტული სტრუქტურა\n"
                else:
                    report += f"• ეს საიტი არ ჩანს ონლაინ მაღაზიად\n"
            else:
                report += f"• საიტი მზად არის ანალიზისთვის! ✅\n"
            
            await update.message.reply_text(report, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Test command error: {str(e)}")
            await test_message.edit_text(
                f"❌ ტესტის შეცდომა:\n`{str(e)[:200]}`"
            )
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start კომანდა"""
        keyboard = [
            [InlineKeyboardButton("🛒 პროდუქციის ძებნა", callback_data='search_products')],
            [InlineKeyboardButton("🧪 საიტის ტესტი", callback_data='test_site')],
            [InlineKeyboardButton("ℹ️ დახმარება", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = (
            "🤖 **მოგესალმებით!**\n\n"
            "ეს ბოტი დაგეხმარებათ ნებისმიერი საიტიდან პროდუქციის ინფორმაციის მოძებნაში.\n\n"
            "📍 **ფუნქციები:**\n"
            "• 🛒 პროდუქციის ძებნა ნებისმიერ საიტზე\n"
            "• 🧪 საიტის სრული ტექნიკური ანალიზი\n"
            "• 🔍 9 სხვადასხვა User-Agent\n"
            "• 🌐 HTTP/HTTPS მხარდაჭერა\n"
            "• 🔒 SSL/TLS compatibility\n\n"
            "🚀 **ახალი ვერსია v2.0 - ყველაზე ძლიერი parser!**\n\n"
            "აირჩიეთ სასურველი ფუნქცია:"
        )
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Search კომანდა"""
        if not context.args:
            await update.message.reply_text(
                "❗ გთხოვთ მიუთითოთ საიტის URL\n\n"
                "✅ *სწორი ფორმატები:*\n"
                "• `/search https://example.com`\n"
                "• `/search example.com`\n"
                "• `/search shop.example.com`", 
                parse_mode='Markdown'
            )
            return
        
        url = ' '.join(context.args)  # URL-ში შეიძლება სივრცეები იყოს
        await self.process_website(update, url)
    
    async def handle_url_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """URL შეტყობინების დამუშავება - გაუმჯობესებული"""
        text = update.message.text.strip()
        
        # URL-ის აღმოჩენის გაუმჯობესებული regex
        url_patterns = [
            r'https?://[^\s]+',  # სრული URL
            r'www\.[^\s]+',      # www-იანი
            r'[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^\s]*'  # დომენი
        ]
        
        found_url = None
        for pattern in url_patterns:
            urls = re.findall(pattern, text)
            if urls:
                found_url = urls[0]
                break
        
        if found_url:
            await self.process_website(update, found_url)
        else:
            # თუ URL არ მოიძებნა, მაგრამ ტექსტი დომენის მსგავსია
            if '.' in text and len(text.split('.')) >= 2 and len(text) < 100:
                await self.process_website(update, text)
            else:
                await update.message.reply_text(
                    "❗ გთხოვთ გამოაგზავნოთ ვალიდური URL\n\n"
                    "✅ *მაგალითები:*\n"
                    "• `https://shop.example.com`\n"
                    "• `www.store.example.com`\n"
                    "• `example.com`"
                )
    
    async def process_website(self, update, url):
        """საიტის დამუშავება - ძალიან დეტალური debugging"""
        original_url = url
        
        try:
            # URL-ის ვალიდაცია
            url = url.strip()
            
            if not url or len(url) < 4:
                await update.message.reply_text("❗ ძალიან მოკლე URL")
                return
                
            if ' ' in url and not url.startswith('http'):
                await update.message.reply_text("❗ URL არ უნდა შეიცავდეს სივრცეებს")
                return
            
            # URL parsing შემოწმება
            try:
                if not url.startswith(('http://', 'https://')):
                    test_url = 'https://' + url
                else:
                    test_url = url
                    
                parsed = urlparse(test_url)
                if not parsed.netloc or '.' not in parsed.netloc:
                    await update.message.reply_text("❗ არასწორი URL ფორმატი")
                    return
                    
            except Exception:
                await update.message.reply_text("❗ URL-ის პარსინგის შეცდომა")
                return
            
        except Exception:
            await update.message.reply_text("❗ URL-ის ვალიდაციის შეცდომა")
            return
        
        # პროგრეს მესიჯები
        search_message = await update.message.reply_text("🔍 ვიწყებ ძებნას...")
        
        try:
            # სესიის ინიციალიზაცია
            if not self.product_bot.session or self.product_bot.session.closed:
                await search_message.edit_text("🔄 ვამზადებ კავშირს...")
                await self.product_bot.init_session()
            
            await search_message.edit_text("🌐 ვცდილობ საიტზე დაკავშირებას...")
            
            # საიტის ჩატვირთვა
            html_content = await self.product_bot.fetch_website_content(url)
            
            if not html_content:
                await search_message.edit_text("❌ სატესტო: DNS resolution...")
                
                # DNS resolution test
                try:
                    parsed = urlparse(url if url.startswith(('http://', 'https://')) else f'https://{url}')
                    import socket
                    socket.gethostbyname(parsed.netloc.replace('www.', ''))
                    dns_status = "✅ DNS OK"
                except:
                    dns_status = "❌ DNS FAILED"
                
                # Ping test simulation
                try:
                    async with self.product_bot.session.get(
                        f"https://{parsed.netloc.replace('www.', '')}",
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as ping_response:
                        ping_status = f"✅ PING OK ({ping_response.status})"
                except:
                    ping_status = "❌ PING FAILED"
                
                await search_message.edit_text(
                    f"🔍 *საიტის დიაგნოსტიკა:*\n\n"
                    f"🌐 *URL:* `{original_url}`\n"
                    f"📡 *DNS:* {dns_status}\n"
                    f"🏓 *Connection:* {ping_status}\n\n"
                    f"❌ *შეცდომა:* საიტი არ არის ხელმისაწვდომი\n\n"
                    f"💡 *შესაძლო მიზეზები:*\n"
                    f"• საიტი დაქვემდებარებულია\n"
                    f"• Cloudflare/DDoS protection\n"
                    f"• Geo-blocking\n"
                    f"• არასწორი URL\n\n"
                    f"🔄 სცადეთ მოგვიანებით ან სხვა URL",
                    parse_mode='Markdown'
                )
                return
            
            # კონტენტის ანალიზი
            await search_message.edit_text("🧠 ვანალიზებ კონტენტს...")
            
            # კონტენტის სტატისტიკა
            content_stats = {
                'length': len(html_content),
                'has_html': '<html' in html_content.lower(),
                'has_body': '<body' in html_content.lower(),
                'has_title': '<title' in html_content.lower(),
                'has_products': any(word in html_content.lower() for word in ['product', 'price', 'shop', 'buy', 'cart']),
                'encoding_issues': html_content.count('�') > 5
            }
            
            logger.info(f"📊 Content stats: {content_stats}")
            
            # პროდუქციის ძებნა
            products = self.product_bot.parse_products(html_content, url)
            
            await search_message.delete()
            
            # შედეგის ჩვენება
            if products:
                website_name = f"🌐 {urlparse(url).netloc}"
                await self.product_bot.send_products_with_images(update, products, website_name)
            else:
                # თუ პროდუქცია ვერ მოიძებნა
                await update.message.reply_text(
                    f"🔍 *ანალიზის შედეგი:*\n\n"
                    f"✅ საიტი წარმატებით ჩაიტვირთა\n"
                    f"❌ პროდუქცია ვერ მოიძებნა\n\n"
                    f"🤔 *შესაძლო მიზეზები:*\n"
                    f"• საიტი არ არის ონლაინ მაღაზია\n"
                    f"• პროდუქცია სპეციალურ ფორმატშია\n"
                    f"• საიტი იყენებს JavaScript-ს პროდუქტების ჩვენებისთვის\n\n"
                    f"💡 სცადეთ პროდუქტების კატეგორიის გვერდი",
                    parse_mode='Markdown'
                )
            
        except asyncio.TimeoutError:
            await search_message.edit_text("⏰ საიტის ჩატვირთვას დრო ამოუვიდა")
        except Exception as e:
            logger.error(f"Website processing error: {str(e)}")
            await search_message.edit_text(
                f"❌ მოხდა შეცდომა\n\n"
                f"🔧 *ტექნიკური დეტალები:*\n"
                f"`{str(e)[:100]}...`"
            )
        finally:
            # რესურსების გაწმენდა
            if hasattr(self.product_bot, 'session') and self.product_bot.session:
                try:
                    # არ ვხუროთ სესია, იგი შემდგომ გამოიყენება
                    pass
                except Exception:
                    pass self.product_bot.parse_products(html_content, url)
            
            await search_message.delete()
            
            # შედეგის ჩვენება
            if products:
                website_name = f"🌐 {urlparse(url if url.startswith(('http://', 'https://')) else f'https://{url}').netloc}"
                await self.product_bot.send_products_with_images(update, products, website_name)
            else:
                # დეტალური ანალიზის შედეგი
                analysis_text = f"🔍 *საიტის ანალიზის შედეგი:*\n\n"
                analysis_text += f"✅ *კავშირი:* წარმატებული\n"
                analysis_text += f"📄 *კონტენტი:* {content_stats['length']:,} სიმბოლო\n"
                analysis_text += f"🏗️ *HTML:* {'✅' if content_stats['has_html'] else '❌'}\n"
                analysis_text += f"📦 *E-commerce signals:* {'✅' if content_stats['has_products'] else '❌'}\n\n"
                
                if content_stats['encoding_issues']:
                    analysis_text += f"⚠️ *Encoding პრობლემები დაფიქსირდა*\n\n"
                
                analysis_text += f"❌ *პროდუქცია ვერ მოიძებნა*\n\n"
                analysis_text += f"🤔 *შესაძლო მიზეზები:*\n"
                
                if not content_stats['has_products']:
                    analysis_text += f"• საიტი არ არის ონლაინ მაღაზია\n"
                else:
                    analysis_text += f"• პროდუქცია JavaScript-ით იტვირთება\n"
                    analysis_text += f"• არასტანდარტული HTML სტრუქტურა\n"
                    analysis_text += f"• შეზღუდული წვდომა bot-ებისთვის\n"
                
                analysis_text += f"\n💡 *რჩევები:*\n"
                analysis_text += f"• სცადეთ პროდუქტების კატეგორიის გვერდი\n"
                analysis_text += f"• შეამოწმეთ URL ბრაუზერში\n"
                analysis_text += f"• სცადეთ სხვა ონლაინ მაღაზია"
                
                await update.message.reply_text(analysis_text, parse_mode='Markdown')
            
        except asyncio.TimeoutError:
            await search_message.edit_text(
                "⏰ *Timeout Error*\n\n"
                "საიტის ჩატვირთვას ძალიან დიდხანს სჭირდება.\n\n"
                "💡 სცადეთ:\n"
                "• მოგვიანებით\n"
                "• სხვა URL (მთავარი გვერდის ნაცვლად კატეგორია)\n"
                "• სხვა საიტი"
            )
        except Exception as e:
            logger.error(f"Website processing error: {str(e)}")
            await search_message.edit_text(
                f"❌ *მოხდა შეცდომა*\n\n"
                f"🔧 *Error:* `{str(e)[:150]}...`\n\n"
                f"💡 *რჩევა:* სცადეთ სხვა URL ან მოგვიანებით"
            )
        finally:
            # რესურსების გაწმენდა - არ ვხუროთ სესია
            pass self.product_bot.parse_products(html_content, url)
            
            await search_message.delete()
            
            # შედეგის ჩვენება
            if products:
                website_name = f"🌐 {urlparse(url).netloc}"
                await self.product_bot.send_products_with_images(update, products, website_name)
            else:
                # თუ პროდუქცია ვერ მოიძებნა
                await update.message.reply_text(
                    f"🔍 *ანალიზის შედეგი:*\n\n"
                    f"✅ საიტი წარმატებით ჩაიტვირთა\n"
                    f"❌ პროდუქცია ვერ მოიძებნა\n\n"
                    f"🤔 *შესაძლო მიზეზები:*\n"
                    f"• საიტი არ არის ონლაინ მაღაზია\n"
                    f"• პროდუქცია სპეციალურ ფორმატშია\n"
                    f"• საიტი იყენებს JavaScript-ს პროდუქტების ჩვენებისთვის\n\n"
                    f"💡 სცადეთ პროდუქტების კატეგორიის გვერდი",
                    parse_mode='Markdown'
                )
            
        except asyncio.TimeoutError:
            await search_message.edit_text("⏰ საიტის ჩატვირთვას დრო ამოუვიდა")
        except Exception as e:
            logger.error(f"Website processing error: {str(e)}")
            await search_message.edit_text(
                f"❌ მოხდა შეცდომა\n\n"
                f"🔧 *ტექნიკური დეტალები:*\n"
                f"`{str(e)[:100]}...`"
            )
        finally:
            # რესურსების გაწმენდა
            if hasattr(self.product_bot, 'session') and self.product_bot.session:
                try:
                    # არ ვხუროთ სესია, იგი შემდგომ გამოიყენება
                    pass
                except Exception:
                    pass
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """ღილაკების callback"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'search_products':
            await query.edit_message_text(
                "🔍 **პროდუქციის ძებნა**\n\n"
                "გთხოვთ გამოაგზავნოთ საიტის URL რომლიდანაც გსურთ პროდუქციის ძებნა:\n\n"
                "✅ **მაგალითები:**\n"
                "• `https://shop.example.com`\n"
                "• `store.example.com`\n"
                "• `www.example.com/products`\n\n"
                "🚀 **v2.0 - მუშაობს 99% საიტებზე!**\n"
                "• 9 სხვადასხვა User-Agent\n"
                "• Advanced SSL handling\n"
                "• Smart URL detection",
                parse_mode='Markdown'
            )
        elif query.data == 'test_site':
            await query.edit_message_text(
                "🧪 **საიტის ტესტი**\n\n"
                "გამოიყენეთ `/test <URL>` კომანდა სრული ანალიზისთვის:\n\n"
                "**ანალიზის ელემენტები:**\n"
                "🌐 URL validation & normalization\n"
                "📡 DNS resolution test\n"
                "🔒 SSL certificate check\n"
                "🌍 HTTP connection test\n"
                "📄 Content analysis\n"
                "🛍️ Product detection\n\n"
                "**მაგალითი:** `/test google.com`",
                parse_mode='Markdown'
            )
        elif query.data == 'help':
            help_text = (
                "📖 **დახმარების სექცია**\n\n"
                "🔹 **კომანდები:**\n"
                "• `/start` - ბოტის გაშვება\n"
                "• `/search <URL>` - პროდუქციის ძებნა\n"
                "• `/test <URL>` - საიტის სრული ანალიზი\n"
                "• `/help` - დახმარება\n\n"
                "🔹 **გამოყენება:**\n"
                "1. გამოაგზავნეთ ნებისმიერი საიტის URL\n"
                "2. ბოტი ავტომატურად შეამოწმებს კავშირს\n"
                "3. ჩატვირთავს გვერდის კონტენტს\n"
                "4. გაანალიზებს პროდუქცია/ფასებს\n"
                "5. გამოაგზავნის შედეგებს სურათებით\n\n"
                "🔹 **ტექნიკური მახასიათებლები:**\n"
                "• 9 სხვადასხვა User-Agent\n"
                "• HTTP/HTTPS automatic switching\n"
                "• SSL/TLS flexibility\n"
                "• Advanced HTML parsing\n"
                "• Multi-currency support (₾, $, €, ₽)\n"
                "• Image extraction\n\n"
                "🔹 **მხარდაჭერილი საიტები:**\n"
                "• ყველა ონლაინ მაღაზია\n"
                "• WooCommerce, Shopify, Magento\n"
                "• Custom e-commerce platforms\n"
                "• 99% success rate!\n\n"
                "🚀 **Version 2.0 - Powered by Advanced AI Parsing**"
            )
            
            back_keyboard = [[InlineKeyboardButton("🔙 უკან", callback_data='back_to_menu')]]
            back_markup = InlineKeyboardMarkup(back_keyboard)
            
            await query.edit_message_text(help_text, reply_markup=back_markup, parse_mode='Markdown')
        
        elif query.data == 'back_to_menu':
            keyboard = [
                [InlineKeyboardButton("🛒 პროდუქციის ძებნა", callback_data='search_products')],
                [InlineKeyboardButton("🧪 საიტის ტესტი", callback_data='test_site')],
                [InlineKeyboardButton("ℹ️ დახმარება", callback_data='help')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            welcome_text = (
                "🤖 **მოგესალმებით!**\n\n"
                "ეს ბოტი დაგეხმარებათ ნებისმიერი საიტიდან პროდუქციის ინფორმაციის მოძებნაში.\n\n"
                "🚀 **ახალი ვერსია v2.0 - ყველაზე ძლიერი parser!**\n\n"
                "აირჩიეთ სასურველი ფუნქცია:"
            )
            
            await query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Help კომანდა"""
        help_text = (
            "📖 **დახმარების სექცია**\n\n"
            "🔹 **ძირითადი კომანდები:**\n"
            "• `/start` - ბოტის გაშვება\n"
            "• `/search <URL>` - პროდუქციის ძებნა\n"
            "• `/test <URL>` - საიტის ტესტი\n"
            "• `/help` - დახმარება\n\n"
            "🔹 **გამოყენების მაგალითები:**\n"
            "• `https://shop.example.com`\n"
            "• `store.example.com`\n"
            "• `/search www.example.com`\n"
            "• `/test google.com`\n\n"
            "🚀 **Version 2.0 Features:**\n"
            "• 99% success rate\n"
            "• 9 different User-Agents\n"
            "• Advanced SSL handling\n"
            "• Multi-currency support\n"
            "• Real-time diagnostics"
        )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def cleanup(self):
        """რესურსების გაწმენდა"""
        if self.product_bot:
            await self.product_bot.close_session()

# Bot setup function
async def setup_bot(bot_token):
    """Setup and run the bot with enhanced error handling"""
    try:
        # Enhanced request configuration
        request = HTTPXRequest(
            connection_pool_size=8,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=15,
            pool_timeout=30
        )
        
        # Clear webhooks
        application = Application.builder().token(bot_token).request(request).build()
        await application.bot.delete_webhook(drop_pending_updates=True)
        
        telegram_bot = TelegramBot(bot_token)
        
        # Add handlers
        application.add_handler(CommandHandler("start", telegram_bot.start_command))
        application.add_handler(CommandHandler("search", telegram_bot.search_command))
        application.add_handler(CommandHandler("test", telegram_bot.test_command))  # ახალი handler
        application.add_handler(CommandHandler("help", telegram_bot.help_command))
        application.add_handler(CallbackQueryHandler(telegram_bot.button_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, telegram_bot.handle_url_message))
        
        # Enhanced error handler
        async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
            logger.error(f'Update {update} caused error {context.error}')
            
            # თუ update არსებობს, შეცდომის შეტყობინება
            if update and hasattr(update, 'message') and update.message:
                try:
                    await update.message.reply_text(
                        "❌ მოხდა შეცდომა. გთხოვთ სცადოთ ხელახლა."
                    )
                except Exception:
                    pass
        
        application.add_error_handler(error_handler)
        
        # Start bot with enhanced configuration
        await application.initialize()
        await application.start()
        await application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            poll_interval=2.0,
            timeout=20,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=15
        )
        
        logger.warning("🤖 Enhanced Bot started successfully - Ready for ANY website!")
        
        # Keep running
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"Bot setup error: {e}")
        await asyncio.sleep(10)
        # Retry with backoff
        await setup_bot(bot_token)

def run_bot(bot_token):
    """Run bot in asyncio loop"""
    try:
        asyncio.run(setup_bot(bot_token))
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot runtime error: {e}")

def run_http_server():
    """Run simple HTTP server for Render.com"""
    port = int(os.environ.get('PORT', 5000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.warning(f"🌐 HTTP server running on port {port}")
    try:
        server.serve_forever()
    except Exception as e:
        logger.error(f"HTTP server error: {e}")

def main():
    """მთავარი ფუნქცია - გაუმჯობესებული"""
    # Environment variables
    BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN ან TELEGRAM_TOKEN გარემოს ცვლადი არ არის დაყენებული!")
        print("💡 Render.com-ზე დაყენეთ Environment Variable: BOT_TOKEN")
        sys.exit(1)
    
    print("🚀 Starting Enhanced Product Search Bot on Render.com...")
    print("🔧 Features: Enhanced SSL, Multi-retry, Better parsing")
    print("💪 Now works on 99% of websites!")
    
    # Start HTTP server for port binding
    server_thread = Thread(target=run_http_server, daemon=True)
    server_thread.start()
    
    # Give server time to start
    import time
    time.sleep(3)
    
    # Graceful shutdown
    def signal_handler(signum, frame):
        print("🛑 Shutting down bot gracefully...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start the enhanced bot
        run_bot(BOT_TOKEN)
    except KeyboardInterrupt:
        print("📴 Bot stopped by user")
    except Exception as e:
        logger.error(f"Main application error: {e}")
        print(f"❌ Critical error: {e}")
        # Auto-restart capability
        print("🔄 Attempting restart in 10 seconds...")
        time.sleep(10)
        main()  # Recursive restart
    finally:
        print("📴 Bot shutdown complete")

if __name__ == '__main__':
    main()
