import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://olympustaff.com"
DATA_DIR = os.path.join("data", "teamx")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

DETAILS_SYNC_LIMIT = 20   # تجهيز بيانات وفصول أفضل 20 عملاً
MAX_PAGES_SAFETY = 35     # عدد صفحات الفهرس لتغطية مكتبة تيم إكس

os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.8,en;q=0.5",
    "Referer": f"{BASE_URL}/",
    "Connection": "keep-alive"
}

def get_session():
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def normalize_url(raw_url: str) -> str:
    trimmed = raw_url.strip()
    if not trimmed:
        return ""
    if trimmed.startswith("//"):
        trimmed = f"https:{trimmed}"
    if not trimmed.startswith("http://") and not trimmed.startswith("https://"):
        trimmed = f"{BASE_URL}{trimmed}" if trimmed.startswith("/") else f"{BASE_URL}/{trimmed}"

    return (
        trimmed.replace("http://", "https://")
        .replace("team1x1.com", "olympustaff.com")
        .replace("team1x1.fun", "olympustaff.com")
        .replace("teamx.top", "olympustaff.com")
        .replace("team-x.org", "olympustaff.com")
        .replace("teamxnovel.com", "olympustaff.com")
        .rstrip("/")
    )

def format_type(raw_type: str) -> str:
    t = raw_type.strip().lower()
    if any(k in t for k in ["manhwa", "مانهوا"]): return "مانهوا"
    if any(k in t for k in ["manhua", "مانها"]): return "مانها"
    if any(k in t for k in ["webtoon", "ويبتون", "ويب تون"]): return "ويب تون"
    if any(k in t for k in ["novel", "رواية", "روايات"]): return "رواية"
    if any(k in t for k in ["comic", "كوميك"]): return "كوميك"
    if any(k in t for k in ["manga", "مانجا", "مانغا"]): return "مانغا"
    return raw_type if raw_type else "مانها"

def format_rating(raw_rating: str) -> str:
    clean = raw_rating.split("/")[0].replace("★", "").replace("–", "").replace("-", "").strip()
    try:
        val = float(clean)
        return f"{val:.1f}" if val > 0 else ""
    except ValueError:
        return ""

def format_status(raw_status: str) -> str:
    s = raw_status.strip().lower()
    if any(k in s for k in ["ongoing", "مستمر", "مستمرة"]): return "مستمر"
    if any(k in s for k in ["completed", "مكتمل", "مكتملة"]): return "مكتمل"
    if any(k in s for k in ["hiatus", "متوقف", "موسم منتهي"]): return "متوقف مؤقتاً"
    if "متروك" in s: return "متروك"
    if "قادم" in s: return "قريباً"
    return "مستمر"

def extract_chapters_teamx(soup: BeautifulSoup, html: str, series_slug: str) -> dict:
    """استخراج الفصول مع خوارزمية سد الفجوات الذكية (Gap Filling)"""
    chapters_map = {}
    number_regex = re.compile(r"\d+(\.\d+)?")
    existing_numbers = set()

    # 1. التقاط الفصول المعروضة في البطاقات
    cards = soup.select("div.enhanced-chapters-grid div.chapter-card a.chapter-link, div.chapter-card a.chapter-link")
    for el in cards:
        href = normalize_url(el.get("href", ""))
        if f"/series/{series_slug}/" in href:
            num_span = el.select_one(".chapter-number") or el.select_one(".chapter-title")
            raw_num = num_span.text.strip() if num_span else href.split("/")[-1]
            match = number_regex.search(raw_num)
            clean_name = match.group(0) if match else raw_num

            chapters_map[href] = {"name": clean_name}
            val = int(float(clean_name)) if match else None
            if val is not None:
                existing_numbers.add(val)

    # 2. فحص أزرار أول وآخر فصل
    for a in soup.select("div.lastend .inepcx a"):
        href = normalize_url(a.get("href", ""))
        if f"/series/{series_slug}/" in href:
            epcur = a.select_one(".epcur")
            raw_text = epcur.text.strip() if epcur else href.split("/")[-1]
            match = number_regex.search(raw_text)
            clean_name = match.group(0) if match else href.split("/")[-1]

            chapters_map[href] = {"name": clean_name}
            val = int(float(clean_name)) if match else None
            if val is not None:
                existing_numbers.add(val)

    # 3. تحديد الحد الأقصى للفصول وسد النواقص
    max_chapter = max(existing_numbers) if existing_numbers else 0
    min_chapter = min(existing_numbers) if existing_numbers else 1

    total_count_match = re.search(r"(?:قائمة الفصول|الفصول)\s*\(([0-9]+)\)", html)
    total_count = int(total_count_match.group(1)) if total_count_match else 0
    if total_count > max_chapter:
        max_chapter = total_count

    # تعويض الفصول الأولى إذا كانت ناقصة
    if min_chapter > 1:
        for i in range(1, min_chapter):
            ch_url = f"{BASE_URL}/series/{series_slug}/{i}"
            if ch_url not in chapters_map:
                chapters_map[ch_url] = {"name": str(i)}

    # سد أي فجوات تسلسلية حتى آخر فصل
    if max_chapter > 0:
        for i in range(1, max_chapter + 1):
            ch_url = f"{BASE_URL}/series/{series_slug}/{i}"
            if ch_url not in chapters_map:
                chapters_map[ch_url] = {"name": str(i)}

    # فحص الفصل التمهيدي 0
    if any(k in html for k in ["الفصل 0", "فصل تمهيدي", "مقدمة"]):
        ch_0_url = f"{BASE_URL}/series/{series_slug}/0"
        if ch_0_url not in chapters_map:
            chapters_map[ch_0_url] = {"name": "0"}

    return chapters_map

