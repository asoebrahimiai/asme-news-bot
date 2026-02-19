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
                    source = s[:50]
                    break

        news_list.append({"url": href, "title": title, "source": source})
        print(f"  Found: {title[:60]}")

    print(f"Total found: {len(news_list)}")
    return news_list[:5]

def translate_to_persian(text: str) -> str:
    """ترجمه با MyMemory API - رایگان و بدون API Key"""
    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:500], "langpair": "en|fa"},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("responseData", {}).get("translatedText", "")
            if result and result != text:
                print(f"  Translated: {result[:60]}")
                return result
    except Exception as e:
        print(f"Translation error: {e}")
    return text

def send_telegram(title_fa: str, source: str, news_url: str) -> bool:
    caption = (
        f"📰 *{title_fa}*\n\n"
        f"🌐 منبع: {source if source else 'ASME'}\n\n"
        f"🔗 [مشاهده خبر]({news_url})\n\n"
        f"_via ASME In the Headlines_"
    )
    if len(caption) > 4096:
        caption = f"📰 *{title_fa}*\n\n🔗 [مشاهده خبر]({news_url})"

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
        print(f"Telegram: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def main(context):
    print("=== ASME Bot Starting ===")

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
            if is_published(databases, news["url"]):
                print(f"Skip: {news['url'][:50]}")
                continue

            title_fa = translate_to_persian(news["title"])
            ok = send_telegram(title_fa, news["source"], news["url"])

            if ok:
                save_to_db(databases, news["url"], news["title"])
                new_count += 1
                log.append(f"OK: {news['title'][:50]}")
                time.sleep(1)
            else:
                log.append(f"FAIL: {news['title'][:40]}")

        except Exception as e:
            print(f"Error: {e}")
            log.append(f"Error: {str(e)[:60]}")

    print(f"=== Done. Published: {new_count} ===")
    return context.res.json({
        "published": new_count,
        "total_found": len(news_list),
        "log": log
    })
