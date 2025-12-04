import os
import logging
import asyncio
import json
import re
from datetime import datetime
from typing import Dict, List, Optional
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ==========================================
# AYARLAR
# ==========================================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'TOKEN_BURAYA')
ALLOWED_USERS = os.getenv('ALLOWED_USERS', '').split(',')
CHECK_INTERVAL = 60 # Saniye (Artık çok hızlı olduğu için 1 dakikada bir bakabilir)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

tracked_products: Dict[str, Dict] = {}

class ZaraFastChecker:
    """Tarayıcısız, Direkt API ile Işık Hızında Kontrol"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json', # JSON istiyoruz
            'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.zara.com/'
        }
    
    def extract_product_id(self, url: str) -> Optional[str]:
        """Linkten p123456 gibi olan ID'yi çeker"""
        # Örnek link: .../gomlek-p0123456.html -> ID: 123456 (Baştaki 0 ve p harfi atılır)
        match = re.search(r'p(\d+)\.html', url)
        return match.group(1) if match else None

    async def get_product_data(self, url: str):
        product_id = self.extract_product_id(url)
        if not product_id:
            logger.error("Ürün ID'si bulunamadı.")
            return None

        # ZARA'NIN GİZLİ API ENDPOINT'İ
        # Bu adres, ürün sayfasındaki tüm detayları JSON olarak verir.
        api_url = f"https://www.zara.com/tr/tr/products-details?productIds={product_id}&ajax=true"

        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(api_url, timeout=10) as response:
                    if response.status != 200:
                        logger.error(f"API Hatası: {response.status}")
                        return None
                    
                    data = await response.json()
                    
                    # Gelen veri bir liste içindedir, ilkini alalım
                    if not data or len(data) == 0:
                        return None
                    
                    product_json = data[0]
                    
                    # --- Verileri Ayıklama ---
                    name = product_json.get('name', 'Zara Ürünü')
                    price_val = product_json.get('price', {}).get('value', 0) / 100 # Fiyat kuruş cinsinden gelir
                    price_fmt = f"{price_val} TL"
                    
                    # Bedenleri ve Stokları Bulma
                    sizes_available = []
                    
                    # "colors" altında beden detayları olur
                    for color in product_json.get('detail', {}).get('colors', []):
                        for size in color.get('sizes', []):
                            size_name = size.get('name')
                            status = size.get('availability') # 'in_stock', 'out_of_stock', 'back_soon'
                            
                            if status == 'in_stock':
                                sizes_available.append(size_name)
                    
                    availability = 'in_stock' if sizes_available else 'out_of_stock'
                    
                    return {
                        'id': product_id,
                        'url': url,
                        'name': name,
                        'price': price_fmt,
                        'availability': availability,
                        'sizes': sizes_available
                    }

            except Exception as e:
                logger.error(f"Bağlantı hatası: {e}")
                return None

# ==========================================
# TELEGRAM BOT KISMI (Değişmedi)
# ==========================================

# Yetki kontrolü decorator
def check_auth(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if ALLOWED_USERS and user_id not in ALLOWED_USERS and ALLOWED_USERS != ['']:
            await update.message.reply_text("⛔ Yetkiniz yok.")
            return
        return await func(update, context)
    return wrapper

@check_auth
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 *Hızlı Zara Bot*\nLink gönder, saniyeler içinde takip başlasın.", parse_mode='Markdown')

@check_auth
async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "zara.com" not in url:
        await update.message.reply_text("❌ Geçersiz link.")
        return

    msg = await update.message.reply_text("⚡ API ile kontrol ediliyor...")
    
    checker = ZaraFastChecker()
    info = await checker.get_product_data(url)
    
    if not info:
        await msg.edit_text("❌ Ürün bilgisi çekilemedi. Linki kontrol edin.")
        return

    user_id = str(update.effective_user.id)
    key = f"{user_id}_{info['id']}"
    
    tracked_products[key] = {
        **info,
        'chat_id': update.effective_chat.id,
        'user_id': user_id
    }
    
    status_icon = "✅" if info['availability'] == 'in_stock' else "🔴"
    sizes_str = ", ".join(info['sizes']) if info['sizes'] else "Yok"
    
    await msg.edit_text(
        f"✅ *Takibe Alındı (Hızlı Mod)*\n"
        f"📦 {info['name']}\n"
        f"💰 {info['price']}\n"
        f"{status_icon} Stok: {sizes_str}",
        parse_mode='Markdown'
    )

@check_auth
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    my_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not my_products:
        await update.message.reply_text("📭 Listeniz boş.")
        return

    keyboard = []
    text = "📋 *Takip Listesi:*\n"
    for key, p in my_products.items():
        st = "✅" if p['availability'] == 'in_stock' else "🔴"
        text += f"{st} {p['name']}\n"
        keyboard.append([InlineKeyboardButton(f"🗑 Sil: {p['name'][:15]}", callback_data=f"del_{key}")])
    
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("del_"):
        key = query.data.replace("del_", "")
        if key in tracked_products:
            del tracked_products[key]
            await query.edit_message_text("🗑 Ürün silindi.")

# PERİYODİK KONTROL
async def periodic_check(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products: return
    
    checker = ZaraFastChecker()
    
    # Listeyi kopyala
    for key, product in list(tracked_products.items()):
        try:
            # Çok hızlı olduğu için her ürün arasında sadece 1 saniye bekle
            new_info = await checker.get_product_data(product['url'])
            
            if not new_info: continue
            
            old_status = product['availability']
            new_status = new_info['availability']
            old_sizes = set(product['sizes'])
            new_sizes = set(new_info['sizes'])
            
            # Bildirim Mantığı:
            # 1. Stok yoktu -> Stok geldi
            # 2. Stok vardı ama YENİ bir beden eklendi (Örn: Sadece S vardı, M de geldi)
            if (old_status == 'out_of_stock' and new_status == 'in_stock') or \
               (new_status == 'in_stock' and not new_sizes.issubset(old_sizes)):
                
                diff_sizes = list(new_sizes - old_sizes)
                sizes_msg = ", ".join(new_info['sizes'])
                
                await context.bot.send_message(
                    chat_id=product['chat_id'],
                    text=f"🚨 *STOK GELDİ!* 🚨\n\n📦 {new_info['name']}\n✅ Mevcut: {sizes_msg}\n🔗 [Satın Al]({product['url']})",
                    parse_mode='Markdown'
                )
            
            # Bilgileri güncelle
            tracked_products[key].update(new_info)
            await asyncio.sleep(1) 
            
        except Exception as e:
            logger.error(f"Hata: {e}")

if __name__ == '__main__':
    if TELEGRAM_TOKEN == 'TOKEN_BURAYA':
        print("Token girmeyi unutma!")
        exit()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("zara.com"), add_url))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    if app.job_queue:
        app.job_queue.run_repeating(periodic_check, interval=CHECK_INTERVAL, first=5)
    
    print("🚀 Hızlı Bot Başlatıldı...")
    app.run_polling()
