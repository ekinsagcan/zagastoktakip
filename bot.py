import os
import logging
import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional

# Telegram Kütüphaneleri
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Selenium Kütüphaneleri
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ==========================================
# AYARLAR (Token buraya)
# ==========================================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'TOKEN_BURAYA_YAZ') 
ALLOWED_USERS = os.getenv('ALLOWED_USERS', '').split(',') 
CHECK_INTERVAL = 300  # 5 dakika

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

tracked_products: Dict[str, Dict] = {}

# ==========================================
# OPTİMİZE EDİLMİŞ SELENIUM MOTORU
# ==========================================
def check_zara_stock_selenium(url: str):
    """
    Resimleri yüklemeden siteye girer, stok kontrolü yapar.
    """
    chrome_options = Options()
    
    # --- HIZLANDIRMA AYARLARI ---
    # 1. Tarayıcıyı gösterme (Arka planda çalışsın)
    chrome_options.add_argument("--headless=new") 
    
    # 2. Resimleri yükleme (Büyük hız artışı sağlar)
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)
    
    # 3. Anti-Bot Ayarları (Siteye girebilmek için şart)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    chrome_options.add_argument("--start-maximized")
    
    driver = webdriver.Chrome(options=chrome_options)
    
    result = {
        'status': 'error',
        'name': 'Zara Ürünü',
        'price': '?',
        'sizes': [],
        'availability': 'out_of_stock'
    }

    try:
        logger.info(f"Siteye gidiliyor (Resimsiz): {url}")
        driver.get(url)
        wait = WebDriverWait(driver, 10)

        # 1. ADIM: İsim ve Fiyat (Hızlıca al, hata verirse geç)
        try:
            result['name'] = driver.find_element(By.TAG_NAME, "h1").text
            result['price'] = driver.find_element(By.CSS_SELECTOR, ".price-current__amount, .money-amount").text
        except:
            pass

        # 2. ADIM: TÜKENDİ Mİ? (Show Similar Products)
        # Bu buton varsa ürün tamamen bitmiştir.
        sold_out_btns = driver.find_elements(By.XPATH, "//button[@data-qa-action='show-similar-products']")
        if sold_out_btns:
            logger.info("Durum: TÜKENDİ (Buton görüldü)")
            result['status'] = 'success'
            result['availability'] = 'out_of_stock'
            return result

        # 3. ADIM: EKLE BUTONUNA TIKLA
        try:
            # Ekle butonunu bul
            add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
            
            # Tıkla (JavaScript ile tıklamak daha garantidir)
            driver.execute_script("arguments[0].click();", add_btn)
            
            # 4. ADIM: BEDEN LİSTESİNİ BEKLE
            # Bedenlerin olduğu kutu görünür olana kadar bekle
            wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
            
            # Bedenleri topla
            size_items = driver.find_elements(By.CSS_SELECTOR, "li.size-selector-list__item")
            available_sizes = []
            
            for item in size_items:
                try:
                    # 'is-disabled' veya 'out-of-stock' class'ı YOKSA stoktadır.
                    classes = item.get_attribute("class")
                    if "is-disabled" not in classes and "out-of-stock" not in classes:
                        text_elem = item.find_element(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']")
                        available_sizes.append(text_elem.text)
                except:
                    continue

            result['sizes'] = available_sizes
            if available_sizes:
                result['availability'] = 'in_stock'
            
            result['status'] = 'success'
            logger.info(f"Stok bulundu: {available_sizes}")

        except TimeoutException:
            # Ekle butonu gelmediyse veya beden penceresi açılmadıysa
            logger.warning("Zaman aşımı (Stok yok veya sayfa yüklenemedi)")
            result['status'] = 'success' # Hata değil, sadece stok yok varsayıyoruz
            
    except Exception as e:
        logger.error(f"Hata: {e}")
    
    finally:
        driver.quit()
        return result

# ==========================================
# TELEGRAM KISMI
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Zara Bot Hazır!\nLink atarak başla.")

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Sadece Zara linki!")
        return

    msg = await update.message.reply_text("⏳ Kontrol ediliyor (Resimler kapalı, hızlı mod)...")
    
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, check_zara_stock_selenium, url)
    
    if data['status'] == 'error':
        await msg.edit_text("❌ Bir hata oluştu.")
        return

    user_id = str(update.effective_user.id)
    key = f"{user_id}_{datetime.now().timestamp()}"
    
    tracked_products[key] = {
        'url': url,
        'name': data['name'],
        'price': data['price'],
        'last_status': data['availability'],
        'user_id': user_id,
        'chat_id': update.effective_chat.id
    }
    
    icon = "✅" if data['availability'] == 'in_stock' else "❌"
    sizes = ", ".join(data['sizes']) if data['sizes'] else "Tükendi"
    
    await msg.edit_text(
        f"✅ *Eklendi*\n📦 {data['name']}\n💰 {data['price']}\n{icon} Durum: {sizes}",
        parse_mode='Markdown'
    )

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not my_products:
        await update.message.reply_text("📭 Liste boş.")
        return

    text = "📋 *Takip Listesi:*\n"
    keyboard = []
    for k, p in my_products.items():
        st = "✅" if p['last_status'] == 'in_stock' else "❌"
        text += f"{st} {p['name']}\n"
        keyboard.append([InlineKeyboardButton(f"Sil: {p['name'][:10]}", callback_data=f"del_{k}")])
        
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("del_"):
        key = query.data.replace("del_", "")
        if key in tracked_products:
            del tracked_products[key]
            await query.edit_message_text("🗑 Silindi.")

async def periodic_check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    
    loop = asyncio.get_running_loop()
    
    for key, product in list(tracked_products.items()):
        try:
            data = await loop.run_in_executor(None, check_zara_stock_selenium, product['url'])
            
            if data['status'] == 'error': continue
            
            old_status = product['last_status']
            new_status = data['availability']
            
            # Stok geldiyse bildirim at
            if old_status == 'out_of_stock' and new_status == 'in_stock':
                sizes = ", ".join(data['sizes'])
                await context.bot.send_message(
                    chat_id=product['chat_id'],
                    text=f"🚨 *STOK GELDİ!* 🚨\n📦 {data['name']}\n✅ Bedenler: {sizes}\n🔗 [Link]({product['url']})",
                    parse_mode='Markdown'
                )
            
            tracked_products[key]['last_status'] = new_status
            
        except Exception as e:
            logger.error(f"Döngü hatası: {e}")
        
        await asyncio.sleep(5)

if __name__ == '__main__':
    if TELEGRAM_TOKEN == 'TOKEN_BURAYA_YAZ':
        print("Token girmeyi unutma!")
        exit()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("zara.com"), add_url))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    if app.job_queue:
        app.job_queue.run_repeating(periodic_check_job, interval=CHECK_INTERVAL, first=10)
    
    print("Bot çalışıyor (Selenium - Resimsiz Mod)...")
    app.run_polling()
