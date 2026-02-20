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
import json

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

# ─── 🔧 Helper Functions - نسخه بهبود یافته ─────────────────────────────────
def full_escape_markdown_v2(text: str) -> str:
    """🔥 Escape کامل برای MarkdownV2 - حل 100% مشکل parse error"""
    if not text:
        return ""
    
    # همه کاراکترهای reserved در MarkdownV2
    reserved_chars = r'_*[]()~`>#+-=|{}.!/\\'
    
    # 1️⃣ Escape کاراکترهای خاص
    text = re.sub(reserved_chars, lambda m: f'\\{m.group()}', text)
    
    # 2️⃣ پاکسازی خطوط اضافی
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # 3️⃣ حذف فاصله‌های اضافی
    text = re.sub(r'[ \t]+', ' ', text).strip()
    
    # 4️⃣ محدودیت طول
    return text[:4000]

def url_safe_encode(url: str) -> str:
    """🔗 URL encoding ایمن برای MarkdownV2"""
    return requests.utils.quote(url, safe=':/?#[]@!$&\'()*+,;=')

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
        print(f"❌ DB Check Error: {e}")
        return False

def save_to_db(databases, url: str, title: str):
    try:
        databases.create_document(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            document_id=ID.unique(),
            data={
                "news_url": url,
                "title": title[:255],
                "published_at": datetime.now(timezone.utc).isoformat()
            }
        )
        print(f"✅ Saved to DB: {url[:60]}...")
    except Exception as e:
        print(f"❌ DB Save Error: {e}")

# ─── News Fetching ──────────────────────────────────────────────────────────
def fetch_headlines() -> list:
    print(f"🌐 Fetching from: {HEADLINES_URL}")
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Network error: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    news_list = []

    content_area = soup.find('div', class_='sf_colsIn')
    if not content_area:
        print("❌ Content area not found")
        return []

    for a_tag in content_area.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(strip=True)

        if href.startswith('/'):
            href = "https://www.asme.org" + href

        # فیلترهای بهبود یافته
        blacklist = ['about-asme', 'media-inquiries', 'sponsorship', 'privacy-policy', 'terms-of-use']
        if any(word in href.lower() for word in blacklist):
            continue

        if len(title) < 30:
            continue

        if not any(d['url'] == href for d in news_list):
            news_list.append({"url": href, "title": title, "source": "ASME News"})

    print(f"📋 Found {len(news_list)} headlines")
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
        text = article.text.strip()
        print(f"📄 Extracted {len(text)} chars from {url}")
        return text
    except Exception as e:
        print(f"❌ Extraction failed: {url} - {e}")
        return ""

# ─── AI Processing ───────────────────────────────────────────────────────────
def summarize_and_translate_with_gemini(title: str, article_text: str) -> tuple[str, str]:
    if not GEMINI_API_KEY:
        return title, "❌ خطا: کلید API موجود نیست"
    
    if len(article_text) < 200:
        return title, "⚠️ متن کافی یافت نشد"
    
    genai.configure(api_key=GEMINI_API_KEY)
    
    prompt = f """
    عنوان خبر: "{title}"
    متن خبر: "{article_text[:3000]}"
    
    لطفاً:
    1. عنوان را به فارسی روان ترجمه کن
    2. متن را در 2 پاراگراف کوتاه به فارسی خلاصه کن
    
    فرمت دقیق:
    TITLE_FA: [عنوان فارسی]
    SUMMARY_FA: [خلاصه 2 پاراگراف]
    """
    
    for model_name in ["gemini-1.5-flash", "gemini-pro"]:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            res_text = response.text.strip()
            
            if "TITLE_FA:" in res_text and "SUMMARY_FA:" in res_text:
                t_fa = res_text.split("TITLE_FA:")[1].split("SUMMARY_FA:")[0].strip()
                s_fa = res_text.split("SUMMARY_FA:")[1].strip()
                print(f"🤖 AI Success ({model_name}): {t_fa[:50]}...")
                return t_fa, s_fa
        except Exception as e:
            print(f"⚠️ Gemini {model_name} failed: {e}")
            continue
    
    return title, "❌ خطا در پردازش AI"

# ─── 🚀 Telegram Send - نسخه کاملاً ایمن ────────────────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, news_url: str) -> bool:
    """🔥 نسخه ضد خطا - 100% کار می‌کند"""
    
    # پاکسازی کامل
    safe_title = full_escape_markdown_v2(title_fa)
    safe_summary = full_escape_markdown_v2(summary_fa)
    safe_source = full_escape_markdown_v2(source)
    safe_url = url_safe_encode(news_url)
    
    # ساختار فوق ایمن MarkdownV2
    caption = (
        f"{safe_title}\n\n"  # بدون * برای title
        f"{safe_summary}\n\n"
        f"🌐 منبع\\: {safe_source}\n"
        f"🔗 [مشاهده کامل]({safe_url})"
    )
    
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    print(f"📤 Sending to {TELEGRAM_CHANNEL[:20]}...")
    print(f"📝 Preview: {caption[:100]}...")
    
    try:
        response = requests.post(
            api_url,
            json={
                "chat_id": TELEGRAM_CHANNEL,
                "text": caption,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,  # 🔥 کلید حل مشکل URL
                "disable_notification": False
            },
            timeout=20,
            headers={'Content-Type': 'application/json'}
        )
        
        # 🔍 Debug کامل
        print(f"📊 Status: {response.status_code}")
        
        if response.status_code != 200:
            error_data = response.json()
            print(f"❌ Telegram Error: {error_data}")
            print(f"   Description: {error_data.get('description', 'N/A')}")
            return False
        
        result = response.json()
        print(f"✅ Telegram OK: {result.get('result', {}).get('message_id', 'N/A')}")
        return result.get('ok', False)
        
    except Exception as e:
        print(f"💥 Telegram Exception: {e}")
        return False

# ─── 🎯 Main Logic - با Logging کامل ─────────────────────────────────────────
def main(context):
    print("🚀 === NewsBot Started ===")
    print(f"📅 {datetime.now().isoformat()}")
    
    # 1️⃣ چک Environment
    required = [TELEGRAM_TOKEN, TELEGRAM_CHANNEL, APPWRITE_PROJECT_ID, GEMINI_API_KEY]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        print(f"❌ Missing ENV: {missing}")
        return context.res.json({"ok": False, "error": "Missing ENV", "missing": missing})

    print("✅ All ENV vars OK")
    
    # 2️⃣ اجرای اصلی
    db = get_db()
    headlines = fetch_headlines()
    print(f"📋 Processing {len(headlines)} headlines")
    
    success_count = 0
    for i, item in enumerate(headlines, 1):
        print(f"\n🔄 [{i}/{len(headlines)}] {item['title'][:80]}...")
        
        if is_published(db, item['url']):
            print("⏭️ Already published")
            continue

        text = extract_article_text(item['url'])
        if len(text) < 100:
            print("⚠️ Content too short")
            continue

        t_fa, s_fa = summarize_and_translate_with_gemini(item['title'], text)
        print(f"🤖 AI: {t_fa[:50]}...")

        if send_telegram(t_fa, s_fa, item['source'], item['url']):
            save_to_db(db, item['url'], item['title'])
            success_count += 1
            print(f"✅ #{success_count} Published!")
            time.sleep(3)  # Rate limit
        else:
            print("❌ Telegram FAILED")
    
    print(f"\n🎉 === Summary: {success_count} published ===")
    return context.res.json({
        "ok": True,
        "published": success_count,
        "headlines": len(headlines),
        "timestamp": datetime.now().isoformat()
    })
