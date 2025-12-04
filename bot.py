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

# --- TARAYICI AYARLARI ---
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
    return webdriver.Chrome(options=chrome_options)

# --- YARDIMCI FONKSİYONLAR ---
def safe_click(driver, by, value, timeout=5):
    try:
        element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
        driver.execute_script("arguments[0].scrollIntoView(true);", element)
        time.sleep(1)
        driver.execute_script("arguments[0].click();", element)
        return True
    except:
        return False

def close_popups(driver):
    """Genel çerez ve konum kapatıcı (Tüm siteler için)"""
    # 1. Çerezler (Onetrust - Çoğu site bunu kullanır)
    try:
        cookie = driver.find_element(By.ID, "onetrust-accept-btn-handler")
        driver.execute_script("arguments[0].click();", cookie)
    except: pass
    
    # 2. Konum Pencereleri (Inditex Grubu)
    try:
        # Zara, Bershka, P&B benzer mantık kullanır
        geo_btns = driver.find_elements(By.CSS_SELECTOR, "button[data-qa-action='stay-in-store'], button[class*='geolocation']")
        for btn in geo_btns:
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1)
    except: pass

# --- SITE MODÜLLERİ ---

def scrape_inditex(driver, url, site_name):
    """Zara, Bershka, Pull&Bear için Ortak Mantık"""
    result = {'name': site_name, 'price': '', 'image': None, 'sizes': [], 'availability': 'out_of_stock'}
    wait = WebDriverWait(driver, 15)
    
    # İsim
    try: result['name'] = driver.find_element(By.TAG_NAME, "h1").text
    except: pass
    
    # Fiyat
    try: result['price'] = driver.find_element(By.CSS_SELECTOR, ".price-current__amount, .money-amount, .current-price-elem").text
    except: pass

    # Resim (Meta Tag)
    try:
        meta = driver.find_element(By.XPATH, "//meta[@property='og:image']")
        result['image'] = meta.get_attribute("content").split("?")[0]
    except: pass

    # Ekle Butonu
    try:
        # Inditex grubu genelde aynı data-qa etiketini kullanır
        add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart' or contains(@class, 'add-to-cart')]")))
        driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
        time.sleep(1)
        driver.execute_script("arguments[0].click();", add_btn)
        
        # Beden Listesi Bekle
        wait.until(EC.visibility_of_element_located((By.XPATH, "//div[contains(@class, 'size-selector') or contains(@class, 'sizes-list')]")))
        time.sleep(2)

        # Bedenleri Tara
        # Bershka/P&B bazen li, bazen button kullanır. Geniş arama yapıyoruz.
        size_elems = driver.find_elements(By.CSS_SELECTOR, "li[class*='size'], button[class*='size-selector']")
        
        for el in size_elems:
            try:
                txt = el.text.strip().split("\n")[0] # Bazen M (US M) yazar, ilkini al
                if not txt: continue
                
                # Disabled kontrolü
                classes = el.get_attribute("class") or ""
                disabled_attr = el.get_attribute("disabled")
                
                if "disabled" not in classes and "out-of-stock" not in classes and disabled_attr is None:
                    # Ayrıca opacity kontrolü (P&B bazen silik yapar)
                    opacity = driver.execute_script("return window.getComputedStyle(arguments[0]).opacity", el)
                    if float(opacity) > 0.5:
                        result['sizes'].append(txt)
            except: continue
            
        if result['sizes']: result['availability'] = 'in_stock'
            
    except TimeoutException:
        pass # Buton yoksa stok yok demektir
        
    return result

