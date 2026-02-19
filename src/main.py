import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query

# ─── تنظیمات محیطی ───────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT   = os.environ.get("TELEGRAM_CHAT", "")
APPWRITE_ENDPOINT = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT  = os.environ.get("APPWRITE_PROJECT", "")
APPWRITE_KEY      = os.environ.get("APPWRITE_KEY", "")
DATABASE_ID     = os.environ.get("DATABASE_ID", "")
COLLECTION_ID   = os.environ.get("COLLECTION_ID", "")

ASME_URL = "https://www.asme.org/about-asme/media-inquiries/asme-in-the-headlines"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAX_NEWS = 8  # حداکثر تعداد اخبار در هر اجرا


# ─── اسکرپ لینک‌های خبری از صفحه ASME ───────────────────────
def scrape_asme_headlines():
    try:
        resp = requests.get(ASME_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        news_items = []
        seen_urls = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(strip=True)

            # فیلتر لینک‌های داخلی ASME و غیرمرتبط
            if not href.startswith("http"):
                continue
            if "asme.org" in href:
                continue
            if len(text) < 20:
                continue
            if href in seen_urls:
                continue

            # حذف کلمات ناخواسته از عنوان
            skip_words = ["subscribe", "sign in", "log in", "menu", "search", "cookie"]
            if any(w in text.lower() for w in skip_words):
                continue

            seen_urls.add(href)
            news_items.append({"title": text, "url": href})

            if len(news_items) >= MAX_NEWS:
                break

        print(f"✅ {len(news_items)} خبر از ASME یافت شد")
        return news_items

    except Exception as e:
        print(f"❌ خطا در اسکرپ ASME: {e}")
        return []


# ─── استخراج متن اصلی مقاله از URL خبر ──────────────────────
def extract_article_text(url: str) -> str:
    """
    متن اصلی مقاله را از URL استخراج می‌کند.
    از سلکتورهای معتبر استفاده می‌کند و محتوای نامربوط را فیلتر می‌کند.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        # حذف تگ‌های غیرمتنی
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "form", "iframe", "noscript",
                         "figure", "figcaption", "picture",
                         "advertisement", "ads", "sidebar"]):
            tag.decompose()

        # حذف المان‌هایی با کلاس‌های نامربوط
        for tag in soup.find_all(True):
            cls = " ".join(tag.get("class", []))
            if any(w in cls.lower() for w in [
                "ad", "sidebar", "related", "recommend",
                "comment", "social", "share", "promo",
                "newsletter", "subscribe", "popup", "modal"
            ]):
                tag.decompose()

        # سلکتورهای اولویت‌دار برای محتوای اصلی
        selectors = [
            "article .content",
            "article .body",
            "article",
            '[class*="article-body"]',
            '[class*="article-content"]',
            '[class*="story-body"]',
            '[class*="post-content"]',
            '[class*="post-body"]',
            '[class*="entry-content"]',
            '[class*="article__body"]',
            '[class*="story__body"]',
            "main article",
            "main .content",
            ".article-text",
            ".story-text",
            ".post-text",
        ]

        text_parts = []

        for selector in selectors:
            el = soup.select_one(selector)
            if not el:
                continue

            paragraphs = el.find_all("p")
            for p in paragraphs:
                t = p.get_text(separator=" ", strip=True)

                # فیلتر پاراگراف‌های کوتاه یا نامربوط
                if len(t) < 80:
                    continue

                skip_phrases = [
                    "cookie", "subscribe", "newsletter", "advertisement",
                    "sign up", "log in", "privacy policy", "terms of use",
                    "copyright", "all rights reserved", "follow us",
                    "read more", "click here", "download", "share this",
                    "you might also like", "related articles",
                    # فیلتر متن‌های تاریخی/مذهبی که ربطی به مهندسی ندارند
                    "jordan", "gilead", "ephraim", "passover",
                    "biblical", "testament", "scripture",
                ]
                if any(ph in t.lower() for ph in skip_phrases):
                    continue

                text_parts.append(t)

                if len(text_parts) >= 3:  # حداکثر ۳ پاراگراف
                    break

            if text_parts:
                break  # اولین سلکتور موفق کافیه

        # Fallback: همه p های صفحه
        if not text_parts:
            all_p = soup.find_all("p")
            for p in all_p:
                t = p.get_text(separator=" ", strip=True)
                if len(t) > 100:
                    text_parts.append(t)
                if len(text_parts) >= 2:
                    break

        combined = " ".join(text_parts)

        # اعتبارسنجی نهایی: اگه متن خیلی کوتاه بود، خالی برگردون
        if len(combined) < 50:
            return ""

        # برش به ۵۰۰ کاراکتر برای ترجمه سریع‌تر
        return combined[:500]

    except Exception as e:
        print(f"⚠️ خطا در استخراج متن از {url}: {e}")
        return ""


# ─── ترجمه متن با MyMemory API ───────────────────────────────
def translate_to_fa(text: str) -> str:
    """
    متن انگلیسی را به فارسی ترجمه می‌کند.
    از MyMemory API استفاده می‌کند.
    """
    if not text or not text.strip():
        return ""

    # کوتاه کردن برای جلوگیری از خطای API
    text = text[:480]

    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={
                "q": text,
                "langpair": "en|fa",
                "de": "newsbot@example.com"  # ایمیل برای افزایش limit
            },
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        translated = data.get("responseData", {}).get("translatedText", "")
        status = data.get("responseStatus", 0)

        # بررسی وضعیت ترجمه
        if status == 200 and translated and translated != text:
            # حذف پیغام خطای MyMemory
            if "MYMEMORY WARNING" in translated:
                return ""
            return translated.strip()

        return ""

    except Exception as e:
        print(f"⚠️ خطا در ترجمه: {e}")
        return ""


# ─── ارسال پیام به تلگرام ────────────────────────────────────
def send_telegram(title: str, title_fa: str, summary_fa: str, url: str) -> bool:
    """
    پیام خبر را به کانال تلگرام ارسال می‌کند.
    """
    # ساخت متن پیام
    lines = []

    # عنوان فارسی یا انگلیسی
    if title_fa:
        lines.append(f"📰 *{title_fa}*")
    else:
        lines.append(f"📰 *{title}*")

    lines.append("")  # خط خالی

    # خلاصه فارسی
    if summary_fa:
        lines.append(summary_fa)
        lines.append("")

    # منبع
    lines.append(f"🔗 [مشاهده خبر کامل]({url})")
    lines.append("")
    lines.append("_via ASME In the Headlines_")

    message = "\n".join(lines)

    try:
        # ارسال با sendMessage
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=15
        )

        if resp.status_code == 200:
            print(f"✅ پیام ارسال شد: {title[:50]}...")
            return True
        else:
            print(f"❌ خطای تلگرام {resp.status_code}: {resp.text[:200]}")
            return False

    except Exception as e:
        print(f"❌ خطا در ارسال تلگرام: {e}")
        return False


# ─── بررسی وجود خبر در دیتابیس ──────────────────────────────
def is_duplicate(databases, url: str) -> bool:
    try:
        result = databases.list_documents(
            database_id=DATABASE_ID,
            collection_id=COLLECTION_ID,
            queries=[Query.equal("news_url", url)]
        )
        return result["total"] > 0
    except Exception as e:
        print(f"⚠️ خطا در بررسی دیتابیس: {e}")
        return False


# ─── ذخیره خبر در دیتابیس ───────────────────────────────────
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
        print(f"❌ خطا در ذخیره دیتابیس: {e}")
        return False


# ─── تابع اصلی Appwrite ──────────────────────────────────────
def main(context):
    print("🚀 شروع اجرای News Checker Bot")
    print(f"⏰ زمان: {datetime.now(timezone.utc).isoformat()}")

    # راه‌اندازی Appwrite
    client = Client()
    client.set_endpoint(APPWRITE_ENDPOINT)
    client.set_project(APPWRITE_PROJECT)
    client.set_key(APPWRITE_KEY)
    databases = Databases(client)

    # اسکرپ اخبار
    news_items = scrape_asme_headlines()
    if not news_items:
        msg = "هیچ خبری یافت نشد."
        print(msg)
        return context.res.json({"status": "no_news", "message": msg})

    sent_count = 0
    skipped_count = 0

    for item in news_items:
        url   = item["url"]
        title = item["title"]

        print(f"\n📌 پردازش: {title[:60]}...")
        print(f"   URL: {url}")

        # بررسی تکراری بودن
        if is_duplicate(databases, url):
            print(f"   ⏭️ تکراری - رد شد")
            skipped_count += 1
            continue

        # ترجمه عنوان
        title_fa = translate_to_fa(title)
        print(f"   🔤 عنوان فارسی: {title_fa[:60] if title_fa else '(ترجمه نشد)'}")

        # استخراج متن مقاله
        article_text = extract_article_text(url)
        print(f"   📄 متن استخراجی: {article_text[:80] if article_text else '(یافت نشد)'}...")

        # ترجمه خلاصه
        summary_fa = ""
        if article_text and len(article_text) >= 50:
            summary_fa = translate_to_fa(article_text)
            print(f"   📝 خلاصه فارسی: {summary_fa[:80] if summary_fa else '(ترجمه نشد)'}...")
        else:
            print(f"   ⚠️ متن کافی برای خلاصه یافت نشد")

        # ارسال به تلگرام
        success = send_telegram(title, title_fa, summary_fa, url)

        if success:
            # ذخیره در دیتابیس
            save_to_db(databases, url, title)
            sent_count += 1
        
        # تاخیر کوتاه بین پیام‌ها
        import time
        time.sleep(1)

    summary = f"✅ {sent_count} خبر ارسال شد | ⏭️ {skipped_count} تکراری رد شد"
    print(f"\n{summary}")

    return context.res.json({
        "status": "ok",
        "sent": sent_count,
        "skipped": skipped_count,
        "message": summary
    })
