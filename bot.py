import os
import logging
import asyncio
import time
import re
from datetime import datetime, timedelta
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
ADMIN_ID = "5952744818" # SENİN ID
CHECK_INTERVAL = 180 

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- VERİTABANI ---
tracked_products: Dict[str, Dict] = {}
pending_adds: Dict[int, str] = {} 
waiting_for_sizes: Dict[int, str] = {} 

# ADMIN İÇİN
known_users: Dict[str, Dict] = {} 
admin_reply_mode: Dict[str, str] = {} 

# --- YETKİ KONTROLÜ ---
async def is_authorized(update: Update):
    user = update.effective_user
    user_id = str(user.id)
    
    if user_id not in known_users:
        known_users[user_id] = {
            'name': user.first_name,
            'username': user.username,
            'joined': datetime.now().strftime("%Y-%m-%d"),
            'last_msg': '-'
        }
    
    if user_id == ADMIN_ID: return True

    if ALLOWED_USERS and user_id not in ALLOWED_USERS and ALLOWED_USERS != ['']:
        try:
            await update.effective_message.reply_text("SEN BENİM SEVGİLİM DEĞİLSİN! HEMEN BURADAN UZAKLAŞ! 😡🔪")
        except: pass
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

def clean_size_text(text):
    if not text: return ""
    text = text.split('\n')[0] 
    text = re.sub(r"\(.*?\)", "", text) 
    return text.strip()

