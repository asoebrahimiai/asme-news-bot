import os
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import time
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query

# ─── تنظیمات ─────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT     = os.environ.get("TELEGRAM_CHAT", "")
APPWRITE_ENDPOINT = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT  = os.environ.get("APPWRITE_PROJECT", "")
APPWRITE_KEY      = os.environ.get("APPWRITE_KEY", "")
DATABASE_ID       = os.environ.get("DATABASE_ID", "")
COLLECTION_ID     = os.environ.get("COLLECTION_ID", "")

MAX_NEWS = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# منابع RSS - به ترتیب اولویت
RSS_SOURCES = [
    # فید رسمی ASME News
    "https://www.asme.org/rss/news",
    # فید Topics & Resources
    "https://www.asme.org/rss/topics-resources",
]

# کلیدواژه‌های فیلتر برای اخبار مرتبط با ASME
ASME_KEYWORDS = [
    "asme", "mechanical engineer", "engineering", "fellow",
    "award", "standard", "manufacturing", "robotics", "aerospace"
]


# ─── خواندن RSS فید ──────────────────────────────────────────
def fetch_rss_news() -> list:
    """اخبار را از RSS فیدهای ASME دریافت می‌کند."""
    news_items = []
    seen_urls = set()

    for rss_url in RSS_SOURCES:
        try:
            print(f"📡 دریافت RSS: {rss_url}")
            resp = requests.get(rss_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()

            root = ET.fromstring(resp.content)
            channel = root.find("channel")
            if channel is None:
                continue

            for item in channel.findall("item"):
                title = item.findtext("title", "").strip()
                link  = item.findtext("link",  "").strip()
                desc  = item.findtext("description", "").strip()

                if not title or not link:
                    continue
                if link in seen_urls:
                    continue
                if len(title) < 15:
                    continue

                seen_urls.add(link)
                news_items.append({
                    "title": title,
                    "url": link,
                    "description": desc
                })

                if len(news_items) >= MAX_NEWS:
                    break

        except Exception as e:
            print(f"⚠️ خطا در RSS {rss_url}: {e}")
            continue

        if len(news_items) >= MAX_NEWS:
            break

    # اگه RSS کار نکرد، از scrape مستقیم استفاده کن
    if not news_items:
        print("🔄 RSS ناموفق بود، تلاش با scrape...")
        news_items = scrape_asme_news_page()

    print(f"✅ {len(news_items)} خبر یافت شد")
    return news_items[:MAX_NEWS]


# ─── اسکرپ مستقیم صفحه ASME News (Fallback) ─────────────────
def scrape_asme_news_page() -> list:
    """
    Fallback: اسکرپ مستقیم از صفحه اخبار ASME.
    این صفحه نسبتاً ایستاست.
    """
    urls_to_try = [
        "https://www.asme.org/topics-resources/society-news",
        "https://www.asme.org/about-asme/news",
    ]

    for page_url in urls_to_try:
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "html.parser")

            news_items = []
            seen_urls = set()

            # جستجو در لینک‌های صفحه
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                text = a.get_text(strip=True)

                # فقط لینک‌های داخلی ASME
                if href.startswith("/"):
                    href = "https://www.asme.org" + href
                elif not href.startswith("https://www.asme.org"):
                    continue

                # فیلتر لینک‌های منوی ناوبری
                skip_paths = [
                    "/cart", "/search", "/sign-in", "/membership",
                    "/codes-standards", "/about-asme/media",
                    "/about-asme/contact", "/about-asme/careers",
                    "/learning-development", "/conferences-events",
                    "/get-involved", "/sitemap", "/terms", "/privacy",
                ]
                if any(sp in href for sp in skip_paths):
                    continue

                if len(text) < 20 or href in seen_urls:
                    continue

                # فقط مقالات خبری
                if "/topics-resources/" in href or "/society-news/" in href or "/news/" in href:
                    seen_urls.add(href)
                    news_items.append({"title": text, "url": href, "description": ""})

                if len(news_items) >= MAX_NEWS:
                    break

            if news_items:
                print(f"✅ {len(news_items)} خبر از {page_url} یافت شد")
                return news_items

        except Exception as e:
            print(f"⚠️ خطا در scrape {page_url}: {e}")

    return []


