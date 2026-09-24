import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://3asq.org"
DATA_DIR = os.path.join("data", "ashiq")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

DETAILS_SYNC_LIMIT = 20   # تجهيز فصول وبيانات أفضل 20 عملاً في ملفات مستقلة
MAX_PAGES_SAFETY = 3     # عدد صفحات الفهرس لتغطية مكتبة العاشق

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

def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = f"{BASE_URL}{url}" if url.startswith("/") else f"{BASE_URL}/{url}"
    return url.replace("http://", "https://").rstrip("/")

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
    if any(k in s for k in ["ongoing", "مستمر", "مستمرة"]): return "مستمر"
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

def extract_chapters_ashiq(session, manga_url: str) -> dict:
    """استخراج خريطة الفصول (الاسم والرابط فقط) عبر AJAX الخاص بقالب Madara"""
    clean_url = normalize_url(manga_url)
    ajax_url = f"{clean_url}/ajax/chapters/"
    elements = []
    
    # 1. طلب AJAX السريع
    try:
        res = session.post(ajax_url, headers={"Referer": clean_url}, timeout=20)
        soup = BeautifulSoup(res.text, "html.parser")
        elements = soup.select("li.wp-manga-chapter a, ul.main.version-chap li a")
    except Exception:
        elements = []

    # 2. بديل احتياطي إذا كان الـ AJAX مغلقاً
    if not elements:
        try:
            res = session.get(clean_url, timeout=20)
            soup = BeautifulSoup(res.text, "html.parser")
            elements = soup.select("li.wp-manga-chapter a, ul.main.version-chap li a")
        except Exception:
            elements = []

    chapters_map = {}
    for a in elements:
        href = a.get("href", "").strip()
        if not href:
            continue
        
        full_url = normalize_url(href)
        if full_url in chapters_map:
            continue

        raw_title = a.text.strip()
        num_match = re.search(r"\d+(\.\d+)?", raw_title)
        if num_match:
            val = float(num_match.group(0))
            clean_name = str(int(val)) if val.is_integer() else str(val)
        else:
            clean_name = raw_title if raw_title else "0"

        chapters_map[full_url] = {"name": clean_name}

    return chapters_map

def scrape_manga_details_ashiq(session, manga_url: str):
    clean_url = normalize_url(manga_url)
    res = session.get(clean_url, timeout=20)
    soup = BeautifulSoup(res.text, "html.parser")

    title_el = soup.select_one("div.post-title h1, h1")
    title = title_el.text.strip() if title_el else "بدون عنوان"

    img_el = soup.select_one("div.summary_image img")
    cover_url = ""
    if img_el:
        cover_url = img_el.get("src", "").strip()
        if not cover_url or "data:image" in cover_url:
            cover_url = img_el.get("data-src", "").strip() or img_el.get("data-lazy-src", "").strip()
    if not cover_url:
        meta_img = soup.select_one("meta[property='og:image']")
        cover_url = meta_img.get("content", "").strip() if meta_img else ""
    if cover_url:
        cover_url = normalize_url(cover_url)

    desc_paragraphs = [
        p.text.replace("<!-- -->", "").strip()
        for p in soup.select("div.manga-excerpt p, div.summary__content p")
        if p.text.strip()
    ]
    description = "\n\n".join(desc_paragraphs) if desc_paragraphs else "لا يوجد وصف"

    rate_el = soup.select_one("#averagerate, div.post-total-rating span.score")
    rating = format_rating(rate_el.text if rate_el else "")

    fav_el = soup.select_one("div.add-bookmark .action_detail span")
    favorites_text = fav_el.text if fav_el else ""
    fav_match = re.search(r"\d+", favorites_text)
    favorites = fav_match.group(0) if fav_match else ""

    genres = [a.text.strip() for a in soup.select("div.genres-content a, .wd-full .mgen a") if a.text.strip()]
    badges = soup.select_one("span.manga-title-badges, div.genres-content")
    badges_text = badges.text if badges else ""

    raw_type_el = soup.find(lambda tag: tag.name in ["div", "span"] and "النوع" in tag.text)
    raw_type = raw_type_el.text if raw_type_el else ""
    is_novel = any("رواية" in x for x in [badges_text, raw_type, title])
    manga_type = format_type("رواية" if is_novel else (raw_type or (genres[0] if genres else badges_text)))

    status_el = soup.find(lambda tag: tag.name in ["div", "span"] and "الحالة" in tag.text)
    status = format_status(status_el.text if status_el else "مستمر")

    # استخراج الفصول السريع
    chapters_map = extract_chapters_ashiq(session, clean_url)
    slug = clean_url.rstrip("/").split("/")[-1]

    return {
        "id": slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": manga_type,
        "status": status,
        "rating": rating,
        "favorites": favorites,
        "genres": genres,
        "is_novel": is_novel,
        "chapters": chapters_map
    }

def fetch_ashiq_catalog(session) -> list:
    print("جاري سحب الفهرس العام لموقع العاشق...")
    catalog = []
    page = 1

    while page <= MAX_PAGES_SAFETY:
        url = f"{BASE_URL}/manga/?m_orderby=views" if page == 1 else f"{BASE_URL}/manga/page/{page}/?m_orderby=views"
        try:
            res = session.get(url, timeout=20)
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("div.page-item-detail.manga")

            if not cards:
                break

            new_in_page = 0
            for card in cards:
                title_links = card.select("h3.h5 a")
                if not title_links:
                    continue
                link = title_links[-1]
                title = link.text.strip()
                manga_url = normalize_url(link.get("href", ""))
                slug = manga_url.split("/")[-1]

                if not any(item["id"] == slug for item in catalog):
                    img = card.select_one("div.item-thumb img")
                    cover = ""
                    if img:
                        cover = img.get("src", "").strip()
                        if not cover or "data:image" in cover:
                            cover = img.get("data-src", "").strip() or img.get("data-lazy-src", "").strip()
                    if cover:
                        cover = normalize_url(cover)

                    score_el = card.select_one("span.score")
                    rating = format_rating(score_el.text if score_el else "")

                    badges = card.select_one("span.manga-title-badges")
                    badges_text = badges.text if badges else ""
                    is_novel = "رواية" in badges_text or "رواية" in title

                    catalog.append({
                        "id": slug,
                        "title": title,
                        "url": manga_url,
                        "cover_url": cover,
                        "type": "رواية" if is_novel else format_type(badges_text),
                        "rating": rating
                    })
                    new_in_page += 1

            if new_in_page == 0:
                break

            page += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ أثناء سحب صفحة {page}: {e}")
            break

    print(f"تم الانتهاء من فهرسة {len(catalog)} عمل في العاشق.")
    return catalog

def sync_ashiq_fast():
    session = get_session()
    catalog = fetch_ashiq_catalog(session)

    # معالجة أول 20 عملاً فقط لتوليد تفاصيلها وفصولها
    top_targets = catalog[:DETAILS_SYNC_LIMIT]

    for index, item in enumerate(top_targets, 1):
        slug = item["id"]
        manga_url = item["url"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")

        try:
            details = scrape_manga_details_ashiq(session, manga_url)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            item["status"] = details["status"]
            item["total_chapters"] = len(details["chapters"])
            print(f"✓ [{index}/{len(top_targets)}] تم تجهيز العاشق: {slug} ({len(details['chapters'])} فصل)")
            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء معالجة {slug}: {e}")

    # حفظ الفهرس العام
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"\n⚡ اكتملت مزامنة العاشق الخاطفة! تم الحفظ في {CATALOG_FILE}")

if __name__ == "__main__":
    sync_ashiq_fast()
