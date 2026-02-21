import os
import requests
import time
import re
import warnings
import json
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
from newspaper import Article, Config

# ─── 🔇 Suppress Warnings ──────────────────────────────
warnings.simplefilter("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

# ─── 🔥 ENV VARIABLES ──────────────────────────────────
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL   = os.getenv("TELEGRAM_CHANNEL")
APPWRITE_ENDPOINT  = os.getenv("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID= os.getenv("APPWRITE_PROJECT_ID")
APPWRITE_API_KEY   = os.getenv("APPWRITE_API_KEY")
DATABASE_ID        = os.getenv("APPWRITE_DATABASE_ID")
COLLECTION_ID      = os.getenv("APPWRITE_COLLECTION_ID")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY") 

HEADLINES_URL = "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

# ─── 🛠 Helper Functions ──────────────────────────────
def full_escape_markdown_v2(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    text = re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)
    return text.strip()

def url_safe_encode(url: str) -> str:
    return requests.utils.quote(url, safe=':/?#[]@!$&\'()*+,;=')

# ─── 💾 Appwrite DB ───────────────────────────────────
def get_db():
    client = Client()
    client.set_endpoint(APPWRITE_ENDPOINT).set_project(APPWRITE_PROJECT_ID).set_key(APPWRITE_API_KEY)
    return Databases(client)

def is_published(databases, url: str, context) -> bool:
    try:
        res = databases.list_documents(DATABASE_ID, COLLECTION_ID, [Query.equal("news_url", [url])])
        return res["total"] > 0
    except Exception as e:
        context.log(f"⚠️ DB Read Error: {e}")
        return False

def save_to_db(databases, url: str, title: str, context):
    try:
        databases.create_document(DATABASE_ID, COLLECTION_ID, ID.unique(), {
            "news_url": url,
            "title": title[:255],
            "published_at": datetime.now(timezone.utc).isoformat()
        })
        context.log("✅ Saved to DB.")
    except Exception as e:
        context.log(f"❌ DB Save Error: {e}")

# ─── 📰 News Fetching & Cleaning ──────────────────────
def fetch_headlines(context):
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        
        content = soup.find('div', class_='sf_colsIn') or soup.find('body')
        if not content: return []

        news = []
        for a in content.find_all("a", href=True):
            href, title = a["href"], a.get_text(strip=True)
            if href.startswith('/'): href = "https://www.asme.org" + href
            
            # فیلترهای اولیه برای حذف لینک‌های غیرخبری
            bad_words = ['about-asme', 'media-inquiries', 'login', 'contact', 'privacy', 'terms']
            if len(title) > 30 and not any(b in href.lower() for b in bad_words):
                if not any(n['url'] == href for n in news):
                    news.append({"url": href, "title": title, "source": "ASME"})
                    
        return news[:9]
    except Exception as e:
        context.log(f"Error fetching headlines: {e}")
        return []

def extract_article_data(url: str, context) -> tuple[str, str]:
    text = ""
    image_url = ""
    
    # متد 1: Newspaper3k (معمولاً هوشمندتر است)
    try:
        config = Config(fetch_images=True, browser_user_agent=HEADERS['User-Agent'], request_timeout=15)
        article = Article(url, config=config)
        article.download()
        article.parse()
        text = article.text.strip()
        image_url = article.top_image
    except Exception:
        pass

    # اگر متن خیلی کوتاه بود یا پیدا نشد، برو سراغ متد 2
    if len(text) < 200:
        context.log("⚠️ Newspaper3k yielded short text, trying BeautifulSoup cleaning...")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "html.parser")
                
                # حذف تگ‌های مزاحم (تبلیغات، منوها، فوتر)
                for script in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                    script.decompose()
                
                # پیدا کردن بدنه اصلی مقاله
                article_body = soup.find('article') or soup.find('main') or soup.find('div', class_='content') or soup.body
                
                if article_body:
                    paragraphs = article_body.find_all('p')
                    # فقط پاراگراف‌هایی که طولانی هستند را نگه دار (حذف تیترهای تبلیغاتی کوتاه)
                    clean_paragraphs = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 60]
                    text = "\n".join(clean_paragraphs)
                    
                    if not image_url:
                        og_image = soup.find("meta", property="og:image")
                        if og_image: image_url = og_image.get("content", "")
        except Exception:
            pass

    return text, image_url

