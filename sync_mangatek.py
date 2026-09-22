import json
import os
import re
import time
from curl_cffi import requests

BASE_URL = "https://mangatek.com"
API_BASE_URL = "https://api.mangatek.com/api"
DATA_DIR = os.path.join("data", "mangatek")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

DETAILS_SYNC_LIMIT = 20   # تجهيز بيانات وفصول أفضل 20 عملاً
MAX_PAGES_SAFETY = 35     # عدد صفحات الفهرس لتغطية مكتبة مانجا تيك

os.makedirs(DATA_DIR, exist_ok=True)

# 🎯 محاكاة تامة لبصمة أندرويد وتوكن الجلسة لتفادي أي حجب
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile; rv:128.0) Gecko/128.0 Firefox/128.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ar,en-US;q=0.8,en;q=0.5",
    "Referer": f"{BASE_URL}/",
    "Origin": BASE_URL,
    "x-app-source": "mangatek",
    "x-device": "Linux | Android | ar | cores:8 | touch:true | dpr:2.5",
    "x-fp": "kuro_android_secure_fingerprint_v1",
    "Connection": "keep-alive"
}

def get_session():
    session = requests.Session(impersonate="chrome120", headers=HEADERS)
    # تفعيل الجلسة عبر pulse تماماً مثل تطبيق وسلوك المتصفح
    try:
        session.get(f"{API_BASE_URL}/session/pulse", timeout=15)
    except Exception:
        pass
    return session

def format_type(raw_type: str) -> str:
    t = raw_type.strip().lower()
    if any(k in t for k in ["novel", "رواية"]): return "رواية"
    if any(k in t for k in ["manhwa", "مانهوا"]): return "مانهوا"
    if any(k in t for k in ["manhua", "مانها"]): return "مانها"
    if any(k in t for k in ["webtoon", "ويبتون", "ويب تون"]): return "ويب تون"
    if any(k in t for k in ["comic", "كوميك"]): return "كوميك"
    if any(k in t for k in ["manga", "مانجا", "مانغا"]): return "مانغا"
    return "مانهوا"

def format_status(raw_status: str) -> str:
    s = raw_status.strip().lower()
    if s in ["ongoing", "on-going", "مستمر"]: return "مستمر"
    if s in ["completed", "مكتمل"]: return "مكتمل"
    if s in ["hiatus", "متوقف", "متوقف مؤقتاً"]: return "متوقف مؤقتاً"
    return raw_status if raw_status else "مستمر"

def format_rating(raw_rating) -> str:
    try:
        clean = str(raw_rating).replace("★", "").replace("–", "").replace("-", "").strip()
        val = float(clean)
        return f"{val:.1f}" if val > 0 else ""
    except (ValueError, TypeError):
        return ""

def scrape_manga_details_mangatek(session, slug: str):
    """جلب تفاصيل العمل وفصوله من API مانجا تيك النقي"""
    url = f"{API_BASE_URL}/manga/{slug}"
    res = session.get(url, timeout=20)
    data = res.json()
    
    item = data.get("data") or data.get("manga") or data
    title = (item.get("title") or item.get("title_ar") or "بدون عنوان").strip()
    
    cover = (item.get("cover_image") or item.get("cover") or "").strip()
    cover_url = cover if cover.startswith("http") else f"{BASE_URL}{cover}" if cover else ""

    description = (item.get("description") or "لا يوجد وصف.").strip()
    status = format_status(item.get("status") or "")
    rating = format_rating(item.get("rating"))

    genres = []
    tags_array = item.get("Tags") or item.get("tags") or []
    for tag in tags_array:
        if isinstance(tag, dict) and tag.get("name"):
            genres.append(tag["name"].strip())

    is_novel = any("رواية" in g for g in genres) or "رواية" in title

    # استخراج خريطة الفصول (بدون لمس روابط الصور)
    chapters_map = {}
    chapters_array = item.get("MangaChapters") or item.get("chapters") or []
    
    for ch in chapters_array:
        if not isinstance(ch, dict): continue
        ch_num = str(ch.get("chapter_number") or ch.get("number") or "").strip()
        if ch_num:
            chapter_url = f"{BASE_URL}/reader/{slug}/{ch_num}"
            chapters_map[chapter_url] = {"name": ch_num}

    return {
        "id": slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": "رواية" if is_novel else format_type(", ".join(genres)),
        "status": status,
        "rating": rating,
        "genres": genres,
        "is_novel": is_novel,
        "chapters": chapters_map
    }

def fetch_mangatek_catalog(session) -> list:
    print("جاري سحب الفهرس العام لموقع مانجا تيك عبر REST API...")
    catalog = []
    page = 1

    while page <= MAX_PAGES_SAFETY:
        url = f"{API_BASE_URL}/manga?page={page}"
        try:
            res = session.get(url, timeout=20)
            if res.status_code != 200:
                break
            
            data = res.json()
            data_array = data.get("data") or data.get("manga") or data.get("items") or []

            if not data_array:
                break

            new_in_page = 0
            for item in data_array:
                if not isinstance(item, dict): continue
                slug = (item.get("slug") or "").strip()
                title = (item.get("title") or item.get("title_ar") or "").strip()

                if not slug or not title:
                    continue

                if not any(entry["id"] == slug for entry in catalog):
                    cover = (item.get("cover_image") or item.get("cover") or "").strip()
                    cover_url = cover if cover.startswith("http") else f"{BASE_URL}{cover}" if cover else ""
                    
                    status = format_status(item.get("status") or "")
                    rating = format_rating(item.get("rating"))
                    is_novel = "رواية" in title

                    catalog.append({
                        "id": slug,
                        "title": title,
                        "url": f"{BASE_URL}/manga/{slug}",
                        "cover_url": cover_url,
                        "type": "رواية" if is_novel else "مانهوا",
                        "status": status,
                        "rating": rating
                    })
                    new_in_page += 1

            print(f"مانجا تيك [صفحة {page}]: تم فهرسة {new_in_page} عمل (المجموع: {len(catalog)})")
            if new_in_page == 0:
                break

            page += 1
            time.sleep(0.15)
        except Exception as e:
            print(f"خطأ أثناء سحب صفحة {page}: {e}")
            break

    print(f"تم الانتهاء من فهرسة {len(catalog)} عمل في MangaTek.")
    return catalog

def sync_mangatek_fast():
    session = get_session()
    catalog = fetch_mangatek_catalog(session)
    top_targets = catalog[:DETAILS_SYNC_LIMIT]

    for index, item in enumerate(top_targets, 1):
        slug = item["id"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")

        try:
            details = scrape_manga_details_mangatek(session, slug)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["total_chapters"] = len(details["chapters"])
            print(f"✓ [{index}/{len(top_targets)}] تم تجهيز مانجا تيك: {slug} ({len(details['chapters'])} فصل)")
            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ أثناء معالجة {slug}: {e}")

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"\n⚡ اكتملت مزامنة مانجا تيك الخاطفة! تم الحفظ في {CATALOG_FILE}")

if __name__ == "__main__":
    sync_mangatek_fast()