# ─── استخراج متن مقاله ───────────────────────────────────────
def extract_article_text(url: str, fallback_desc: str = "") -> str:
    """متن اصلی مقاله را استخراج می‌کند."""

    # اگه URL از NewsBreak یا سایت‌های JS-heavy است، از description استفاده کن
    js_heavy_domains = ["newsbreak.com", "medium.com", "substack.com"]
    if any(d in url for d in js_heavy_domains):
        print(f"   ⚠️ سایت JS-heavy، از description استفاده می‌شود")
        return fallback_desc[:400] if fallback_desc else ""

    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        # حذف تگ‌های نامربوط
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "form", "iframe", "noscript",
                         "figure", "figcaption", "picture"]):
            tag.decompose()

        # حذف کلاس‌های نامربوط
        for tag in soup.find_all(True):
            cls = " ".join(tag.get("class", []))
            if any(w in cls.lower() for w in [
                "sidebar", "related", "recommend", "comment",
                "social", "share", "promo", "newsletter",
                "subscribe", "popup", "ad-", "-ad"
            ]):
                tag.decompose()

        # سلکتورها به ترتیب اولویت
        selectors = [
            "article .content",
            "article",
            '[class*="article-body"]',
            '[class*="article-content"]',
            '[class*="story-body"]',
            '[class*="post-content"]',
            '[class*="entry-content"]',
            "main",
            ".content",
        ]

        # کلمات فیلتر نامربوط (مذهبی، تاریخی، و غیره)
        skip_phrases = [
            "cookie", "subscribe", "newsletter", "advertisement",
            "sign up", "log in", "privacy policy", "terms of use",
            "copyright ©", "all rights reserved",
            # متون نامربوط
            "jordan river", "gilead", "ephraim", "shibboleth",
            "passover", "biblical", "scripture", "testament",
        ]

        for selector in selectors:
            el = soup.select_one(selector)
            if not el:
                continue

            paragraphs = el.find_all("p")
            text_parts = []

            for p in paragraphs:
                t = p.get_text(separator=" ", strip=True)
                if len(t) < 80:
                    continue
                if any(ph in t.lower() for ph in skip_phrases):
                    continue
                text_parts.append(t)
                if len(text_parts) >= 2:
                    break

            if text_parts:
                combined = " ".join(text_parts)
                return combined[:500]

        # Fallback: description از RSS
        return fallback_desc[:400] if fallback_desc else ""

    except Exception as e:
        print(f"   ⚠️ خطا در استخراج: {e}")
        return fallback_desc[:400] if fallback_desc else ""


# ─── ترجمه با MyMemory ───────────────────────────────────────
def translate_to_fa(text: str) -> str:
    if not text or len(text.strip()) < 5:
        return ""

    text = text[:480]

    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={
                "q": text,
                "langpair": "en|fa",
                "de": "asmenewsbot@gmail.com"
            },
            timeout=12
        )
        data = resp.json()
        translated = data.get("responseData", {}).get("translatedText", "")
        status = data.get("responseStatus", 0)

        if status == 200 and translated and "MYMEMORY WARNING" not in translated:
            return translated.strip()
        return ""

    except Exception as e:
        print(f"   ⚠️ ترجمه ناموفق: {e}")
        return ""


# ─── ارسال به تلگرام ─────────────────────────────────────────
def send_telegram(title: str, title_fa: str, summary_fa: str, url: str) -> bool:
    display_title = title_fa if title_fa else title

    lines = [f"📰 *{display_title}*", ""]

    if summary_fa and len(summary_fa) > 20:
        lines += [summary_fa, ""]

    lines += [
        f"🔗 [مشاهده خبر کامل]({url})",
        "",
        "_via ASME News_"
    ]

    message = "\n".join(lines)

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=12
        )
        if resp.status_code == 200:
            print(f"   ✅ ارسال موفق")
            return True
        else:
            print(f"   ❌ خطای تلگرام: {resp.status_code} - {resp.text[:100]}")
            return False
    except Exception as e:
        print(f"   ❌ خطا: {e}")
        return False


# ─── دیتابیس ─────────────────────────────────────────────────
def is_duplicate(databases, url: str) -> bool:
    try:
        r = databases.list_documents(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            queries=[Query.equal("news_url", url)]
        )
        return r["total"] > 0
    except Exception as e:
        print(f"   ⚠️ خطای DB بررسی: {e}")
        return False


def save_to_db(databases, url: str, title: str) -> bool:
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
        return True
    except Exception as e:
        print(f"   ❌ خطای DB ذخیره: {e}")
        return False


# ─── تابع اصلی ───────────────────────────────────────────────
def main(context):
    print("🚀 شروع ASME News Bot")
    print(f"⏰ {datetime.now(timezone.utc).isoformat()}")

    # راه‌اندازی Appwrite
    client = Client()
    client.set_endpoint(APPWRITE_ENDPOINT)
    client.set_project(APPWRITE_PROJECT)
    client.set_key(APPWRITE_KEY)
    databases = Databases(client)

    # دریافت اخبار
    news_items = fetch_rss_news()

    if not news_items:
        print("❌ هیچ خبری یافت نشد")
        return context.res.json({"status": "no_news"})

    sent = 0
    skipped = 0

    for item in news_items:
        url   = item["url"]
        title = item["title"]
        desc  = item.get("description", "")

        print(f"\n📌 {title[:70]}")

        if is_duplicate(databases, url):
            print(f"   ⏭️ تکراری")
            skipped += 1
            continue

        # ترجمه عنوان
        title_fa = translate_to_fa(title)
        print(f"   🔤 {title_fa[:60] if title_fa else '(ترجمه نشد)'}")

        # استخراج و ترجمه خلاصه
        article_text = extract_article_text(url, fallback_desc=desc)
        summary_fa = ""
        if article_text and len(article_text) >= 50:
            summary_fa = translate_to_fa(article_text)
            print(f"   📝 {summary_fa[:60] if summary_fa else '(ترجمه نشد)'}")

        # ارسال
        if send_telegram(title, title_fa, summary_fa, url):
            save_to_db(databases, url, title)
            sent += 1

        time.sleep(1.5)

    result = f"✅ {sent} ارسال | ⏭️ {skipped} تکراری"
    print(f"\n{result}")
    return context.res.json({"status": "ok", "sent": sent, "skipped": skipped})
