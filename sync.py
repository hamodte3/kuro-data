import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://azorafly.com"
DATA_DIR = "data"
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join(DATA_DIR, "new.json")

DETAILS_SYNC_LIMIT = 20  # سحب تفاصيل وفصول أحدث 20 عملاً تم تحديثها
MAX_DELTA_PAGES = 5      # فحص أول 5 صفحات فقط كل ساعة

os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
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
    return (
        url.replace("http://", "https://")
        .replace("azoramanga.com", "azorafly.com")
        .replace("/manga/", "/series/")
        .strip()
    )

def format_chapter_number(raw_num) -> str:
    """تنسيق رقم الفصل مع الحفاظ التام على الأرقام العشرية (مثل 89.5)"""
    try:
        val = float(raw_num)
        return str(int(val)) if val.is_integer() else str(val)
    except (ValueError, TypeError):
        return str(raw_num).strip()

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
    return soup.get_text().strip()

def clean_description(raw_desc: str, title: str) -> str:
    cleaned = clean_html_text(raw_desc)
    lines = [l.strip() for l in cleaned.splitlines()]
    clean_lines = [
        l for l in lines 
        if l and l.lower() != title.lower() 
        and not any(bad in l.lower() for bad in ["تحديث:", "اجعل الكل مقروء", "أوضع علامة", "الفصول"])
    ]
    return "\n\n".join(clean_lines).strip() if clean_lines else "لا يوجد وصف."

def load_existing_catalog() -> dict:
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["id"]: item for item in data if "id" in item}
    except Exception as e:
        print(f"خطأ أثناء قراءة الكاتلوج القديم: {e}")
        return {}

