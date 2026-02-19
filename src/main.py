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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# پیکربندی Gemini
if GEMINI_API_KEY:
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
    except: return False

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
    except Exception as e: print(f"DB Error: {e}")

# ─── دریافت لیست اخبار ────────────────────────────────
def fetch_headlines() -> list:
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
    except: return []

# ─── پردازش هوشمند با Gemini ───────────────────────────
def get_ai_content(url: str, title_en: str):
    try:
        config = Config()
        config.browser_user_agent = HEADERS["User-Agent"]
        article = Article(url, config=config)
        article.download()
        article.parse()
        
        prompt = f"""
        Extract the essence of this engineering news.
        1. Translate the title to professional Persian.
        2. Write a 1-paragraph summary (max 100 words) in Persian.
        Avoid irrelevant topics like neighbors or private property. Focus on the engineering/academic achievement.

        Title: {title_en}
        Content: {article.text[:3000]}
        
        Format:
        TITLE: [Persian Title]
        SUMMARY: [Persian Summary]
        """
        response = model.generate_content(prompt)
        text = response.text
        t_fa = text.split("TITLE:")[1].split("SUMMARY:")[0].strip()
        s_fa = text.split("SUMMARY:")[1].strip()
        return t_fa, s_fa
    except Exception as e:
        print(f"AI Processing failed: {e}")
        return None, None

# ─── ارسال به تلگرام ───────────────────────────────────────────
def send_telegram(title, summary, source, url):
    msg = f"📰 **{title}**\n\n🔹 **چکیده خبر:**\n{summary}\n\n"
    if source: msg += f"🌐 **منبع:** {source}\n"
    msg += f"🔗 [مشاهده خبر کامل]({url})\n───\n🆔 @ASME_Persian_News"
    
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
            "chat_id": TELEGRAM_CHANNEL, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": False
        }, timeout=15)
        return r.status_code == 200
    except: return False

# ─── اجرای تابع ──────────────────────────────────────
def main(context):
    if not GEMINI_API_KEY:
        return context.res.json({"error": "Gemini Key missing"}, status_code=500)
    
    db = get_db()
    news_list = fetch_headlines()
    count = 0

    for news in reversed(news_list):
        if is_published(db, news["url"]): continue
        
        t_fa, s_fa = get_ai_content(news["url"], news["title"])
        if t_fa and s_fa:
            if send_telegram(t_fa, s_fa, news["source"], news["url"]):
                save_to_db(db, news["url"], news["title"])
                count += 1
                time.sleep(3)

    return context.res.json({"published": count})
