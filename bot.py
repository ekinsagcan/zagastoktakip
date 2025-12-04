import os
import logging
import asyncio
from datetime import datetime
from typing import Dict

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
from selenium.common.exceptions import TimeoutException

# --- AYARLAR ---
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHECK_INTERVAL = 300  # 5 Dakika (Saniye cinsinden)

# Loglama Ayarları
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Veritabanı (Bellekte tutulur, bot kapanırsa sıfırlanır)
tracked_products: Dict[str, Dict] = {}

# --- SELENIUM MOTORU ---
def get_driver():
    """Docker uyumlu, hızlı ve gizli Chrome sürücüsü oluşturur."""
    chrome_options = Options()
    
    # Hız ve Performans Ayarları
    chrome_options.page_load_strategy = 'eager'  # Sayfanın %100 yüklenmesini beklemez (Hızlandırır)
    chrome_options.add_argument("--headless=new") # Penceresiz mod
    
    # Docker İçin Kritik Ayarlar (Bunlar olmazsa çöker)
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Anti-Bot Tespiti Engelleme
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Gereksizleri Kapat (Resimler vb.)
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)
    
    return webdriver.Chrome(options=chrome_options)

async def check_stock_selenium(url: str):
    """Siteye girer, Ekle butonuna tıklar ve Bedenleri okur."""
    driver = None
    result = {
        'status': 'error',
        'name': 'Zara Ürünü',
        'availability': 'out_of_stock',
        'sizes': []
    }

    try:
        # Selenium senkron olduğu için loop içinde bloklamadan çalıştırıyoruz
        loop = asyncio.get_running_loop()
        
        def sync_process():
            inner_driver = get_driver()
            try:
                logger.info(f"🔍 Kontrol ediliyor: {url}")
                inner_driver.get(url)
                wait = WebDriverWait(inner_driver, 10)

                # 1. Ürün Adı Alma
                try:
                    result['name'] = inner_driver.find_element(By.TAG_NAME, "h1").text
                except: pass

                # 2. 'Tükendi' Kontrolü (Benzer Ürünler Butonu)
                if len(inner_driver.find_elements(By.XPATH, "//button[@data-qa-action='show-similar-products']")) > 0:
                    logger.info("❌ Ürün tamamen tükenmiş.")
                    result['status'] = 'success'
                    return result

                # 3. 'Ekle' Butonuna Tıklama
                try:
                    add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                    inner_driver.execute_script("arguments[0].click();", add_btn)
                    
                    # 4. Beden Penceresini Bekleme (Kritik Nokta)
                    wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
                    
                    # 5. Bedenleri Okuma
                    size_items = inner_driver.find_elements(By.CSS_SELECTOR, "li.size-selector-list__item")
                    available_sizes = []
                    
                    for item in size_items:
                        try:
                            # Class kontrolü: is-disabled veya out-of-stock değilse stoktadır
                            classes = item.get_attribute("class")
                            if "is-disabled" not in classes and "out-of-stock" not in classes:
                                txt = item.find_element(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']").text
                                available_sizes.append(txt)
                        except: continue
                    
                    result['sizes'] = available_sizes
                    if available_sizes:
                        result['availability'] = 'in_stock'
                    
                    result['status'] = 'success'
                    
                except TimeoutException:
                    logger.warning("⚠️ Ekle butonu bulunamadı veya pencere açılmadı.")
                    result['status'] = 'success' # Hata değil, stok yok varsayıyoruz
            
            finally:
                inner_driver.quit()
            return result

        # İşlemi thread havuzunda çalıştır
        return await loop.run_in_executor(None, sync_process)

    except Exception as e:
        logger.error(f"Sistem Hatası: {e}")
        return result

# --- TELEGRAM BOT KOMUTLARI ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Zara Stok Takip Botu*\n\n"
        "Link göndererek takibe başlayabilirsin.\n"
        "Her 5 dakikada bir kontrol edilir.",
        parse_mode='Markdown'
    )

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Sadece Zara linki kabul edilir.")
        return

    msg = await update.message.reply_text("⏳ Kontrol ediliyor, lütfen bekleyin...")
    
    data = await check_stock_selenium(url)
    
    if data['status'] == 'error':
        await msg.edit_text("❌ Siteye erişim hatası. Daha sonra tekrar dene.")
        return

    # Ürünü kaydet
    user_id = str(update.effective_user.id)
    key = f"{user_id}_{datetime.now().timestamp()}"
    
    tracked_products[key] = {
        'url': url,
        'name': data['name'],
        'last_status': data['availability'],
        'chat_id': update.effective_chat.id,
        'user_id': user_id
    }
    
    status_icon = "✅" if data['availability'] == 'in_stock' else "🔴"
    sizes_str = ", ".join(data['sizes']) if data['sizes'] else "Tükendi"
    
    await msg.edit_text(
        f"✅ *Listeye Eklendi*\n\n"
        f"📦 {data['name']}\n"
        f"{status_icon} Durum: {sizes_str}",
        parse_mode='Markdown'
    )

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not my_products:
        await update.message.reply_text("📭 Takip listeniz boş.")
        return

    keyboard = []
    text = "📋 *Takip Listesi:*\n"
    for k, v in my_products.items():
        icon = "✅" if v['last_status'] == 'in_stock' else "🔴"
        text += f"{icon} {v['name']}\n"
        keyboard.append([InlineKeyboardButton(f"🗑 Sil: {v['name'][:15]}", callback_data=f"del_{k}")])
    
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("del_"):
        key = query.data.replace("del_", "")
        if key in tracked_products:
            del tracked_products[key]
            await query.edit_message_text("🗑 Ürün silindi.")
        else:
            await query.edit_message_text("❌ Ürün zaten silinmiş.")

# --- PERİYODİK KONTROL GÖREVİ ---
async def check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    
    logger.info(f"🔄 Periyodik kontrol: {len(tracked_products)} ürün taranıyor...")
    
    # Sözlük üzerinde dönerken hata almamak için kopyasını alıyoruz
    for key, product in list(tracked_products.items()):
        try:
            data = await check_stock_selenium(product['url'])
            
            if data['status'] == 'error': continue
            
            old_status = product['last_status']
            new_status = data['availability']
            
            # Eğer önceden stok yoktuysa VE şimdi stok geldiyse -> BİLDİRİM AT
            if old_status == 'out_of_stock' and new_status == 'in_stock':
                sizes_text = ", ".join(data['sizes'])
                await context.bot.send_message(
                    chat_id=product['chat_id'],
                    text=f"🚨 *STOK GELDİ! KOŞ!* 🚨\n\n"
                         f"📦 {data['name']}\n"
                         f"✅ Bedenler: {sizes_text}\n"
                         f"🔗 [Satın Al]({product['url']})",
                    parse_mode='Markdown'
                )
            
            # Durumu güncelle
            tracked_products[key]['last_status'] = new_status
            
            # Sitelere ardışık yüklenmemek için bekle
            await asyncio.sleep(5)
            
        except Exception as e:
            logger.error(f"Job Hatası: {e}")

# --- ANA ÇALIŞTIRMA ---
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("❌ HATA: TELEGRAM_BOT_TOKEN bulunamadı!")
        exit()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("zara.com"), add_product))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    if app.job_queue:
        app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=10)
    
    print("✅ Bot Docker üzerinde başlatıldı...")
    app.run_polling()
