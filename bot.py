import os
import logging
import asyncio
import json
import re
from datetime import datetime
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
# Playwright importu
from playwright.async_api import async_playwright

# Logging ayarları
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables (Senin ayarların)
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ALLOWED_USERS = os.getenv('ALLOWED_USERS', '').split(',')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '300')) 

# Ürün veritabanı
tracked_products: Dict[str, Dict] = {}

class ZaraStockChecker:
    """Zara ürün stok kontrolü için sınıf (Playwright tabanlı)"""
    
    def __init__(self):
        self.base_url = "https://www.zara.com"
    
    def extract_product_id(self, url: str) -> Optional[str]:
        match = re.search(r'p(\d+)\.html', url)
        return match.group(1) if match else None
    
    async def get_product_info(self, url: str) -> Optional[Dict]:
        """Ürün bilgilerini ve stok durumunu Playwright ile getirir"""
        product_id = self.extract_product_id(url)
        if not product_id:
            return None

        async with async_playwright() as p:
            try:
                # Senin kodundaki tarayıcı ayarlarını korudum
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                page = await context.new_page()
                
                logger.info(f"Sayfaya gidiliyor: {url}")
                await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                
                # İsim ve Fiyatı al (Sayfa ilk açıldığında görünenler)
                try:
                    await page.wait_for_selector('h1', timeout=15000)
                    name = await page.eval_on_selector("h1", "el => el.innerText")
                except:
                    name = "Zara Ürünü"

                try:
                    price = await page.eval_on_selector(".price-current__amount, .money-amount", "el => el.innerText")
                except:
                    price = "Fiyat Alınamadı"

                # --- KRİTİK GÜNCELLEME BURADA BAŞLIYOR ---
                
                availability = 'unknown'
                sizes_available = []

                # 1. ADIM: "Tükendi" (Benzer Ürünler) butonu var mı?
                # Varsa direkt stok yok de ve çık.
                is_sold_out = await page.query_selector("button[data-qa-action='show-similar-products']")
                
                if is_sold_out:
                    logger.info("Ürün Tükendi (Benzer Ürünler butonu görüldü).")
                    availability = 'out_of_stock'
                
                else:
                    # 2. ADIM: "Ekle" Butonuna Tıkla
                    # BeautifulSoup bunu yapamazdı, Playwright yapabilir.
                    try:
                        add_button = await page.query_selector("button[data-qa-action='add-to-cart']")
                        
                        if add_button:
                            await add_button.click()
                            
                            # 3. ADIM: Beden penceresinin (Modal) açılmasını bekle
                            # Senin verdiğin data-qa-qualifier etiketini bekliyoruz.
                            try:
                                await page.wait_for_selector("div[data-qa-qualifier='size-selector-sizes-size-label']", state="visible", timeout=5000)
                                
                                # 4. ADIM: Açılan penceredeki bedenleri oku
                                # Disabled olmayan (stokta olan) bedenleri topluyoruz.
                                size_elements = await page.query_selector_all("li.size-selector-list__item")
                                
                                for element in size_elements:
                                    # Sınıf listesini kontrol et (disabled mi?)
                                    class_list = await element.get_attribute("class")
                                    if "is-disabled" in class_list or "out-of-stock" in class_list:
                                        continue
                                    
                                    # Beden ismini al
                                    text_element = await element.query_selector("div[data-qa-qualifier='size-selector-sizes-size-label']")
                                    if text_element:
                                        text = await text_element.inner_text()
                                        sizes_available.append(text)
                                
                                if sizes_available:
                                    availability = 'in_stock'
                                else:
                                    availability = 'out_of_stock'
                                    
                            except Exception as e:
                                logger.warning(f"Beden penceresi açılmadı veya zaman aşımı: {e}")
                                availability = 'out_of_stock' # Pencere açılmadıysa muhtemelen hata var veya stok yok
                        else:
                            # Ekle butonu yoksa stok yoktur
                            availability = 'out_of_stock'
                            
                    except Exception as click_error:
                        logger.error(f"Buton tıklama hatası: {click_error}")
                        availability = 'out_of_stock'

                await browser.close()

                return {
                    'id': product_id,
                    'url': url,
                    'name': name,
                    'price': price,
                    'availability': availability,
                    'sizes': sizes_available,
                    'last_check': datetime.now().isoformat()
                }

            except Exception as e:
                logger.error(f"Playwright hatası: {e}")
                return None


