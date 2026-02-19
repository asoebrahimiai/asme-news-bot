import os
import requests
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
from datetime import datetime, timezone
import time
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
        print(f"Error checking if published: {e}")
        return False

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
    except Exception as e:
        print(f"Error saving to DB: {e}")

# ─── News Fetching and Parsing (REVISED & ROBUST) ───────────────────────────
def fetch_headlines() -> list:
    print("Fetching headlines from ASME...")
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching headlines page: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    news_list = []
    
    # Find the main content area to avoid grabbing irrelevant links
    content_area = soup.find('div', class_='sf_colsIn')
    if not content_area:
        content_area = soup.body

    for a_tag in content_area.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(strip=True)

        if not href.startswith("http") or "asme.org" in href or len(title) < 20:
            continue

        source = ""
        parent_p = a_tag.find_parent('p')
        if parent_p:
            source_text = parent_p.get_text(" ", strip=True).replace(title, "").strip()
            # Clean up the source string by removing date/separator junk
            source_text = source_text.split("–")[0].split("-")[0].strip()
            if 2 < len(source_text) < 80: # A reasonable length for a source name
                source = source_text

        if not any(d['url'] == href for d in news_list):
            news_list.append({"url": href, "title": title, "source": source})
            print(f"  Found: {title[:60]} | Source: [{source}]")

    print(f"Total unique headlines found: {len(news_list)}")
    return news_list[:5] # Limit to 5 for processing

# ─── Article Extraction (using Newspaper3k) ──────────────────────────────────
def extract_article_text(url: str) -> str:
    print(f"Extracting text from: {url[:70]}")
    try:
        config = Config()
        config.browser_user_agent = HEADERS['User-Agent']
        config.request_timeout = 15
        
        article = Article(url, config=config)
        article.download()
        article.parse()
        
        return article.text
    except Exception as e:
        print(f"Error extracting article with newspaper3k: {e}")
        return ""

# ─── AI Summarization & Translation (using Gemini) ───────────────────────────
def summarize_and_translate_with_gemini(title: str, article_text: str) -> tuple[str, str]:
    if not GEMINI_API_KEY:
        return title, "خطا: کلید API برای Gemini تنظیم نشده است."
    
    genai.configure(api_key=GEMINI_API_KEY)

    prompt = f"""
    You are a professional news translator and summarizer.
    First, translate the following news TITLE to Persian.
    Second, summarize the following ARTICLE TEXT into one or two clear and concise Persian paragraphs.
    The summary should be neutral and informative.
    
    TITLE: "{title}"
    ARTICLE TEXT: "{article_text[:4000]}"

    Respond ONLY with the Persian translation and summary in this format:
    TITLE_FA: [Persian Title Here]
    SUMMARY_FA: [Persian Summary Here]
    """
    
    models_to_try = ["gemini-1.5-flash-latest", "gemini-1.0-pro"]
    
    for model_name in models_to_try:
        try:
            print(f"Attempting to use Gemini model: {model_name}")
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            
            text = response.text
            title_fa = text.split("TITLE_FA:")[1].split("SUMMARY_FA:")[0].strip()
            summary_fa = text.split("SUMMARY_FA:")[1].strip()

            if title_fa and summary_fa:
                return title_fa, summary_fa
            else:
                raise ValueError("Invalid response format from Gemini")

        except Exception as e:
            print(f"Error with model {model_name}: {e}")
            if "API version v1beta" in str(e) or "is not found" in str(e):
                print("This model seems unavailable. Trying the next one.")
                continue
            return title, f"خطا در پردازش با مدل {model_name}: {str(e)[:100]}"
            
    return title, "خطا: هیچکدام از مدل‌های هوش مصنوعی در دسترس نبودند. لطفاً نسخه کتابخانه google-generativeai را در requirements.txt بررسی کنید."


# ─── Telegram Bot Functions ──────────────────────────────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, news_url: str) -> bool:
    msg_parts = [f"📰 *{title_fa}*\n"]
    if summary_fa:
        msg_parts.append(f"{summary_fa}\n")
    if source:
        msg_parts.append(f"🌐 *منبع:* {source}")
    msg_parts.append(f"\n🔗 [مشاهده خبر کامل]({news_url})")
    msg_parts.append("\n_via ASME In the Headlines_")

    caption = "\n".join(msg_parts)

    api_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    try:
        r = requests.post(
            f"{api_base}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHANNEL,
                "text": caption,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            },
            timeout=20
        )
        print(f"Telegram API response status: {r.status_code}")
        if r.status_code != 200:
            print(f"Telegram error details: {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        print(f"Exception during Telegram send: {e}")
        return False

# ─── Main Execution Logic ────────────────────────────────────────────────────
def main(context):
    # <<<--- پیام دیباگ برای اطمینان از اجرای کد جدید ---<<<
    print("✅✅✅ RUNNING LATEST CODE VERSION v3.0 ✅✅✅")
    print("===================================")
    print(f"ASME Bot Execution Started at {datetime.now(timezone.utc)}")
    print("===================================")

    if not all([TELEGRAM_TOKEN, TELEGRAM_CHANNEL, DATABASE_ID, COLLECTION_ID, GEMINI_API_KEY]):
        error_msg = "One or more critical environment variables are not set."
        print(f"FATAL: {error_msg}")
        return context.res.json({"error": error_msg, "published": 0}, status_code=400)

    databases = get_db()
    news_list = fetch_headlines()

    if not news_list:
        print("No new headlines found to process.")
        return context.res.json({"published": 0, "message": "No headlines found"})

    new_count = 0
    log = []

    for news in news_list:
        try:
            if is_published(databases, news["url"]):
                print(f"Skipping (already published): {news['url'][:60]}")
                continue

            print(f"\n--- Processing: {news['title'][:70]} ---")
            article_text = extract_article_text(news["url"])
            if not article_text:
                print("  -> Could not extract article text. Skipping.")
                log.append(f"FAIL extract: {news['title'][:40]}")
                continue
            
            # Use Gemini for translation and summarization
            title_fa, summary_fa = summarize_and_translate_with_gemini(news["title"], article_text)
            
            print(f"  -> Gemini Title: {title_fa[:60]}")
            print(f"  -> Gemini Summary (len): {len(summary_fa)}")
            
            # Send to Telegram
            ok = send_telegram(title_fa, summary_fa, news["source"], news["url"])

            if ok:
                save_to_db(databases, news["url"], news["title"])
                new_count += 1
                log.append(f"OK: {news['title'][:50]}")
                time.sleep(2)  # Avoid rate limiting
            else:
                log.append(f"FAIL telegram: {news['title'][:40]}")
                time.sleep(5)

        except Exception as e:
            error_details = f"Unexpected error while processing '{news.get('title', 'N/A')}': {e}"
            print(error_details)
            log.append(error_details)
            
    print("\n===================================")
    print(f"ASME Bot Execution Finished. Published: {new_count}/{len(news_list)}")
    print("===================================")
    
    return context.res.json({
        "published": new_count,
        "total_found": len(news_list),
        "log": log
    })
