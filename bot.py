import os
import logging
import asyncio
import json
import re
from datetime import datetime
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
# Playwright importu
from playwright.async_api import async_playwright

# Logging ayarları
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# AYARLAR (Tokenlarını buraya girebilirsin)
# ==========================================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN') # Veya direkt 'TOKEN_BURAYA'
ALLOWED_USERS = os.getenv('ALLOWED_USERS', '').split(',') # Veya ['USER_ID']
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '300'))  # 5 dakika default

# Ürün veritabanı
tracked_products: Dict[str, Dict] = {}

class ZaraStockChecker:
    """Zara ürün stok kontrolü için sınıf (Playwright + Akıllı Mantık)"""
    
    def __init__(self):
        self.base_url = "https://www.zara.com"
    
    def extract_product_id(self, url: str) -> Optional[str]:
        match = re.search(r'p(\d+)\.html', url)
        return match.group(1) if match else None
    
    async def get_product_info(self, url: str) -> Optional[Dict]:
        """Ürün bilgilerini ve stok durumunu getirir"""
        product_id = self.extract_product_id(url)
        if not product_id:
            return None

        async with async_playwright() as p:
            try:
                # Tarayıcıyı başlat (Headless: False yaparsan işlemleri izleyebilirsin)
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                page = await context.new_page()
                
                # Sayfaya git
                logger.info(f"Sayfa yükleniyor: {url}")
                await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                
                # Çerez uyarısı vs. varsa geçmek için kısa bekleme
                await page.wait_for_timeout(2000)

                # --- 1. ADIM: İsim ve Fiyat Alma (Temel Bilgiler) ---
                try:
                    name_el = await page.query_selector("h1")
                    name = await name_el.inner_text() if name_el else "Zara Ürünü"
                    
                    price_el = await page.query_selector(".price-current__amount, .money-amount")
                    price_text = await price_el.inner_text() if price_el else "Fiyat Alınamadı"
                except:
                    name = "Zara Ürünü"
                    price_text = "Belirsiz"

                availability = 'out_of_stock'
                sizes_available = []

                # --- 2. ADIM: TÜKENDİ Mİ KONTROL ET ---
                # "Benzer Ürünler" butonu varsa ürün tamamen bitmiştir.
                sold_out_btn = await page.query_selector("button[data-qa-action='show-similar-products']")
                
                if sold_out_btn:
                    logger.info("Ürün TÜKENDİ (Benzer ürünler butonu tespit edildi).")
                    availability = 'out_of_stock'
                
                else:
                    # --- 3. ADIM: EKLE BUTONUNA TIKLA ---
                    # Ürün var görünüyor, bedenleri görmek için 'Ekle'ye tıklamalıyız.
                    try:
                        add_btn = await page.wait_for_selector("button[data-qa-action='add-to-cart']", timeout=5000)
                        if add_btn:
                            await add_btn.click()
                            logger.info("'Ekle' butonuna tıklandı, modal bekleniyor...")
                            
                            # --- 4. ADIM: BEDEN MODALINI BEKLE VE OKU ---
                            # Modalın açılmasını bekle
                            await page.wait_for_selector("div[data-qa-qualifier='size-selector-sizes-size-label']", state="visible", timeout=5000)
                            
                            # Tüm beden elementlerini bul
                            size_elements = await page.query_selector_all("li.size-selector-list__item")
                            
                            for el in size_elements:
                                # Beden metnini al (data-qa etiketi içindeki)
                                label_el = await el.query_selector("div[data-qa-qualifier='size-selector-sizes-size-label']")
                                if not label_el: continue
                                
                                size_text = await label_el.inner_text()
                                
                                # Bedenin durumu ne? (disabled / out-of-stock)
                                # Playwright ile class string'ini alıyoruz
                                class_attr = await el.get_attribute("class")
                                is_disabled = "is-disabled" in class_attr if class_attr else False
                                
                                # Eğer disabled değilse stokta demektir
                                if not is_disabled:
                                    sizes_available.append(size_text)

                            if sizes_available:
                                availability = 'in_stock'
                                logger.info(f"Stok bulundu: {sizes_available}")
                            else:
                                logger.info("Modal açıldı ama aktif beden bulunamadı.")
                                
                    except Exception as e:
                        logger.warning(f"Ekleme işlemi sırasında hata (Muhtemelen buton yok): {e}")
                        # Buton bulunamazsa veya tıklanamazsa stok yok sayıyoruz
                        availability = 'out_of_stock'

                await browser.close()

                return {
                    'id': product_id,
                    'url': url,
                    'name': name,
                    'price': price_text,
                    'availability': availability,
                    'sizes': sizes_available,
                    'last_check': datetime.now().isoformat()
                }

            except Exception as e:
                logger.error(f"Playwright genel hatası: {e}")
                return None

