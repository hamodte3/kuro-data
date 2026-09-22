import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://despair-manga.net"
DATA_DIR = os.path.join("data", "despair")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

DETAILS_SYNC_LIMIT = 20   # تجهيز بيانات وفصول أفضل 20 عملاً
MAX_PAGES_SAFETY = 40     # عدد صفحات الفهرس لتغطية مكتبة ديسبير

os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{BASE_URL}/",
    "Connection": "keep-alive"
}

def get_session():
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def normalize_page_url(raw_url: str) -> str:
    trimmed = raw_url.strip()
    if not trimmed:
        return ""
    if trimmed.startswith("//"):
        trimmed = f"https:{trimmed}"
    if not trimmed.startswith("http://") and not trimmed.startswith("https://"):
        trimmed = f"{BASE_URL}{trimmed}" if trimmed.startswith("/") else f"{BASE_URL}/{trimmed}"

    clean = (
        trimmed.replace("http://", "https://")
        .replace("despair-world.com", "despair-manga.net")
        .replace("despair-manga.com", "despair-manga.net")
        .replace("despair-manga.net//", "despair-manga.net/")
    )
    if "/manga/" in clean and not clean.endswith("/") and "." not in clean.split("/")[-1]:
        clean = f"{clean}/"
    return clean

def clean_image_url(raw_url: str) -> str:
    clean = raw_url.replace("\\/", "/").strip()
    if clean.startswith("//"):
        clean = f"https:{clean}"
    return clean.split("?")[0].rstrip("/")

def format_type(raw_type: str) -> str:
    t = raw_type.strip().lower()
    if any(k in t for k in ["manhwa", "مانهوا"]): return "مانهوا"
    if any(k in t for k in ["manhua", "مانها"]): return "مانها"
    if any(k in t for k in ["webtoon", "ويبتون", "ويب تون"]): return "ويب تون"
    if any(k in t for k in ["novel", "رواية"]): return "رواية"
    if any(k in t for k in ["comic", "كوميك"]): return "كوميك"
    if any(k in t for k in ["manga", "مانجا", "مانغا"]): return "مانغا"
    return raw_type if raw_type else "مانغا"

def format_status(raw_status: str) -> str:
    s = raw_status.strip().lower()
    if any(k in s for k in ["ongoing", "on-going", "مستمر", "مستمرة"]): return "مستمر"
    if any(k in s for k in ["completed", "مكتمل", "مكتملة"]): return "مكتمل"
    if any(k in s for k in ["hiatus", "متوقف"]): return "متوقف مؤقتاً"
    return "مستمر"

def format_rating(raw_rating: str) -> str:
    clean = raw_rating.replace("★", "").replace("–", "").replace("-", "").strip()
    try:
        val = float(clean)
        return f"{val:.1f}" if val > 0 else ""
    except ValueError:
        return ""

def extract_chapters_despair(soup: BeautifulSoup) -> dict:
    chapters_map = {}
    elements = soup.select("div#chapterlist ul li, div.eplister ul li")

    for element in elements:
        link = element.select_one("a")
        if not link:
            continue
        raw_href = link.get("href", "").strip()
        if not raw_href or "{{" in raw_href or raw_href.startswith("#"):
            continue

        ch_url = normalize_page_url(raw_href)
        if ch_url in chapters_map:
            continue

        data_num = element.get("data-num", "").strip()
        chap_span = element.select_one("span.chapternum")
        raw_title = data_num if data_num else (chap_span.text.strip() if chap_span else link.text.strip())

        num_match = re.search(r"\d+(\.\d+)?", raw_title)
        if num_match:
            val = float(num_match.group(0))
            clean_name = str(int(val)) if val.is_integer() else str(val)
        else:
            clean_name = raw_title if raw_title else "0"

        chapters_map[ch_url] = {"name": clean_name}

    return chapters_map

