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

def full_escape_markdown_v2(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    text = re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)
    return text.strip()

def url_safe_encode(url: str) -> str:
    return requests.utils.quote(url, safe=':/?#[]@!$&\'()*+,;=')

# ─── Appwrite DB ──────────────────────────────────────
def get_db():
    client = Client()
    client.set_endpoint(APPWRITE_ENDPOINT).set_project(APPWRITE_PROJECT_ID).set_key(APPWRITE_API_KEY)
    return Databases(client)

def is_published(databases, url: str, context) -> bool:
    try:
        # تغییر یافته به news_url طبق درخواست شما
        res = databases.list_documents(DATABASE_ID, COLLECTION_ID, [Query.equal("news_url", [url])])
        return res["total"] > 0
    except Exception as e:
        context.log(f"⚠️ DB Read Error: {e}")
        return False

def save_to_db(databases, url: str, title: str, context):
    try:
        databases.create_document(DATABASE_ID, COLLECTION_ID, ID.unique(), {
            "news_url": url, # تغییر یافته به news_url
            "title": title[:255],
            "published_at": datetime.now(timezone.utc).isoformat()
        })
        context.log("✅ Saved to DB successfully.")
    except Exception as e:
        context.log(f"❌ DB Save Error: {e}")

# ─── News Fetching ─────────────────────────────────────
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
            if href.startswith('/'):
                href = "https://www.asme.org" + href
            
            if len(title) > 30 and not any(b in href.lower() for b in ['about-asme', 'media-inquiries', 'login']):
                if not any(n['url'] == href for n in news):
                    news.append({"url": href, "title": title, "source": "ASME"})
                    
        return news[:5]
    except Exception as e:
        context.log(f"Error fetching headlines: {e}")
        return []

