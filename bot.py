import os
import logging
import asyncio
from datetime import datetime
from typing import Dict

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# ==========================================
# AYARLAR
# ==========================================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'TOKEN_BURAYA') 
ALLOWED_USERS = os.getenv('ALLOWED_USERS', '').split(',') 
CHECK_INTERVAL = 300  

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

tracked_products: Dict[str, Dict] = {}

def get_driver():
    chrome_options = Options()
    
    # --- DONMAYI ENGELLEYEN AYARLAR ---
    chrome_options.page_load_strategy = 'eager'  # Sayfanın tamamen bitmesini bekleme (HIZLI MOD)
    
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox") 
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080") # Docker için ekran boyutu ŞART
    chrome_options.add_argument("--disable-gpu")
    
    # Resimleri kapat
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)
    
    # Anti-Bot
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    return webdriver.Chrome(options=chrome_options)

async def check_zara_stock_selenium(url: str, context: ContextTypes.DEFAULT_TYPE = None, chat_id=None):
    """
    Stok kontrolü yapar. Hata alırsa ekran görüntüsü atar.
    """
    driver = None
    result = {
        'status': 'error',
        'name': 'Zara Ürünü',
        'price': '?',
        'sizes': [],
        'availability': 'out_of_stock'
    }

    try:
        logger.info(f"Tarayıcı başlatılıyor: {url}")
        
        # Driver'ı asenkron olmayan bir blokta başlatıyoruz (loop içinde çalıştığı için)
        driver = get_driver()
        wait = WebDriverWait(driver, 10) # Maksimum 10 saniye bekle

        driver.get(url)
        
        # Sayfanın hafifçe oturması için kısa bekleme
        await asyncio.sleep(2)

        # 1. İsim ve Fiyat
        try:
            result['name'] = driver.find_element(By.TAG_NAME, "h1").text
            result['price'] = driver.find_element(By.CSS_SELECTOR, ".price-current__amount, .money-amount").text
        except:
            pass

        # 2. Tükendi Kontrolü
        if len(driver.find_elements(By.XPATH, "//button[@data-qa-action='show-similar-products']")) > 0:
            result['status'] = 'success'
            result['availability'] = 'out_of_stock'
            return result

        # 3. Ekle Butonu ve Modal
        try:
            # Butonu bul
            add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
            driver.execute_script("arguments[0].click();", add_btn)
            
            # Modal açılmasını bekle
            wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
            
            # Bedenleri oku
            size_items = driver.find_elements(By.CSS_SELECTOR, "li.size-selector-list__item")
            available_sizes = []
            
            for item in size_items:
                try:
                    classes = item.get_attribute("class")
                    if "is-disabled" not in classes and "out-of-stock" not in classes:
                        txt = item.find_element(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']").text
                        available_sizes.append(txt)
                except:
                    continue

            result['sizes'] = available_sizes
            result['availability'] = 'in_stock' if available_sizes else 'out_of_stock'
            result['status'] = 'success'

        except TimeoutException:
            # Ekle butonu yoksa veya modal açılmadıysa
            logger.warning("Zaman aşımı.")
            result['status'] = 'success' 
            # Hata ekran görüntüsü al (Opsiyonel: debug için)
            # driver.save_screenshot("/app/debug_timeout.png")

    except Exception as e:
        logger.error(f"Kritik Hata: {e}")
        
        # HATA DURUMUNDA FOTOĞRAF ÇEK VE YOLLA
        if driver and context and chat_id:
            try:
                filename = f"error_{datetime.now().timestamp()}.png"
                driver.save_screenshot(filename)
                await context.bot.send_photo(chat_id=chat_id, photo=open(filename, 'rb'), caption=f"❌ Hata Aldım: {str(e)[:100]}")
                os.remove(filename)
            except:
                pass
                
    finally:
        if driver:
            driver.quit()
        return result

# ==========================================
# TELEGRAM FONKSİYONLARI
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot Hazır. Link gönder.")

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Sadece Zara linki.")
        return

    msg = await update.message.reply_text("⏳ Kontrol ediliyor (Lütfen bekleyin)...")
    
    # İşlemi thread içinde çalıştır
    loop = asyncio.get_running_loop()
    
    # check_zara_selenium fonksiyonunu wrapper ile çağırıyoruz çünkü async
    def run_sync():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Burası biraz karmaşık çünkü Selenium senkron, Telegram asenkron.
        # Basitleştirmek için burada doğrudan fonksiyonu çağırmıyoruz, 
        # yukarıdaki check_zara_stock_selenium'u direkt await ile çağıracağız.
        pass

    # Docker içinde Selenium'u bloklamadan çalıştırmak için en temiz yöntem:
    # Fonksiyonu direkt await et, ama driver oluşturmayı optimize ettik.
    data = await check_zara_stock_selenium(url, context, update.effective_chat.id)
    
    if data['status'] == 'error':
        await msg.edit_text("❌ İşlem başarısız oldu (Hata fotosu gönderildiyse kontrol et).")
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
    
    await msg.edit_text(f"✅ *Eklendi*\n📦 {data['name']}\n{icon} Durum: {sizes}", parse_mode='Markdown')

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    if not my_products:
        await update.message.reply_text("📭 Liste boş.")
        return

    text = "📋 *Liste:*\n"
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
    for key, product in list(tracked_products.items()):
        try:
            data = await check_zara_stock_selenium(product['url'])
            if data['status'] == 'error': continue
            
            if product['last_status'] == 'out_of_stock' and data['availability'] == 'in_stock':
                await context.bot.send_message(
                    chat_id=product['chat_id'],
                    text=f"🚨 *STOK GELDİ!* \n📦 {data['name']}\n✅ {', '.join(data['sizes'])}\n🔗 {product['url']}",
                    parse_mode='Markdown'
                )
            tracked_products[key]['last_status'] = data['availability']
        except:
            pass
        await asyncio.sleep(5)

if __name__ == '__main__':
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("zara.com"), add_url))
    app.add_handler(CallbackQueryHandler(callback_handler))
    if app.job_queue:
        app.job_queue.run_repeating(periodic_check_job, interval=CHECK_INTERVAL, first=10)
    print("Bot başlatıldı...")
    app.run_polling()
