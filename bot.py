import os
import logging
import asyncio
import time
from datetime import datetime
from typing import Dict, List

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
ALLOWED_USERS = os.getenv('ALLOWED_USERS', '').split(',')
CHECK_INTERVAL = 300 

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Veritabanı
tracked_products: Dict[str, Dict] = {}
pending_adds: Dict[str, str] = {} 
waiting_for_sizes: Dict[str, str] = {} 

# --- YETKİ KONTROLÜ ---
async def is_authorized(update: Update):
    user_id = str(update.effective_user.id)
    if ALLOWED_USERS and user_id not in ALLOWED_USERS and ALLOWED_USERS != ['']:
        await update.effective_message.reply_text("SEN BENİM SEVGİLİM DEĞİLSİN HEMEN BURADAN UZAKLAŞ 😡")
        return False
    return True

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
    
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)
    
    return webdriver.Chrome(options=chrome_options)

async def check_stock_selenium(url: str):
    if "zara.com" in url and "/tr/tr" not in url:
        url = url.replace("zara.com/", "zara.com/tr/tr/")

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
            wait = WebDriverWait(driver, 10) 

            # 0. KONUM
            try:
                geo_btn = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-qa-action='stay-in-store']")))
                driver.execute_script("arguments[0].click();", geo_btn)
            except: pass

            # 1. ÇEREZ
            try:
                cookie = driver.find_element(By.ID, "onetrust-accept-btn-handler")
                driver.execute_script("arguments[0].click();", cookie)
            except: pass

            # 2. VERİ
            try: result['name'] = driver.find_element(By.TAG_NAME, "h1").text
            except: pass

            try: result['price'] = driver.find_element(By.CSS_SELECTOR, ".price-current__amount, .money-amount").text
            except: pass

            try:
                meta_img = driver.find_element(By.XPATH, "//meta[@property='og:image']")
                img = meta_img.get_attribute("content").split("?")[0]
                result['image'] = img
            except: pass

            # 3. STOK KONTROL (GÜNCELLENDİ)
            try:
                add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
                driver.execute_script("arguments[0].click();", add_btn)
                
                wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
                time.sleep(1.5) 
                
                # Beden etiketlerini bul
                labels = driver.find_elements(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']")
                available_sizes = []
                
                for label in labels:
                    try:
                        # 1. Beden ismini al (Örn: M)
                        size_name = label.text.strip()
                        if not size_name: continue

                        # 2. Üst elemente (parent) çıkıp TÜM yazıyı oku
                        # Bu sayede "M - Benzer Ürünler" veya "M - Çok Yakında" yazısını yakalarız
                        full_text = driver.execute_script("""
                            var el = arguments[0];
                            var parent = el.closest('li') || el.closest('button');
                            return parent ? parent.innerText : '';
                        """, label).upper() # Kontrol kolay olsun diye büyük harfe çevir
                        
                        # 3. YASAKLI KELİME FİLTRESİ
                        # Eğer bu kelimeler varsa, buton aktif olsa bile STOK YOKTUR.
                        forbidden_words = ["BENZER", "SIMILAR", "YAKINDA", "SOON", "TÜKENDİ", "OUT OF STOCK"]
                        
                        if any(word in full_text for word in forbidden_words):
                            continue # Bu bedeni atla, stok yok say

                        # 4. Standart Disabled Kontrolü
                        is_disabled = driver.execute_script("""
                            var el = arguments[0];
                            var parent = el.closest('li') || el.closest('button');
                            if (!parent) return false;
                            var classes = parent.className;
                            return classes.includes('is-disabled') || classes.includes('out-of-stock') || parent.hasAttribute('disabled');
                        """, label)
                        
                        if not is_disabled: 
                            available_sizes.append(size_name)

                    except: continue
                
                result['sizes'] = available_sizes
                result['availability'] = 'in_stock' if available_sizes else 'out_of_stock'
                result['status'] = 'success'
                
            except TimeoutException:
                result['status'] = 'success'
        
        except Exception as e:
            logger.error(f"Hata: {e}")
        finally:
            driver.quit()
        return result

    return await loop.run_in_executor(None, sync_process)

# --- UI FONKSİYONLARI ---

def create_ui(data, url, target_sizes):
    available_targets = []
    if 'HEPSI' in target_sizes:
        available_targets = data['sizes']
    else:
        available_targets = [s for s in data['sizes'] if s.upper() in target_sizes]

    if available_targets:
        status_line = "🟢 <b>STOKTA MEVCUT!</b>"
        sizes_formatted = "  ".join([f"<code>[{s}]</code>" for s in available_targets])
    else:
        status_line = "🔴 <b>ARADIĞIN BEDEN YOK</b>"
        sizes_formatted = "<i>Beklemedeyiz...</i>"

    tracked_str = "Tümü" if 'HEPSI' in target_sizes else ", ".join(target_sizes)
    separator = "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
    
    caption = (
        f"<b>{data.get('name', 'Zara Ürünü')}</b>\n"
        f"{separator}\n"
        f"🏷 <b>Fiyat:</b> {data.get('price', '-')}\n"
        f"🎯 <b>Takip Edilen:</b> {tracked_str}\n"
        f"📦 <b>Durum:</b> {status_line}\n\n"
        f"📏 <b>Mevcut Stoklar:</b>\n"
        f"└ {sizes_formatted}\n\n"
        f"🔗 <a href='{url}'>Link</a>"
    )
    return caption

async def set_commands(application: Application):
    commands = [
        BotCommand("start", "Başlat"),
        BotCommand("list", "Ürünlerim")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    msg = "👋 <b>Selam! Aşkım</b>\n\nSenin için zara ürünlerini takip edicem. Link gönder gerisine karışma. 😉"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# --- LİSTELEME ---
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    if update.callback_query: await update.callback_query.answer()

    await update.effective_message.reply_text("Listeye bakmaya üşendim şuan ya... 🥱")
    await asyncio.sleep(2)
    
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not my_products:
        await update.effective_message.reply_text("Şaka şaka... Ama cidden listen boş aşkım. Link at da çalışayım. 😘")
        return

    await update.effective_message.reply_text("Şaka şaka aşkım 🥰 İşte takip listen:")

    for k, v in my_products.items():
        is_happy = False
        if 'HEPSI' in v['target_sizes']:
            is_happy = (v['last_status'] == 'in_stock')
        else:
            is_happy = (v['last_status'] == 'in_stock_target')

        icon = "🟢" if is_happy else "🔴"
        target_str = "Tümü" if 'HEPSI' in v['target_sizes'] else ",".join(v['target_sizes'])
        
        text = f"{icon} <b>{v['name']}</b>\n🎯 Hedef: {target_str}\n🔗 <a href='{v['url']}'>Link</a>"
        keyboard = [[InlineKeyboardButton("🗑️ Sil", callback_data=f"del_{k}")]]
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True)

# --- ADIM 1: LINK GELDİ ---
async def add_product_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return

    user_id = update.effective_user.id
    text = update.message.text

    if "zara.com" in text:
        pending_adds[user_id] = text
        if user_id in waiting_for_sizes: del waiting_for_sizes[user_id]

        keyboard = [
            [InlineKeyboardButton("Evet çok seviyorum ❤️", callback_data="love_yes")],
            [InlineKeyboardButton("Hayır ⚠️", callback_data="love_no")]
        ]
        await update.message.reply_text(
            "🤔 <b>Bir saniye... Önce önemli bir soru:</b>\n\nSevgilini seviyor musun?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return

    if user_id in waiting_for_sizes:
        await process_size_input(update, context)
        return

    await update.message.reply_text("❌ Aşkım ya Zara linki at ya da sorduğumda beden yaz, kafamı karıştırma.", parse_mode=ParseMode.HTML)

# --- ADIM 3: BEDEN GİRİŞİ ---
async def process_size_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw_text = update.message.text.upper().strip() 
    url = waiting_for_sizes[user_id]

    target_sizes = []
    if "HEPSI" in raw_text or "TÜMÜ" in raw_text or "HERŞEY" in raw_text:
        target_sizes = ['HEPSI']
    else:
        parts = raw_text.replace(" ", ",").split(",")
        target_sizes = [p.strip() for p in parts if p.strip()]
    
    if not target_sizes:
        await update.message.reply_text("⚠️ Hiç beden anlamadım. Tekrar yazar mısın? (Örn: S, M)")
        return

    del waiting_for_sizes[user_id]

    await update.message.reply_text(f"Tamamdır! <b>{', '.join(target_sizes)}</b> bedenleri için bakıyorum... 🕵️‍♀️", parse_mode=ParseMode.HTML)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    check_data = await check_stock_selenium(url)

    if check_data['status'] == 'error':
        await update.message.reply_text("⚠️ Siteye giremedim aşkım ya, sonra tekrar deneriz.")
        return

    initial_status = 'out_of_stock'
    if 'HEPSI' in target_sizes:
        if check_data['availability'] == 'in_stock': initial_status = 'in_stock_target'
    else:
        available_targets = [s for s in check_data['sizes'] if s.upper() in target_sizes]
        if available_targets: initial_status = 'in_stock_target'

    key = f"{user_id}_{datetime.now().timestamp()}"
    tracked_products[key] = {
        'url': url,
        'name': check_data['name'],
        'price': check_data['price'],
        'image': check_data['image'],
        'last_status': initial_status, 
        'target_sizes': target_sizes,
        'chat_id': update.effective_chat.id,
        'user_id': str(user_id)
    }

    caption = create_ui(check_data, url, target_sizes)
    keyboard = [
        [InlineKeyboardButton("🔄 Yenile", callback_data=f"refresh_{key}"), InlineKeyboardButton("❌ Sil", callback_data=f"del_{key}")]
    ]
    if check_data['image']:
        try: await update.message.reply_photo(photo=check_data['image'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        except: await update.message.reply_text(text=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))


# --- ADIM 2: BUTON CEVAPLARI ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return

    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "love_yes":
        if user_id not in pending_adds:
            await query.edit_message_text("⚠️ Link zaman aşımına uğradı, tekrar atar mısın?")
            return

        url = pending_adds.pop(user_id)
        waiting_for_sizes[user_id] = url
        
        await query.edit_message_text(
            "🥰 <b>Ben de seni çok seviyorum aşkımmm!</b>\n\n"
            "Peki hangi bedenleri takip edeyim?\n"
            "👉 Bedenleri virgülle ayırarak yaz (Örn: <b>XS, S</b>)\n"
            "👉 Fark etmez diyorsan <b>Hepsi</b> yaz.",
            parse_mode=ParseMode.HTML
        )

    elif data == "love_no":
        if user_id in pending_adds: del pending_adds[user_id]
        if user_id in waiting_for_sizes: del waiting_for_sizes[user_id]
        await query.edit_message_text("😡 <b>İnşallah stoğa girmez hiç!</b>\nBenimle bi daha konuşma.", parse_mode=ParseMode.HTML)

    elif data.startswith("del_"):
        key = data.replace("del_", "")
        if key in tracked_products: 
            product_name = tracked_products[key]['name']
            del tracked_products[key]
            await query.delete_message()
            await context.bot.send_message(query.message.chat_id, f"🗑️ <b>{product_name}</b> listenden sildim.", parse_mode=ParseMode.HTML)
        else:
            await query.answer("Zaten silmişsin.", show_alert=True)
    
    elif data.startswith("refresh_"):
        key = data.replace("refresh_", "")
        if key in tracked_products:
            await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
            product = tracked_products[key]
            check_data = await check_stock_selenium(product['url'])
            
            if check_data['status'] == 'success':
                current_status = 'out_of_stock'
                if 'HEPSI' in product['target_sizes']:
                    if check_data['availability'] == 'in_stock': current_status = 'in_stock_target'
                else:
                    matches = [s for s in check_data['sizes'] if s.upper() in product['target_sizes']]
                    if matches: current_status = 'in_stock_target'

                tracked_products[key]['last_status'] = current_status
                new_caption = create_ui(check_data, product['url'], product['target_sizes'])
                try: 
                    await query.edit_message_caption(caption=new_caption, parse_mode=ParseMode.HTML, reply_markup=query.message.reply_markup)
                    await query.answer("✅ Güncel.")
                except: 
                    await query.answer("✅ Değişiklik yok.")
            else:
                await query.answer("⚠️ Hata oluştu.", show_alert=True)

# --- OTOMATİK KONTROL ---
async def check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    for key, product in list(tracked_products.items()):
        try:
            data = await check_stock_selenium(product['url'])
            if data['status'] == 'error': continue
            
            is_target_found = False
            found_sizes = []
            
            if 'HEPSI' in product['target_sizes']:
                if data['availability'] == 'in_stock':
                    is_target_found = True
                    found_sizes = data['sizes']
            else:
                found_sizes = [s for s in data['sizes'] if s.upper() in product['target_sizes']]
                if found_sizes: is_target_found = True
            
            current_status = 'in_stock_target' if is_target_found else 'out_of_stock'

            if product['last_status'] == 'out_of_stock' and current_status == 'in_stock_target':
                caption = (
                    f"🚨🚨 <b>AŞKIM KOŞ STOK GELDİ!</b> 🚨🚨\n\n"
                    f"💎 <b>{data['name']}</b>\n"
                    f"🎯 İstediğin: {', '.join(product['target_sizes'])}\n"
                    f"✅ <b>Gelen Bedenler:</b> <code>{', '.join(found_sizes)}</code>\n\n"
                    f"👇 <b>HEMEN AL BUTONUNA BAS!</b>"
                )
                keyboard = [[InlineKeyboardButton("🛒 SATIN AL", url=product['url'])]]
                
                if product.get('image'):
                    try: await context.bot.send_photo(product['chat_id'], photo=product['image'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
                    except: await context.bot.send_message(product['chat_id'], text=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await context.bot.send_message(product['chat_id'], text=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            
            tracked_products[key]['last_status'] = current_status
            await asyncio.sleep(5)
        except: pass

async def post_init(application: Application):
    await set_commands(application)

if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), add_product_request))
    app.add_handler(CallbackQueryHandler(button_callback))
    if app.job_queue:
        app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=10)
    print("Final Filtered Bot Başladı 🎯...")
    app.run_polling()