# ─── 🧠 Groq AI Logic (Strict JSON) ───────────────────
def summarize_with_groq(title: str, text: str, context) -> tuple[str, str]:
    if not GROQ_API_KEY:
        return title, "کلید GROQ_API_KEY تنظیم نشده است."

    # پرامپت بسیار سخت‌گیرانه برای جلوگیری از توهم و ترجمه متون نامربوط
    prompt = f"""You are a professional engineering news editor.
    
    Task 1: Read the text below. Ignore any "Recommended for you", "Related stories", or advertisements at the end. Focus ONLY on the main story related to the title.
    Task 2: Translate the title to Persian.
    Task 3: Summarize the MAIN story in Persian (2 paragraphs). Do not include unrelated topics like 'dog rescue' or 'scandals' unless they are the main topic.

    Source Title: {title}
    Source Text: {text[:3500]}

    Output JSON Format:
    {{
      "title_fa": "Persian Title",
      "summary_fa": "Persian Summary"
    }}"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a JSON-only response bot. You filter out junk text."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2, # کاهش خلاقیت برای دقت بیشتر
        "response_format": {"type": "json_object"}
    }
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if resp.status_code == 200:
            data = resp.json()
            content_str = data['choices'][0]['message']['content']
            parsed = json.loads(content_str)
            return parsed.get("title_fa", title), parsed.get("summary_fa", "خلاصه تولید نشد.")
        else:
            context.log(f"⚠️ Groq Error: {resp.status_code} - {resp.text}")
            # اگر خطای ۴۲۹ یا ۵۰۰ داد، ارور واقعی گروق را برمی‌گرداند نه ارور گوگل را
            return title, f"خطای سرویس هوش مصنوعی Groq (کد {resp.status_code})"
            
    except Exception as e:
        context.log(f"💥 Groq Exception: {e}")
        return title, "خطا در ارتباط با سرور هوش مصنوعی."

# ─── ✈️ Telegram Sender ───────────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, url: str, image_url: str, context) -> bool:
    safe_title = full_escape_markdown_v2(title_fa)
    safe_source = full_escape_markdown_v2(source)
    safe_url = url_safe_encode(url)
    
    # کوتاه کردن خلاصه اگر خیلی طولانی بود (برای کپشن عکس)
    if len(summary_fa) > 850: summary_fa = summary_fa[:850] + "..."
    safe_summary = full_escape_markdown_v2(summary_fa)

    caption = f"*{safe_title}*\n\n{safe_summary}\n\n🌐 منبع: {safe_source}\n🔗 [مشاهده کامل]({safe_url})"
    
    # متد ارسال عکس
    if image_url and image_url.startswith('http'):
        api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHANNEL, 
            "photo": image_url, 
            "caption": caption, 
            "parse_mode": "MarkdownV2"
        }
    else:
        # متد ارسال متن (اگر عکس نبود)
        api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHANNEL, 
            "text": caption, 
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": False
        }

    try:
        resp = requests.post(api_url, json=payload, timeout=20)
        if resp.status_code == 200:
            context.log("✅ Telegram sent.")
            return True
        else:
            context.log(f"❌ TG Error {resp.status_code}: {resp.text}")
            # اگر ارسال عکس شکست خورد، متن خالی بفرست
            if "photo" in payload:
                context.log("🔄 Retrying as text...")
                payload.pop("photo")
                payload.pop("caption")
                payload["text"] = caption
                api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                requests.post(api_url, json=payload, timeout=20)
                return True
    except Exception as e:
        context.log(f"💥 TG Network Error: {e}")
    
    return False

# ─── 🏁 Main Execution ────────────────────────────────
def main(context):
    start_time = time.time()
    context.log("🚀 NewsBot v13.0 - CLEAN & PURE GROQ")

    if not all([TELEGRAM_TOKEN, TELEGRAM_CHANNEL, GROQ_API_KEY]):
        context.log("❌ CRITICAL: Missing ENV Variables")
        return context.res.json({"error": "Missing ENV"})

    db = get_db()
    headlines = fetch_headlines(context)
    
    success_count = 0
    for item in headlines:
        if time.time() - start_time > 110: break # جلوگیری از تایم‌اوت

        if is_published(db, item['url'], context):
            context.log(f"⏭️ Skipping (Exists): {item['title'][:20]}...")
            continue

        context.log(f"🔄 Processing: {item['title'][:30]}...")
        text, image_url = extract_article_data(item['url'], context)
        
        # اگر متن اصلی پیدا نشد یا خیلی کوتاه بود (احتمالاً صفحه لاگین یا ویدیو)
        if len(text) < 150:
            context.log("⚠️ Text too short/irrelevant. Skipping.")
            continue

        title_fa, summary_fa = summarize_with_groq(item['title'], text, context)

        # اگر خروجی هنوز حاوی ارور قدیمی گوگل بود (محض احتیاط)
        if "محدودیت استفاده رایگان" in summary_fa:
            summary_fa = "خطای غیرمنتظره در پردازش متن."

        if send_telegram(title_fa, summary_fa, item['source'], item['url'], image_url, context):
            save_to_db(db, item['url'], item['title'], context)
            success_count += 1
            time.sleep(2)

    return context.res.json({"ok": True, "sent": success_count})