def scrape_manga_details_teamx(session, manga_url: str):
    clean_url = normalize_url(manga_url)
    res = session.get(clean_url, timeout=20)
    html = res.text
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("div.author-info-title h1, h1") or soup.select_one("div.author-info-title h6")
    title = title_el.text.strip() if title_el else "بدون عنوان"

    img_node = soup.select_one("div.text-right img, img[alt='Manga Image']")
    cover = ""
    if img_node:
        cover = img_node.get("src", "").strip()
        if not cover or "data:image" in cover:
            cover = img_node.get("data-src", "").strip() or img_node.get("data-lazy-src", "").strip()
    if not cover:
        meta_img = soup.select_one("meta[property='og:image']")
        cover = meta_img.get("content", "").strip() if meta_img else ""
    cover_url = normalize_url(cover) if cover else ""

    desc_el = soup.select_one("div.review-content p")
    description = desc_el.text.strip() if desc_el else "لا يوجد وصف"
    if description == "لا يوجد وصف":
        meta_desc = soup.select_one("meta[name='description']")
        if meta_desc and meta_desc.get("content"):
            description = meta_desc.get("content").strip()

    rate_el = soup.select_one("#average_rating")
    rating = format_rating(rate_el.text if rate_el else "")

    fav_el = soup.select_one("#rating_count")
    favorites = fav_el.text.strip() if fav_el else ""

    type_el = soup.find(lambda t: t.name in ["div", "span"] and "النوع" in t.text)
    raw_type = type_el.find("a").text.strip() if (type_el and type_el.find("a")) else ""

    status_el = soup.find(lambda t: t.name in ["div", "span"] and "الحالة" in t.text)
    raw_status = status_el.find("a").text.strip() if (status_el and status_el.find("a")) else "مستمر"

    genres = [a.text.strip() for a in soup.select("div.review-author-info a") if a.text.strip()]
    is_novel = "novel" in raw_type.lower() or any("رواية" in g for g in genres) or "رواية" in title

    series_slug = clean_url.split("/")[-1]
    chapters_map = extract_chapters_teamx(soup, html, series_slug)

    return {
        "id": series_slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": format_type("رواية" if is_novel else raw_type),
        "status": format_status(raw_status),
        "rating": rating,
        "favorites": favorites,
        "genres": genres,
        "is_novel": is_novel,
        "chapters": chapters_map
    }

def fetch_teamx_catalog(session) -> list:
    print("جاري سحب الفهرس العام لموقع تيم إكس...")
    catalog = []
    page = 1

    while page <= MAX_PAGES_SAFETY:
        url = f"{BASE_URL}/series" if page == 1 else f"{BASE_URL}/series?page={page}"
        try:
            res = session.get(url, timeout=20)
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("div.listupd div.bsx, div.bsx")

            if not cards:
                break

            new_in_page = 0
            for card in cards:
                a_tag = card.select_one("a")
                if not a_tag:
                    continue

                manga_url = normalize_url(a_tag.get("href", ""))
                slug = manga_url.split("/")[-1]

                if not any(item["id"] == slug for item in catalog):
                    title = a_tag.get("title", "").strip() or (card.select_one(".tt, .title").text.strip() if card.select_one(".tt, .title") else "")
                    if not title:
                        continue

                    img = card.select_one("img")
                    raw_cover = ""
                    if img:
                        raw_cover = img.get("src", "").strip()
                        if not raw_cover or "data:image" in raw_cover:
                            raw_cover = img.get("data-src", "").strip() or img.get("data-lazy-src", "").strip()
                    cover_url = normalize_url(raw_cover) if raw_cover else ""

                    raw_type = card.select_one("span.type, .type")
                    type_text = raw_type.text.strip() if raw_type else ""

                    raw_status = card.select_one("span.status, .status")
                    status_text = raw_status.text.strip() if raw_status else ""

                    is_novel = "novel" in type_text.lower() or "رواية" in type_text or "رواية" in title

                    catalog.append({
                        "id": slug,
                        "title": title,
                        "url": manga_url,
                        "cover_url": cover_url,
                        "type": "رواية" if is_novel else format_type(type_text),
                        "status": format_status(status_text)
                    })
                    new_in_page += 1

            print(f"تيم إكس [صفحة {page}]: تم فهرسة {new_in_page} عمل (المجموع: {len(catalog)})")
            if new_in_page == 0:
                break

            page += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ أثناء سحب صفحة {page}: {e}")
            break

    print(f"تم الانتهاء من فهرسة {len(catalog)} عمل في تيم إكس.")
    return catalog

def sync_teamx_fast():
    session = get_session()
    catalog = fetch_teamx_catalog(session)
    top_targets = catalog[:DETAILS_SYNC_LIMIT]

    for index, item in enumerate(top_targets, 1):
        slug = item["id"]
        manga_url = item["url"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")

        try:
            details = scrape_manga_details_teamx(session, manga_url)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["total_chapters"] = len(details["chapters"])
            print(f"✓ [{index}/{len(top_targets)}] تم تجهيز تيم إكس: {slug} ({len(details['chapters'])} فصل)")
            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء معالجة {slug}: {e}")

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"\n⚡ اكتملت مزامنة تيم إكس الخاطفة! تم الحفظ في {CATALOG_FILE}")

if __name__ == "__main__":
    sync_teamx_fast()
