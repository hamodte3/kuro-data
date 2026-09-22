import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://azorafly.com"
DATA_DIR = "data"
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

FULL_SYNC_LIMIT = 15       # أفضل 15 عملاً تسحب فصولها وصورها بالكامل
CATALOG_PAGES = 3          # عدد صفحات الفهرس (تسحب حوالي 60-70 عملاً كبطاقات بحث)

os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{BASE_URL}/",
    "Connection": "keep-alive"
}

def get_session():
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def normalize_url(url: str) -> str:
    return (
        url.replace("http://", "https://")
        .replace("azoramanga.com", "azorafly.com")
        .replace("/manga/", "/series/")
        .strip()
    )

def format_type(raw_type: str) -> str:
    t = raw_type.strip().lower()
    if any(k in t for k in ["novel", "رواية"]): return "رواية"
    if any(k in t for k in ["manhwa", "مانهوا"]): return "مانهوا"
    if any(k in t for k in ["manhua", "مانها"]): return "مانها"
    if any(k in t for k in ["webtoon", "ويبتون", "ويب تون"]): return "ويب تون"
    if any(k in t for k in ["comic", "كوميك"]): return "كوميك"
    if any(k in t for k in ["manga", "مانجا", "مانغا"]): return "مانغا"
    return raw_type if raw_type else "مانهوا"

def format_status(raw_status: str) -> str:
    s = raw_status.strip().lower()
    if any(k in s for k in ["ongoing", "مستمر", "مستمرة"]): return "مستمر"
    if any(k in s for k in ["completed", "مكتمل", "مكتملة"]): return "مكتمل"
    if any(k in s for k in ["hiatus", "متوقف", "متوقفة"]): return "متوقف مؤقتاً"
    return "مستمر"

def format_rating(raw_rating: str) -> str:
    clean = raw_rating.replace("★", "").replace("–", "").replace("-", "").strip()
    try:
        val = float(clean)
        return f"{val:.1f}" if val > 0 else ""
    except ValueError:
        return ""

def clean_html_text(text: str) -> str:
    if not text: return ""
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup.select("script, style, iframe, .ads, .watermark, .c-tabs-item, .post-title, .manga-action, .list-chapters, .chapters-list, ul, li, h1, h2, h3, h4, .post-status, .manga-info"):
        tag.decompose()
    cleaned = soup.get_text()
    cleaned = (
        cleaned.replace("&nbsp;", " ")
        .replace("&quot;", "\"")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .strip()
    )
    return cleaned

def clean_description(raw_desc: str, title: str) -> str:
    cleaned = clean_html_text(raw_desc)
    lines = [line.strip() for line in cleaned.splitlines()]
    clean_lines = []
    ignore_keywords = [
        "تحديث:", "الملخص التقييمات", "اجعل الكل مقروء", "أوضع علامة",
        "على جميع الفصول", "الفصول", "جديد", "أيام", "ساعات", "دقائق",
        "htthttps", "cookies"
    ]
    for line in lines:
        l = line.lower()
        if not line or line.lower() == title.lower(): continue
        if any(bad in l for bad in ignore_keywords): continue
        if re.search(r"فصل\s*\d+", l): continue
        clean_lines.append(line)
    return "\n\n".join(clean_lines).strip() if clean_lines else "لا يوجد وصف."

def filter_clean_image_urls(raw_urls: list) -> list:
    cleaned = []
    for url in raw_urls:
        u = url.strip().lower()
        if not u or u.endswith(".gif"): continue
        if any(bad in u for bad in ["banner", "advertisement", "tracking", "pixel", "logo", "avatar"]): continue
        if url not in cleaned: cleaned.append(url)
    return cleaned

def scrape_chapter_images(session, chapter_url: str) -> list:
    try:
        res = session.get(normalize_url(chapter_url), timeout=25)
        soup = BeautifulSoup(res.text, "html.parser")
        raw_images = []
        selectors = [
            "div.comic-images-wrapper img[data-reader-page-image]",
            "div.comic-images-wrapper figure.image-container img",
            "img[data-reader-page-image]"
        ]
        comic_images = soup.select(", ".join(selectors))
        for img in comic_images:
            src = img.get("src", "").strip() or img.get("data-src", "").strip()
            if src:
                full_src = src if src.startswith("http") else f"{BASE_URL}{src}"
                raw_images.append(full_src.replace("http://", "https://"))

        if not raw_images:
            for meta in soup.select("section[itemprop=articleBody] meta[itemprop=image]"):
                content = meta.get("content", "").strip()
                if content:
                    raw_images.append(content.replace("http://", "https://"))

        return filter_clean_image_urls(raw_images)
    except Exception as e:
        print(f"خطأ أثناء سحب صور {chapter_url}: {e}")
        return []

