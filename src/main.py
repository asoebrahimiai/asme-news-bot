import os
import requests
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
from datetime import datetime, timezone
import time
from newspaper import Article, Config
import google.generativeai as genai

# ─── Environment Variables ───────────────────────────────────────────────────
# متغیرهای محیطی که باید در تنظیمات فانکشن Appwrite ست شوند
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHANNEL  = os.environ.get("TELEGRAM_CHANNEL", "")
APPWRITE_ENDPOINT   = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID = os.environ.get("APPWRITE_PROJECT_ID", "")
APPWRITE_API_KEY    = os.environ.get("APPWRITE_API_KEY", "")
DATABASE_ID       = os.environ.get("APPWRITE_DATABASE_ID", "")
COLLECTION_ID     = os.environ.get("APPWRITE_COLLECTION_ID", "")
# کلید API جدید برای مدل Gemini
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")

HEADLINES_URL = "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines"

# هدر برای درخواست‌ها جهت شبیه‌سازی مرورگر
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5'
}

# ─── Appwrite Database Functions ─────────────────────────────────────────────
def get_db():
    """اتصال به دیتابیس Appwrite را برقرار می‌کند"""
    client = Client()
    client.set_endpoint(APPWRITE_ENDPOINT)
    client.set_project(APPWRITE_PROJECT_ID)
    client.set_key(APPWRITE_API_KEY)
    return Databases(client)

def is_published(databases, url: str) -> bool:
    """بررسی می‌کند که آیا یک خبر قبلاً در دیتابیس ثبت شده است یا خیر"""
    try:
        res = databases.list_documents(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            queries=[Query.equal("news_url", [url])]
        )
        return res["total"] > 0
    except Exception as e:
        print(f"Error checking if published in DB: {e}")
        return False

def save_to_db(databases, url: str, title: str):
    """خبر منتشر شده را در دیتابیس ذخیره می‌کند تا از انتشار مجدد جلوگیری شود"""
    try:
        databases.create_document(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            document_id=ID.unique(),
            data={
                "news_url": url,
                "title": title,
                "published_at": datetime.now(timezone.utc).isoformat()
            }
        )
    except Exception as e:
        print(f"Error saving to DB: {e}")

# ─── News Fetching and Parsing ───────────────────────────────────────────────
def fetch_headlines() -> list:
    """لیست عناوین اخبار را از صفحه اصلی ASME استخراج می‌کند"""
    print("Fetching headlines from ASME...")
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching headlines page: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    news_list = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(strip=True)

        if not href.startswith("http") or "asme.org" in href or len(title) < 20:
            continue

        source = ""
        parent_tag = a_tag.find_parent()
        if parent_tag:
            # تلاش برای پیدا کردن نام منبع خبر
            source_candidate = parent_tag.get_text(strip=True).replace(title, "").strip()
            if source_candidate:
                source = source_candidate

        news_list.append({"url": href, "title": title, "source": source})
        print(f"  Found: {title[:70]}")

    print(f"Total relevant headlines found: {len(news_list)}")
    return news_list[:5] # پردازش ۵ خبر جدید در هر اجرا

def extract_article_text(url: str) -> str:
    """با استفاده از کتابخانه newspaper3k متن اصلی مقاله را استخراج می‌کند"""
    print(f"  Extracting article from: {url[:60]}")
    try:
        config = Config()
        config.browser_user_agent = HEADERS['User-Agent']
        
        article = Article(url, config=config)
        article.download()
        article.parse()
        
        return article.text
    except Exception as e:
        print(f"  Error extracting article content: {e}")
        return ""

