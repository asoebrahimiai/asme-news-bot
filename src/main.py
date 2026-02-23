import os
import sys
import json
import hashlib
import feedparser
import requests
import warnings
import re
import time
from datetime import datetime, timezone
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query

# ─── 🔇 Suppress Warnings ────────────────────────────────
if not sys.warnoptions:
    warnings.simplefilter("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

# ─── 🔥 ENV VARIABLES ───────────────────────────────────
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL   = os.getenv("TELEGRAM_CHANNEL")
APPWRITE_ENDPOINT  = os.getenv("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID= os.getenv("APPWRITE_PROJECT_ID")
APPWRITE_API_KEY   = os.getenv("APPWRITE_API_KEY")
DATABASE_ID        = os.getenv("APPWRITE_DATABASE_ID")
COLLECTION_ID      = os.getenv("APPWRITE_COLLECTION_ID")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY")
MAX_POSTS          = int(os.getenv("MAX_POSTS", "3"))

# ─── 🌐 RSS SOURCES (منابع تخصصی) ──────────────────────
RSS_SOURCES = {
    "ScienceDaily_Materials": {
        "url": "https://www.sciencedaily.com/rss/matter_energy/materials_science.xml",
        "emoji": "🔬",
        "category": "علم مواد"
    },
    "Phys_org_Tech": {
        "url": "https://phys.org/rss-feed/technology-news/",
        "emoji": "🔭",
        "category": "فناوری"
    },
    "MIT_News_Engineering": {
        "url": "https://news.mit.edu/rss/topic/engineering",
        "emoji": "⚙️",
        "category": "مهندسی MIT"
    },
    "TechXplore": {
        "url": "https://techxplore.com/rss-feed/",
        "emoji": "🤖",
        "category": "فناوری پیشرفته"
    }
}

# ─── 💾 Appwrite DB (مدیریت دیتابیس و جلوگیری از تکرار) ───
def get_db():
    client = Client()
    client.set_endpoint(APPWRITE_ENDPOINT).set_project(APPWRITE_PROJECT_ID).set_key(APPWRITE_API_KEY)
    return Databases(client)

def make_hash(url: str) -> str:
    """تبدیل لینک به هش برای ذخیره بهینه و بدون خطا در دیتابیس"""
    return hashlib.md5(url.encode()).hexdigest()[:15]

def is_published(databases, url: str) -> bool:
    try:
        url_hash = make_hash(url)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = databases.list_documents(DATABASE_ID, COLLECTION_ID, [Query.equal("news_url", [url_hash])])
            return res["total"] > 0
    except Exception:
        return False

def save_to_db(databases, url: str, title: str):
    try:
        url_hash = make_hash(url)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            databases.create_document(DATABASE_ID, COLLECTION_ID, ID.unique(), {
                "news_url": url_hash,
                "title": title[:255],
                "published_at": datetime.now(timezone.utc).isoformat()
            })
    except Exception:
        pass

# ─── 🛡️ Text Cleaner (فیلتر نابودگر کاراکترهای خارجی) ───
def clean_foreign_chars(text: str) -> str:
    """
    حذف قطعی کاراکترهای چینی، ژاپنی، کره‌ای و روسی از متن ترجمه شده.
    """
    if not text: return ""
    # الگو شامل محدوده یونیکدهای CJK (شرق آسیا) و سیریلیک (روسی)
    pattern = re.compile(r'[\u2E80-\u2FD5\u3190-\u319f\u3400-\u4DBF\u4E00-\u9FCC\uF900-\uFAAD\u0400-\u04FF]+')
    
    cleaned_text = pattern.sub('', text)
    return cleaned_text.replace('  ', ' ').strip()

# ─── 🧠 Groq AI Translator (هوش مصنوعی با قوانین سخت‌گیرانه) ───
def translate_and_summarize(title: str, text: str, context) -> tuple[str, str]:
    if not GROQ_API_KEY: return title, text

    prompt = f"""You are an elite Iranian engineering editor.
    Task 1: Translate the title into fluent Persian (Farsi).
    Task 2: Summarize the main story in 1-2 paragraphs in highly professional Persian.

    CRITICAL CONSTRAINTS:
    - Output strictly in Persian alphabet.
    - NEVER include any Chinese, Japanese, or Cyrillic letters.
    - If the original text has metaphors like 'ghost' or foreign terms, translate them to pure Persian (e.g., 'شبح').
    - Technical terms should be natural.

    Title: {title}
    Text: {text[:3000]}

    Output JSON Format:
    {{
      "title_fa": "عنوان فارسی",
      "summary_fa": "خلاصه فارسی"
    }}"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {
                "role": "system", 
                "content": "You are a JSON-only bot. You output strictly pure Persian text. Asian or Cyrillic characters are strictly forbidden."
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2, # دمای پایین برای کاهش توهم (Hallucination)
        "response_format": {"type": "json_object"}
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            data = json.loads(resp.json()['choices'][0]['message']['content'])
            
            # عبور خروجی از فیلتر نابودگر قبل از ارسال
            title_fa = clean_foreign_chars(data.get("title_fa", title))
            summary_fa = clean_foreign_chars(data.get("summary_fa", text))
            
            return title_fa, summary_fa
        else:
            context.log(f"⚠️ Groq returned status {resp.status_code}")
    except Exception as e:
        context.log(f"❌ Groq API Error: {e}")
        
    return title, text

# ─── ✈️ Telegram Sender ───────────────────────────────
def send_to_telegram(source_cfg: dict, title_fa: str, summary_fa: str, link: str, context) -> bool:
    emoji = source_cfg["emoji"]
    category = source_cfg["category"]
    
    # پاکسازی کاراکترهای مخرب مارک‌داون برای جلوگیری از خطای تلگرام
    safe_title = title_fa.replace('*', '').replace('_', '').replace('`', '')
    safe_summary = summary_fa.replace('*', '').replace('_', '').replace('`', '')

    msg = f"{emoji} *{safe_title}*\n\n🏷 {category}\n\n📄 {safe_summary}\n\n🔗 [مطالعه کامل]({link})"

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL,
        "text": msg,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code == 200:
            return True
        else:
            context.log(f"⚠️ Telegram Error: {resp.text}")
            return False
    except Exception as e:
        context.log(f"❌ Telegram Request Error: {e}")
        return False

# ─── 🏁 Main Logic (Appwrite Entry Point) ──────────────
def main(context):
    start_time = time.time()
    context.log("🚀 NewsBot v18.1 (Anti-Leak + RSS + Round-Robin)")
    
    db = get_db()
    pools = {}

    # 1. واکشی اخبار از خوراک‌های RSS
    for source_name, source_cfg in RSS_SOURCES.items():
        try:
            resp = requests.get(source_cfg["url"], timeout=15)
            feed = feedparser.parse(resp.content)
            entries = feed.get("entries", [])
            
            # فیلتر کردن اخباری که قبلاً در دیتابیس ثبت شده‌اند
            new_entries = []
            for e in entries:
                link = e.get("link", "")
                if link and not is_published(db, link):
                    new_entries.append(e)
            
            pools[source_name] = new_entries
            context.log(f"📊 {source_name}: {len(entries)} total | {len(new_entries)} unread")
        except Exception as e:
            context.log(f"❌ Failed to fetch {source_name}: {e}")

    # 2. الگوریتم انتخاب عادلانه و یکی‌درمیان (Round-Robin)
    active_sources = {k: list(v) for k, v in pools.items() if v}
    selected_items = []
    
    if active_sources:
        source_names = list(active_sources.keys())
        idx = 0
        while len(selected_items) < MAX_POSTS and any(active_sources.values()):
            source = source_names[idx % len(source_names)]
            if active_sources[source]:
                selected_items.append((source, active_sources[source].pop(0)))
            idx += 1

    context.log(f"🎯 Selected {len(selected_items)} items via Round-Robin")
    
    # 3. پردازش، ترجمه و ارسال به تلگرام
    success_count = 0
    
    for source_name, entry in selected_items:
        # محافظت در برابر تایم‌اوت Appwrite (توقف پس از 110 ثانیه)
        if time.time() - start_time > 110:
            context.log("⏱️ Time limit reached. Stopping gracefully.")
            break

        source_cfg = RSS_SOURCES[source_name]
        raw_title = entry.get("title", "")
        raw_summary = entry.get("summary", "")
        link = entry.get("link", "")

        # حذف تگ‌های HTML از خلاصه RSS
        raw_summary = re.sub(r"<[^>]+>", "", raw_summary).strip()
        
        context.log(f"🤖 Translating [{source_name}]: {raw_title[:40]}...")
        title_fa, summary_fa = translate_and_summarize(raw_title, raw_summary, context)

        if send_to_telegram(source_cfg, title_fa, summary_fa, link, context):
            save_to_db(db, link, raw_title)
            success_count += 1
            context.log("✅ Posted & Saved successfully.")
            time.sleep(2) # تاخیر ضد-اسپم تلگرام

    context.log(f"🏁 Execution finished. Total sent: {success_count}")
    return context.res.json({"ok": True, "posted": success_count})