def fetch_catalog(session, pages_count=3) -> list:
    """بناء الفهرس العام السريع للأعمال"""
    print(f"جاري سحب الفهرس من {pages_count} صفحات...")
    catalog = []
    
    for page in range(1, pages_count + 1):
        url = f"{BASE_URL}/series" if page == 1 else f"{BASE_URL}/series?page={page}"
        try:
            res = session.get(url, timeout=25)
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("div:has(a.text-foreground[href^='/series/'])")

            for container in cards:
                link = container.select_one("a.text-foreground[href^='/series/']:not([href*='/chapter'])")
                if not link: continue
                title = link.text.strip()
                if "الحالة" in title or not title: continue

                manga_url = link.get("href", "").strip()
                if not manga_url.startswith("http"): manga_url = f"{BASE_URL}{manga_url}"
                manga_url = normalize_url(manga_url)
                slug = manga_url.rstrip("/").split("/")[-1]

                if not any(item["id"] == slug for item in catalog):
                    cover_el = container.select_one("img.object-cover")
                    cover = cover_el.get("src", "").strip() if cover_el else ""
                    if cover and not cover.startswith("http"): cover = f"{BASE_URL}{cover}"

                    catalog.append({
                        "id": slug,
                        "title": title,
                        "url": manga_url,
                        "cover_url": cover.replace("http://", "https://"),
                        "is_fully_cached": False
                    })
        except Exception as e:
            print(f"خطأ أثناء سحب صفحة {page}: {e}")

    print(f"تم تسجيل {len(catalog)} عمل في الفهرس العام.")
    return catalog