# ─── Translation Functions ───────────────────────────────────────────────────
def translate_to_persian(text: str) -> str:
    """ترجمه متون کوتاه (مانند عناوین) با استفاده از سرویس MyMemory"""
    if not text:
        return ""
    try:
        params = {'q': text, 'langpair': 'en|fa'}
        resp = requests.get("https://api.mymemory.translated.net/get", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        translated_text = data.get("responseData", {}).get("translatedText", "")
        return translated_text if translated_text else text
    except requests.RequestException as e:
        print(f"  Translation error (MyMemory): {e}")
        return text # در صورت خطا، متن اصلی را برمی‌گرداند

def summarize_and_translate_with_gemini(text: str) -> str:
    """متن مقاله را با استفاده از Gemini 1.5 Flash خلاصه‌سازی و به فارسی ترجمه می‌کند"""
    if not text:
        return ""
    if not GEMINI_API_KEY:
        print("  GEMINI_API_KEY is not set. Skipping summary.")
        return ""
        
    print("  Summarizing and translating with Gemini...")
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        As a professional news editor, your task is to first summarize the following English news article into one or two concise paragraphs, capturing the most important points. Then, translate this summary into fluent and natural Persian.

        RULES:
        1.  Your final output must ONLY be the Persian translation of the summary.
        2.  Do not include any English text, introductory phrases like "خلاصه:" or any explanations.
        3.  The translation should be professional and engaging for a news channel audience.

        ARTICLE TEXT:
        ---
        {text}
        ---
        """
        
        response = model.generate_content(prompt)
        return response.text.strip()
        
    except Exception as e:
        print(f"  Error with Gemini API: {e}")
        return f"خطا در پردازش متن با هوش مصنوعی: {e}"


# ─── Telegram Sender ──────────────────────────────────────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, news_url: str) -> bool:
    """پیام نهایی را فرمت کرده و به کانال تلگرام ارسال می‌کند"""
    msg_parts = [f"**{title_fa}**\n"]

    if summary_fa:
        msg_parts.append(f"{summary_fa}\n")

    if source:
        msg_parts.append(f"🌐 **منبع:** {source}")

    msg_parts.append(f"🔗 [مشاهده خبر کامل]({news_url})")
    msg_parts.append("\n*via ASME In the Headlines*")

    message = "\n".join(msg_parts)
    
    # اطمینان از اینکه پیام از حد مجاز تلگرام طولانی‌تر نباشد
    if len(message) > 4096:
        message = message[:4090] + "..."

    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }

    try:
        resp = requests.post(api_url, json=payload, timeout=20)
        print(f"  Telegram response status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"  Telegram error details: {resp.text}")
        return resp.status_code == 200
    except requests.RequestException as e:
        print(f"  Exception while sending to Telegram: {e}")
        return False

# ─── Main Function (Appwrite Entrypoint) ─────────────────────────────────────
def main(context):
    """نقطه شروع اجرای فانکشن"""
    start_time = time.time()
    print(f"====== ASME Bot Execution Started at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} ======")

    if not all([TELEGRAM_TOKEN, TELEGRAM_CHANNEL, APPWRITE_PROJECT_ID, APPWRITE_API_KEY, DATABASE_ID, COLLECTION_ID]):
        error_msg = "One or more required environment variables are not set."
        print(f"FATAL: {error_msg}")
        return context.res.json({"status": "failed", "error": error_msg}, status_code=500)

    databases = get_db()
    news_list = fetch_headlines()

    if not news_list:
        print("No new headlines found to process.")
        return context.res.json({"published": 0, "message": "No headlines found"})

    published_count = 0
    logs = []

    for news in news_list:
        try:
            if is_published(databases, news["url"]):
                print(f"Skipping (already published): {news['url'][:70]}")
                continue

            print(f"\nProcessing: {news['title'][:80]}")

            # 1. ترجمه عنوان (سریع و ساده)
            title_fa = translate_to_persian(news["title"])
            print(f"  Translated Title: {title_fa[:70]}")
            time.sleep(1) # فاصله بین درخواست‌ها

            # 2. استخراج متن کامل مقاله
            article_text = extract_article_text(news["url"])
            print(f"  Extracted article length: {len(article_text)} chars")

            # 3. خلاصه‌سازی و ترجمه با Gemini
            summary_fa = ""
            if article_text:
                summary_fa = summarize_and_translate_with_gemini(article_text)
                print(f"  Gemini summary length: {len(summary_fa)} chars")
            else:
                print("  No article text to summarize.")
            
            # 4. ارسال به تلگرام
            is_sent = send_telegram(title_fa, summary_fa, news["source"], news["url"])

            if is_sent:
                save_to_db(databases, news["url"], news["title"])
                published_count += 1
                logs.append(f"SUCCESS: {news['title'][:60]}")
                print(f"  Successfully posted and saved: {news['title'][:70]}")
                time.sleep(3) # فاصله بیشتر بعد از یک ارسال موفق
            else:
                logs.append(f"FAIL (Telegram): {news['title'][:60]}")
                print(f"  Failed to post to Telegram: {news['title'][:70]}")

        except Exception as e:
            error_log = f"CRITICAL ERROR processing '{news.get('title', 'N/A')}': {e}"
            print(error_log)
            logs.append(error_log)

    end_time = time.time()
    duration = round(end_time - start_time, 2)
    print(f"\n====== Execution Finished in {duration} seconds. Published: {published_count}/{len(news_list)} ======")
    
    return context.res.json({
        "status": "completed",
        "published_count": published_count,
        "total_found": len(news_list),
        "execution_duration_sec": duration,
        "logs": logs
    })
