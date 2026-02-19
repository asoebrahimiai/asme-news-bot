import os
import requests
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
from datetime import datetime, timezone
import time

# کتابخانه‌های استخراج محتوا و هوش مصنوعی
from newspaper import Article, Config
import google.generativeai as genai

# ─── تنظیمات محیطی ────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "")
APPWRITE_ENDPOINT   = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID = os.environ.get("APPWRITE_PROJECT_ID", "")
APPWRITE_API_KEY    = os.environ.get("APPWRITE_API_KEY", "")
DATABASE_ID   = os.environ.get("APPWRITE_DATABASE_ID", "")
COLLECTION_ID = os.environ.get("APPWRITE_COLLECTION_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") # کلید هوش مصنوعی

# پیکربندی Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

HEADLINES_URL = "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
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

# ─── استخراج متن و پردازش با Gemini ───────────────────────────
def get_ai_summary(url: str, title_en: str):
    """استخراج متن و تولید چکیده و ترجمه عنوان توسط هوش مصنوعی"""
    try:
        config = Config()
        config.browser_user_agent = HEADERS["User-Agent"]
        article = Article(url, config=config)
        article.download()
        article.parse()
        
        full_text = article.text
        if len(full_text) < 200:
            return None, None

        # طراحی دستور (Prompt) برای هوش مصنوعی
        prompt = f"""
        You are a professional engineering news editor. Based on the following news article, please provide:
        1. A formal Persian translation of the Title.
        2. A concise one-paragraph summary of the news in Persian (max 100 words).
        
        Article Title: {title_en}
        Article Content: {full_text[:3000]}
        
        Format your response exactly like this:
        TITLE: [Persian Title]
        SUMMARY: [Persian Summary]
        """
        
        response = model.generate_content(prompt)
        output = response.text
        
        # تجزیه پاسخ AI
        title_fa = output.split("TITLE:")[1].split("SUMMARY:")[0].strip()
        summary_fa = output.split("SUMMARY:")[1].strip()
        
        return title_fa, summary_fa
    except Exception as e:
        print(f"AI Error: {e}")
        return None, None

# ─── ارسال به تلگرام ───────────────────────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, news_url: str) -> bool:
    message = f"📰 **{title_fa}**\n\n"
    message += f"🔹 **چکیده خبر:**\n{summary_fa}\n\n"
    if source:
        message += f"🌐 **منبع:** {source}\n"
    message += f"🔗 [مشاهده خبر کامل]({news_url})\n"
    message += "───\n"
    message += "🆔 @ASME_Persian_News"

    api_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    try:
        r = requests.post(f"{api_base}/sendMessage", json={
            "chat_id": TELEGRAM_CHANNEL,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False
        }, timeout=15)
        return r.status_code == 200
    except Exception:
        return False

# ─── تابع اصلی ──────────────────────────────────────
def main(context):
    print("=== ASME Smart Bot Started ===")
    
    if not all([TELEGRAM_TOKEN, TELEGRAM_CHANNEL, GEMINI_API_KEY]):
        return context.res.json({"error": "Config missing"}, status_code=500)

    databases = get_db()
    news_list = fetch_headlines()

    new_count = 0
    for news in reversed(news_list):
        if is_published(databases, news["url"]):
            continue

        print(f"Processing with AI: {news['title']}")
        
        # استفاده از هوش مصنوعی برای تولید محتوا
        title_fa, summary_fa = get_ai_summary(news["url"], news["title"])

        if title_fa and summary_fa:
            if send_telegram(title_fa, summary_fa, news["source"], news["url"]):
                save_to_db(databases, news["url"], news["title"])
                new_count += 1
                time.sleep(4) # وقفه برای جلوگیری از اسپم

    return context.res.json({"published": new_count})