# Yetki kontrolü
def check_authorization(func):
    """Kullanıcı yetkisi kontrolü için decorator"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if ALLOWED_USERS and user_id not in ALLOWED_USERS and ALLOWED_USERS != ['']:
            await update.message.reply_text(
                "⛔ Bu botu kullanma yetkiniz yok.\n"
                f"Kullanıcı ID: {user_id}"
            )
            return
        return await func(update, context)
    return wrapper


@check_authorization
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 *Zara Stok Takip Botuna Hoş Geldiniz!*\n\n"
        "Bu bot ile Zara ürünlerinin stok durumunu takip edebilirsiniz.\n\n"
        "*Komutlar:*\n"
        "• /add - Yeni ürün ekle\n"
        "• /list - Takip edilen ürünleri listele\n"
        "• /remove - Ürün takibini durdur\n"
        "• /check - Manuel stok kontrolü yap\n"
        "• /help - Yardım menüsü\n\n"
        "Başlamak için bir Zara ürün linki gönderin! 🛍️"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')


@check_authorization
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 *Nasıl Kullanılır?*\n\n"
        "*1. Ürün Eklemek için:*\n"
        "• /add komutunu kullanın\n"
        "• Veya direkt Zara ürün linkini gönderin\n"
        "• Örnek: `https://www.zara.com/tr/tr/product-p12345.html`\n\n"
        "*2. Ürünleri Görmek için:*\n"
        "• /list komutu ile tüm takip edilen ürünleri görün\n\n"
        "*3. Ürün Silmek için:*\n"
        "• /remove komutu ile listeden seçerek silin\n\n"
        "*4. Manuel Kontrol için:*\n"
        "• /check komutu ile anında stok kontrolü yapın\n\n"
        "Bot otomatik olarak her 5 dakikada bir ürünleri kontrol eder ve "
        "stokta yeni ürün olduğunda size bildirim gönderir! 🔔"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


@check_authorization
async def add_product_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 Lütfen takip etmek istediğiniz Zara ürününün linkini gönderin:\n\n"
        "Örnek:\n"
        "`https://www.zara.com/tr/tr/product-p12345.html`",
        parse_mode='Markdown'
    )


@check_authorization
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    
    if 'zara.com' not in url:
        await update.message.reply_text(
            "❌ Lütfen geçerli bir Zara ürün linki gönderin."
        )
        return
    
    status_msg = await update.message.reply_text("🔍 Ürün bilgileri alınıyor (Ekle butonuna basılıyor, lütfen bekleyin)...")
    
    checker = ZaraStockChecker()
    product_info = await checker.get_product_info(url)
    
    if not product_info:
        await status_msg.edit_text(
            "❌ Ürün bilgileri alınamadı. Link geçersiz veya site yanıt vermiyor."
        )
        return
    
    user_id = str(update.effective_user.id)
    product_key = f"{user_id}_{product_info['id']}"
    
    if product_key in tracked_products:
        await status_msg.edit_text(
            "⚠️ Bu ürün zaten takip ediliyor!"
        )
        return
    
    tracked_products[product_key] = {
        **product_info,
        'user_id': user_id,
        'chat_id': update.effective_chat.id,
        'added_at': datetime.now().isoformat()
    }
    
    stock_emoji = "✅" if product_info['availability'] == 'in_stock' else "❌"
    sizes_text = ", ".join(product_info['sizes']) if product_info['sizes'] else "Yok"
    
    response = (
        f"✨ *Ürün Eklendi!*\n\n"
        f"📦 *{product_info['name']}*\n"
        f"💰 Fiyat: {product_info['price']}\n"
        f"{stock_emoji} Stok: {'Mevcut' if product_info['availability'] == 'in_stock' else 'Tükendi'}\n"
        f"👕 Bedenler: {sizes_text}\n\n"
        f"Ürün stok durumu otomatik olarak takip edilecek. "
        f"Yeni stok geldiğinde bildirim alacaksınız! 🔔"
    )
    
    await status_msg.edit_text(response, parse_mode='Markdown')


@check_authorization
async def list_products_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not user_products:
        await update.message.reply_text(
            "📭 Henüz takip edilen ürün yok.\n\n"
            "Ürün eklemek için /add komutunu kullanın veya "
            "direkt Zara ürün linkini gönderin."
        )
        return
    
    response = "🛍️ *Takip Edilen Ürünler:*\n\n"
    
    for i, (key, product) in enumerate(user_products.items(), 1):
        stock_emoji = "✅" if product['availability'] == 'in_stock' else "❌"
        sizes_text = ", ".join(product['sizes'][:3]) if product['sizes'] else "Yok"
        if len(product['sizes']) > 3:
            sizes_text += "..."
        
        response += (
            f"{i}. *{product['name'][:40]}...*\n"
            f"   💰 {product['price']}\n"
            f"   {stock_emoji} Stok: {'Mevcut' if product['availability'] == 'in_stock' else 'Tükendi'}\n"
            f"   👕 Bedenler: {sizes_text}\n"
            f"   🔗 [Ürüne Git]({product['url']})\n\n"
        )
    
    response += f"_Toplam {len(user_products)} ürün takip ediliyor._"
    
    await update.message.reply_text(
        response, 
        parse_mode='Markdown',
        disable_web_page_preview=True
    )


@check_authorization
async def remove_product_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not user_products:
        await update.message.reply_text(
            "📭 Silinecek ürün yok."
        )
        return
    
    keyboard = []
    for key, product in user_products.items():
        keyboard.append([
            InlineKeyboardButton(
                f"🗑️ {product['name'][:35]}...",
                callback_data=f"remove_{key}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Hangi ürünü silmek istiyorsunuz?",
        reply_markup=reply_markup
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("❌ İşlem iptal edildi.")
        return
    
    if query.data.startswith("remove_"):
        product_key = query.data.replace("remove_", "")
        
        if product_key in tracked_products:
            product = tracked_products[product_key]
            del tracked_products[product_key]
            
            await query.edit_message_text(
                f"✅ *Ürün silindi:*\n\n"
                f"{product['name']}\n\n"
                f"Artık bu ürün için bildirim almayacaksınız.",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Ürün bulunamadı.")


@check_authorization
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_products = {k: v for k, v in tracked_products.items() if v['user_id'] == user_id}
    
    if not user_products:
        await update.message.reply_text("📭 Kontrol edilecek ürün yok.")
        return
    
    status_msg = await update.message.reply_text(
        f"🔍 {len(user_products)} ürün kontrol ediliyor..."
    )
    
    checker = ZaraStockChecker()
    results = []
    
    for key, product in user_products.items():
        new_info = await checker.get_product_info(product['url'])
        if new_info:
            tracked_products[key].update(new_info)
            results.append((product['name'], new_info['availability'], new_info['sizes']))
        
        await asyncio.sleep(2)
    
    response = "📊 *Stok Kontrol Sonuçları:*\n\n"
    for name, availability, sizes in results:
        emoji = "✅" if availability == 'in_stock' else "❌"
        status = "Stokta" if availability == 'in_stock' else "Tükendi"
        sizes_str = f"({', '.join(sizes)})" if sizes else ""
        response += f"{emoji} {name[:35]}...: {status} {sizes_str}\n"
    
    await status_msg.edit_text(response, parse_mode='Markdown')


async def periodic_check(context: ContextTypes.DEFAULT_TYPE):
    if not tracked_products:
        return
    
    logger.info(f"Periyodik kontrol başlatıldı - {len(tracked_products)} ürün")
    
    checker = ZaraStockChecker()
    
    for key, product in list(tracked_products.items()):
        try:
            new_info = await checker.get_product_info(product['url'])
            
            if not new_info:
                continue
            
            old_availability = product['availability']
            new_availability = new_info['availability']
            
            # Stok durumu değiştiyse bildirim gönder
            if old_availability != 'in_stock' and new_availability == 'in_stock':
                sizes_text = ", ".join(new_info['sizes']) if new_info['sizes'] else "Yok"
                
                message = (
                    "🎉 *STOK GELDİ!*\n\n"
                    f"📦 *{new_info['name']}*\n"
                    f"💰 Fiyat: {new_info['price']}\n"
                    f"👕 Bedenler: {sizes_text}\n\n"
                    f"🔗 [Ürünü Satın Al]({product['url']})\n\n"
                    f"⚡ Hemen sipariş verin, stok tükenmeden!"
                )
                
                await context.bot.send_message(
                    chat_id=product['chat_id'],
                    text=message,
                    parse_mode='Markdown',
                    disable_web_page_preview=False
                )
            
            tracked_products[key].update(new_info)
            
        except Exception as e:
            logger.error(f"Ürün kontrolünde hata ({key}): {e}")
        
        await asyncio.sleep(5)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Hata oluştu: {context.error}")


def main():
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN environment variable tanımlanmamış!")
        return
    
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_product_command))
    application.add_handler(CommandHandler("list", list_products_command))
    application.add_handler(CommandHandler("remove", remove_product_command))
    application.add_handler(CommandHandler("check", check_command))
    
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r'https?://.*zara\.com.*'),
        handle_url
    ))
    
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)
    
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            periodic_check,
            interval=CHECK_INTERVAL,
            first=10
        )
        logger.info(f"⏱️ Periyodik kontrol aktif - {CHECK_INTERVAL} saniye aralıklarla")
    else:
        logger.warning("⚠️ JobQueue başlatılamadı.")
    
    logger.info("🤖 Bot başlatılıyor...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