async def check_stock_selenium(url: str):
    if "zara.com" in url and "/tr/tr" not in url:
        url = url.replace("zara.com/", "zara.com/tr/tr/")

    result = {
        'status': 'error', 
        'name': 'Zara Ürünü', 
        'availability': 'out_of_stock', 
        'sizes': [], 
        'image': None, 
        'price': 'Fiyat Yok',
        'is_one_size': False
    }
    
    loop = asyncio.get_running_loop()
    
    def sync_process():
        driver = get_driver()
        try:
            driver.get(url)
            wait = WebDriverWait(driver, 15)
            
            try:
                geo_btn = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-qa-action='stay-in-store']")))
                driver.execute_script("arguments[0].click();", geo_btn)
            except: pass
            try:
                cookie = driver.find_element(By.ID, "onetrust-accept-btn-handler")
                driver.execute_script("arguments[0].click();", cookie)
            except: pass

            try: result['name'] = driver.find_element(By.TAG_NAME, "h1").text
            except: pass
            try: result['price'] = driver.find_element(By.CSS_SELECTOR, ".price-current__amount, .money-amount").text
            except: pass
            try:
                meta_img = driver.find_element(By.XPATH, "//meta[@property='og:image']")
                img = meta_img.get_attribute("content").split("?")[0]
                result['image'] = img
            except: pass

            # --- TEK BEDEN (ÇANTA) KONTROLÜ ---
            keywords = ["ÇANTA", "BAG", "PARFÜM", "PERFUME", "KOLYE", "KÜPE", "ŞAL", "KEMER", "CÜZDAN", "WALLET"]
            is_accessory = any(k in result['name'].upper() for k in keywords)
            
            if is_accessory:
                result['is_one_size'] = True
                try:
                    add_btn = wait.until(EC.presence_of_element_located((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                    if add_btn.is_enabled() and "disabled" not in add_btn.get_attribute("class"):
                        result['availability'] = 'in_stock'
                        result['sizes'] = ['Standart']
                    else:
                        result['availability'] = 'out_of_stock'
                    result['status'] = 'success'
                    return result
                except:
                    result['status'] = 'success'
                    result['availability'] = 'out_of_stock'
                    return result

            # --- NORMAL (BEDENLİ) ÜRÜN ---
            try:
                add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
                driver.execute_script("arguments[0].click();", add_btn)
                
                wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
                time.sleep(1.5) 
                
                labels = driver.find_elements(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']")
                available_sizes = []
                
                for label in labels:
                    try:
                        raw_text = label.text.strip()
                        if not raw_text: continue
                        
                        full_text_script = "var el = arguments[0]; var parent = el.closest('li') || el.closest('button'); return parent ? parent.innerText : '';"
                        full_text = driver.execute_script(full_text_script, label).upper()
                        forbidden = ["BENZER", "SIMILAR", "YAKINDA", "SOON", "TÜKENDİ", "OUT OF STOCK", "GELİNCE"]
                        if any(f in full_text for f in forbidden): continue
                        
                        is_disabled = driver.execute_script("var el = arguments[0]; var parent = el.closest('li') || el.closest('button'); if (!parent) return false; var classes = parent.className; return classes.includes('is-disabled') || classes.includes('out-of-stock') || parent.hasAttribute('disabled');", label)
                        if not is_disabled:
                            clean_name = clean_size_text(raw_text)
                            available_sizes.append(clean_name)
                    except: continue
                
                result['sizes'] = available_sizes
                result['availability'] = 'in_stock' if available_sizes else 'out_of_stock'
                result['status'] = 'success'

            except TimeoutException:
                result['status'] = 'success'
                result['availability'] = 'out_of_stock'
        
        except Exception as e:
            logger.error(f"Hata: {e}")
            result['status'] = 'error'
        finally:
            driver.quit()
        return result
    return await loop.run_in_executor(None, sync_process)

def create_ui(data, url, target_sizes, last_check_time=None):
    available_targets = []
    
    if data.get('is_one_size'):
        if data['availability'] == 'in_stock':
            status_line = "🟢 <b>STOKTA MEVCUT!</b>"
            sizes_formatted = "Standart Beden"
        else:
            status_line = "🔴 <b>TÜKENDİ</b>"
            sizes_formatted = "<i>Stokta yok</i>"
        tracked_str = "Standart"
    else:
        if 'HEPSI' in target_sizes: available_targets = data['sizes']
        else: available_targets = [s for s in data['sizes'] if s.upper() in target_sizes]

        if available_targets:
            status_line = "🟢 <b>AŞKIM STOKTA!!</b>"
            sizes_formatted = "  ".join([f"<code>[{s}]</code>" for s in available_targets])
        else:
            status_line = "🔴 <b>Tükenmiş Bebeğim :(</b>"
            sizes_formatted = "<i>Pusudayım, bekliyorum...</i>"
        tracked_str = "Tümü" if 'HEPSI' in target_sizes else ", ".join(target_sizes)
    
    if last_check_time:
        check_time = (last_check_time + timedelta(hours=3)).strftime("%H:%M")
    else:
        check_time = (datetime.now() + timedelta(hours=3)).strftime("%H:%M")
    
    caption = (
        f"💎 <b>{data.get('name', 'Zara Güzelliği')}</b>\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🏷 <b>Fiyat:</b> {data.get('price', '-')}\n"
        f"🎯 <b>Takip:</b> {tracked_str}\n"
        f"📦 <b>Durum:</b> {status_line}\n\n"
        f"📏 <b>Mevcut:</b>\n"
        f"└ {sizes_formatted}\n\n"
        f"🕒 <i>Son Kontrol: {check_time}</i>\n"
        f"🔗 <a href='{url}'>Siteye Git Aşkım</a>"
    )
    return caption

# --- ADMIN PANELİ ---
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID: return 
    keyboard = [[InlineKeyboardButton("👥 Kullanıcılar", callback_data="adm_list_users")], [InlineKeyboardButton("❌ Kapat", callback_data="adm_close")]]
    await update.message.reply_text("👮‍♂️ <b>Admin Paneli</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if not data.startswith("adm_"): return 
    await query.answer()
    if data == "adm_close": await query.delete_message(); return
    if data == "adm_list_users":
        if not known_users: await query.edit_message_text("Boş."); return
        keyboard = []
        for uid, udata in known_users.items(): keyboard.append([InlineKeyboardButton(f"👤 {udata.get('name')}", callback_data=f"adm_view_{uid}")])
        keyboard.append([InlineKeyboardButton("🔙 Geri", callback_data="adm_menu")])
        await query.edit_message_text("👥 <b>Kullanıcılar:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    elif data.startswith("adm_view_"):
        target_id = data.replace("adm_view_", "")
        user_products = {k: v for k, v in tracked_products.items() if v['user_id'] == target_id}
        info_text = f"👤 ID: {target_id}\n📦 Ürün Sayısı: {len(user_products)}"
        keyboard = [[InlineKeyboardButton("📩 Mesaj", callback_data=f"adm_msg_{target_id}")], [InlineKeyboardButton("🔙", callback_data="adm_list_users")]]
        await query.edit_message_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    elif data.startswith("adm_msg_"):
        target_id = data.replace("adm_msg_", "")
        admin_reply_mode[ADMIN_ID] = target_id 
        await query.edit_message_text(f"✍️ <b>{target_id}</b>'ye yaz:", parse_mode=ParseMode.HTML)
    elif data == "adm_menu": await admin_command(update, context)

# --- GENEL HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    msg = "👋 <b>Selam Aşkım!</b>\n\nSen yorulma diye Zara ürünlerini ben takip ediyorum. Linki at gerisine karışma sen. 😉❤️"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    if update.callback_query: await update.callback_query.answer()
    await update.effective_message.reply_text("Listeye bakmaya üşendim şuan ya... 🥱")
    await asyncio.sleep(2)
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    if not my_products: await update.effective_message.reply_text("Şaka şaka... Listen boş aşkım."); return
    await update.effective_message.reply_text("Şaka şaka aşkım 🥰 İşte listen:")
    for k, v in my_products.items():
        is_happy = False
        if v.get('is_one_size'):
            is_happy = (v['last_status'] == 'in_stock')
            target_str = "Standart"
        else:
            if 'HEPSI' in v['target_sizes']: is_happy = (v['last_status'] == 'in_stock')
            else: is_happy = (v['last_status'] == 'in_stock_target')
            target_str = "Tümü" if 'HEPSI' in v['target_sizes'] else ",".join(v['target_sizes'])

        icon = "🟢" if is_happy else "🔴"
        last_check = v.get('last_check', datetime.now()) + timedelta(hours=3)
        time_str = last_check.strftime("%H:%M")
        text = f"{icon} <b>{v['name']}</b>\n🕒 <i>{time_str}</i>\n🎯 Hedef: {target_str}\n🔗 <a href='{v['url']}'>Link</a>"
        keyboard = [[InlineKeyboardButton("🗑️ Sil", callback_data=f"del_{k}")]]
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    if user_id not in known_users: known_users[user_id] = {'name': update.effective_user.first_name}
    
    if user_id == ADMIN_ID and user_id in admin_reply_mode:
        target_user = admin_reply_mode.pop(user_id)
        try: await context.bot.send_message(target_user, f"👨‍💻 <b>Admin:</b>\n{text}", parse_mode=ParseMode.HTML); await update.message.reply_text("✅")
        except: await update.message.reply_text("❌")
        return

    if not await is_authorized(update): return

    if "zara.com" in text:
        uid_int = update.effective_user.id
        pending_adds[uid_int] = text
        if uid_int in waiting_for_sizes: del waiting_for_sizes[uid_int]
        keyboard = [[InlineKeyboardButton("Evet çok seviyorum ❤️", callback_data="love_yes")], [InlineKeyboardButton("Hayır ⚠️", callback_data="love_no")]]
        await update.message.reply_text("🤔 <b>Bir saniye... Önce önemli bir soru:</b>\n\nSevgilini seviyor musun?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    if update.effective_user.id in waiting_for_sizes: await process_size_input(update, context); return
    if user_id != ADMIN_ID: await update.message.reply_text("❌ Sadece link at aşkım.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update): return
    query = update.callback_query
    if query.data.startswith("refresh_"): await query.answer("⏳ Bakıyorum...", cache_time=1)
    else: await query.answer()
    
    data = query.data
    user_id = query.from_user.id

    if data == "love_yes":
        if user_id not in pending_adds: await query.edit_message_text("⚠️ Link zaman aşımı."); return
        url = pending_adds.pop(user_id)
        
        await query.edit_message_text("🥰 <b>Ben de seni çok seviyorum aşkımmm!</b>\n\nÜrünü analiz ediyorum, 10sn bekle...", parse_mode=ParseMode.HTML)
        await context.bot.send_chat_action(chat_id=user_id, action="typing")
        
        check_data = await check_stock_selenium(url)
        
        if check_data['status'] == 'error':
            await context.bot.send_message(user_id, "⚠️ Siteye giremedim aşkım.")
            return

        if check_data['is_one_size']:
            key = f"{user_id}_{datetime.now().timestamp()}"
            tracked_products[key] = {
                'url': url, 'name': check_data['name'], 'price': check_data['price'], 'image': check_data['image'],
                'last_status': check_data['availability'], 'target_sizes': ['STANDART'], 'last_check': datetime.now(),
                'chat_id': user_id, 'user_id': str(user_id), 'is_one_size': True
            }
            caption = create_ui(check_data, url, ['STANDART'], datetime.now())
            keyboard = [[InlineKeyboardButton("🔄", callback_data=f"refresh_{key}"), InlineKeyboardButton("❌", callback_data=f"del_{key}")], [InlineKeyboardButton("📋 Listem", callback_data="show_list")]]
            
            if check_data['image']: await context.bot.send_photo(user_id, photo=check_data['image'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            else: await context.bot.send_message(user_id, caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        
        else:
            waiting_for_sizes[user_id] = url 
            await context.bot.send_message(user_id, "Peki hangi bedenleri takip edeyim?\n👉 <b>XS, S</b> gibi yaz veya <b>Hepsi</b> de.", parse_mode=ParseMode.HTML)

    elif data == "love_no":
        if user_id in pending_adds: del pending_adds[user_id]
        await query.edit_message_text("😡 Hıh.", parse_mode=ParseMode.HTML)

    elif data == "show_list": await list_products(update, context)
    elif data.startswith("del_"):
        key = data.replace("del_", "")
        if key in tracked_products: del tracked_products[key]; await query.delete_message(); await context.bot.send_message(query.message.chat_id, "🗑️ Silindi.")
        else:
            try: await query.edit_message_text("Zaten yok.")
            except: pass
    
    elif data.startswith("refresh_"):
        key = data.replace("refresh_", "")
        if key in tracked_products:
            await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
            product = tracked_products[key]
            check_data = await check_stock_selenium(product['url'])
            tracked_products[key]['last_check'] = datetime.now()
            
            if check_data['status'] == 'success':
                tracked_products[key]['last_status'] = check_data['availability']
                new_caption = create_ui(check_data, product['url'], product['target_sizes'], tracked_products[key]['last_check'])
                try: await query.edit_message_caption(caption=new_caption, parse_mode=ParseMode.HTML, reply_markup=query.message.reply_markup)
                except: pass
            else:
                try: await context.bot.send_message(query.message.chat_id, "⚠️ Hata oluştu.")
                except: pass

async def process_size_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw_text = update.message.text.upper().strip()
    if user_id not in waiting_for_sizes: return
    url = waiting_for_sizes[user_id]

    target_sizes = []
    if "HEPSI" in raw_text or "TÜMÜ" in raw_text: target_sizes = ['HEPSI']
    else: target_sizes = [p.strip() for p in raw_text.replace(" ", ",").split(",") if p.strip()]
    
    if not target_sizes: await update.message.reply_text("⚠️ Anlamadım aşkım tekrar yaz."); return
    del waiting_for_sizes[user_id]

    await update.message.reply_text(f"Tamamdır, <b>{', '.join(target_sizes)}</b> için bakıyorum...", parse_mode=ParseMode.HTML)
    
    check_data = await check_stock_selenium(url)
    
    # --- ERROR KONTROLÜ DÜZELTİLDİ ---
    if check_data['status'] == 'error':
        await update.message.reply_text("⚠️ Siteye giremedim bebeğim, sonra deneriz.")
        return

    initial_status = 'out_of_stock'
    if 'HEPSI' in target_sizes:
        if check_data['availability'] == 'in_stock': initial_status = 'in_stock_target'
    else:
        matches = [s for s in check_data['sizes'] if s.upper() in target_sizes]
        if matches: initial_status = 'in_stock_target'

    key = f"{user_id}_{datetime.now().timestamp()}"
    tracked_products[key] = {
        'url': url, 'name': check_data['name'], 'price': check_data['price'], 'image': check_data['image'],
        'last_status': initial_status, 'target_sizes': target_sizes, 'last_check': datetime.now(),
        'chat_id': update.effective_chat.id, 'user_id': str(user_id), 'is_one_size': False
    }
    
    caption = create_ui(check_data, url, target_sizes)
    keyboard = [[InlineKeyboardButton("🔄 Yenile", callback_data=f"refresh_{key}"), InlineKeyboardButton("❌ Sil", callback_data=f"del_{key}")], [InlineKeyboardButton("📋 Listem", callback_data="show_list")]]
    
    if check_data['image']: await update.message.reply_photo(photo=check_data['image'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(text=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    for key, product in list(tracked_products.items()):
        try:
            data = await check_stock_selenium(product['url'])
            tracked_products[key]['last_check'] = datetime.now()
            
            if data['status'] == 'error':
                await context.bot.send_message(product['chat_id'], f"⚠️ Aşkım şu siteye giremedim:\n{product['url']}")
                continue

            is_target_found = False
            found_sizes = []
            if product.get('is_one_size'):
                is_target_found = (data['availability'] == 'in_stock')
            else:
                if 'HEPSI' in product['target_sizes']:
                    if data['availability'] == 'in_stock': is_target_found = True; found_sizes = data['sizes']
                else:
                    found_sizes = [s for s in data['sizes'] if s.upper() in product['target_sizes']]
                    if found_sizes: is_target_found = True
            
            current_status = 'in_stock_target' if is_target_found else 'out_of_stock'

            if product['last_status'] == 'out_of_stock' and current_status == 'in_stock_target':
                caption = (f"🚨🚨 <b>AŞKIM KOŞ STOK GELDİ!</b> 🚨🚨\n\n💎 <b>{data['name']}</b>\n👇 <b>HEMEN AL!</b>")
                keyboard = [[InlineKeyboardButton("🛒 SATIN AL", url=product['url'])]]
                if product.get('image'):
                    try: await context.bot.send_photo(product['chat_id'], photo=product['image'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
                    except: await context.bot.send_message(product['chat_id'], text=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
                else: await context.bot.send_message(product['chat_id'], text=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            
            tracked_products[key]['last_status'] = current_status
            await asyncio.sleep(5)
        except: pass

async def post_init(application: Application):
    await application.bot.set_my_commands([BotCommand("start", "Başlat"), BotCommand("list", "Listem")])

if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(CommandHandler("admin", admin_command)) 
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)) 
    if app.job_queue: app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=10)
    print("Final Bot Başladı 🚀...")
    app.run_polling()