def scrape_manga_details_despair(session, manga_url: str):
    valid_url = normalize_page_url(manga_url)
    res = session.get(valid_url, timeout=20)
    soup = BeautifulSoup(res.text, "html.parser")

    title_el = soup.select_one("h1.entry-title, h1")
    title = title_el.text.strip() if title_el else "بدون عنوان"

    img_node = soup.select_one("div.thumb img, img.wp-post-image")
    cover = ""
    if img_node:
        cover = img_node.get("src", "").strip()
        if not cover or "data:image" in cover:
            cover = img_node.get("data-src", "").strip() or img_node.get("data-lazy-src", "").strip()
    if not cover:
        meta_img = soup.select_one("meta[property='og:image']")
        cover = meta_img.get("content", "").strip() if meta_img else ""
    cover_url = clean_image_url(cover) if cover else ""

    desc_paragraphs = [
        p.text.strip() for p in soup.select("div.entry-content[itemprop=description] p, div.entry-content-single p, div.summary__content p")
        if p.text.strip()
    ]
    description = "\n\n".join(desc_paragraphs) if desc_paragraphs else "لا يوجد وصف"

    rate_el = soup.select_one("div.num[itemprop=ratingValue], .numscore")
    rating = format_rating(rate_el.text if rate_el else "")

    status_el = soup.find(lambda t: t.name in ["div", "span"] and "Status" in t.text)
    raw_status = status_el.find("i").text.strip() if (status_el and status_el.find("i")) else "مستمر"
    status = format_status(raw_status)

    type_el = soup.find(lambda t: t.name in ["div", "span"] and "Type" in t.text)
    raw_type = type_el.find("a").text.strip() if (type_el and type_el.find("a")) else ""

    fav_el = soup.select_one("div.bmc")
    fav_text = fav_el.text if fav_el else ""
    fav_match = re.search(r"\d+", fav_text)
    favorites = fav_match.group(0) if fav_match else ""

    genres = [a.text.strip() for a in soup.select("div.wd-full span.mgen a") if a.text.strip()]
    is_novel = "novel" in raw_type.lower() or any("رواية" in g for g in genres) or "رواية" in title
    manga_type = format_type("رواية" if is_novel else (raw_type or (genres[0] if genres else "مانغا")))

    meta_time = soup.select_one("meta[property='article:modified_time']")
    last_update = meta_time.get("content", "").split("T")[0] if meta_img else ""

    chapters_map = extract_chapters_despair(soup)
    slug = valid_url.rstrip("/").split("/")[-1]

    return {
        "id": slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": manga_type,
        "status": status,
        "last_update": last_update,
        "rating": rating,
        "favorites": favorites,
        "genres": genres,
        "is_novel": is_novel,
        "chapters": chapters_map
    }

def fetch_despair_catalog(session) -> list:
    print("جاري سحب الفهرس العام لموقع ديسبير...")
    catalog = []
    page = 1

    while page <= MAX_PAGES_SAFETY:
        url = f"{BASE_URL}/all-manga/" if page == 1 else f"{BASE_URL}/all-manga/page/{page}/"
        try:
            res = session.get(url, timeout=20)
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("div.bsx")

            if not cards:
                break

            new_in_page = 0
            for card in cards:
                link = card.select_one("a")
                if not link:
                    continue

                manga_url = normalize_page_url(link.get("href", ""))
                slug = manga_url.rstrip("/").split("/")[-1]

                if not any(item["id"] == slug for item in catalog):
                    title = link.get("title", "").strip() or (card.select_one(".tt").text.strip() if card.select_one(".tt") else "")
                    if not title:
                        continue

                    img_node = card.select_one("img")
                    raw_cover = ""
                    if img_node:
                        raw_cover = img_node.get("src", "").strip()
                        if not raw_cover or "data:image" in raw_cover:
                            raw_cover = img_node.get("data-src", "").strip() or img_node.get("data-lazy-src", "").strip()
                    cover_url = clean_image_url(raw_cover)

                    type_el = card.select_one("span.type")
                    raw_type = type_el.text.strip() if type_el else ""

                    status_el = card.select_one("span.status")
                    raw_status = status_el.text.strip() if status_el else ""

                    is_novel = "novel" in raw_type.lower() or "رواية" in title

                    catalog.append({
                        "id": slug,
                        "title": title,
                        "url": manga_url,
                        "cover_url": cover_url,
                        "type": "رواية" if is_novel else format_type(raw_type),
                        "status": format_status(raw_status)
                    })
                    new_in_page += 1

            print(f"ديسبير [صفحة {page}]: تم فهرسة {new_in_page} عمل (المجموع: {len(catalog)})")
            if new_in_page == 0:
                break

            page += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ أثناء سحب صفحة {page}: {e}")
            break

    print(f"تم الانتهاء من فهرسة {len(catalog)} عمل في ديسبير.")
    return catalog

def sync_despair_fast():
    session = get_session()
    catalog = fetch_despair_catalog(session)
    top_targets = catalog[:DETAILS_SYNC_LIMIT]

    for index, item in enumerate(top_targets, 1):
        slug = item["id"]
        manga_url = item["url"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")

        try:
            details = scrape_manga_details_despair(session, manga_url)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["total_chapters"] = len(details["chapters"])
            print(f"✓ [{index}/{len(top_targets)}] تم تجهيز ديسبير: {slug} ({len(details['chapters'])} فصل)")
            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء معالجة {slug}: {e}")

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"\n⚡ اكتملت مزامنة ديسبير الخاطفة! تم الحفظ في {CATALOG_FILE}")

if __name__ == "__main__":
    sync_despair_fast()
