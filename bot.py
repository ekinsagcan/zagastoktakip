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

# Selenium Kütüphaneleri (Senin çalışan altyapın)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ==========================================
# AYARLAR (Token'ı buraya yaz)
# ==========================================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'TOKEN_BURAYA_YAZ') 
ALLOWED_USERS = os.getenv('ALLOWED_USERS', '').split(',') 
CHECK_INTERVAL = 300  # 5 dakika (Saniye cinsinden)

# Loglama
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Veritabanı (Bellekte)
tracked_products: Dict[str, Dict] = {}

# ==========================================
# SELENIUM MOTORU (Önceki Çalışan Kod)
# ==========================================
def create_driver():
    """Anti-detect özellikli driver oluşturur"""
    chrome_options = Options()
    # Bot olduğunu gizleyen kritik ayarlar
    chrome_options.add_argument("--headless=new") # Arka planda çalışması için (Test ederken bunu kaldırabilirsin)
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    driver = webdriver.Chrome(options=chrome_options)
    
    # WebDriver izlerini sil
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver

def check_zara_stock_selenium(url: str, target_size: str = "TÜMÜ"):
    """
    Selenium kullanarak siteye girer, QA etiketleri ile kontrol yapar.
    """
    driver = create_driver()
    result = {
        'status': 'error',
        'name': 'Bilinmiyor',
        'price': '?',
        'sizes': [],
        'availability': 'out_of_stock'
    }

    try:
        logger.info(f"Siteye gidiliyor: {url}")
        driver.get(url)
        
        # Sayfanın yüklenmesi için bekleme
        wait = WebDriverWait(driver, 15)
        
        # 1. Ürün Adı ve Fiyat (Bilgi amaçlı)
        try:
            name_elem = driver.find_element(By.TAG_NAME, "h1")
            result['name'] = name_elem.text
            
            price_elem = driver.find_element(By.CSS_SELECTOR, ".price-current__amount, .money-amount")
            result['price'] = price_elem.text
        except:
            pass

        # 2. ADIM: TÜKENDİ Mİ? (Show Similar Products)
        try:
            sold_out_btn = driver.find_elements(By.XPATH, "//button[@data-qa-action='show-similar-products']")
            if len(sold_out_btn) > 0:
                logger.info("Selenium: Ürün tamamen tükenmiş.")
                result['status'] = 'success'
                result['availability'] = 'out_of_stock'
                return result
        except:
            pass

        # 3. ADIM: EKLE BUTONUNA TIKLA
        try:
            add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
            driver.execute_script("arguments[0].click();", add_btn) # JS click daha güvenilirdir
            logger.info("Selenium: Ekle butonuna tıklandı.")
        except TimeoutException:
            logger.warning("Selenium: Ekle butonu bulunamadı.")
            return result

        # 4. ADIM: BEDEN MODALINI BEKLE VE OKU
        try:
            # Modalın içindeki beden listesinin görünür olmasını bekle
            wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
            
            # Tüm beden elementlerini bul (li tagleri içinde)
            size_items = driver.find_elements(By.CSS_SELECTOR, "li.size-selector-list__item")
            
            available_sizes = []
            
            for item in size_items:
                try:
                    # Beden ismini al
                    label = item.find_element(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']").text
                    
                    # Class kontrolü (disabled veya out-of-stock var mı?)
                    classes = item.get_attribute("class")
                    if "is-disabled" not in classes and "out-of-stock" not in classes:
                        available_sizes.append(label)
                except:
                    continue
            
            result['sizes'] = available_sizes
            if available_sizes:
                result['availability'] = 'in_stock'
            
            result['status'] = 'success'
            logger.info(f"Selenium: Bulunan stoklar: {available_sizes}")

        except TimeoutException:
            logger.warning("Selenium: Beden penceresi açılmadı veya zaman aşımı.")
    
    except Exception as e:
        logger.error(f"Selenium Hatası: {e}")
    
    finally:
        driver.quit()
        return result

# ==========================================
# TELEGRAM BOT FONKSİYONLARI
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Zara Stok Botuna Hoşgeldin!\nLink göndererek takibe başlayabilirsin.")

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Lütfen geçerli bir Zara linki gönderin.")
        return

    status_msg = await update.message.reply_text("⏳ Tarayıcı başlatılıyor ve siteye giriliyor (bu işlem 10-15sn sürebilir)...")
    
    # Selenium'u bloklamadan çalıştırmak için run_in_executor kullanıyoruz
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, check_zara_stock_selenium, url)
    
    if data['status'] == 'error':
        await status_msg.edit_text("❌ Siteye erişirken hata oluştu. Daha sonra tekrar deneyin.")
        return

    # Ürünü kaydet
    user_id = str(update.effective_user.id)
    product_key = f"{user_id}_{datetime.now().timestamp()}" # Basit unique key
    
    tracked_products[product_key] = {
        'url': url,
        'name': data['name'],
        'price': data['price'],
        'last_status': data['availability'],
        'user_id': user_id,
        'chat_id': update.effective_chat.id
    }
    
    stock_emoji = "✅" if data['availability'] == 'in_stock' else "❌"
    sizes_str = ", ".join(data['sizes']) if data['sizes'] else "Yok"
    
    await status_msg.edit_text(
        f"✅ *Takibe Alındı!*\n\n"
        f"📦 {data['name']}\n"
        f"💰 {data['price']}\n"
        f"{stock_emoji} Durum: {sizes_str}\n\n"
        f"Her 5 dakikada bir kontrol edilecek.",
        parse_mode='Markdown'
    )

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not my_products:
        await update.message.reply_text("📭 Listeniz boş.")
        return

    text = "📋 *Takip Listesi:*\n"
    keyboard = []
    
    for key, p in my_products.items():
        text += f"- {p['name']} ({p['last_status']})\n"
        keyboard.append([InlineKeyboardButton(f"🗑 Sil: {p['name'][:15]}", callback_data=f"del_{key}")])
        
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("del_"):
        key = query.data.replace("del_", "")
        if key in tracked_products:
            del tracked_products[key]
            await query.edit_message_text("✅ Ürün silindi.")
        else:
            await query.edit_message_text("❌ Ürün zaten silinmiş.")

