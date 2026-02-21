import os
import requests
import time
import re
import warnings
import json
from datetime import datetime, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
from newspaper import Article, Config

# ─── 🔇 Suppress Warnings (Including Appwrite Deprecations) ───
warnings.filterwarnings("ignore")
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

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Cache-Control': 'max-age=0',
}

# ─── 🌐 SITES TO MONITOR (Updated URLs & Selectors) ────
SITES_TO_MONITOR = [
    {
        "source_name": "ASME",
        "url": "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines",
        "base_url": "https://www.asme.org",
        "link_selector": ".headline-list a, article a, div.sf_colsIn a"
    },
    {
        "source_name": "MIT_MechE",
        "url": "https://news.mit.edu/topic/mechanical-engineering",
        "base_url": "https://news.mit.edu",
        "link_selector": ".term-page--news-article--item--title--link, h3.title a, h3 a"
    }
]

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
        # Use fallback method for broader SDK compatibility
        method = getattr(databases, "list_rows", getattr(databases, "list_documents", None))
        if method:
            res = method(DATABASE_ID, COLLECTION_ID, [Query.equal("news_url", [url])])
            return res["total"] > 0
    except Exception as e:
        context.log(f"⚠️ DB Read Error: {e}")
    return False

def save_to_db(databases, url: str, title: str, context):
    try:
        method = getattr(databases, "create_row", getattr(databases, "create_document", None))
        if method:
            method(DATABASE_ID, COLLECTION_ID, ID.unique(), {
                "news_url": url,
                "title": title[:255],
                "published_at": datetime.now(timezone.utc).isoformat()
            })
            context.log(f"✅ Saved to DB: {title[:20]}...")
    except Exception as e:
        context.log(f"❌ DB Save Error: {e}")

# ─── 📰 News Fetching (Smart Fallback) ─────────────────
def fetch_headlines(context):
    all_news = []

    for site in SITES_TO_MONITOR:
        context.log(f"\n🔍 Scanning site: {site['source_name']}")
        try:
            resp = requests.get(site["url"], headers=HEADERS, timeout=20)
            resp.raise_for_status()
            
            context.log(f"📄 Downloaded {len(resp.content)} bytes.")
            soup = BeautifulSoup(resp.content, "html.parser")
            links = soup.select(site["link_selector"])
            
            if not links:
                context.log(f"⚠️ Selectors failed for {site['source_name']}. Initiating Smart Fallback...")
                # Search only in main content body to avoid Menus and Footers
                main_area = soup.find('main') or soup.find(id=re.compile('main|content', re.I)) or soup.find('div', class_=re.compile('content|main', re.I)) or soup
                links = main_area.find_all('a')

            context.log(f"👀 Filtering {len(links)} raw links...")
            site_news_count = 0

            # Extended blacklist to prevent static pages from being identified as news
            bad_words = [
                'login', 'contact', 'privacy', 'terms', 'subscribe', 'cart', 'checkout', 
                'register', 'javascript:', '#', 'events', 'certification', 'publications', 
                'codes-standards', 'membership', 'about-asme', 'author', 'category'
            ]

            for a in links:
                href = a.get("href")
                title = a.get_text(strip=True)

                if not href or not title or len(title) < 30 or title.lower() in ['read more', 'continue']:
                    continue

                full_url = urljoin(site["base_url"], href)

                if not any(b in full_url.lower() for b in bad_words):
                    if not any(n['url'] == full_url for n in all_news):
                        all_news.append({
                            "url": full_url,
                            "title": title,
                            "source": site["source_name"]
                        })
                        site_news_count += 1
                        context.log(f"✅ Extracted: {title[:35]}...")

                if site_news_count >= 3:
                    break

        except Exception as e:
            context.log(f"⚠️ Error fetching from {site['source_name']}: {e}")

    context.log(f"\n📋 Total approved headlines across all sites: {len(all_news)}")
    return all_news

