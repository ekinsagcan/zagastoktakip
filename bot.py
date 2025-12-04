import os
import logging
import asyncio
import time
from datetime import datetime
from typing import Dict

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# --- AYARLAR ---
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHECK_INTERVAL = 300 

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

tracked_products: Dict[str, Dict] = {}

def get_driver():
    chrome_options = Options()
    chrome_options.page_load_strategy = 'eager' 
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Resimleri artık kapatmıyoruz çünkü ürün fotosu alacağız!
    # prefs = {"profile.managed_default_content_settings.images": 2} 
    # chrome_options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=chrome_options)

async def check_stock_selenium(url: str, context: ContextTypes.DEFAULT_TYPE = None, chat_id=None):
    driver = None
    result = {
        'status': 'error',
        'name': 'Zara Ürünü',
        'availability': 'out_of_stock',
        'sizes': [],
        'image': None, # Ürün resmi için
        'price': ''
    }

    try:
        loop = asyncio.get_running_loop()
        
        def sync_process():
            inner_driver = get_driver()
            try:
                inner_driver.get(url)
                wait = WebDriverWait(inner_driver, 15)
                time.sleep(2)

                # --- 0. KONUM ---
                try:
                    geo_btn = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-qa-action='stay-in-store']")))
                    time.sleep(3) 
                    inner_driver.execute_script("arguments[0].click();", geo_btn)
                    time.sleep(2)
                except: pass

                # --- 1. ÇEREZ ---
                try:
                    cookie = inner_driver.find_element(By.ID, "onetrust-accept-btn-handler")
                    inner_driver.execute_script("arguments[0].click();", cookie)
                except: pass

                # --- VERİ ÇEKME (İsim, Fiyat, Resim) ---
                try:
                    result['name'] = inner_driver.find_element(By.TAG_NAME, "h1").text
                except: pass

                try:
                    # Fiyatı çekmeye çalışalım
                    price_el = inner_driver.find_element(By.CSS_SELECTOR, ".price-current__amount, .money-amount")
                    result['price'] = price_el.text
                except: pass

                try:
                    # Ürün görselini al (İlk resim)
                    # Zara genelde picture tag veya img kullanır
                    img_el = inner_driver.find_element(By.XPATH, "//ul[contains(@class,'product-detail-images')]//img")
                    result['image'] = img_el.get_attribute("src")
                except: pass

                # --- 2. EKLE BUTONU ---
                try:
                    add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                    inner_driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
                    time.sleep(1)
                    inner_driver.execute_script("arguments[0].click();", add_btn)
                    
                    # Modal bekle
                    wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
                    time.sleep(2) 
                    
                    # --- 3. AKILLI BEDEN TARAMA ---
                    labels = inner_driver.find_elements(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']")
                    available_sizes = []
                    
                    for label in labels:
                        try:
                            txt = label.text.strip()
                            if not txt: continue
                            
                            is_disabled = inner_driver.execute_script("""
                                var el = arguments[0];
                                var parent = el.closest('li') || el.closest('button');
                                if (!parent) return false;
                                var classes = parent.className;
                                return classes.includes('is-disabled') || classes.includes('out-of-stock') || parent.hasAttribute('disabled');
                            """, label)
                            
                            if not is_disabled:
                                available_sizes.append(txt)
                        except: continue
                    
                    result['sizes'] = available_sizes
                    result['availability'] = 'in_stock' if available_sizes else 'out_of_stock'
                    result['status'] = 'success'
                    
                except TimeoutException:
                    result['status'] = 'success' 
            
            except Exception as e:
                logger.error(f"İç Hata: {e}")
            finally:
                inner_driver.quit()
            return result

        final_data = await loop.run_in_executor(None, sync_process)
        return final_data

    except Exception as e:
        logger.error(f"Genel Hata: {e}")
        return result

# --- TELEGRAM BOT KOMUTLARI ---

async def set_commands(application: Application):
    """Bot komut menüsünü ayarlar"""
    commands = [
        BotCommand("start", "Botu başlat"),
        BotCommand("add", "Yeni ürün ekle (Link ile)"),
        BotCommand("list", "Takip listem"),
        BotCommand("help", "Yardım")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    msg = (
        f"👋 *Merhaba {user}!*\n\n"
        "🛍️ *Zara Stok Takip Botuna* hoş geldin.\n"
        "İstediğin ürünün linkini bana gönder, senin için sürekli kontrol edeyim.\n\n"
        "👇 *Başlamak için bir link yapıştır!*"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Lütfen geçerli bir *Zara* linki gönderin.", parse_mode=ParseMode.MARKDOWN)
        return

    msg = await update.message.reply_text("🔎 *Ürün taranıyor ve fotoğrafı alınıyor...*", parse_mode=ParseMode.MARKDOWN)
    
    data = await check_stock_selenium(url, context, update.effective_chat.id)
    
    if data['status'] == 'error':
        await msg.edit_text("⚠️ Üzgünüm, şu an siteye erişemiyorum. Lütfen biraz sonra tekrar dene.")
        return

    # Veritabanına kaydet
    key = f"{update.effective_user.id}_{datetime.now().timestamp()}"
    tracked_products[key] = {
        'url': url,
        'name': data['name'],
        'price': data['price'],
        'image': data['image'],
        'last_status': data['availability'],
        'chat_id': update.effective_chat.id,
        'user_id': str(update.effective_user.id)
    }
    
    # --- GÜZEL ARAYÜZ ---
    await msg.delete() # "Taranıyor" mesajını sil

    status_text = "🟢 *STOKTA VAR*" if data['availability'] == 'in_stock' else "🔴 *TÜKENDİ*"
    sizes_text = ", ".join(data['sizes']) if data['sizes'] else "Stok yok"
    
    caption = (
        f"✅ *TAKİBE ALINDI*\n\n"
        f"👗 *{data['name']}*\n"
        f"🏷️ Fiyat: `{data['price']}`\n"
        f"📊 Durum: {status_text}\n"
        f"📏 Bedenler: `{sizes_text}`\n\n"
        f"🔔 _Stok durumu değiştiğinde sana haber vereceğim._"
    )

    # Butonlar
    keyboard = [
        [InlineKeyboardButton("🔗 Ürüne Git", url=url)],
        [InlineKeyboardButton("🗑️ Takibi Bırak", callback_data=f"del_{key}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if data['image']:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=data['image'],
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=caption,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not my_products:
        await update.message.reply_text("📭 Takip listen bomboş.", parse_mode=ParseMode.MARKDOWN)
        return

    await update.message.reply_text(f"📋 *Takip Listen ({len(my_products)} Ürün)*", parse_mode=ParseMode.MARKDOWN)

    for k, v in my_products.items():
        icon = "🟢" if v['last_status'] == 'in_stock' else "🔴"
        text = f"{icon} *{v['name']}*\n🔗 [Link]({v['url']})"
        
        keyboard = [[InlineKeyboardButton("🗑️ Sil", callback_data=f"del_{k}")]]
        
        await update.message.reply_text(
            text, 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("del_"):
        key = query.data.replace("del_", "")
        if key in tracked_products: 
            product_name = tracked_products[key]['name']
            del tracked_products[key]
            await query.edit_message_text(f"🗑️ *{product_name}* silindi.", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text("❌ Ürün zaten silinmiş.")

# --- BİLDİRİM GÖNDERİMİ (EN ÖNEMLİ KISIM) ---
async def check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    for key, product in list(tracked_products.items()):
        try:
            data = await check_stock_selenium(product['url'])
            if data['status'] == 'error': continue
            
            # STOK GELDİ Mİ?
            if product['last_status'] == 'out_of_stock' and data['availability'] == 'in_stock':
                
                caption = (
                    f"🚨🚨 *STOK GELDİ! KOŞ!* 🚨🚨\n\n"
                    f"👗 *{data['name']}*\n"
                    f"📏 *Mevcut Bedenler:* `{', '.join(data['sizes'])}`\n"
                    f"🏷️ Fiyat: `{product.get('price', '-')}`\n\n"
                    f"👇 *HEMEN AL BUTONUNA BAS!*"
                )
                
                keyboard = [[InlineKeyboardButton("🛒 SATIN AL", url=product['url'])]]
                
                # Resim varsa resimli at, yoksa mesaj at
                if product.get('image'):
                    await context.bot.send_photo(
                        chat_id=product['chat_id'],
                        photo=product['image'],
                        caption=caption,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    await context.bot.send_message(
                        chat_id=product['chat_id'],
                        text=caption,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
            
            # Durumu güncelle
            tracked_products[key]['last_status'] = data['availability']
            await asyncio.sleep(5)
        except: pass

async def post_init(application: Application):
    await set_commands(application)

if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(CommandHandler("add", add_product)) # /add komutu da çalışsın
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("zara.com"), add_product))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    if app.job_queue:
        app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=10)
    
    print("Bot Başladı (UI Versiyonu)...")
    app.run_polling()
