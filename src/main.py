import os
import sys
import logging
import json
import hashlib
import feedparser
import requests
import warnings
from datetime import datetime, timezone
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query

# ─── 🔇 Suppress Warnings & Setup Logging ───────────────
if not sys.warnoptions:
    warnings.simplefilter("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
logging.getLogger("appwrite").setLevel(logging.ERROR)

# ─── 🔥 ENV VARIABLES ──────────────────────────────────
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL   = os.getenv("TELEGRAM_CHANNEL")
APPWRITE_ENDPOINT  = os.getenv("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID= os.getenv("APPWRITE_PROJECT_ID")
APPWRITE_API_KEY   = os.getenv("APPWRITE_API_KEY")
DATABASE_ID        = os.getenv("APPWRITE_DATABASE_ID")
COLLECTION_ID      = os.getenv("APPWRITE_COLLECTION_ID")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY")
MAX_POSTS          = int(os.getenv("MAX_POSTS", "3"))

# ─── 🌐 RSS SOURCES (منابع تخصصی و علمی) ───────────────
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

# ─── 💾 Appwrite DB (جایگزین فایل tmp برای جلوگیری از تکرار) ───
def get_db():
    client = Client()
    client.set_endpoint(APPWRITE_ENDPOINT).set_project(APPWRITE_PROJECT_ID).set_key(APPWRITE_API_KEY)
    return Databases(client)

def make_hash(url: str) -> str:
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
    except Exception as e:
        logger.error(f"DB Save Error: {e}")

# ─── 🧠 Groq AI Translator ─────────────────────────────
def translate_and_summarize(title: str, text: str) -> tuple[str, str]:
    if not GROQ_API_KEY: return title, text

    prompt = f"""You are an engineering news editor.
    Translate the Title to fluent Persian.
    Summarize the main story in pure, professional Persian (Farsi) in 1-2 paragraphs.
    NO English words (except brand names), NO Chinese/Cyrillic characters.

    Title: {title}
    Text: {text[:3000]}

    Output JSON Format:
    {{
      "title_fa": "Persian Title",
      "summary_fa": "Persian Summary"
    }}"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.25,
        "response_format": {"type": "json_object"}
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            data = json.loads(resp.json()['choices'][0]['message']['content'])
            return data.get("title_fa", title), data.get("summary_fa", text)
    except Exception:
        pass
    return title, text

# ─── ✈️ Telegram Sender ───────────────────────────────
def send_to_telegram(source_cfg: dict, title_fa: str, summary_fa: str, link: str) -> bool:
    emoji = source_cfg["emoji"]
    category = source_cfg["category"]
    
    # Cleaning markdown breaking characters for safe Markdown mode
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
        return resp.status_code == 200
    except Exception:
        return False

# ─── 🏁 Main Logic ────────────────────────────────────
def main(context):
    logger.info("🚀 NewsBot v18.0 (Hybrid RSS + Appwrite DB + Groq AI)")
    
    db = get_db()
    pools = {}

    # 1. Fetch from RSS Feeds
    for source_name, source_cfg in RSS_SOURCES.items():
        try:
            resp = requests.get(source_cfg["url"], timeout=15)
            feed = feedparser.parse(resp.content)
            entries = feed.get("entries", [])
            
            # Filter unread items strictly using Appwrite DB
            new_entries = []
            for e in entries:
                link = e.get("link", "")
                if link and not is_published(db, link):
                    new_entries.append(e)
            
            pools[source_name] = new_entries
            logger.info(f"📊 {source_name}: {len(entries)} total | {len(new_entries)} unread")
        except Exception as e:
            logger.error(f"❌ Failed to fetch {source_name}: {e}")

    # 2. Round-Robin Fair Selection
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

    logger.info(f"🎯 Selected {len(selected_items)} items via Round-Robin")
    
    # 3. Process, Translate, and Send
    success_count = 0
    import time
    
    for source_name, entry in selected_items:
        source_cfg = RSS_SOURCES[source_name]
        raw_title = entry.get("title", "")
        raw_summary = entry.get("summary", "")
        link = entry.get("link", "")

        import re
        raw_summary = re.sub(r"<[^>]+>", "", raw_summary).strip()
        
        logger.info(f"🤖 Translating [{source_name}]: {raw_title[:40]}...")
        title_fa, summary_fa = translate_and_summarize(raw_title, raw_summary)

        if send_to_telegram(source_cfg, title_fa, summary_fa, link):
            save_to_db(db, link, raw_title)
            success_count += 1
            logger.info(f"✅ Posted & Saved successfully.")
            time.sleep(2)

    return context.res.json({"ok": True, "posted": success_count})
