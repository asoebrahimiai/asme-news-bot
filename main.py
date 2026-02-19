import os
import requests
import asyncio
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query
import telegram
from googletrans import Translator
from datetime import datetime

# ==================== تنظیمات ====================
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL")
APPWRITE_ENDPOINT   = os.environ.get("APPWRITE_ENDPOINT")
APPWRITE_PROJECT_ID = os.environ.get("APPWRITE_PROJECT_ID")
APPWRITE_API_KEY    = os.environ.get("APPWRITE_API_KEY")
DATABASE_ID   = os.environ.get("APPWRITE_DATABASE_ID")
COLLECTION_ID = os.environ.get("APPWRITE_COLLECTION_ID")

HEADLINES_URL = "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
}

client = Client()
client.set_endpoint(APPWRITE_ENDPOINT)
client.set_project(APPWRITE_PROJECT_ID)
client.set_key(APPWRITE_API_KEY)
databases = Databases(client)
translator = Translator()


# ==================== توابع ====================

def translate_to_persian(text: str) -> str:
    try:
        return translator.translate(text, src="en", dest="fa").text
    except Exception:
        return text


def is_published(url: str) -> bool:
    try:
        res = databases.list_documents(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            queries=[Query.equal("news_url", [url])]
        )
        return res["total"] > 0
    except Exception:
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
                "published_at": datetime.utcnow().isoformat() + "Z"
            }
        )
    except Exception as e:
        print(f"DB Error: {e}")


def fetch_headlines() -> list:
    try:
        resp = requests.get(HEADLINES_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"Fetch Error: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    news_list = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(strip=True)

        if not href.startswith("http") or "asme.org" in href:
            continue
        if len(title) < 20:
            continue

        source = ""
        date_str = ""
        parent = a_tag.parent
        if parent:
            lines = [l.strip() for l in parent.get_text(separator="\n", strip=True).split("\n") if l.strip()]
            for line in lines:
                if line != title and len(line) > 3:
                    if any(char.isdigit() for char in line):
                        date_str = line
                    elif not source:
                        source = line

        news_list.append({"url": href, "title": title, "source": source, "date": date_str})

    return news_list[:20]


def get_og_image(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        og = soup.find("meta", property="og:image")
        if og:
            return og.get("content")
    except Exception:
        pass
    return None


async def send_telegram(title_fa, source, date_str, image_url, news_url):
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    caption = (
        f"📰 *{title_fa}*\n\n"
        f"🌐 منبع: {source}\n"
        f"📅 تاریخ: {date_str}\n\n"
        f"🔗 [مشاهده خبر اصلی]({news_url})\n"
        f"_via ASME In the Headlines_"
    )
    if len(caption) > 1024:
        caption = f"📰 *{title_fa}*\n\n🔗 [مشاهده خبر]({news_url})\n🌐 {source}"

    try:
        if image_url:
            try:
                await bot.send_photo(chat_id=TELEGRAM_CHANNEL, photo=image_url,
                                     caption=caption, parse_mode="Markdown")
                return True
            except Exception:
                pass
        await bot.send_message(chat_id=TELEGRAM_CHANNEL, text=caption,
                               parse_mode="Markdown", disable_web_page_preview=False)
        return True
    except Exception as e:
        print(f"Telegram Error: {e}")
        return False


async def process():
    news_list = fetch_headlines()
    print(f"Found: {len(news_list)} headlines")
    new_count = 0

    for news in news_list:
        if is_published(news["url"]):
            continue
        title_fa = translate_to_persian(news["title"])
        image_url = get_og_image(news["url"])
        ok = await send_telegram(title_fa, news["source"], news["date"],
                                  image_url, news["url"])
        if ok:
            save_to_db(news["url"], news["title"])
            new_count += 1
            await asyncio.sleep(5)

    return new_count


# ==================== Entry Point برای Appwrite ====================
def main(context):
    count = asyncio.run(process())
    return context.res.json({"published": count, "status": "ok"})
