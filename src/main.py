import os
import requests
import asyncio
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
from datetime import datetime, timezone

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "")
APPWRITE_ENDPOINT   = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT_ID = os.environ.get("APPWRITE_PROJECT_ID", "")
APPWRITE_API_KEY    = os.environ.get("APPWRITE_API_KEY", "")
DATABASE_ID   = os.environ.get("APPWRITE_DATABASE_ID", "")
COLLECTION_ID = os.environ.get("APPWRITE_COLLECTION_ID", "")

# این URL محتوای ایستا (HTML) دارد
HEADLINES_URL = "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

client = Client()
client.set_endpoint(APPWRITE_ENDPOINT)
client.set_project(APPWRITE_PROJECT_ID)
client.set_key(APPWRITE_API_KEY)
databases = Databases(client)


def translate_to_persian(text: str) -> str:
    try:
        resp = requests.post(
            "https://libretranslate.com/translate",
            json={"q": text, "source": "en", "target": "fa", "format": "text"},
            headers={"Content-Type": "application/json"},
            timeout=20
        )
        if resp.status_code == 200:
            result = resp.json().get("translatedText", "")
            if result:
                return result
    except Exception as e:
        print(f"Translation error: {e}")
    return text


def is_published(url: str) -> bool:
    try:
        res = databases.list_documents(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            queries=[Query.equal("news_url", [url])]
        )
        return res["total"] > 0
    except Exception as e:
        print(f"Appwrite check error: {e}")
        return False


def save_to_db(url: str, title: str):
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
        print(f"Saved to DB: {url[:60]}")
    except Exception as e:
        print(f"Appwrite save error: {e}")


def fetch_headlines() -> list:
    print(f"Fetching: {HEADLINES_URL}")
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=30)
        print(f"HTTP status: {resp.status_code}, size: {len(resp.content)} bytes")
        resp.raise_for_status()
    except Exception as e:
        print(f"Fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    news_list = []

    # پیدا کردن لینک‌های خارجی در صفحه
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(strip=True)

        # فقط لینک‌های خارجی (نه asme.org)
        if not href.startswith("http"):
            continue
        if "asme.org" in href:
            continue
        if len(title) < 20:
            continue

        # اطلاعات اضافه از parent element
        source = ""
        date_str = ""
        parent = a_tag.find_parent()
        if parent:
            siblings_text = parent.get_text(separator="|", strip=True)
            parts = [p.strip() for p in siblings_text.split("|") if p.strip() and p.strip() != title]
            for part in parts:
                if any(char.isdigit() for char in part) and len(part) < 30:
                    date_str = part
                elif not source and len(part) > 2:
                    source = part[:50]

        news_list.append({
            "url": href,
            "title": title,
            "source": source,
            "date": date_str
        })
        print(f"Found: {title[:60]} | {source}")

    print(f"Total external links found: {len(news_list)}")
    return news_list[:15]


def get_og_image(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]
    except Exception:
        pass
    return None


def send_telegram_sync(title_fa: str, source: str, date_str: str,
                        image_url, news_url: str) -> bool:
    caption = (
        f"📰 *{title_fa}*\n\n"
        f"🌐 منبع: {source if source else 'ASME'}\n"
        f"📅 {date_str if date_str else ''}\n\n"
        f"🔗 [مشاهده خبر]({news_url})\n\n"
        f"_via ASME In the Headlines_"
    )
    if len(caption) > 1024:
        caption = f"📰 *{title_fa}*\n\n🔗 [مشاهده خبر]({news_url})"

    api_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

    try:
        if image_url:
            r = requests.post(
                f"{api_base}/sendPhoto",
                json={
                    "chat_id": TELEGRAM_CHANNEL,
                    "photo": image_url,
                    "caption": caption,
                    "parse_mode": "Markdown"
                },
                timeout=20
            )
            print(f"Telegram photo response: {r.status_code} {r.text[:100]}")
            if r.status_code == 200:
                return True

        # ارسال پیام متنی
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
        print(f"Telegram message response: {r.status_code} {r.text[:100]}")
        return r.status_code == 200

    except Exception as e:
        print(f"Telegram send error: {e}")
        return False


def main(context):
    print("=== ASME News Bot Starting ===")

    # چک متغیرها
    print(f"TOKEN set: {bool(TELEGRAM_TOKEN)}")
    print(f"CHANNEL: {TELEGRAM_CHANNEL}")
    print(f"DB_ID: {DATABASE_ID}")
    print(f"COLLECTION_ID: {COLLECTION_ID}")

    if not TELEGRAM_TOKEN:
        return context.res.json({"error": "TELEGRAM_TOKEN not set", "published": 0})
    if not DATABASE_ID or not COLLECTION_ID:
        return context.res.json({"error": "Database config missing", "published": 0})

    news_list = fetch_headlines()

    if not news_list:
        print("No headlines found!")
        return context.res.json({"published": 0, "message": "No headlines found"})

    new_count = 0
    log = []

    for news in news_list:
        try:
            if is_published(news["url"]):
                print(f"Skip (already published): {news['url'][:50]}")
                continue

            print(f"Processing new article: {news['title'][:60]}")
            title_fa = translate_to_persian(news["title"])
            print(f"Translated: {title_fa[:60]}")

            image_url = get_og_image(news["url"])
            print(f"Image: {image_url[:60] if image_url else 'None'}")

            ok = send_telegram_sync(
                title_fa, news["source"], news["date"],
                image_url, news["url"]
            )

            if ok:
                save_to_db(news["url"], news["title"])
                new_count += 1
                log.append(f"Published: {news['title'][:60]}")
                import time
                time.sleep(3)
            else:
                log.append(f"Failed to send: {news['title'][:40]}")

        except Exception as e:
            print(f"Error: {e}")
            log.append(f"Error: {str(e)[:60]}")

    print(f"=== Done. Published {new_count} articles ===")
    return context.res.json({
        "published": new_count,
        "total_found": len(news_list),
        "log": log,
        "status": "ok"
    })
