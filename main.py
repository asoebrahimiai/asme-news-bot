import os
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
import requests
from bs4 import BeautifulSoup
import hashlib


def main(context):
    # 1. Appwrite client
    client = Client()
    client.set_endpoint(os.getenv("APPWRITE_ENDPOINT"))
    client.set_project(os.getenv("APPWRITE_PROJECT_ID"))
    client.set_key(os.getenv("APPWRITE_API_KEY"))

    db = Databases(client)

    DB_ID = os.getenv("APPWRITE_DB_ID")
    COLLECTION_ID = os.getenv("APPWRITE_COLLECTION_ID")

    # 2. Fetch ASME news
    url = "https://www.asme.org/topics-resources/society-news/asme-news"
    html = requests.get(url, timeout=10).text
    soup = BeautifulSoup(html, "html.parser")

    cards = soup.select("div.c-article-card")

    saved = 0

    for card in cards:
        title = card.get_text(strip=True)
        link = card.find("a")["href"]

        h = hashlib.sha256(link.encode()).hexdigest()

        article = {
            "title": title,
            "link": link,
            "hash": h
        }

        try:
            db.create_document(
                DB_ID,
                COLLECTION_ID,
                ID.unique(),
                article
            )
            saved += 1
        except Exception:
            pass  # duplicate → OK

    context.log(f"Saved {saved} new articles ✅")
