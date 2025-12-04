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

async def check_stock_selenium(url: str, context: ContextTypes.DEFAULT_TYPE = None, chat_id=None):
    driver = None
    result = {
        'status': 'error',
        'name': 'Zara Ürünü',
        'availability': 'out_of_stock',
        'sizes': [],
        'image': None, 
        'price': ''
    }

    try:
        loop = asyncio.get_running_loop()
        
        def sync_process():
            inner_driver = get_driver()
            try:
                inner_driver.get(url)
                wait = WebDriverWait(inner_driver, 15)
                time.sleep(2)

                # --- 0. KONUM PENCERESİ (Önceki fix) ---
                try:
                    geo_btn = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-qa-action='stay-in-store']")))
                    time.sleep(3) 
                    inner_driver.execute_script("arguments[0].click();", geo_btn)
                    time.sleep(2)
                except: pass

                # --- 1. ÇEREZ ---
                try:
                    cookie = inner_driver.find_element(By.ID, "onetrust-accept-btn-handler")
                    inner_driver.execute_script("arguments[0].click();", cookie)
                except: pass

                # --- 2. VERİ ÇEKME (İsim, Fiyat, RESİM) ---
                try:
                    result['name'] = inner_driver.find_element(By.TAG_NAME, "h1").text
                except: pass

                try:
                    price_el = inner_driver.find_element(By.CSS_SELECTOR, ".price-current__amount, .money-amount")
                    result['price'] = price_el.text
                except: pass

                # --- RESİM ÇEKME (GÜNCELLENDİ: META TAG YÖNTEMİ) ---
                try:
                    # Yöntem 1: En garantisi (og:image)
                    meta_img = inner_driver.find_element(By.XPATH, "//meta[@property='og:image']")
                    img_url = meta_img.get_attribute("content")
                    if img_url:
                        # Zara bazen query parametreleri ekler, temizleyelim
                        result['image'] = img_url.split("?")[0]
                except:
                    try:
                        # Yöntem 2: JSON-LD Schema (Yedek)
                        import json
                        script_tag = inner_driver.find_element(By.XPATH, "//script[@type='application/ld+json']")
                        data = json.loads(script_tag.get_attribute("innerHTML"))
                        if isinstance(data, list): data = data[0]
                        result['image'] = data.get('image', [None])[0]
                    except:
                        pass # Resim bulunamazsa metin olarak devam eder

                # --- 3. EKLE BUTONU ---
                try:
                    add_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@data-qa-action='add-to-cart']")))
                    inner_driver.execute_script("arguments[0].scrollIntoView(true);", add_btn)
                    time.sleep(1)
                    inner_driver.execute_script("arguments[0].click();", add_btn)
                    
                    wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-qa-qualifier='size-selector-sizes-size-label']")))
                    time.sleep(2) 
                    
                    # --- 4. AKILLI BEDEN TARAMA ---
                    labels = inner_driver.find_elements(By.CSS_SELECTOR, "[data-qa-qualifier='size-selector-sizes-size-label']")
                    available_sizes = []
                    
                    for label in labels:
                        try:
                            txt = label.text.strip()
                            if not txt: continue
                            
                            is_disabled = inner_driver.execute_script("""
                                var el = arguments[0];
                                var parent = el.closest('li') || el.closest('button');
                                if (!parent) return false;
                                var classes = parent.className;
                                return classes.includes('is-disabled') || classes.includes('out-of-stock') || parent.hasAttribute('disabled');
                            """, label)
                            
                            if not is_disabled:
                                available_sizes.append(txt)
                        except: continue
                    
                    result['sizes'] = available_sizes
                    result['availability'] = 'in_stock' if available_sizes else 'out_of_stock'
                    result['status'] = 'success'
                    
                except TimeoutException:
                    result['status'] = 'success' 
            
            except Exception as e:
                logger.error(f"İç Hata: {e}")
            finally:
                inner_driver.quit()
            return result

        final_data = await loop.run_in_executor(None, sync_process)
        return final_data

    except Exception as e:
        logger.error(f"Genel Hata: {e}")
        return result

