import os
import logging
import asyncio
from datetime import datetime
from typing import Dict

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
)

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
    
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=chrome_options)

async def check_stock_selenium(url: str, context: ContextTypes.DEFAULT_TYPE = None, chat_id=None):
    driver = None
    result = {
        'status': 'error',
        'name': 'Zara Ürünü',
        'availability': 'out_of_stock',
        'sizes': [],
        'screenshot': None # Screenshot yolu
    }

    try:
        loop = asyncio.get_running_loop()
        
        def sync_process():
            inner_driver = get_driver()
            try:
                logger.info(f"🔍 Kontrol ediliyor: {url}")
                inner_driver.get(url)
                wait = WebDriverWait(inner_driver, 10)

                # Sayfanın yüklenmesi için kesin bekleme
                import time
                time.sleep(3) 

                # 1. Çerezleri Kapatmayı Dene
                try:
                    cookie = inner_driver.find_element(By.ID, "onetrust-accept-btn-handler")
                    cookie.click()
                except: pass

                # İsim Al
                try:
                    result['name'] = inner_driver.find_element(By.TAG_NAME, "h1").text
                except: pass

                # --- 2. ADIM: SADECE EKLE BUTONUNA ODAKLAN ---
                # "Tükendi" buton kontrolünü kaldırdık, yanıltıyor olabilir.
                
                try:
                    # Butonu bulmaya çalış
                    add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                    
                    # Görünür alana getir ve tıkla
                    inner_driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
                    time.sleep(1)
                    inner_driver.execute_script("arguments[0].click();", add_btn)
                    
                    # Modal bekle
                    wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
                    
                    # Bedenleri oku
                    size_items = inner_driver.find_elements(By.CSS_SELECTOR, "li.size-selector-list__item")
                    available_sizes = []
                    
                    for item in size_items:
                        try:
                            classes = item.get_attribute("class")
                            if "is-disabled" not in classes and "out-of-stock" not in classes:
                                txt = item.find_element(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']").text
                                available_sizes.append(txt)
                        except: continue
                    
                    result['sizes'] = available_sizes
                    result['availability'] = 'in_stock' if available_sizes else 'out_of_stock'
                    result['status'] = 'success'
                    
                except TimeoutException:
                    # Buton yoksa veya modal açılmadıysa
                    logger.warning("⚠️ Ekle butonu bulunamadı.")
                    result['status'] = 'success' # Hata değil, stok yok
            
            except Exception as e:
                logger.error(f"İç Hata: {e}")
            
            finally:
                # --- SONUÇ NE OLURSA OLSUN FOTOĞRAF ÇEK ---
                # Özellikle manuel kontrollerde (chat_id varsa)
                if chat_id:
                    screenshot_name = f"debug_{datetime.now().timestamp()}.png"
                    inner_driver.save_screenshot(screenshot_name)
                    result['screenshot'] = screenshot_name # Dosya adını kaydet
                
                inner_driver.quit()
            
            return result

        # Thread içinde çalıştır
        final_data = await loop.run_in_executor(None, sync_process)
        
        # Eğer screenshot alındıysa gönder
        if final_data['screenshot'] and os.path.exists(final_data['screenshot']) and context and chat_id:
            caption_text = "📸 Botun gördüğü ekran.\n"
            caption_text += "Durum: STOK VAR" if final_data['availability'] == 'in_stock' else "Durum: TÜKENDİ"
            
            await context.bot.send_photo(
                chat_id=chat_id, 
                photo=open(final_data['screenshot'], 'rb'),
                caption=caption_text
            )
            os.remove(final_data['screenshot']) # Temizlik

        return final_data

    except Exception as e:
        logger.error(f"Genel Hata: {e}")
        return result

# --- TELEGRAM BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Zara Bot. Link gönder.")

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Sadece Zara linki.")
        return

    msg = await update.message.reply_text("📸 Siteye giriliyor, fotoğraf çekilip gönderilecek...")
    
    # Check fonksiyonu artık fotoğrafı kendisi gönderiyor
    data = await check_stock_selenium(url, context, update.effective_chat.id)
    
    if data['status'] == 'error':
        await msg.edit_text("❌ Kritik hata oluştu.")
        return

    key = f"{update.effective_user.id}_{datetime.now().timestamp()}"
    tracked_products[key] = {
        'url': url,
        'name': data['name'],
        'last_status': data['availability'],
        'chat_id': update.effective_chat.id,
        'user_id': str(update.effective_user.id)
    }
    
    icon = "✅" if data['availability'] == 'in_stock' else "🔴"
    sizes = ", ".join(data['sizes']) if data['sizes'] else "Tükendi"
    
    await msg.edit_text(f"✅ *Takip Başladı*\n📦 {data['name']}\n{icon} Tespit Edilen: {sizes}", parse_mode='Markdown')

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    if not my_products:
        await update.message.reply_text("Liste boş.")
        return
    text = "Liste:\n"
    keyboard = []
    for k, v in my_products.items():
        text += f"{v['name']}\n"
        keyboard.append([InlineKeyboardButton("Sil", callback_data=f"del_{k}")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("del_"):
        key = query.data.replace("del_", "")
        if key in tracked_products: del tracked_products[key]
        await query.edit_message_text("Silindi.")

async def check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    for key, product in list(tracked_products.items()):
        try:
            # Otomatik kontrolde chat_id göndermiyoruz, her 5 dakikada bir foto atmasın
            data = await check_stock_selenium(product['url'])
            if data['status'] == 'error': continue
            
            if product['last_status'] == 'out_of_stock' and data['availability'] == 'in_stock':
                await context.bot.send_message(product['chat_id'], f"🚨 STOK GELDİ!\n{data['name']}\n{product['url']}")
            
            tracked_products[key]['last_status'] = data['availability']
            await asyncio.sleep(5)
        except: pass

if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("zara.com"), add_product))
    app.add_handler(CallbackQueryHandler(button_callback))
    if app.job_queue:
        app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=10)
    print("Bot Başladı...")
    app.run_polling()
