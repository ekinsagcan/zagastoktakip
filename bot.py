import os
import logging
import asyncio
import time
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
        'screenshot': None 
    }

    try:
        loop = asyncio.get_running_loop()
        
        def sync_process():
            inner_driver = get_driver()
            try:
                logger.info(f"🔍 Kontrol ediliyor: {url}")
                inner_driver.get(url)
                wait = WebDriverWait(inner_driver, 15)
                
                time.sleep(2)

                # --- 0. ADIM: KONUM PENCERESİ ---
                try:
                    geo_btn = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-qa-action='stay-in-store']")))
                    logger.info("🌍 Konum butonu tespit edildi, 5sn bekleniyor...")
                    time.sleep(5) 
                    inner_driver.execute_script("arguments[0].click();", geo_btn)
                    time.sleep(3)
                except:
                    logger.info("🌍 Konum penceresi geçildi.")

                # --- 1. ADIM: ÇEREZLERİ KAPAT ---
                try:
                    cookie = inner_driver.find_element(By.ID, "onetrust-accept-btn-handler")
                    inner_driver.execute_script("arguments[0].click();", cookie)
                except: pass

                # İsim Al
                try:
                    result['name'] = inner_driver.find_element(By.TAG_NAME, "h1").text
                except: pass

                # --- 2. ADIM: EKLE BUTONUNA TIKLA ---
                try:
                    add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                    
                    inner_driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
                    time.sleep(1)
                    inner_driver.execute_script("arguments[0].click();", add_btn)
                    
                    # Modal bekle
                    wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
                    
                    # --- KRİTİK GÜNCELLEME: BEDENLERİN YÜKLENMESİNİ BEKLE ---
                    time.sleep(2) # Pencere açıldıktan sonra içindeki yazıların gelmesi için bekle
                    
                    # --- 3. ADIM: AKILLI BEDEN TARAMA ---
                    # Sadece tek bir class'a bakmak yerine, data-qa etiketini bulup analiz edeceğiz.
                    labels = inner_driver.find_elements(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']")
                    available_sizes = []
                    
                    logger.info(f"Bulunan toplam beden etiketi sayısı: {len(labels)}")
                    
                    for label in labels:
                        try:
                            txt = label.text.strip()
                            if not txt: continue
                            
                            # JavaScript ile bu elementin veya ebeveynlerinin 'disabled' olup olmadığına bak.
                            # Bu yöntem en garantisidir.
                            is_disabled = inner_driver.execute_script("""
                                var el = arguments[0];
                                // En yakın 'li' (liste öğesi) veya 'button' ebeveynini bul
                                var parent = el.closest('li') || el.closest('button');
                                if (!parent) return false;
                                
                                // Class listesinde 'disabled', 'out-of-stock' var mı kontrol et
                                var classes = parent.className;
                                return classes.includes('is-disabled') || classes.includes('out-of-stock') || parent.hasAttribute('disabled');
                            """, label)
                            
                            if not is_disabled:
                                available_sizes.append(txt)
                                logger.info(f"✅ Stokta bulundu: {txt}")
                            else:
                                logger.info(f"🚫 Stok yok (Disabled): {txt}")
                                
                        except Exception as e:
                            logger.error(f"Beden okuma hatası: {e}")
                            continue
                    
                    result['sizes'] = available_sizes
                    result['availability'] = 'in_stock' if available_sizes else 'out_of_stock'
                    result['status'] = 'success'
                    
                except TimeoutException:
                    logger.warning("⚠️ Ekle butonu bulunamadı veya pencere açılmadı.")
                    result['status'] = 'success' 
            
            except Exception as e:
                logger.error(f"İç Hata: {e}")
            
            finally:
                if chat_id:
                    screenshot_name = f"debug_{datetime.now().timestamp()}.png"
                    inner_driver.save_screenshot(screenshot_name)
                    result['screenshot'] = screenshot_name 
                
                inner_driver.quit()
            
            return result

        final_data = await loop.run_in_executor(None, sync_process)
        
        if final_data['screenshot'] and os.path.exists(final_data['screenshot']) and context and chat_id:
            caption_text = "📸 Botun gördüğü ekran.\n"
            caption_text += f"Durum: {'STOK VAR' if final_data['availability'] == 'in_stock' else 'TÜKENDİ'}\n"
            caption_text += f"Bulunan Bedenler: {', '.join(final_data['sizes']) if final_data['sizes'] else 'Yok'}"
            
            await context.bot.send_photo(
                chat_id=chat_id, 
                photo=open(final_data['screenshot'], 'rb'),
                caption=caption_text
            )
            os.remove(final_data['screenshot']) 

        return final_data

    except Exception as e:
        logger.error(f"Genel Hata: {e}")
        return result

# --- TELEGRAM BOT KOMUTLARI ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Zara Bot. Link gönder.")

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Sadece Zara linki.")
        return

    msg = await update.message.reply_text("📸 Kontrol ediliyor (5-15sn sürebilir)...")
    
    data = await check_stock_selenium(url, context, update.effective_chat.id)
    
    if data['status'] == 'error':
        await msg.edit_text("❌ Hata.")
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
