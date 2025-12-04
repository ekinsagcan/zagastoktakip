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

# Veritabanı
tracked_products: Dict[str, Dict] = {}
pending_adds: Dict[str, str] = {} 

# --- TARAYICI MOTORU (TURBO MOD) ---
def get_driver():
    chrome_options = Options()
    # Eager modu: Sayfanın %100 bitmesini beklemez, iskelet yüklenince başlar (HIZLI)
    chrome_options.page_load_strategy = 'eager' 
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # HIZ İÇİN: Tarayıcıda resimleri yüklemeyi engelliyoruz.
    # (Merak etme, ürün fotosunu HTML kodundan çektiğimiz için sana yine foto gelecek!)
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)
    
    return webdriver.Chrome(options=chrome_options)

async def check_stock_selenium(url: str):
    # --- 1. OTOMATİK LINK DÜZELTME ---
    # Eğer linkte /tr/tr yoksa, biz ekleriz.
    if "zara.com" in url and "/tr/tr" not in url:
        # Link yapısını bozmadan araya ekleyelim
        url = url.replace("zara.com/", "zara.com/tr/tr/")
        logger.info(f"🇹🇷 Link Türkçe siteye çevrildi: {url}")

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
            # Bekleme süresini azalttık, dinamik bekleme kullanacağız
            wait = WebDriverWait(driver, 10) 

            # 0. KONUM PENCERESİ (Hızlı Geçiş)
            try:
                # Maksimum 3 saniye bekle, varsa kapat, yoksa devam et
                geo_btn = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-qa-action='stay-in-store']")))
                driver.execute_script("arguments[0].click();", geo_btn)
            except: pass

            # 1. ÇEREZ (Varsa tek tıkla geç)
            try:
                cookie = driver.find_element(By.ID, "onetrust-accept-btn-handler")
                driver.execute_script("arguments[0].click();", cookie)
            except: pass

            # 2. HIZLI VERİ ÇEKME
            try: result['name'] = driver.find_element(By.TAG_NAME, "h1").text
            except: pass

            try: result['price'] = driver.find_element(By.CSS_SELECTOR, ".price-current__amount, .money-amount").text
            except: pass

            # RESİM (Meta Tag'den alıyoruz, sayfanın yüklenmesini beklemeye gerek yok)
            try:
                meta_img = driver.find_element(By.XPATH, "//meta[@property='og:image']")
                img = meta_img.get_attribute("content").split("?")[0]
                result['image'] = img
            except: pass

            # 3. STOK KONTROL (Akıllı Tarama)
            try:
                add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
                driver.execute_script("arguments[0].click();", add_btn)
                
                # Modal açılmasını bekle
                wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
                
                # Ufak bir bekleme (Animasyon için) - Bunu 2 saniyeye indirdim
                time.sleep(1.5) 
                
                labels = driver.find_elements(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']")
                available_sizes = []
                
                for label in labels:
                    try:
                        txt = label.text.strip()
                        if not txt: continue
                        # Disabled kontrolü
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
                # Buton yoksa veya modal açılmadıysa stok yoktur
                result['status'] = 'success'
        
        except Exception as e:
            logger.error(f"Hata: {e}")
        finally:
            driver.quit()
        return result

    return await loop.run_in_executor(None, sync_process)

# --- UI FONKSİYONLARI ---

def create_ui(data, url):
    if data['availability'] == 'in_stock':
        status_line = "🟢 <b>STOKTA ZATEN ASK</b>"
        sizes_formatted = "  ".join([f"<code>[{s}]</code>" for s in data['sizes']])
    else:
        status_line = "🔴 <b>TÜKENMİS MLSF</b>"
        sizes_formatted = "<i>Stoğa girince bakcam</i>"

    separator = "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
    
    caption = (
        f"<b>{data.get('name', 'Zara Ürünü')}</b>\n"
        f"{separator}\n"
        f"🏷 <b>Fiyat:</b> {data.get('price', '-')}\n"
        f"📦 <b>Durum:</b> {status_line}\n\n"
        f"📏 <b>Bedenler:</b>\n"
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
    msg = (
        "👋 <b>Selam! Aşkım</b>\n\n"
        "Senin için zara ürünlerini takip edicem. Link gönder gerisine karışma. 😉"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# --- LİSTE (Şakalı) ---
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Listeye bakmaya üşendim şuan ya... 🥱")
    await asyncio.sleep(2)
    
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not my_products:
        await update.message.reply_text("Şaka şaka... Ama cidden listen boş aşkım. Link at da çalışayım. 😘")
        return

    await update.message.reply_text("Şaka şaka aşkım 🥰 İşte takip listen:")

    for k, v in my_products.items():
        icon = "🟢" if v['last_status'] == 'in_stock' else "🔴"
        text = f"{icon} <b>{v['name']}</b>\n🔗 <a href='{v['url']}'>Link</a>"
        keyboard = [[InlineKeyboardButton("🗑️ Sil", callback_data=f"del_{k}")]]
        await update.message.reply_text(
            text, 
            parse_mode=ParseMode.HTML, 
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )

# --- ÜRÜN EKLEME (SORU KISMI) ---
async def add_product_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Sadece Zara linki at aşkım.", parse_mode=ParseMode.HTML)
        return

    # Link düzeltme işlemini burada önizleme yapabiliriz ama asıl işlem Selenium'da
    user_id = update.effective_user.id
    pending_adds[user_id] = url

    keyboard = [
        [InlineKeyboardButton("Evet çok seviyorum ❤️", callback_data="love_yes")],
        [InlineKeyboardButton("Hayır ⚠️", callback_data="love_no")]
    ]
    
    await update.message.reply_text(
        "🤔 <b>Bir saniye... Önce önemli bir soru:</b>\n\n"
        "Sevgilini seviyor musun?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

# --- BUTON İŞLEMLERİ ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "love_yes":
        if user_id not in pending_adds:
            await query.edit_message_text("⚠️ Link zaman aşımına uğradı, tekrar atar mısın?")
            return

        url = pending_adds.pop(user_id) 
        
        await query.edit_message_text(
            "🥰 <b>Ben de seni çok seviyorum aşkımmm!</b>\n\n"
            "Hemen senin için ürüne bakıyorum, bekle...",
            parse_mode=ParseMode.HTML
        )
        
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
        
        check_data = await check_stock_selenium(url)
        
        if check_data['status'] == 'error':
            await context.bot.send_message(query.message.chat_id, "⚠️ Siteye giremedim aşkım ya, sonra tekrar deneriz.")
            return

        key = f"{user_id}_{datetime.now().timestamp()}"
        # Veritabanına kaydederken düzeltilmiş URL ile kaydetmek önemli değil, 
        # çünkü bot check yaparken yine düzeltecek. Ama temiz olsun.
        if "zara.com" in url and "/tr/tr" not in url:
             url = url.replace("zara.com/", "zara.com/tr/tr/")

        tracked_products[key] = {
            'url': url,
            'name': check_data['name'],
            'price': check_data['price'],
            'image': check_data['image'],
            'last_status': check_data['availability'],
            'chat_id': query.message.chat_id,
            'user_id': str(user_id)
        }
        
        caption = create_ui(check_data, url)
        keyboard = [
            [InlineKeyboardButton("🔄 Yenile", callback_data=f"refresh_{key}"), InlineKeyboardButton("❌ Sil", callback_data=f"del_{key}")],
            [InlineKeyboardButton("🔗 Zara'da Aç", url=url)]
        ]
        
        if check_data['image']:
            try:
                await context.bot.send_photo(query.message.chat_id, photo=check_data['image'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            except:
                await context.bot.send_message(query.message.chat_id, caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await context.bot.send_message(query.message.chat_id, caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "love_no":
        if user_id in pending_adds:
            del pending_adds[user_id]
        
        await query.edit_message_text(
            "😡 <b>İnşallah stoğa girmez hiç!</b>\n"
            "Benimle bi daha konuşma. Takip falan etmiyorum ürünü.",
            parse_mode=ParseMode.HTML
        )

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
                tracked_products[key]['last_status'] = check_data['availability']
                new_caption = create_ui(check_data, product['url'])
                try: 
                    await query.edit_message_caption(caption=new_caption, parse_mode=ParseMode.HTML, reply_markup=query.message.reply_markup)
                    await query.answer("✅ Kontrol ettim, güncel.")
                except: 
                    await query.answer("✅ Değişiklik yok aşkım.")
            else:
                await query.answer("⚠️ Hata oluştu.", show_alert=True)

async def check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    for key, product in list(tracked_products.items()):
        try:
            data = await check_stock_selenium(product['url'])
            if data['status'] == 'error': continue
            
            if product['last_status'] == 'out_of_stock' and data['availability'] == 'in_stock':
                caption = (
                    f"🚨🚨 <b>AŞKIM KOŞ STOK GELDİ!</b> 🚨🚨\n\n"
                    f"💎 <b>{data['name']}</b>\n"
                    f"📏 Bedenler: <code>{', '.join(data['sizes'])}</code>\n\n"
                    f"👇 <b>HEMEN AL BUTONUNA BAS!</b>"
                )
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
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("zara.com"), add_product_request))
    app.add_handler(CallbackQueryHandler(button_callback))
    if app.job_queue:
        app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=10)
    print("Turbo Love Bot Başladı ❤️...")
    app.run_polling()