def update_global_new_releases(new_releases: list):
    if not new_releases:
        return

    existing = []
    if os.path.exists(GLOBAL_NEW_FILE):
        try:
            with open(GLOBAL_NEW_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    combined = new_releases + existing
    seen = set()
    deduped = []
    for item in combined:
        key = (item.get("id"), item.get("chapter"))
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    with open(GLOBAL_NEW_FILE, "w", encoding="utf-8") as f:
        json.dump(deduped[:15], f, ensure_ascii=False, indent=2)
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لأزورا في {GLOBAL_NEW_FILE}")

def scrape_manga_details(session, manga_url: str):
    clean_url = normalize_url(manga_url).rstrip("/")
    cache_url = f"{clean_url}?_t={int(time.time())}"
    custom_headers = {
        **HEADERS,
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache"
    }

    res = session.get(cache_url, headers=custom_headers, timeout=25)
    html = res.text
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("h1[itemprop=name]") or soup.select_one("h1")
    title = title_el.text.strip() if title_el else "بدون عنوان"

    cover_el = soup.select_one("img[itemprop=image]")
    cover_url = cover_el.get("src", "").strip() if cover_el else ""
    if not cover_url:
        meta_img = soup.select_one("meta[property='og:image']")
        cover_url = meta_img.get("content", "").strip() if meta_img else ""
    if cover_url and not cover_url.startswith("http"): 
        cover_url = f"{BASE_URL}{cover_url}"
    cover_url = cover_url.replace("http://", "https://")

    status_el = soup.select_one('.post-content_item:-soup-contains("الحالة"), .post-status')
    raw_status = status_el.text if status_el else ""
    if not raw_status and "COMPLETED" in html:
        raw_status = "completed"
    status = format_status(raw_status)

    time_el = soup.find(lambda tag: tag.name in ["span", "div", "p"] and "منذ" in tag.text)
    last_update = time_el.text.strip() if time_el else ""

    novel_badge = any("رواية" in s.text.strip() for s in soup.select("span.blue, span.bg-blue"))
    is_novel = novel_badge or ("رواية" in title) or ("/novel/" in clean_url)
    
    type_el = soup.select_one('div:has(h1:-soup-contains("النوع")) div.inline span')
    manga_type = format_type("رواية" if is_novel else (type_el.text.strip() if type_el else "مانهوا"))

    # استخراج التصنيفات الحقيقية
    genre_nodes = soup.select("a[itemprop='genre'], a[href*='genres='], .genres-content a, .manga-tags a")
    genres = list(dict.fromkeys([a.text.strip() for a in genre_nodes if a.text.strip()]))

    desc_el = soup.select_one("div[itemprop=description], .review-content p, div.summary__content p")
    final_desc = clean_description(str(desc_el) if desc_el else "", title)

    rate_meta = soup.select_one("meta[itemprop='ratingValue']")
    rate_el = soup.select_one(".score.font-bold, .post-total-rating .score")
    raw_rate = rate_meta.get("content", "") if rate_meta else (rate_el.text if rate_el else "")
    rating = format_rating(raw_rate)

    series_slug = clean_url.split("/")[-1]
    chapters_map = {}

    # 🎯 1. استخراج postId لاستدعاء الـ API الرسمي المكتشف
    post_id = None
    post_id_match = re.search(r'postId(?:&quot;|"):\s*\[\s*\d+\s*,\s*(\d+)\]', html) or re.search(r'"postId":\s*(\d+)', html)
    if post_id_match:
        post_id = post_id_match.group(1)

    # 🎯 2. جلب كل الفصول دفعة واحدة عبر Endpoint أزورا الحقيقي
    if post_id:
        try:
            api_url = f"https://api.azorafly.com/api/chapters?postId={post_id}&take=1000"
            api_res = session.get(api_url, headers={
                "Origin": BASE_URL,
                "Referer": clean_url,
                "Accept": "application/json"
            }, timeout=20)

            if api_res.status_code == 200:
                data = api_res.json()
                chapters_list = data.get("post", {}).get("chapters", [])
                for ch in chapters_list:
                    slug = ch.get("slug")
                    num_val = ch.get("number")
                    if slug and num_val is not None:
                        clean_name = format_chapter_number(num_val)
                        full_url = f"{BASE_URL}/series/{series_slug}/{slug}"
                        chapters_map[full_url] = {"name": clean_name}
        except Exception as e:
            print(f"تنبيه: تعذر سحب الفصول عبر API أزورا: {e}")

    # خطة احتياطية عبر مسح روابط الصفحة إن تعطل الـ API
    if not chapters_map:
        for a in soup.select("a[href*='/chapter-'], a[href*='/chapter_']"):
            href = a.get("href", "").strip()
            if href:
                full_url = normalize_url(href if href.startswith("http") else f"{BASE_URL}{href}")
                slug = full_url.rstrip("/").split("/")[-1].split("?")[0]
                raw_num = slug.lower().replace("chapter-", "").replace("chapter_", "").replace("_", ".").replace("-", ".")
                num_match = re.search(r"\d+(\.\d+)?", raw_num)
                clean_name = format_chapter_number(num_match.group(0)) if num_match else raw_num
                chapters_map[full_url] = {"name": clean_name}

    return {
        "id": series_slug,
        "title": title,
        "cover_url": cover_url,
        "description": final_desc,
        "type": manga_type,
        "status": status,
        "last_update": last_update,
        "rating": rating,
        "genres": genres,
        "is_novel": is_novel,
        "chapters": chapters_map
    }

def sync_fast():
    session = get_session()
    print(f"بدء المزامنة الذكية لأزورا (فحص أول {MAX_DELTA_PAGES} صفحات)...")
    
    catalog_dict = load_existing_catalog()
    recent_targets = []

    # 1. سحب أول 5 صفحات
    for page in range(1, MAX_DELTA_PAGES + 1):
        url = f"{BASE_URL}/series" if page == 1 else f"{BASE_URL}/series?page={page}"
        try:
            res = session.get(url, timeout=25)
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("div:has(a.text-foreground[href^='/series/'])")
            if not cards: break

            new_in_page = 0
            for container in cards:
                link = container.select_one("a.text-foreground[href^='/series/']:not([href*='/chapter'])")
                if not link: continue
                title = link.text.strip()
                if "الحالة" in title or not title: continue

                manga_url = normalize_url(link.get("href", ""))
                slug = manga_url.rstrip("/").split("/")[-1]

                cover_el = container.select_one("img.object-cover")
                cover = cover_el.get("src", "").strip() if cover_el else ""
                if cover and not cover.startswith("http"): cover = f"{BASE_URL}{cover}"
                cover_url = cover.replace("http://", "https://")

                if slug in catalog_dict:
                    catalog_dict[slug]["title"] = title
                    catalog_dict[slug]["url"] = manga_url
                    if cover_url:
                        catalog_dict[slug]["cover_url"] = cover_url
                    item_ref = catalog_dict.pop(slug)
                    catalog_dict = {slug: item_ref, **catalog_dict}
                else:
                    catalog_dict = {
                        slug: {
                            "id": slug,
                            "title": title,
                            "url": manga_url,
                            "cover_url": cover_url,
                            "type": "مانهوا",
                            "total_chapters": 0,
                            "genres": []
                        },
                        **catalog_dict
                    }

                if not any(t["id"] == slug for t in recent_targets):
                    recent_targets.append(catalog_dict[slug])

                new_in_page += 1

            if new_in_page == 0: break
            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ في صفحة {page}: {e}")
            break

    # 2. جلب تفاصيل وفصول أحدث 20 عملاً
    targets_to_scrape = recent_targets[:DETAILS_SYNC_LIMIT]
    new_releases = []

    for item in targets_to_scrape:
        slug = item["id"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        prev_chaps = item.get("total_chapters", 0)

        try:
            details = scrape_manga_details(session, item["url"])
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            current_chaps = len(details["chapters"])
            item["type"] = details["type"]
            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["total_chapters"] = current_chaps
            item["genres"] = details.get("genres", [])
            
            print(f"✓ تم تجهيز: {slug} ({current_chaps} فصل) - تصنيفات: {item['genres']}")

            if current_chaps > prev_chaps and current_chaps > 0:
                new_releases.append({
                    "id": slug,
                    "title": item["title"],
                    "chapter": f"الفصل {current_chaps}",
                    "type": item.get("type", "مانهوا"),
                    "cover_url": item.get("cover_url", "")
                })

            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ مع {slug}: {e}")

    # 3. حفظ الفهرس التراكمي الشامل بالتصنيفات
    final_merged_catalog = list(catalog_dict.values())
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    # 4. تحديث إشعارات new.json
    if new_releases:
        update_global_new_releases(new_releases)

    print(f"\n⚡ اكتملت المزامنة الخاطفة! الكاتلوج يحتوي {len(final_merged_catalog)} عملاً محفوظاً.")

if __name__ == "__main__":
    sync_fast()