def scrape_mango(driver, url):
    """Mango Özel Mantığı"""
    result = {'name': 'Mango Ürünü', 'price': '', 'image': None, 'sizes': [], 'availability': 'out_of_stock'}
    wait = WebDriverWait(driver, 15)

    try: result['name'] = driver.find_element(By.TAG_NAME, "h1").text
    except: pass
    
    try: result['price'] = driver.find_element(By.CSS_SELECTOR, "span[data-testid='current-price']").text
    except: pass
    
    try: 
        meta = driver.find_element(By.XPATH, "//meta[@property='og:image']")
        result['image'] = meta.get_attribute("content")
    except: pass

    # Mango'da Bedenler Genelde Sayfada Açıktır (Dropdown veya Liste)
    try:
        # Beden butonlarını bul
        sizes = driver.find_elements(By.CSS_SELECTOR, "span[data-testid='size-selector-size']")
        
        # Eğer beden seçimi zorunluysa 'Sepete Ekle' pasif olabilir.
        # Mango'da stokta olan bedenler tıklanabilir olur.
        for s in sizes:
            try:
                # Ebeveyn elemente bak (buton)
                parent = s.find_element(By.XPATH, "./..") 
                if parent.is_enabled() and "unavailable" not in parent.get_attribute("class"):
                    result['sizes'].append(s.text)
            except: continue
            
        if result['sizes']:
            result['availability'] = 'in_stock'
        else:
            # Alternatif: Eğer tek beden varsa ve direkt 'Ekle' butonu aktifse
            add_btn = driver.find_elements(By.CSS_SELECTOR, "button[data-testid='add-to-cart']")
            if add_btn and add_btn[0].is_enabled():
                result['availability'] = 'in_stock'
                result['sizes'] = ['Standart']
                
    except: pass
    return result

# --- ANA KONTROL MERKEZİ ---

async def check_stock_selenium(url: str, context: ContextTypes.DEFAULT_TYPE = None, chat_id=None):
    result = {'status': 'error', 'name': 'Ürün', 'availability': 'out_of_stock', 'sizes': [], 'image': None, 'price': ''}
    
    loop = asyncio.get_running_loop()
    
    def sync_process():
        driver = get_driver()
        try:
            logger.info(f"🔍 Tarama Başladı: {url}")
            driver.get(url)
            time.sleep(3)
            
            close_popups(driver) # Ortak popup kapatıcı

            # URL'ye göre siteyi tanı ve ilgili modüle git
            if "zara.com" in url:
                return scrape_inditex(driver, url, "Zara")
            elif "bershka.com" in url:
                return scrape_inditex(driver, url, "Bershka")
            elif "pullandbear.com" in url:
                return scrape_inditex(driver, url, "Pull&Bear")
            elif "mango.com" in url:
                return scrape_mango(driver, url)
            else:
                return scrape_inditex(driver, url, "Bilinmeyen Site") # Varsayılan olarak Inditex dene

        except Exception as e:
            logger.error(f"Hata: {e}")
            return result
        finally:
            driver.quit()

    final_data = await loop.run_in_executor(None, sync_process)
    
    # Eksik verileri doldur
    final_data['status'] = 'success'
    return final_data

# --- TELEGRAM ARAYÜZ (AYNI KALDI) ---

def create_product_message(data, url):
    if data['availability'] == 'in_stock':
        status_line = "🟢 <b>STOKTA VAR</b>"
        sizes_formatted = f"<code>{', '.join(data['sizes'])}</code>"
    else:
        status_line = "🔴 <b>TÜKENDİ</b>"
        sizes_formatted = "<i>Stok bulunmuyor</i>"

    check_time = datetime.now().strftime("%H:%M")

    caption = (
        f"💎 <b>{data.get('name', 'Ürün')}</b>\n"
        f"🔗 <a href='{url}'>Ürün Linki</a>\n\n"
        f"💰 <b>{data.get('price', '')}</b>\n"
        f"〰️〰️〰️〰️〰️〰️〰️\n"
        f"📊 Durum: {status_line}\n"
        f"📏 Bedenler: {sizes_formatted}\n"
        f"〰️〰️〰️〰️〰️〰️〰️\n"
        f"🕒 <i>Son Güncelleme: {check_time}</i>"
    )
    return caption

