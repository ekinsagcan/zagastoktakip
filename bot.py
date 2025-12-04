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
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException

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
    chrome_options.add_argument("--window-size=1920,1080") # Çözünürlük önemli
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
        'sizes': []
    }

    try:
        loop = asyncio.get_running_loop()
        
        def sync_process():
            inner_driver = get_driver()
            try:
                logger.info(f"🔍 Kontrol: {url}")
                inner_driver.get(url)
                wait = WebDriverWait(inner_driver, 15) # Süreyi biraz artırdık

                # 1. ADIM: ÇEREZLERİ KAPAT (EN ÖNEMLİ KISIM)
                try:
                    # Zara genelde 'Onetrust' kullanır
                    cookie_btn = wait.until(EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler")))
                    cookie_btn.click()
                    logger.info("🍪 Çerez penceresi kapatıldı.")
                except:
                    logger.info("🍪 Çerez penceresi bulunamadı veya zaten kapalı.")

                # Ürün Adı
                try:
                    result['name'] = inner_driver.find_element(By.TAG_NAME, "h1").text
                except: pass

                # 2. ADIM: TÜKENDİ Mİ?
                if len(inner_driver.find_elements(By.XPATH, "//button[@data-qa-action='show-similar-products']")) > 0:
                    result['status'] = 'success'
                    return result

                # 3. ADIM: EKLE BUTONUNA TIKLA
                try:
                    add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                    
                    # Normal tıklama bazen çalışmaz, JavaScript ile zorla tıklatıyoruz
                    inner_driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
                    inner_driver.execute_script("arguments[0].click();", add_btn)
                    logger.info("🖱️ Ekle butonuna tıklandı.")
                    
                    # 4. ADIM: BEDEN LİSTESİNİ BEKLE
                    # Modalın görünür olmasını bekle
                    wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
                    
                    # 5. ADIM: BEDENLERİ OKU
                    size_items = inner_driver.find_elements(By.CSS_SELECTOR, "li.size-selector-list__item")
                    available_sizes = []
                    
                    for item in size_items:
                        try:
                            classes = item.get_attribute("class")
                            # Disabled veya out-of-stock değilse al
                            if "is-disabled" not in classes and "out-of-stock" not in classes:
                                txt = item.find_element(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']").text
                                available_sizes.append(txt)
                        except: continue
                    
                    result['sizes'] = available_sizes
                    if available_sizes:
                        result['availability'] = 'in_stock'
                    else:
                        # Modal açıldı ama aktif beden yoksa gerçekten stok yoktur
                        pass
                    
                    result['status'] = 'success'
                    
                except TimeoutException:
                    # Ekle butonu var ama modal açılmadıysa veya buton bulunamadıysa
                    # BURADA EKRAN GÖRÜNTÜSÜ ALIYORUZ Kİ SORUNU GÖRELİM
                    logger.warning("⚠️ Zaman aşımı! Ekran görüntüsü alınıyor...")
                    if context and chat_id:
                        try:
                            inner_driver.save_screenshot("debug.png")
                        except: pass
                    
                    result['status'] = 'success' # Hata değil, stok yok varsay
            
            finally:
                # Eğer screenshot varsa ve chat_id verildiyse gönder (Senkron dışına taşıyacağız)
                pass 
                inner_driver.quit()
            return result

        # İşlemi çalıştır
        res = await loop.run_in_executor(None, sync_process)
        
        # Hata fotoğrafı varsa gönder
        if os.path.exists("debug.png") and context and chat_id and res['availability'] == 'out_of_stock':
            await context.bot.send_photo(
                chat_id=chat_id, 
                photo=open("debug.png", 'rb'), 
                caption=f"❌ Stok Yok Dedi. O anki ekran görüntüsü bu.\nEğer stok görüyorsan kodda düzeltme yapmalıyız."
            )
            os.remove("debug.png")

        return res

    except Exception as e:
        logger.error(f"Sistem Hatası: {e}")
        return result

# --- TELEGRAM BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Zara Bot Başladı.\nLink gönder.")

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Sadece Zara linki.")
        return

    msg = await update.message.reply_text("⏳ Kontrol ediliyor...")
    
    # Chat ID'yi de gönderiyoruz ki foto atabilsin
    data = await check_stock_selenium(url, context, update.effective_chat.id)
    
    if data['status'] == 'error':
        await msg.edit_text("❌ Hata oluştu.")
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
    
    await msg.edit_text(f"✅ *Eklendi*\n📦 {data['name']}\n{icon} Durum: {sizes}", parse_mode='Markdown')

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # (Bu kısım aynı kalabilir, kısalttım)
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    if not my_products:
        await update.message.reply_text("Boş.")
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
            # Otomatik kontrolde fotoğraf atmasın diye chat_id göndermiyoruz
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