def extract_article_data(url: str, context) -> tuple[str, str]:
    text = ""
    image_url = ""

    try:
        config = Config(fetch_images=True, browser_user_agent=HEADERS['User-Agent'], request_timeout=15)
        article = Article(url, config=config)
        article.download()
        article.parse()
        text = article.text.strip()
        image_url = article.top_image
    except Exception:
        pass

    if len(text) < 200:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "html.parser")
                for script in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    script.decompose()
                article_body = soup.find('article') or soup.find('main') or soup.body
                if article_body:
                    paragraphs = article_body.find_all('p')
                    clean_paragraphs = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 60]
                    text = "\n".join(clean_paragraphs)
                    if not image_url:
                        og_image = soup.find("meta", property="og:image")
                        if og_image: image_url = og_image.get("content", "")
        except Exception:
            pass

    return text, image_url

# ─── 🧠 Groq AI Logic ─────────────────────────────────
def summarize_with_groq(title: str, text: str, context) -> tuple[str, str]:
    if not GROQ_API_KEY:
        return title, "کلید GROQ_API_KEY تنظیم نشده است."

    prompt = f"""You are a professional engineering news editor.
    Task 1: Read the text below. Ignore ads.
    Task 2: Translate the title to Persian.
    Task 3: Summarize the MAIN story in Persian (2 paragraphs).

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
            {"role": "system", "content": "You are a JSON-only response bot. You output only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
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
            return title, f"خطای Groq (کد {resp.status_code})"
    except Exception:
        return title, "خطا در ارتباط با سرور هوش مصنوعی."

# ─── ✈️ Telegram Sender ───────────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, url: str, image_url: str, context) -> bool:
    safe_title = full_escape_markdown_v2(title_fa)
    safe_source = full_escape_markdown_v2(source)
    safe_url = url_safe_encode(url)
    
    if len(summary_fa) > 850: summary_fa = summary_fa[:850] + "..."
    safe_summary = full_escape_markdown_v2(summary_fa)

    caption = f"*{safe_title}*\n\n{safe_summary}\n\n🌐 منبع: {safe_source}\n🔗 [مشاهده کامل]({safe_url})"

    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto" if image_url and image_url.startswith('http') else f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHANNEL, "parse_mode": "MarkdownV2"}
    
    if "sendPhoto" in api_url:
        payload["photo"] = image_url
        payload["caption"] = caption
    else:
        payload["text"] = caption
        payload["disable_web_page_preview"] = False

    try:
        resp = requests.post(api_url, json=payload, timeout=20)
        if resp.status_code == 200:
            context.log("✅ Telegram sent.")
            return True
        elif "sendPhoto" in api_url:
            payload.pop("photo", None)
            payload.pop("caption", None)
            payload["text"] = caption
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload, timeout=20)
            return True
    except Exception:
        pass
    return False

# ─── 🏁 Main Execution ────────────────────────────────
def main(context):
    start_time = time.time()
    context.log("🚀 NewsBot v16.1 - SMART TARGETING Edition")

    db = get_db()
    headlines = fetch_headlines(context)

    success_count = 0
    for item in headlines:
        if time.time() - start_time > 110:
            context.log("⏱️ Time limit reached. Stopping.")
            break

        if is_published(db, item['url'], context):
            context.log(f"⏭️ Skipping (Exists in DB): {item['title'][:20]}...")
            continue

        context.log(f"🔄 Processing: {item['title'][:30]}...")
        text, image_url = extract_article_data(item['url'], context)

        if len(text) < 150:
            context.log("⚠️ Text too short. Skipping.")
            continue

        title_fa, summary_fa = summarize_with_groq(item['title'], text, context)

        if send_telegram(title_fa, summary_fa, item['source'], item['url'], image_url, context):
            save_to_db(db, item['url'], item['title'], context)
            success_count += 1
            time.sleep(2)

    return context.res.json({"ok": True, "sent": success_count})