# Yetki kontrolü
def check_authorization(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if ALLOWED_USERS and user_id not in ALLOWED_USERS and ALLOWED_USERS != ['']:
            await update.message.reply_text(f"⛔ Yetkiniz yok. ID: {user_id}")
            return
        return await func(update, context)
    return wrapper

@check_authorization
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 *Zara Stok Botu (Gelişmiş Versiyon)*\n\n"
        "Komutlar:\n"
        "• /add - Link göndererek ekle\n"
        "• /list - Listele\n"
        "• /remove - Sil\n"
        "• /check - Manuel Kontrol\n"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

@check_authorization
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Zara linkini yapıştırın, gerisini bot halleder.")

@check_authorization
async def add_product_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔗 Zara ürün linkini gönderin:")

@check_authorization
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if 'zara.com' not in url:
        await update.message.reply_text("❌ Sadece Zara linkleri!")
        return
    
    status_msg = await update.message.reply_text("🔍 Gelişmiş tarama yapılıyor (Ekle butonuna tıklanıyor)...")
    
    checker = ZaraStockChecker()
    product_info = await checker.get_product_info(url)
    
    if not product_info:
        await status_msg.edit_text("❌ Ürün bilgileri alınamadı.")
        return
    
    user_id = str(update.effective_user.id)
    product_key = f"{user_id}_{product_info['id']}"
    
    tracked_products[product_key] = {
        **product_info,
        'user_id': user_id,
        'chat_id': update.effective_chat.id,
        'added_at': datetime.now().isoformat()
    }
    
    stock_emoji = "✅" if product_info['availability'] == 'in_stock' else "❌"
    sizes_text = ", ".join(product_info['sizes']) if product_info['sizes'] else "Yok"
    
    response = (
        f"✨ *Takibe Alındı*\n"
        f"📦 {product_info['name']}\n"
        f"💰 {product_info['price']}\n"
        f"{stock_emoji} Durum: {sizes_text}"
    )
    await status_msg.edit_text(response, parse_mode='Markdown')

@check_authorization
async def list_products_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not user_products:
        await update.message.reply_text("📭 Listeniz boş.")
        return
    
    response = "🛍️ *Listeniz:*\n\n"
    for i, (key, p) in enumerate(user_products.items(), 1):
        stock = "✅" if p['availability'] == 'in_stock' else "❌"
        sizes = ", ".join(p['sizes']) if p['sizes'] else "Tükendi"
        response += f"{i}. {p['name']}\n   {stock} {sizes}\n   🔗 [Link]({p['url']})\n\n"
    
    await update.message.reply_text(response, parse_mode='Markdown', disable_web_page_preview=True)

@check_authorization
async def remove_product_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not user_products:
        await update.message.reply_text("📭 Silinecek ürün yok.")
        return
    
    keyboard = []
    for key, product in user_products.items():
        keyboard.append([InlineKeyboardButton(f"🗑️ {product['name'][:20]}...", callback_data=f"remove_{key}")])
    keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel")])
    
    await update.message.reply_text("Silmek istediğinizi seçin:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("İptal edildi.")
        return
    
    if query.data.startswith("remove_"):
        key = query.data.replace("remove_", "")
        if key in tracked_products:
            del tracked_products[key]
            await query.edit_message_text("✅ Ürün silindi.")
        else:
            await query.edit_message_text("❌ Ürün zaten yok.")

@check_authorization
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not user_products:
        await update.message.reply_text("📭 Ürün yok.")
        return
    
    status_msg = await update.message.reply_text(f"🔍 {len(user_products)} ürün taranıyor...")
    checker = ZaraStockChecker()
    results = []
    
    for key, product in user_products.items():
        new_info = await checker.get_product_info(product['url'])
        if new_info:
            tracked_products[key].update(new_info)
            sizes = ", ".join(new_info['sizes']) if new_info['sizes'] else "Yok"
            emoji = "✅" if new_info['availability'] == 'in_stock' else "❌"
            results.append(f"{emoji} {product['name'][:15]}...: {sizes}")
        await asyncio.sleep(2)
    
    await status_msg.edit_text("\n".join(results), parse_mode='Markdown')

async def periodic_check(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    checker = ZaraStockChecker()
    
    for key, product in list(tracked_products.items()):
        try:
            new_info = await checker.get_product_info(product['url'])
            if not new_info: continue
            
            # Eğer önceden stok yoktuysa VE şimdi stok varsa (veya yeni beden geldiyse)
            old_availability = product['availability']
            new_availability = new_info['availability']
            old_sizes = set(product.get('sizes', []))
            new_sizes = set(new_info.get('sizes', []))
            
            # Stok durumu değiştiyse veya yeni bir beden eklendiyse haber ver
            if (old_availability != 'in_stock' and new_availability == 'in_stock') or \
               (new_availability == 'in_stock' and not new_sizes.issubset(old_sizes)):
                
                msg = (
                    "🚨 *STOK ALARMI!* 🚨\n\n"
                    f"📦 {new_info['name']}\n"
                    f"💰 {new_info['price']}\n"
                    f"✅ *Mevcut Bedenler:* {', '.join(new_info['sizes'])}\n\n"
                    f"🔗 [Hemen Al]({product['url']})"
                )
                await context.bot.send_message(chat_id=product['chat_id'], text=msg, parse_mode='Markdown')
            
            tracked_products[key].update(new_info)
        except Exception as e:
            logger.error(f"Döngü hatası: {e}")
        await asyncio.sleep(5)

def main():
    if not TELEGRAM_TOKEN:
        print("Lütfen script içindeki TELEGRAM_TOKEN alanını doldurun!")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_product_command))
    application.add_handler(CommandHandler("list", list_products_command))
    application.add_handler(CommandHandler("remove", remove_product_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'zara\.com'), handle_url))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    if application.job_queue:
        application.job_queue.run_repeating(periodic_check, interval=CHECK_INTERVAL, first=10)
    
    print("Bot çalışıyor...")
    application.run_polling()

if __name__ == '__main__':
    main()