# ─── Article Extract (Text & Image) ────────────────────
def extract_article_data(url: str, context) -> tuple[str, str]:
    text = ""
    image_url = ""
    
    # اولویت اول: Newspaper3k (برای استخراج هوشمند متن اصلی و عکس بدون اضافات سایت)
    try:
        config = Config(fetch_images=True, browser_user_agent=HEADERS['User-Agent'], request_timeout=15)
        article = Article(url, config=config)
        article.download()
        article.parse()
        text = article.text.strip()
        image_url = article.top_image # استخراج عکس اصلی
        if len(text) > 100:
            context.log("✅ Extracted text and image using newspaper3k.")
            return text, image_url
    except Exception as e:
        context.log(f"Newspaper3k error: {e}")

    # اولویت دوم: BeautifulSoup (فقط در صورت شکست اولویت اول)
    context.log("⚠️ Falling back to BeautifulSoup...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "html.parser")
            
            if not image_url:
                og_image = soup.find("meta", property="og:image")
                if og_image: image_url = og_image.get("content", "")

            paragraphs = soup.find_all('p')
            text = "\n".join([p.get_text(strip=True) for p in paragraphs])
    except Exception:
        pass
        
    return text, image_url

# ─── 🦙 Groq Logic (JSON Mode) ─────────────────────────
def summarize_with_groq(title: str, text: str, context) -> tuple[str, str]:
    if not GROQ_API_KEY or len(text) < 100:
        return title, "متن مقاله برای پردازش بسیار کوتاه است یا GROQ_API_KEY تنظیم نشده است."

    # درخواست اکید از هوش مصنوعی برای بازگرداندن فرمت JSON
    prompt = f"""Translate the title to Persian and summarize the text in Persian.
Source Title: {title}
Source Text: {text[:3000]}

You must respond ONLY with a valid JSON object using this exact structure:
{{
  "title_fa": "Persian translation of the title",
  "summary_fa": "Persian summary in 2 informative paragraphs"
}}"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a professional technical translator for engineering news. Respond ONLY in valid JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"} # فعال‌سازی حالت JSON
    }
    
    try:
        context.log("🤖 Sending to Groq API (JSON mode)...")
        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        
        if resp.status_code == 200:
            data = resp.json()
            res_content = data['choices'][0]['message']['content']
            
            # پارس کردن خروجی تمیز JSON
            parsed_json = json.loads(res_content)
            t_fa = parsed_json.get("title_fa", title)
            s_fa = parsed_json.get("summary_fa", "خلاصه دریافت نشد.")
            
            context.log("✅ Groq AI Success (JSON Parsed)")
            return t_fa, s_fa
            
        else:
            context.log(f"⚠️ Groq Failed: {resp.status_code} - {resp.text}")
            return title, f"⚠️ خطای سرور Groq (کد {resp.status_code})."
            
    except Exception as e:
        context.log(f"💥 Groq Request Error: {e}")
        return title, "خطا در ارتباط با هوش مصنوعی Groq."

# ─── 🚀 Telegram Logic (With Photo) ─────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, url: str, image_url: str, context) -> bool:
    safe_title = full_escape_markdown_v2(title_fa)
    safe_source = full_escape_markdown_v2(source)
    safe_url = url_safe_encode(url)

    # محدودیت کپشن عکس در تلگرام ۱۰۲۴ کاراکتر است، بنابراین متن خلاصه را در صورت نیاز کوتاه می‌کنیم
    if image_url:
        max_summary_length = 800 
        if len(summary_fa) > max_summary_length:
            summary_fa = summary_fa[:max_summary_length] + "..."
            
    safe_summary = full_escape_markdown_v2(summary_fa)
    caption = f"*{safe_title}*\n\n{safe_summary}\n\n🌐 منبع: {safe_source}\n🔗 [مشاهده کامل]({safe_url})"

    try:
        # اگر عکس پیدا شد، پیام تصویری ارسال می‌شود
        if image_url and image_url.startswith('http'):
            api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHANNEL,
                "photo": image_url,
                "caption": caption,
                "parse_mode": "MarkdownV2"
            }
            context.log("🖼️ Sending as Photo to Telegram...")
        # اگر عکس نداشت، پیام متنی ساده ارسال می‌شود
        else:
            api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHANNEL,
                "text": caption,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": False
            }
            context.log("📝 Sending as Text to Telegram...")

        resp = requests.post(api_url, json=payload, timeout=15)

        if resp.status_code == 200:
            context.log("✅ Telegram sent successfully.")
            return True
        else:
            context.log(f"❌ Telegram Error {resp.status_code}: {resp.text}")
            
            # سیستم نجات: اگر ارسال عکس به دلیل خطای سایز یا فرمت خراب شد، پیام متنی را امتحان کن
            if image_url:
                context.log("🔄 Retrying as Text message due to Photo failure...")
                api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                payload["text"] = caption
                payload["disable_web_page_preview"] = False
                payload.pop("photo", None)
                payload.pop("caption", None)
                retry_resp = requests.post(api_url, json=payload, timeout=15)
                if retry_resp.status_code == 200:
                    context.log("✅ Fallback Telegram text sent successfully.")
                    return True

    except Exception as e:
        context.log(f"💥 Telegram Net Error: {e}")
    return False

# ─── 🎯 MAIN FUNCTION ──────────────────────────────────
def main(context):
    start_time = time.time()
    context.log("🚀 NewsBot v12.0 - With Images & JSON Logic")

    if not all([TELEGRAM_TOKEN, TELEGRAM_CHANNEL, GROQ_API_KEY]):
        context.log("❌ Missing Environment Variables")
        return context.res.json({"error": "Missing ENV"})

    db = get_db()
    headlines = fetch_headlines(context)
    context.log(f"📋 Found {len(headlines)} headlines")

    if not headlines:
        return context.res.json({"ok": True, "msg": "No headlines"})

    success_count = 0
    
    for item in headlines:
        if time.time() - start_time > 100:
            context.log("⚠️ Timeout approaching. Stopping.")
            break

        context.log(f"🔄 Processing: {item['title'][:40]}...")

        if is_published(db, item['url'], context):
            context.log("⏭️ Already in DB.")
            continue

        # استخراج متن و عکس
        text, image_url = extract_article_data(item['url'], context)
        
        if len(text) < 100:
            context.log("⏭️ Text too short. Skipping.")
            continue

        # تولید محتوا با Groq (فرمت JSON)
        title_fa, summary_fa = summarize_with_groq(item['title'], text, context)

        # ارسال به تلگرام به همراه عکس
        if send_telegram(title_fa, summary_fa, item['source'], item['url'], image_url, context):
            save_to_db(db, item['url'], item['title'], context)
            success_count += 1
            time.sleep(2)

    exec_time = round(time.time() - start_time, 2)
    context.log(f"🎉 Finished. Sent: {success_count} | Time: {exec_time}s")
    
    return context.res.json({"ok": True, "published": success_count})
