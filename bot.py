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

# --- TARAYICI MOTORU ---
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

async def check_stock_selenium(url: str):
    result = {
        'status': 'error',
        'name': 'Zara Ürünü',
        'availability': 'out_of_stock',
        'sizes': [],
        'image': None, 
        'price': 'Fiyat Yok'
    }
    
    loop = asyncio.get_running_loop()
    
    def sync_process():
        driver = get_driver()
        try:
            driver.get(url)
            wait = WebDriverWait(driver, 15)
            time.sleep(2)

            # 0. KONUM PENCERESİ (Zara Özel Fix)
            try:
                geo_btn = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-qa-action='stay-in-store']")))
                time.sleep(3) 
                driver.execute_script("arguments[0].click();", geo_btn)
                time.sleep(2)
            except: pass

            # 1. ÇEREZ
            try:
                cookie = driver.find_element(By.ID, "onetrust-accept-btn-handler")
                driver.execute_script("arguments[0].click();", cookie)
            except: pass

            # 2. VERİ ÇEKME
            try: result['name'] = driver.find_element(By.TAG_NAME, "h1").text
            except: pass

            try: result['price'] = driver.find_element(By.CSS_SELECTOR, ".price-current__amount, .money-amount").text
            except: pass

            # RESİM (Meta Tag)
            try:
                meta_img = driver.find_element(By.XPATH, "//meta[@property='og:image']")
                img = meta_img.get_attribute("content").split("?")[0]
                result['image'] = img
            except: pass

            # 3. STOK KONTROL (Akıllı Tarama)
            try:
                add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", add_btn)
                
                wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
                time.sleep(2) 
                
                labels = driver.find_elements(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']")
                available_sizes = []
                
                for label in labels:
                    try:
                        txt = label.text.strip()
                        if not txt: continue
                        is_disabled = driver.execute_script("""
                            var el = arguments[0];
                            var parent = el.closest('li') || el.closest('button');
                            if (!parent) return false;
                            var classes = parent.className;
                            return classes.includes('is-disabled') || classes.includes('out-of-stock') || parent.hasAttribute('disabled');
                        """, label)
                        if not is_disabled: available_sizes.append(txt)
                    except: continue
                
                result['sizes'] = available_sizes
                result['availability'] = 'in_stock' if available_sizes else 'out_of_stock'
                result['status'] = 'success'
                
            except TimeoutException:
                result['status'] = 'success' # Stok yok
        
        except Exception as e:
            logger.error(f"Hata: {e}")
        finally:
            driver.quit()
        return result

    return await loop.run_in_executor(None, sync_process)

# --- UI TASARIM FONKSİYONLARI ---

def create_ui(data, url):
    """Premium Kart Tasarımı"""
    
    if data['availability'] == 'in_stock':
        status_line = "🟢 <b>STOKTA MEVCUT</b>"
        # Bedenleri şık kutucuklar halinde göster
        sizes_formatted = "  ".join([f"<code>[{s}]</code>" for s in data['sizes']])
    else:
        status_line = "🔴 <b>TÜKENDİ</b>"
        sizes_formatted = "<i>Şu an stok bulunmuyor.</i>"

    # Minimalist Çizgi
    separator = "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
    
    caption = (
        f"<b>{data.get('name', 'Zara Ürünü')}</b>\n"
        f"{separator}\n"
        f"🏷 <b>Fiyat:</b> {data.get('price', '-')}\n"
        f"📦 <b>Durum:</b> {status_line}\n\n"
        f"📏 <b>Bedenler:</b>\n"
        f"└ {sizes_formatted}\n\n"
        f"🔗 <a href='{url}'>Ürünü Sitede Görüntüle</a>"
    )
    return caption

async def set_commands(application: Application):
    commands = [
        BotCommand("start", "Panel"),
        BotCommand("add", "Ürün Ekle"),
        BotCommand("list", "Ürünlerim")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    msg = (
        f"👋 <b>Selam {user},</b>\n\n"
        "💎 <b>Zara Stok Takip Sistemine</b> hoş geldin.\n"
        "Senin için ürünleri saniye saniye izliyorum.\n\n"
        "🚀 <b>Nasıl Başlarım?</b>\n"
        "Tek yapman gereken bir Zara linki göndermek."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Sadece <b>Zara</b> linkleri kabul edilir.", parse_mode=ParseMode.HTML)
        return

    status_msg = await update.message.reply_text("🔎 <i>Ürün analiz ediliyor...</i>", parse_mode=ParseMode.HTML)
    
    data = await check_stock_selenium(url)
    
    if data['status'] == 'error':
        await status_msg.edit_text("⚠️ Siteye erişilemedi.")
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
    
    await status_msg.delete() 

    caption = create_ui(data, url)
    
    keyboard = [
        [InlineKeyboardButton("🔄 Yenile", callback_data=f"refresh_{key}"), InlineKeyboardButton("❌ Sil", callback_data=f"del_{key}")],
        [InlineKeyboardButton("🔗 Zara'da Aç", url=url)]
    ]
    
    if data['image']:
        try:
            await context.bot.send_photo(update.effective_chat.id, photo=data['image'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        except:
             await context.bot.send_message(update.effective_chat.id, caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await context.bot.send_message(update.effective_chat.id, caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not my_products:
        await update.message.reply_text("📭 <b>Listen boş.</b>", parse_mode=ParseMode.HTML)
        return

    await update.message.reply_text(f"📋 <b>Takip Listen ({len(my_products)} Ürün)</b>", parse_mode=ParseMode.HTML)

    for k, v in my_products.items():
        icon = "🟢" if v['last_status'] == 'in_stock' else "🔴"
        text = f"{icon} <b>{v['name']}</b>\n🔗 <a href='{v['url']}'>Ürüne Git</a>"
        keyboard = [[InlineKeyboardButton("🔄 Kontrol", callback_data=f"refresh_{k}"), InlineKeyboardButton("🗑️ Sil", callback_data=f"del_{k}")]]
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data.startswith("del_"):
        key = data.replace("del_", "")
        if key in tracked_products: 
            del tracked_products[key]
            await query.answer("🗑️ Ürün silindi!")
            await query.delete_message()
        else:
            await query.answer("❌ Ürün zaten yok.", show_alert=True)
    
    elif data.startswith("refresh_"):
        key = data.replace("refresh_", "")
        if key in tracked_products:
            # Kullanıcıya işlem yapıldığını hissettir ama mesajı hemen bozma
            await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
            
            product = tracked_products[key]
            check_data = await check_stock_selenium(product['url'])
            
            if check_data['status'] == 'success':
                tracked_products[key]['last_status'] = check_data['availability']
                new_caption = create_ui(check_data, product['url'])
                
                # Sadece içerik değiştiyse mesajı güncelle, yoksa bildirim ver
                if query.message.caption != new_caption.replace("<b>", "").replace("</b>", ""): # Basit karşılaştırma
                    keyboard = query.message.reply_markup
                    try: 
                        await query.edit_message_caption(caption=new_caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)
                        await query.answer("✅ Durum güncellendi!")
                    except: 
                        await query.answer("✅ Güncel - Değişiklik yok.")
                else:
                    await query.answer("✅ Güncel - Değişiklik yok.")
            else:
                await query.answer("⚠️ Hata oluştu.", show_alert=True)

async def check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    for key, product in list(tracked_products.items()):
        try:
            data = await check_stock_selenium(product['url'])
            if data['status'] == 'error': continue
            
            # STOK GELDİ Mİ?
            if product['last_status'] == 'out_of_stock' and data['availability'] == 'in_stock':
                
                caption = (
                    f"🚨🚨 <b>STOK GELDİ! YAKALA!</b> 🚨🚨\n\n"
                    f"💎 <b>{data['name']}</b>\n"
                    f"📏 <b>Bedenler:</b> <code>{', '.join(data['sizes'])}</code>\n\n"
                    f"👇 <b>HEMEN AL BUTONUNA BAS!</b>"
                )
                keyboard = [[InlineKeyboardButton("🛒 SATIN AL (ZARA)", url=product['url'])]]
                
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
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("zara.com"), add_product))
    app.add_handler(CallbackQueryHandler(button_callback))
    if app.job_queue:
        app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=10)
    print("Zara Bot Başladı (Ultra UI)...")
    app.run_polling()
