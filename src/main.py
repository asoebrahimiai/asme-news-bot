import os
import requests
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
from datetime import datetime, timezone
import time

# کتابخانه برای استخراج هوشمند محتوا
from newspaper import Article, Config

# ─── تنظیمات محیطی ────────────────────────────────────────
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
}

# ─── Appwrite ───────────────────────────────────
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
    except Exception:
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

# ─── دریافت لیست اخبار ────────────────────────────────
def fetch_headlines() -> list:
    print("Fetching headlines...")
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        news_list = []

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            title = a_tag.get_text(strip=True)

            if not href.startswith("http") or "asme.org" in href or len(title) < 25:
                continue
            
            source = ""
            parent = a_tag.find_parent()
            if parent:
                for sibling in parent.find_all(string=True, recursive=False):
                    s = sibling.strip()
                    if s and s != title and len(s) > 2:
                        source = s.replace("via ", "").strip()[:80]
                        break

            news_list.append({"url": href, "title": title, "source": source})

        return news_list[:5]
    except Exception as e:
        print(f"Fetch error: {e}")
        return []

# ─── استخراج هوشمند چکیده (اصلاح شده) ───────────────
def extract_article_summary(url: str) -> str:
    try:
        config = Config()
        config.browser_user_agent = HEADERS["User-Agent"]
        config.request_timeout = 15
        
        article = Article(url, config=config)
        article.download()
        article.parse()

        # استخراج پاراگراف‌هایی که واقعاً متن خبری هستند
        paragraphs = [p.strip() for p in article.text.split('\n') if len(p.strip()) > 100]
        
        # ترکیب دو پاراگراف اول برای ایجاد یک چکیده جامع
        raw_summary = " ".join(paragraphs[:2])
        return raw_summary[:700] # محدودیت برای جلوگیری از خطای ترجمه
    except Exception as e:
        print(f"Summary extraction error: {e}")
        return ""

# ─── ترجمه ایمن (بدون محدودیت طول) ───────────────────────────
def translate_to_persian(text: str) -> str:
    if not text or len(text) < 5:
        return ""
    try:
        # تقسیم متن به تکه‌های ۴۰۰ کاراکتری برای جلوگیری از Query Limit
        chunks = [text[i:i+400] for i in range(0, len(text), 400)]
        translated_parts = []

        for chunk in chunks:
            api_url = "https://api.mymemory.translated.net/get"
            params = {"q": chunk, "langpair": "en|fa"}
            resp = requests.get(api_url, params=params, timeout=15)
            if resp.status_code == 200:
                translated_parts.append(resp.json().get("responseData", {}).get("translatedText", ""))
            time.sleep(0.5)

        return " ".join(translated_parts)
    except Exception:
        return ""

# ─── ارسال به تلگرام (قالب‌بندی جدید) ───────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, news_url: str) -> bool:
    # ساختار پیام با چکیده در یک پاراگراف
    message = f"📰 **{title_fa.strip()}**\n\n"
    
    if summary_fa:
        message += f"🔹 **چکیده خبر:**\n{summary_fa.strip()}\n\n"

    if source:
        message += f"🌐 **منبع:** {source}\n"

    message += f"🔗 [مشاهده خبر کامل]({news_url})\n"
    message += "───\n"
    message += "_via ASME In the Headlines_"

    api_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    try:
        r = requests.post(
            f"{api_base}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHANNEL,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            },
            timeout=15
        )
        return r.status_code == 200
    except Exception:
        return False

# ─── تابع اصلی ──────────────────────────────────────
def main(context):
    print("=== ASME Bot Execution Started ===")
    
    if not all([TELEGRAM_TOKEN, TELEGRAM_CHANNEL, APPWRITE_PROJECT_ID, APPWRITE_API_KEY]):
        return context.res.json({"error": "Config missing"}, status_code=500)

    databases = get_db()
    news_list = fetch_headlines()

    new_count = 0
    for news in reversed(news_list):
        if is_published(databases, news["url"]):
            continue

        print(f"Processing: {news['title']}")
        
        # ۱. استخراج و ترجمه عنوان
        title_fa = translate_to_persian(news["title"])
        
        # ۲. استخراج و ترجمه چکیده
        en_summary = extract_article_summary(news["url"])
        summary_fa = translate_to_persian(en_summary)

        # ۳. ارسال
        if send_telegram(title_fa, summary_fa, news["source"], news["url"]):
            save_to_db(databases, news["url"], news["title"])
            new_count += 1
            time.sleep(3)

    return context.res.json({"published": new_count})
