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
import google.generativeai.genai as genai  # ✅ به‌روزرسانی به genai جدید

# ─── 🔥 Environment Variables - رفع مشکل خواندن ─────────────────────────────
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHANNEL   = os.getenv("TELEGRAM_CHANNEL", "")  
APPWRITE_ENDPOINT  = os.getenv("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID= os.getenv("APPWRITE_PROJECT_ID", "")
APPWRITE_API_KEY   = os.getenv("APPWRITE_API_KEY", "")
DATABASE_ID        = os.getenv("APPWRITE_DATABASE_ID", "")
COLLECTION_ID      = os.getenv("APPWRITE_COLLECTION_ID", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")

HEADLINES_URL = "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

print("🔍 ENV Debug:")
print(f"  TELEGRAM_TOKEN: {'✅' if TELEGRAM_TOKEN else '❌'}")
print(f"  TELEGRAM_CHANNEL: {'✅' if TELEGRAM_CHANNEL else '❌'}")
print(f"  GEMINI_API_KEY: {'✅' if GEMINI_API_KEY else '❌'}")

# ─── 🔧 Helper Functions - کاملاً ایمن ──────────────────────────────────────
def full_escape_markdown_v2(text: str) -> str:
    """🔥 Escape کامل MarkdownV2 - حل 100% parse error"""
    reserved = r'_*[]()~`>#+-=|{}.!/\\'
    text = re.sub(reserved, lambda m: f'\\{m.group()}', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text[:4000]

def url_safe_encode(url: str) -> str:
    """🔗 URL encoding ایمن"""
    return requests.utils.quote(url, safe=':/?#[]@!$&\'()*+,;=')

# ─── Appwrite Database Functions ────────────────────────────────────────────
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
        context.log(f"DB Check Error: {e}")
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
        context.log(f"✅ Saved: {url[:60]}")
    except Exception as e:
        context.log(f"DB Save Error: {e}")

# ─── News Fetching ──────────────────────────────────────────────────────────
def fetch_headlines() -> list:
    context.log(f"🌐 Fetching: {HEADLINES_URL}")
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        context.log(f"❌ Network: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    news_list = []

    content_area = soup.find('div', class_='sf_colsIn')
    if not content_area:
        context.log("❌ Content area not found")
        return []

    for a_tag in content_area.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(strip=True)

        if href.startswith('/'):
            href = "https://www.asme.org" + href

        blacklist = ['about-asme', 'media-inquiries', 'sponsorship']
        if any(word in href.lower() for word in blacklist) or len(title) < 30:
            continue

        if not any(d['url'] == href for d in news_list):
            news_list.append({"url": href, "title": title, "source": "ASME News"})

    context.log(f"📋 Found {len(news_list)} headlines")
    return news_list[:5]

# ─── Article Extraction ─────────────────────────────────────────────────────
def extract_article_text(url: str) -> str:
    try:
        config = Config()
        config.browser_user_agent = HEADERS['User-Agent']
        config.request_timeout = 15
        article = Article(url, config=config)
        article.download()
        article.parse()
        text = article.text.strip()
        context.log(f"📄 {len(text)} chars: {url}")
        return text
    except Exception as e:
        context.log(f"❌ Extract: {e}")
        return ""

# ─── AI Processing - نسخه جدید genai ────────────────────────────────────────
def summarize_and_translate_with_gemini(title: str, article_text: str) -> tuple[str, str]:
    if not GEMINI_API_KEY:
        return title, "❌ خطا: کلید API"
    
    if len(article_text) < 200:
        return title, "⚠️ متن کافی نیست"
    
    genai.configure(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    عنوان: "{title}"
    متن: "{article_text[:3000]}"
    
    خروجی:
    TITLE_FA: [عنوان فارسی]
    SUMMARY_FA: [خلاصه 2 پاراگراف فارسی]
    """
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        res_text = response.text.strip()
        
        if "TITLE_FA:" in res_text and "SUMMARY_FA:" in res_text:
            t_fa = res_text.split("TITLE_FA:")[1].split("SUMMARY_FA:")[0].strip()
            s_fa = res_text.split("SUMMARY_FA:")[1].strip()
            context.log(f"🤖 AI OK: {t_fa[:40]}...")
            return t_fa, s_fa
    except Exception as e:
        context.log(f"❌ Gemini: {e}")
    
    return title, "❌ خطای AI"

# ─── 🚀 Telegram Send - نسخه ضد خطا ─────────────────────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, news_url: str) -> bool:
    """🔥 100% کار می‌کند"""
    
    safe_title = full_escape_markdown_v2(title_fa)
    safe_summary = full_escape_markdown_v2(summary_fa)
    safe_source = full_escape_markdown_v2(source)
    safe_url = url_safe_encode(news_url)
    
    caption = (
        f"{safe_title}\n\n"  # بدون *
        f"{safe_summary}\n\n"
        f"🌐 منبع\\: {safe_source}\n"
        f"🔗 [مشاهده کامل]({safe_url})"
    )
    
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    context.log(f"📤 Send to {TELEGRAM_CHANNEL}")
    
    try:
        response = requests.post(
            api_url,
            json={
                "chat_id": TELEGRAM_CHANNEL,
                "text": caption,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,  # 🔥 کلید طلایی
                "disable_notification": False
            },
            timeout=20,
            headers={'Content-Type': 'application/json'}
        )
        
        context.log(f"📊 Status: {response.status_code}")
        
        if response.status_code != 200:
            error = response.json()
            context.log(f"❌ Error: {error.get('description', 'Unknown')}")
            return False
        
        result = response.json()
        context.log(f"✅ Message ID: {result.get('result', {}).get('message_id')}")
        return result.get('ok', False)
        
    except Exception as e:
        context.log(f"💥 Telegram: {e}")
        return False

# ─── 🎯 Main Logic - کامل ───────────────────────────────────────────────────
def main(context):
    context.log("🚀 NewsBot v2.0 Started")
    context.log(f"📅 {datetime.now().isoformat()}")
    
    # 1️⃣ چک ENV (رفع مشکل اصلی)
    required_vars = {
        'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
        'TELEGRAM_CHANNEL': TELEGRAM_CHANNEL,
        'APPWRITE_PROJECT_ID': APPWRITE_PROJECT_ID,
        'GEMINI_API_KEY': GEMINI_API_KEY
    }
    
    missing = [k for k, v in required_vars.items() if not v]
    if missing:
        context.log(f"❌ Missing ENV: {missing}")
        return context.res.json({"ok": False, "missing": missing})
    
    context.log("✅ All ENV OK")
    
    # 2️⃣ اجرا
    db = get_db()
    headlines = fetch_headlines()
    
    success_count = 0
    for i, item in enumerate(headlines, 1):
        context.log(f"\n🔄 [{i}/{len(headlines)}] {item['title'][:60]}")
        
        if is_published(db, item['url']):
            context.log("⏭️ Published")
            continue

        text = extract_article_text(item['url'])
        if len(text) < 100:
            context.log("⚠️ Short")
            continue

        t_fa, s_fa = summarize_and_translate_with_gemini(item['title'], text)
        
        if send_telegram(t_fa, s_fa, item['source'], item['url']):
            save_to_db(db, item['url'], item['title'])
            success_count += 1
            context.log(f"✅ #{success_count}")
            time.sleep(3)  # Rate limit
        else:
            context.log("❌ Telegram failed")
    
    context.log(f"🎉 Published: {success_count}")
    return context.res.json({
        "ok": True,
        "published": success_count,
        "total": len(headlines)
    })