# ==========================================
# PERİYODİK KONTROL (ARKAPLAN GÖREVİ)
# ==========================================
async def periodic_check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products:
        return
    
    logger.info(f"🔄 Periyodik kontrol başladı: {len(tracked_products)} ürün.")
    
    loop = asyncio.get_running_loop()
    
    # Listeyi kopyala (loop sırasında dictionary değişirse hata almamak için)
    for key, product in list(tracked_products.items()):
        try:
            # Selenium işlemini ayrı thread'de çalıştır
            data = await loop.run_in_executor(None, check_zara_stock_selenium, product['url'])
            
            if data['status'] == 'error':
                continue
                
            old_status = product['last_status']
            new_status = data['availability']
            
            # Durum güncelle
            tracked_products[key]['last_status'] = new_status
            
            # Eğer ürün önceden yoktu ama şimdi geldiyse BİLDİRİM AT
            if old_status == 'out_of_stock' and new_status == 'in_stock':
                sizes_str = ", ".join(data['sizes'])
                msg = (
                    f"🚨 *STOK GELDİ!* 🚨\n\n"
                    f"📦 {data['name']}\n"
                    f"💰 {data['price']}\n"
                    f"✅ *Mevcut Bedenler:* {sizes_str}\n\n"
                    f"🔗 [Satın Al]({product['url']})"
                )
                await context.bot.send_message(chat_id=product['chat_id'], text=msg, parse_mode='Markdown')
                
        except Exception as e:
            logger.error(f"Döngü hatası: {e}")
        
        # Sitelere art arda istek atmamak için biraz bekle
        await asyncio.sleep(10)

# ==========================================
# ANA ÇALIŞTIRMA
# ==========================================
if __name__ == '__main__':
    if TELEGRAM_TOKEN == 'TOKEN_BURAYA_YAZ':
        print("Lütfen script dosyasını açıp TELEGRAM_TOKEN kısmına bot tokenınızı yazın!")
        exit()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Komutlar
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("zara.com"), add_url))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Periyodik Görev
    if app.job_queue:
        app.job_queue.run_repeating(periodic_check_job, interval=CHECK_INTERVAL, first=10)
    
    print("Bot başlatıldı (Selenium Modu)...")
    app.run_polling()
