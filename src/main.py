import os
import requests
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
from datetime import datetime, timezone
import time

# کتابخانه جدید برای استخراج هوشمند محتوا
from newspaper import Article, Config

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "")
APPWRITE_ENDPOINT   = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID = os.environ.get("APPWRITE_PROJECT_ID", "")
APPWRITE_API_KEY    = os.environ.get("APPWRITE_API_KEY", "")
DATABASE_ID   = os.environ.get("APPWRITE_DATABASE_ID", "")
COLLECTION_ID = os.environ.get("APPWRITE_COLLECTION_ID", "")

HEADLINES_URL = "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ─── Appwrite ──────────────────────────────────────────────────
def get_db():
    client = Client()
    client.set_endpoint(APPWRITE_ENDPOINT)
    client.set_project(APPWRITE_PROJECT_ID)
    client.set_key(APPWRITE_API_KEY)
    return Databases(client)

def is_published(databases, url: str) -> bool:
    try:
        res = databases.list_documents(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            queries=[Query.equal("news_url", [url])]
        )
        return res["total"] > 0
    except Exception as e:
        print(f"DB check error: {e}")
        return False

def save_to_db(databases, url: str, title: str):
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
        print(f"DB save error: {e}")

# ─── دریافت اخبار از صفحه اصلی ────────────────────────────────
def fetch_headlines() -> list:
    print("Fetching headlines...")
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"Fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    news_list = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(strip=True)

        if not href.startswith("http"):
            continue
        if "asme.org" in href:
            continue
        if len(title) < 20:
            continue
        
        # نکته: روش استخراج منبع همچنان شکننده است.
        # این کد به دنبال متن‌های هم‌سطح (sibling) با تگ لینک می‌گردد.
        # اگر ساختار سایت ASME تغییر کند، این بخش ممکن است منبع را اشتباه تشخیص دهد.
        # برای بهبود، باید ساختار دقیق HTML را بررسی و سلکتور بهتری پیدا کرد.
        source = ""
        parent = a_tag.find_parent()
        if parent:
            for sibling in parent.find_all(string=True, recursive=False):
                s = sibling.strip()
                if s and s != title and len(s) > 2:
                    source = s[:80]
                    break

        news_list.append({"url": href, "title": title, "source": source})
        print(f"  Found: {title[:70]}")

    print(f"Total found: {len(news_list)}")
    return news_list[:5]

# ─── استخراج متن از صفحه خبر (نسخه اصلاح شده و هوشمند) ───────────────
def extract_article_text(url: str) -> str:
    """ورود به لینک خبر و استخراج هوشمند پاراگراف‌های اصلی با newspaper3k"""
    try:
        # تنظیمات برای جلوگیری از خطای SSL و تعیین هدر
        config = Config()
        config.browser_user_agent = HEADERS["User-Agent"]
        config.request_timeout = 20
        config.memoize_articles = False # جلوگیری از کش کردن در محیط سرورلس

        article = Article(url, config=config)
        article.download()
        article.parse()

        # استخراج متن اصلی و محدود کردن آن
        full_text = article.text
        if not full_text:
            return ""

        # چند پاراگراف اول متن اصلی را برای خلاصه برمی‌گردانیم
        paragraphs = full_text.split('\n\n')
        summary_text = " ".join(paragraphs[:3])

        # کوتاه کردن به ۸۰۰ کاراکتر برای جلوگیری از طولانی شدن
        return summary_text[:800]

    except Exception as e:
        print(f"Article fetch error ({url[:50]}): {e}")
        return ""

# ─── ترجمه با MyMemory ─────────────────────────────────────────
def translate_to_persian(text: str) -> str:
    # نکته: MyMemory یک سرویس رایگان با محدودیت‌هایی در کیفیت و تعداد درخواست است.
    # برای ترجمه‌های تخصصی و دقیق‌تر، استفاده از APIهای پولی مانند
    # Google Translate API یا DeepL API پیشنهاد می‌شود.
    if not text:
        return ""
    try:
        api_url = "https://api.mymemory.translated.net/get"
        params = {"q": text[:900], "langpair": "en|fa"}
        
        resp = requests.get(api_url, params=params, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        result = data.get("responseData", {}).get("translatedText", "")
        
        if result and result.lower() != text.lower():
            return result
        
        # اگر ترجمه موفق نبود، متن اصلی را برنگردان
        return ""

    except Exception as e:
        print(f"Translation error: {e}")
        return ""

# ─── ارسال به تلگرام ───────────────────────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, news_url: str) -> bool:
    msg_parts = [f"📰 *{title_fa.strip()}*\n"]

    if summary_fa:
        msg_parts.append(f"{summary_fa.strip()}\n")

    if source:
        # پاکسازی منبع از کاراکترهای اضافی
        cleaned_source = source.replace("via ", "").strip()
        msg_parts.append(f"🌐 *منبع:* {cleaned_source}")

    msg_parts.append(f"🔗 [مشاهده خبر کامل]({news_url})")
    msg_parts.append("\n_via ASME In the Headlines_")

    caption = "\n".join(msg_parts)

    if len(caption) > 4096:
        # کوتاه کردن پیام در صورت نیاز
        summary_cutoff = 4096 - len(title_fa) - len(source) - 200
        summary_fa_short = summary_fa[:summary_cutoff]
        msg_parts = [
            f"📰 *{title_fa.strip()}*\n",
            f"{summary_fa_short}... (خلاصه شده)\n",
            f"🌐 *منبع:* {source}",
            f"🔗 [مشاهده خبر کامل]({news_url})",
            "\n_via ASME In the Headlines_"
        ]
        caption = "\n".join(msg_parts)
    
    api_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    try:
        r = requests.post(
            f"{api_base}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHANNEL,
                "text": caption,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            },
            timeout=15
        )
        print(f"Telegram status: {r.status_code}")
        if r.status_code != 200:
            print(f"Telegram error body: {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram exception: {e}")
        return False

# ─── تابع اصلی ─────────────────────────────────────────────────
def main(context):
    print("=== ASME Bot Starting ===")
    
    if not all([TELEGRAM_TOKEN, TELEGRAM_CHANNEL, APPWRITE_PROJECT_ID, APPWRITE_API_KEY, DATABASE_ID, COLLECTION_ID]):
        error_msg = "One or more environment variables are not set."
        print(f"Error: {error_msg}")
        return context.res.json({"error": error_msg}, status_code=500)

    databases = get_db()
    news_list = fetch_headlines()

    if not news_list:
        return context.res.json({"published": 0, "message": "No new headlines found"})

    new_count = 0
    log = []

    for news in reversed(news_list): # پردازش از قدیمی به جدید
        try:
            if is_published(databases, news["url"]):
                print(f"Skip (already published): {news['url'][:60]}")
                continue

            print(f"\nProcessing: {news['title'][:70]}")

            article_text = extract_article_text(news["url
