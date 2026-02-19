import os
import requests
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
from datetime import datetime, timezone
import time
import re
from newspaper import Article, Config
import google.generativeai as genai

# ─── Environment Variables ───────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHANNEL  = os.environ.get("TELEGRAM_CHANNEL", "")
APPWRITE_ENDPOINT   = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID = os.environ.get("APPWRITE_PROJECT_ID", "")
APPWRITE_API_KEY    = os.environ.get("APPWRITE_API_KEY", "")
DATABASE_ID       = os.environ.get("APPWRITE_DATABASE_ID", "")
COLLECTION_ID     = os.environ.get("APPWRITE_COLLECTION_ID", "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")

HEADLINES_URL = "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ─── Helper Functions ────────────────────────────────────────────────────────
def escape_markdown(text: str) -> str:
    """جلوگیری از خطای تلگرام با پاکسازی کاراکترهای رزرو شده در Markdown"""
    parse_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(parse_chars)}])', r'\\\1', text)

# ─── Appwrite Database Functions ─────────────────────────────────────────────
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
        print(f"Error checking DB: {e}")
        return False

def save_to_db(databases, url: str, title: str):
    try:
        databases.create_document(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            document_id=ID.unique(),
            data={
                "news_url": url,
                "title": title[:255], # محدودیت طول رشته در دیتابیس
                "published_at": datetime.now(timezone.utc).isoformat()
            }
        )
    except Exception as e:
        print(f"Error saving to DB: {e}")

# ─── News Fetching ──────────────────────────────────────────────────────────
def fetch_headlines() -> list:
    print(f"Fetching from: {HEADLINES_URL}")
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        print(f"Network error: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    news_list = []
    
    # جستجوی گسترده‌تر در صورت تغییر کلاس‌های سایت
    content_area = soup.find('div', class_='sf_colsIn') or soup.find('main') or soup.body

    for a_tag in content_area.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(strip=True)

        # تبدیل لینک‌های نسبی به کامل
        if href.startswith('/'):
            href = "https://www.asme.org" + href
        
        # اصلاح فیلتر: اجازه دادن به اخبار خارجی و داخلی معتبر
        if not href.startswith("http") or len(title) < 15:
            continue
            
        # استخراج منبع از متن والد
        source = "ASME News"
        parent = a_tag.find_parent(['p', 'div', 'li'])
        if parent:
            raw_text = parent.get_text(" ", strip=True)
            if "–" in raw_text:
                source = raw_text.split("–")[0].strip()
            elif "-" in raw_text:
                source = raw_text.split("-")[0].strip()

        if not any(d['url'] == href for d in news_list):
            news_list.append({"url": href, "title": title, "source": source})

    print(f"Found {len(news_list)} potential headlines.")
    return news_list[:5]

# ─── Article Extraction ──────────────────────────────────────────────────────
def extract_article_text(url: str) -> str:
    try:
        config = Config()
        config.browser_user_agent = HEADERS['User-Agent']
        config.request_timeout = 15
        article = Article(url, config=config)
        article.download()
        article.parse()
        return article.text
    except Exception as e:
        print(f"Extraction failed for {url}: {e}")
        return ""

# ─── AI Processing ───────────────────────────────────────────────────────────
def summarize_and_translate_with_gemini(title: str, article_text: str) -> tuple[str, str]:
    if not GEMINI_API_KEY: return title, "Error: No API Key"
    
    genai.configure(api_key=GEMINI_API_KEY)
    prompt = f """
    You are a professional journalist.
    1. Translate this title to Persian: "{title}"
    2. Summarize this text in 2 concise Persian paragraphs: "{article_text[:3500]}"
    Format:
    TITLE_FA: [translation]
    SUMMARY_FA: [summary]
    """
    
    # استفاده از مدل‌های پایدارتر
    for model_name in ["gemini-1.5-flash", "gemini-pro"]:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            res_text = response.text
            
            t_fa = res_text.split("TITLE_FA:")[1].split("SUMMARY_FA:")[0].strip()
            s_fa = res_text.split("SUMMARY_FA:")[1].strip()
            return t_fa, s_fa
        except Exception as e:
            print(f"Gemini {model_name} failed: {e}")
            continue
    return title, "خطا در پردازش هوش مصنوعی"

# ─── Telegram Send ───────────────────────────────────────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, news_url: str) -> bool:
    # ایمن‌سازی متن برای مارک‌داون
    safe_title = escape_markdown(title_fa)
    safe_summary = escape_markdown(summary_fa)
    safe_source = escape_markdown(source)

    caption = (
        f"*{safe_title}*\n\n"
        f"{safe_summary}\n\n"
        f"🌐 *منبع:* {safe_source}\n"
        f"🔗 [مشاهده خبر کامل]({news_url})"
    )

    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        res = requests.post(api_url, json={
            "chat_id": TELEGRAM_CHANNEL,
            "text": caption,
            "parse_mode": "MarkdownV2", # استفاده از نسخه ۲ برای پایداری بیشتر
            "disable_web_page_preview": False
        }, timeout=15)
        return res.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

# ─── Main Logic ──────────────────────────────────────────────────────────────
def main(context):
    print("Execution started...")
    
    if not all([TELEGRAM_TOKEN, APPWRITE_PROJECT_ID, GEMINI_API_KEY]):
        return context.res.json({"ok": False, "error": "Missing Env Vars"})

    db = get_db()
    headlines = fetch_headlines()
    success_count = 0

    for item in headlines:
        if is_published(db, item['url']):
            continue

        text = extract_article_text(item['url'])
        if not text: continue

        t_fa, s_fa = summarize_and_translate_with_gemini(item['title'], text)
        
        if send_telegram(t_fa, s_fa, item['source'], item['url']):
            save_to_db(db, item['url'], item['title'])
            success_count += 1
            time.sleep(2)

    return context.res.json({"published": success_count})