def scrape_manga_details(session, manga_url: str):
    clean_url = normalize_url(manga_url).rstrip("/")
    res = session.get(clean_url, timeout=25)
    html = res.text
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("h1[itemprop=name]")
    if not title_el:
        for h in soup.select("h1"):
            if "الحالة" not in h.text:
                title_el = h
                break
    title = title_el.text.strip() if title_el else "بدون عنوان"

    cover_el = soup.select_one("img[itemprop=image]")
    cover_url = cover_el.get("src", "").strip() if cover_el else ""
    if not cover_url:
        meta_img = soup.select_one("meta[property='og:image']")
        cover_url = meta_img.get("content", "").strip() if meta_img else ""
    if cover_url and not cover_url.startswith("http"): cover_url = f"{BASE_URL}{cover_url}"
    cover_url = cover_url.replace("http://", "https://")

    status_el = soup.select_one(".post-content_item:contains(الحالة), .post-status, div:has(h1:contains(الحالة))")
    status = format_status(status_el.text if status_el else "")

    time_units = "لحظات|ثواني|ثوان|دقيقة|دقيقتين|دقائق|ساعة|ساعتين|ساعات|يوم|يومين|أيام|ايام|أسبوع|اسبوع|أسبوعين|اسبوعين|أسابيع|اسابيع|شهر|شهرين|أشهر|اشهر|شهور|سنة|سنتين|سنوات|سنين"
    time_el = soup.find(lambda tag: tag.name in ["span", "div", "p"] and "منذ" in tag.text)
    raw_time = time_el.text.strip() if time_el else ""
    match_time = re.search(rf"منذ\s+(?:\d+\s+)?({time_units})(?:\s+تقريبا|\s+تقريباً)?", raw_time)
    last_update = match_time.group(0).strip() if match_time else ""

    novel_badge = any("رواية" in s.text.strip() and ("blue" in s.get("class", [])) for s in soup.select("span"))
    is_novel = novel_badge or ("رواية" in title) or ("/novel/" in clean_url)
    type_el = soup.select_one("div:has(h1:contains(النوع)) div.inline span")
    manga_type = format_type("رواية" if is_novel else (type_el.text.strip() if type_el else "مانهوا"))

    genres = [a.text.strip() for a in soup.select(".genres-content a, .manga-tags a") if a.text.strip()]

    desc_el = soup.select_one(".review-content p, div.summary__content p, div.manga-excerpt p, div[itemprop=description] p")
    raw_desc = str(desc_el) if desc_el else ""
    if not raw_desc:
        meta_desc = soup.select_one("meta[property='og:description']")
        if meta_desc and len(meta_desc.get("content", "")) > 40: raw_desc = meta_desc.get("content", "")
    final_desc = clean_description(raw_desc, title)

    fav_el = soup.select_one(".bookmark-count, .manga-action .count, .count-bookmark")
    favorites = fav_el.text.strip() if fav_el else ""
    rate_el = soup.select_one(".score.font-bold, .post-total-rating .score")
    rating = format_rating(rate_el.text if rate_el else "")

    series_slug = clean_url.split("/")[-1]
    found_chapters = {}

    for a in soup.select("a[href*='/chapter-'], a[href*='/chapter_']"):
        href = a.get("href", "").strip()
        if href:
            full_url = normalize_url(href if href.startswith("http") else f"{BASE_URL}{href}")
            slug = full_url.rstrip("/").split("/")[-1].split("?")[0]
            raw_num = slug.lower().replace("chapter-", "").replace("chapter_", "").replace("_", ".").replace("-", ".")
            num_match = re.search(r"\d+(\.\d+)?", raw_num)
            clean_name = num_match.group(0) if num_match else raw_num
            found_chapters[full_url] = clean_name

    for match in re.finditer(r"chapter-[0-9]+(?:[-._][0-9a-zA-Z]+)*", html, re.IGNORECASE):
        slug = match.group(0)
        full_url = f"{BASE_URL}/series/{series_slug}/{slug}"
        if full_url not in found_chapters:
            raw_num = slug.lower().replace("chapter-", "").replace("_", ".").replace("-", ".")
            num_match = re.search(r"\d+(\.\d+)?", raw_num)
            clean_name = num_match.group(0) if num_match else raw_num
            found_chapters[full_url] = clean_name

    chapters_list = [{"name": name, "url": url} for url, name in found_chapters.items()]

    return {
        "id": series_slug,
        "title": title,
        "cover_url": cover_url,
        "description": final_desc,
        "type": manga_type,
        "status": status,
        "last_update": last_update,
        "favorites": favorites,
        "rating": rating,
        "genres": genres,
        "is_novel": is_novel,
        "chapters_to_sync": chapters_list
    }

def sync_hybrid():
    session = get_session()
    catalog = fetch_catalog(session, pages_count=CATALOG_PAGES)
    
    # معالجة أول 15 عملاً فقط بشكل تفصيلي مع الصور
    top_15 = catalog[:FULL_SYNC_LIMIT]

    for index, item in enumerate(top_15, 1):
        slug = item["id"]
        manga_url = item["url"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        
        print(f"\n[{index}/{len(top_15)}] معالجة تفصيلية كاملة: {slug}")

        try:
            existing_data = {"id": slug, "chapters": {}}
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)

            details = scrape_manga_details(session, manga_url)

            for key in ["title", "cover_url", "description", "type", "status", "last_update", "favorites", "rating", "genres", "is_novel"]:
                existing_data[key] = details[key]

            new_chapters = 0
            for ch in details["chapters_to_sync"]:
                ch_url = ch["url"]
                ch_name = ch["name"]

                if ch_url in existing_data["chapters"]:
                    continue

                images = scrape_chapter_images(session, ch_url)
                if images:
                    existing_data["chapters"][ch_url] = {
                        "name": ch_name,
                        "images": images
                    }
                    new_chapters += 1

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)

            # وسم العمل في الفهرس بأنه مخزن بالكامل مع آخر البيانات
            item["is_fully_cached"] = True
            item["type"] = details["type"]
            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["latest_chapter"] = details["chapters_to_sync"][0]["name"] if details["chapters_to_sync"] else ""
            item["last_update"] = details["last_update"]

            print(f"تم حفظ {slug} (فصول جديدة: {new_chapters})")
            time.sleep(1)

        except Exception as e:
            print(f"خطأ أثناء معالجة {slug}: {e}")

    # حفظ الفهرس العام
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"\nاكتملت المزامنة بنجاح! تم أرشفة أفضل 15 عملاً + بناء فهرس لـ {len(catalog)} عمل.")

if __name__ == "__main__":
    sync_hybrid()
