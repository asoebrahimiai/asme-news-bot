import os
import requests
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
from datetime import datetime, timezone
import time

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "")
APPWRITE_ENDPOINT   = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID = os.environ.get("APPWRITE_PROJECT_ID", "")
APPWRITE_API_KEY    = os.environ.get("APPWRITE_API_KEY", "")
DATABASE_ID   = os.environ.get("APPWRITE_DATABASE_ID", "")
COLLECTION_ID = os.environ.get("APPWRITE_COLLECTION_ID", "")

HEADLINES_URL = "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ─── Appwrite ──────────────────────────────────────────────────
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
        print(f"DB check error: {e}")
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
        print(f"DB save error: {e}")

# ─── دریافت اخبار از صفحه اصلی ────────────────────────────────
def fetch_headlines() -> list:
    print("Fetching headlines...")
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"Fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    news_list = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(strip=True)

        if not href.startswith("http"):
            continue
        if "asme.org" in href:
            continue
        if len(title) < 20:
            continue

        source = ""
        parent = a_tag.find_parent()
        if parent:
            for sibling in parent.find_all(string=True, recursive=False):
                s = sibling.strip()
                if s and s != title and len(s) > 2:
                    source = s[:80]
                    break

        news_list.append({"url": href, "title": title, "source": source})
        print(f"  Found: {title[:70]}")

    print(f"Total found: {len(news_list)}")
    return news_list[:5]

# ─── استخراج متن از صفحه خبر ──────────────────────────────────
def extract_article_text(url: str) -> str:
    """ورود به لینک خبر و استخراج پاراگراف‌های اصلی"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        # حذف المان‌های اضافی
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "form", "iframe", "noscript"]):
            tag.decompose()

        # جستجو در تگ‌های معمول محتوا
        content_tags = ["article", "main", ".article-body",
                        ".post-content", ".entry-content", ".story-body"]
        text_parts = []

        for selector in content_tags:
            el = soup.select_one(selector)
            if el:
                paragraphs = el.find_all("p")
                for p in paragraphs[:6]:
                    t = p.get_text(strip=True)
                    if len(t) > 60:
                        text_parts.append(t)
                if text_parts:
                    break

        # اگه چیزی پیدا نشد، همه پاراگراف‌ها
        if not text_parts:
            for p in soup.find_all("p")[:8]:
                t = p.get_text(strip=True)
                if len(t) > 60:
                    text_parts.append(t)

        # حداکثر ۳ پاراگراف اول
        combined = " ".join(text_parts[:3])
        # کوتاه کردن به ۸۰۰ کاراکتر
        return combined[:800] if combined else ""

    except Exception as e:
        print(f"Article fetch error ({url[:50]}): {e}")
        return ""

# ─── ترجمه با MyMemory ─────────────────────────────────────────
def translate_to_persian(text: str) -> str:
    if not text:
        return text
    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:500], "langpair": "en|fa"},
            timeout=12
        )
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("responseData", {}).get("translatedText", "")
            if result and result != text:
                return result
    except Exception as e:
        print(f"Translation error: {e}")
    return text

# ─── ارسال به تلگرام ───────────────────────────────────────────
def send_telegram(title_fa: str, summary_fa: str, source: str, news_url: str) -> bool:
    # ساخت پیام
    msg_parts = [f"📰 *{title_fa}*\n"]

    if summary_fa:
        msg_parts.append(f"{summary_fa}\n")

    if source:
        msg_parts.append(f"🌐 *منبع:* {source}")

    msg_parts.append(f"🔗 [مشاهده خبر کامل]({news_url})")
    msg_parts.append("_via ASME In the Headlines_")

    caption = "\n".join(msg_parts)

    # اگه خیلی بلند بود کوتاه کن
    if len(caption) > 4096:
        caption = (
            f"📰 *{title_fa}*\n\n"
            f"{summary_fa[:500]}...\n\n"
            f"🔗 [مشاهده خبر کامل]({news_url})"
        )

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
            timeout=15
        )
        print(f"Telegram status: {r.status_code}")
        if r.status_code != 200:
            print(f"Telegram error body: {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram exception: {e}")
        return False

# ─── تابع اصلی ─────────────────────────────────────────────────
def main(context):
    print("=== ASME Bot Starting ===")
    print(f"TOKEN set: {bool(TELEGRAM_TOKEN)}")
    print(f"CHANNEL: {TELEGRAM_CHANNEL}")

    if not TELEGRAM_TOKEN:
        return context.res.json({"error": "TELEGRAM_TOKEN not set"})
    if not DATABASE_ID or not COLLECTION_ID:
        return context.res.json({"error": "Database config missing"})

    databases = get_db()
    news_list = fetch_headlines()

    if not news_list:
        return context.res.json({"published": 0, "message": "No headlines found"})

    new_count = 0
    log = []

    for news in news_list:
        try:
            # بررسی انتشار قبلی
            if is_published(databases, news["url"]):
                print(f"Skip (already published): {news['url'][:60]}")
                continue

            print(f"\nProcessing: {news['title'][:70]}")

            # ۱. ترجمه عنوان
            title_fa = translate_to_persian(news["title"])
            print(f"  Title FA: {title_fa[:60]}")
            time.sleep(1)

            # ۲. استخراج متن خبر
            article_text = extract_article_text(news["url"])
            print(f"  Article text length: {len(article_text)}")

            # ۳. ترجمه خلاصه
            summary_fa = ""
            if article_text:
                summary_fa = translate_to_persian(article_text)
                print(f"  Summary FA length: {len(summary_fa)}")
                time.sleep(1)

            # ۴. ارسال به تلگرام
            ok = send_telegram(title_fa, summary_fa, news["source"], news["url"])

            if ok:
                save_to_db(databases, news["url"], news["title"])
                new_count += 1
                log.append(f"OK: {news['title'][:50]}")
                time.sleep(2)
            else:
                log.append(f"FAIL telegram: {news['title'][:40]}")

        except Exception as e:
            print(f"Unexpected error: {e}")
            log.append(f"Error: {str(e)[:60]}")

    print(f"\n=== Done. Published: {new_count}/{len(news_list)} ===")
    return context.res.json({
        "published": new_count,
        "total_found": len(news_list),
        "log": log
    })