async def set_commands(application: Application):
    commands = [
        BotCommand("start", "Botu başlat"),
        BotCommand("add", "Ürün ekle"),
        BotCommand("list", "Listem"),
        BotCommand("help", "Yardım")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 <b>Multi-Brand Stok Botu</b>\nZara, Bershka, Pull&Bear, Mango destekler.", parse_mode=ParseMode.HTML)

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    # Geçerli siteler kontrolü
    allowed_sites = ["zara.com", "bershka.com", "pullandbear.com", "mango.com"]
    if not any(site in url for site in allowed_sites):
        await update.message.reply_text("❌ Sadece Zara, Bershka, Pull&Bear ve Mango linkleri kabul edilir.", parse_mode=ParseMode.HTML)
        return

    loading_msg = await update.message.reply_text("🔎 <i>Site analiz ediliyor...</i>", parse_mode=ParseMode.HTML)
    
    data = await check_stock_selenium(url, context, update.effective_chat.id)
    
    if data['status'] == 'error':
        await loading_msg.edit_text("⚠️ Siteye erişim hatası.")
        return

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
    
    await loading_msg.delete() 

    caption = create_product_message(data, url)
    keyboard = [
        [InlineKeyboardButton("🔗 Siteye Git", url=url)],
        [InlineKeyboardButton("🔄 Kontrol Et", callback_data=f"refresh_{key}"), InlineKeyboardButton("❌ Sil", callback_data=f"del_{key}")]
    ]
    
    if data['image']:
        try:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=data['image'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        except:
             await context.bot.send_message(update.effective_chat.id, caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await context.bot.send_message(update.effective_chat.id, caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not my_products:
        await update.message.reply_text("📭 Listen boş.", parse_mode=ParseMode.HTML)
        return

    await update.message.reply_text(f"📋 <b>Takip Listen ({len(my_products)} Ürün)</b>", parse_mode=ParseMode.HTML)

    for k, v in my_products.items():
        icon = "🟢" if v['last_status'] == 'in_stock' else "🔴"
        text = f"{icon} <b>{v['name']}</b>\n🔗 <a href='{v['url']}'>Link</a>"
        keyboard = [[InlineKeyboardButton("🗑️ Sil", callback_data=f"del_{k}")]]
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("del_"):
        key = data.replace("del_", "")
        if key in tracked_products: 
            del tracked_products[key]
            await query.edit_message_caption("🗑️ Silindi.")
    
    elif data.startswith("refresh_"):
        key = data.replace("refresh_", "")
        if key in tracked_products:
            product = tracked_products[key]
            await context.bot.send_chat_action(chat_id=product['chat_id'], action="typing")
            check_data = await check_stock_selenium(product['url'])
            if check_data['status'] == 'success':
                tracked_products[key]['last_status'] = check_data['availability']
                new_caption = create_product_message(check_data, product['url'])
                keyboard = [[InlineKeyboardButton("🔗 Site", url=product['url']), InlineKeyboardButton("🔄", callback_data=f"refresh_{key}"), InlineKeyboardButton("❌", callback_data=f"del_{key}")]]
                try: await query.edit_message_caption(caption=new_caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
                except: await query.edit_message_text(text=new_caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    for key, product in list(tracked_products.items()):
        try:
            data = await check_stock_selenium(product['url'])
            if data['status'] == 'error': continue
            
            if product['last_status'] == 'out_of_stock' and data['availability'] == 'in_stock':
                caption = f"🚨 <b>STOK GELDİ!</b>\n\n💎 {data['name']}\n📏 {', '.join(data['sizes'])}"
                keyboard = [[InlineKeyboardButton("🛒 SATIN AL", url=product['url'])]]
                if product.get('image'):
                    try: await context.bot.send_photo(product['chat_id'], photo=product['image'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
                    except: await context.bot.send_message(product['chat_id'], text=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                     await context.bot.send_message(product['chat_id'], text=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            
            tracked_products[key]['last_status'] = data['availability']
            await asyncio.sleep(5)
        except: pass

async def post_init(application: Application):
    await set_commands(application)

if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(CommandHandler("add", add_product))
    app.add_handler(MessageHandler(filters.TEXT & (filters.Regex("zara.com") | filters.Regex("bershka.com") | filters.Regex("pullandbear.com") | filters.Regex("mango.com")), add_product))
    app.add_handler(CallbackQueryHandler(button_callback))
    if app.job_queue:
        app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=10)
    print("Multi-Site Bot Başladı...")
    app.run_polling()
