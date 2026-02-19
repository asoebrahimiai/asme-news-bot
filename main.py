import os
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
import telegram
import asyncio
from googletrans import Translator
from datetime import datetime

# بارگذاری متغیرهای محیطی
load_dotenv()

# ==================== تنظیمات ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL")
APPWRITE_ENDPOINT = os.getenv("APPWRITE_ENDPOINT")
APPWRITE_PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID")
APPWRITE_API_KEY = os.getenv("APPWRITE_API_KEY")
DATABASE_ID = os.getenv("APPWRITE_DATABASE_ID")
COLLECTION_ID = os.getenv("APPWRITE_COLLECTION_ID")

ASME_URL = "https://www.asme.org/topics-resources/society-news/asme-news"

# ==================== اتصال به Appwrite ====================
client = Client()
client.set_endpoint(APPWRITE_ENDPOINT)
client.set_project(APPWRITE_PROJECT_ID)
client.set_key(APPWRITE_API_KEY)
databases = Databases(client)

# ==================== مترجم ====================
translator = Translator()


def translate_to_persian(text):
    """ترجمه متن به فارسی"""
    try:
        result = translator.translate(text, dest='fa')
        return result.text
    except Exception as e:
        print(f"خطا در ترجمه: {e}")
        return text


def is_news_published(news_url):
    """چک می‌کند آیا این خبر قبلاً منتشر شده؟"""
    try:
        result = databases.list_documents(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            queries=[Query.equal("news_url", news_url)]
        )
        return result['total'] > 0
    except Exception as e:
        print(f"خطا در بررسی دیتابیس: {e}")
        return False


def save_news_to_db(news_url, title):
    """ذخیره خبر منتشر شده در دیتابیس"""
    try:
        databases.create_document(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            document_id=ID.unique(),
            data={
                "news_url": news_url,
                "title": title,
                "published_at": datetime.utcnow().isoformat()
            }
        )
        print(f"خبر ذخیره شد: {title}")
    except Exception as e:
        print(f"خطا در ذخیره: {e}")


def scrape_asme_news():
    """دریافت لیست اخبار از سایت ASME"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        response = requests.get(ASME_URL, headers=headers, timeout=30)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        news_list = []
        
        # پیدا کردن مطالب خبری
        articles = soup.find_all('article') or soup.find_all(class_=['card', 'news-item', 'article-card'])
        
        if not articles:
            # روش جایگزین - پیدا کردن لینک‌های خبری
            links = soup.find_all('a', href=True)
            for link in links:
                href = link.get('href', '')
                if '/topics-resources/content/' in href or '/topics-resources/society-news/' in href:
                    full_url = href if href.startswith('http') else f"https://www.asme.org{href}"
                    title = link.get_text(strip=True)
                    if title and len(title) > 20:
                        news_list.append({
                            'url': full_url,
                            'title': title,
                            'image': None
                        })
        
        for article in articles[:10]:  # فقط ۱۰ خبر آخر
            try:
                # پیدا کردن لینک
                link = article.find('a', href=True)
                if not link:
                    continue
                    
                href = link.get('href', '')
                full_url = href if href.startswith('http') else f"https://www.asme.org{href}"
                
                # پیدا کردن عنوان
                title_tag = article.find(['h2', 'h3', 'h4'])
                title = title_tag.get_text(strip=True) if title_tag else link.get_text(strip=True)
                
                # پیدا کردن عکس
                img_tag = article.find('img')
                image_url = None
                if img_tag:
                    image_url = img_tag.get('src') or img_tag.get('data-src')
                    if image_url and not image_url.startswith('http'):
                        image_url = f"https://www.asme.org{image_url}"
                
                if title and full_url:
                    news_list.append({
                        'url': full_url,
                        'title': title,
                        'image': image_url
                    })
                    
            except Exception as e:
                print(f"خطا در پردازش مقاله: {e}")
                continue
        
        return news_list
        
    except Exception as e:
        print(f"خطا در دریافت سایت: {e}")
        return []


def get_article_details(url):
    """دریافت جزئیات یک خبر خاص"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # پیدا کردن خلاصه خبر
        description = ""
        meta_desc = soup.find('meta', {'name': 'description'})
        if meta_desc:
            description = meta_desc.get('content', '')
        
        if not description:
            paragraphs = soup.find_all('p')
            for p in paragraphs[:3]:
                text = p.get_text(strip=True)
                if len(text) > 50:
                    description += text + " "
                    if len(description) > 300:
                        break
        
        # پیدا کردن عکس اصلی
        image_url = None
        og_image = soup.find('meta', {'property': 'og:image'})
        if og_image:
            image_url = og_image.get('content')
        
        if not image_url:
            img = soup.find('img', class_=['hero', 'featured', 'main-image'])
            if img:
                image_url = img.get('src')
                if image_url and not image_url.startswith('http'):
                    image_url = f"https://www.asme.org{image_url}"
        
        return description[:500], image_url
        
    except Exception as e:
        print(f"خطا در دریافت جزئیات: {e}")
        return "", None


async def send_to_telegram(title_fa, description_fa, image_url, source_url):
    """ارسال خبر به کانال تلگرام"""
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    
    # متن پیام
    message = f"""📰 *{title_fa}*

{description_fa}

🔗 [منبع خبر]({source_url})
🌐 ASME News
"""
    
    try:
        if image_url:
            # ارسال با عکس
            await bot.send_photo(
                chat_id=TELEGRAM_CHANNEL,
                photo=image_url,
                caption=message,
                parse_mode='Markdown'
            )
        else:
            # ارسال بدون عکس
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL,
                text=message,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
        print(f"خبر ارسال شد: {title_fa[:50]}...")
        return True
    except Exception as e:
        print(f"خطا در ارسال تلگرام: {e}")
        return False


async def main_process():
    """پردازش اصلی"""
    print(f"\n{'='*50}")
    print(f"شروع بررسی: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print('='*50)
    
    # دریافت اخبار
    news_list = scrape_asme_news()
    print(f"تعداد اخبار یافت شده: {len(news_list)}")
    
    new_count = 0
    
    for news in news_list:
        url = news['url']
        title = news['title']
        
        # چک می‌کنیم آیا قبلاً منتشر شده؟
        if is_news_published(url):
            print(f"قبلاً منتشر شده: {title[:50]}")
            continue
        
        print(f"خبر جدید پیدا شد: {title[:50]}")
        
        # دریافت جزئیات بیشتر
        description, image_url = get_article_details(url)
        
        # اگر عکس از صفحه اصلی داریم، آن را استفاده کن
        if news['image'] and not image_url:
            image_url = news['image']
        
        # ترجمه عنوان و توضیح
        print("در حال ترجمه...")
        title_fa = translate_to_persian(title)
        description_fa = translate_to_persian(description) if description else ""
        
        # ارسال به تلگرام
        success = await send_to_telegram(title_fa, description_fa, image_url, url)
        
        if success:
            # ذخیره در دیتابیس
            save_news_to_db(url, title)
            new_count += 1
            
            # صبر کن تا spam نشی
            await asyncio.sleep(3)
    
    print(f"\nتعداد اخبار جدید منتشر شده: {new_count}")
    print("بررسی تمام شد.")


def run():
    """اجرای اصلی"""
    while True:
        asyncio.run(main_process())
        
        # صبر ۱ ساعت
        print(f"\nصبر ۱ ساعت تا بررسی بعدی...")
        time.sleep(3600)


if __name__ == "__main__":
    run()