# --- TELEGRAM BOT KOMUTLARI ---

async def set_commands(application: Application):
    commands = [
        BotCommand("start", "Botu başlat"),
        BotCommand("add", "Yeni ürün ekle"),
        BotCommand("list", "Listem"),
        BotCommand("help", "Yardım")
    ]
    await application.bot.set_my_commands(commands)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    msg = (
        f"👋 *Merhaba {user}!*\n\n"
        "🛍️ *Zara Stok Takip Botuna* hoş geldin.\n"
        "Link gönder, takibe başlayayım."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Geçersiz link.", parse_mode=ParseMode.MARKDOWN)
        return

    msg = await update.message.reply_text("🔎 *Ürün taranıyor...*", parse_mode=ParseMode.MARKDOWN)
    
    data = await check_stock_selenium(url, context, update.effective_chat.id)
    
    if data['status'] == 'error':
        await msg.edit_text("⚠️ Hata oluştu.")
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
    
    await msg.delete() 

    status_text = "🟢 *STOKTA VAR*" if data['availability'] == 'in_stock' else "🔴 *TÜKENDİ*"
    sizes_text = ", ".join(data['sizes']) if data['sizes'] else "Stok yok"
    
    caption = (
        f"✅ *TAKİBE ALINDI*\n\n"
        f"👗 *{data['name']}*\n"
        f"🏷️ Fiyat: `{data['price']}`\n"
        f"📊 Durum: {status_text}\n"
        f"📏 Bedenler: `{sizes_text}`"
    )

    keyboard = [
        [InlineKeyboardButton("🔗 Ürüne Git", url=url)],
        [InlineKeyboardButton("🗑️ Takibi Bırak", callback_data=f"del_{key}")]
    ]
    
    # Resim varsa gönder, yoksa sadece mesaj
    if data['image']:
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=data['image'],
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            # Resim gönderme hatası olursa (bazen URL çok uzun olur) metin olarak at
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=caption,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not my_products:
        await update.message.reply_text("📭 Listen boş.", parse_mode=ParseMode.MARKDOWN)
        return

    await update.message.reply_text(f"📋 *Listen ({len(my_products)} Ürün)*", parse_mode=ParseMode.MARKDOWN)

    for k, v in my_products.items():
        icon = "🟢" if v['last_status'] == 'in_stock' else "🔴"
        text = f"{icon} *{v['name']}*\n🔗 [Link]({v['url']})"
        keyboard = [[InlineKeyboardButton("🗑️ Sil", callback_data=f"del_{k}")]]
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("del_"):
        key = query.data.replace("del_", "")
        if key in tracked_products: 
            product_name = tracked_products[key]['name']
            del tracked_products[key]
            await query.edit_message_text(f"🗑️ *{product_name}* silindi.", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text("❌ Ürün zaten silinmiş.")

async def check_job(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    for key, product in list(tracked_products.items()):
        try:
            data = await check_stock_selenium(product['url'])
            if data['status'] == 'error': continue
            
            if product['last_status'] == 'out_of_stock' and data['availability'] == 'in_stock':
                
                caption = (
                    f"🚨🚨 *STOK GELDİ! KOŞ!* 🚨🚨\n\n"
                    f"👗 *{data['name']}*\n"
                    f"📏 *Mevcut Bedenler:* `{', '.join(data['sizes'])}`\n"
                    f"🏷️ Fiyat: `{product.get('price', '-')}`\n\n"
                    f"👇 *HEMEN AL BUTONUNA BAS!*"
                )
                keyboard = [[InlineKeyboardButton("🛒 SATIN AL", url=product['url'])]]
                
                if product.get('image'):
                    try:
                        await context.bot.send_photo(product['chat_id'], photo=product['image'], caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
                    except:
                         await context.bot.send_message(product['chat_id'], text=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await context.bot.send_message(product['chat_id'], text=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            
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
    print("Bot Başladı (Resim Fix)...")
    app.run_polling()
