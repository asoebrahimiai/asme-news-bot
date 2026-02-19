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

# ─── تنظیمات ───
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "")
APPWRITE_ENDPOINT   = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID = os.environ.get("APPWRITE_PROJECT_ID", "")
APPWRITE_API_KEY    = os.environ.get("APPWRITE_API_KEY", "")
DATABASE_ID   = os.environ.get("APPWRITE_DATABASE_ID", "")
COLLECTION_ID = os.environ.get("APPWRITE_COLLECTION_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# پیکربندی هوش مصنوعی
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def get_db():
    client = Client().set_endpoint(APPWRITE_ENDPOINT).set_project(APPWRITE_PROJECT_ID).set_key(APPWRITE_API_KEY)
    return Databases(client)

def is_published(databases, url):
    try:
        res = databases.list_documents(DATABASE_ID, COLLECTION_ID, [Query.equal("news_url", [url])])
        return res["total"] > 0
    except: return False

def fetch_headlines():
    try:
        resp = requests.get("https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        news = []
        for a in soup.find_all("a", href=True):
            if "http" in a['href'] and not "asme.org" in a['href'] and len(a.text) > 25:
                news.append({"url": a['href'], "title": a.text.strip()})
        return news[:5]
    except: return []

def get_ai_content(url, title_en):
    try:
        article = Article(url)
        article.download()
        article.parse()
        
        prompt = f"Translate title to Persian and summarize in 1 Persian paragraph. Title: {title_en}. Content: {article.text[:2500]}. Format: TITLE: (persian title) SUMMARY: (persian summary)"
        response = model.generate_content(prompt)
        raw = response.text
        t_fa = raw.split("TITLE:")[1].split("SUMMARY:")[0].strip()
        s_fa = raw.split("SUMMARY:")[1].strip()
        return t_fa, s_fa
    except Exception as e:
        print(f"AI ERROR for {url}: {e}")
        return None, None

def send_telegram(title, summary, url):
    text = f"📰 **{title}**\n\n🔹 **چکیده خبر:**\n{summary}\n\n🔗 [مشاهده خبر کامل]({url})\n───\n🆔 @ASME_Persian_News"
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                     json={"chat_id": TELEGRAM_CHANNEL, "text": text, "parse_mode": "Markdown"})
    return r.status_code == 200

def main(context):
    print("Execution Started...")
    if not GEMINI_API_KEY or not TELEGRAM_TOKEN:
        print("CRITICAL: Missing Keys!")
        return context.res.json({"error": "Config missing"})

    db = get_db()
    news_list = fetch_headlines()
    count = 0

    for news in reversed(news_list):
        if is_published(db, news["url"]):
            continue
        
        print(f"Processing: {news['title']}")
        t_fa, s_fa = get_ai_content(news["url"], news["title"])
        
        if t_fa and s_fa:
            if send_telegram(t_fa, s_fa, news["url"]):
                db.create_document(DATABASE_ID, COLLECTION_ID, ID.unique(), 
                                  {"news_url": news["url"], "title": news["title"], "published_at": datetime.now(timezone.utc).isoformat()})
                count += 1
                print(f"Successfully sent: {news['url']}")
                time.sleep(3)
        else:
            print(f"Skipped {news['url']} due to AI/Extraction error.")

    return context.res.json({"published": count})
