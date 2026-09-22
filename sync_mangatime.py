import json
import os
import re
import time
import urllib.parse
from curl_cffi import requests

BASE_URL = "https://mangatime.org"
API_URL = f"{BASE_URL}/api/trpc"
DATA_DIR = os.path.join("data", "mangatime")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

DETAILS_SYNC_LIMIT = 20   # تجهيز بيانات وفصول أفضل 20 عملاً
MAX_PAGES_SAFETY = 70     # عدد صفحات الفهرس لتغطية مكتبة مانغاتايم

os.makedirs(DATA_DIR, exist_ok=True)

# 🎯 محاكاة ترويسات تطبيق الهاتف لتخطي قفل الويب
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "X-MT-Platform": "app",                  # 🎯 هذا المفتاح يخبر السيرفر بتجاوز فحص إعلانات الويب
    "Origin": "https://localhost",            # 🎯 محاكاة بيئة Capacitor الأصلية
    "Referer": "https://localhost/",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site"
}

def get_session():
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def fetch_trpc(session, procedure: str, input_data: dict):
    """إرسال طلب tRPC نظيف وفك الاستجابة المباشرة"""
    input_encoded = urllib.parse.quote(json.dumps(input_data))
    url = f"{API_URL}/{procedure}?batch=1&input={input_encoded}"
    try:
        res = session.get(url, timeout=20)
        data = res.json()
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        return data.get("result", {}).get("data", {}).get("json", {})
    except Exception as e:
        print(f"خطأ أثناء طلب {procedure}: {e}")
        return None

def extract_items_list(json_node) -> list:
    """استخراج مصفوفة العناصر بمرونة من كائن الـ tRPC"""
    if isinstance(json_node, list):
        return json_node
    if isinstance(json_node, dict):
        for k in ["results", "items", "works", "series", "data"]:
            if k in json_node and isinstance(json_node[k], list):
                return json_node[k]
    return []

def format_type(raw_type: str) -> str:
    t = raw_type.strip().lower()
    if "manhwa" in t: return "مانهوا"
    if "manhua" in t: return "مانها"
    if "manga" in t: return "مانغا"
    if "novel" in t: return "رواية"
    if "webtoon" in t: return "ويب تون"
    if "comic" in t: return "كوميك"
    return raw_type if raw_type else "مانغا"

def format_status(raw_status: str) -> str:
    s = raw_status.strip().lower()
    if s in ["ongoing", "on-going"]: return "مستمر"
    if s == "completed": return "مكتمل"
    if s == "hiatus": return "متوقف مؤقتاً"
    return raw_status if raw_status else "مستمر"

def format_rating(raw_rating) -> str:
    try:
        val = float(raw_rating)
        return f"{val:.1f}" if val > 0 else ""
    except (ValueError, TypeError):
        return ""

def format_last_update(raw_date: str) -> str:
    if not raw_date: return ""
    return raw_date.split("T")[0].strip()

def scrape_manga_details_mangatime(session, catalog_item: dict):
    slug = catalog_item["id"]
    info_input = {"0": {"json": {"slug": slug}}}
    series_data = fetch_trpc(session, "content.getSeriesBySlug", info_input) or {}

    title = series_data.get("title") or catalog_item.get("title") or "بدون عنوان"
    cover_url = series_data.get("coverUrl") or series_data.get("cover") or series_data.get("bannerUrl") or catalog_item.get("cover_url") or ""
    if cover_url.startswith("/"):
        cover_url = f"{BASE_URL}{cover_url}"

    description = series_data.get("description") or series_data.get("synopsis") or "لا يوجد وصف"
    raw_type = series_data.get("type") or catalog_item.get("type", "")
    raw_status = series_data.get("status") or catalog_item.get("status", "")
    is_novel = "رواية" in raw_type or "novel" in raw_type.lower() or "رواية" in title

    stats = series_data.get("stats") or {}
    raw_rating = stats.get("rating") or series_data.get("rating") or catalog_item.get("rating") or ""
    favorites = str(stats.get("favorites") or series_data.get("favorites") or "")
    last_update = format_last_update(series_data.get("updatedAt", ""))

    genres = [g.get("name") for g in series_data.get("genres", []) if isinstance(g, dict) and g.get("name")]

    # 🎯 التوليد السحري المباشر: سطر واحد ينهي أزمة الفصول كلها
    total_chapters = int(stats.get("chapterCount") or series_data.get("chapterCount") or 0)
    
    chapters_map = {
        f"{BASE_URL}/manga/{slug}/chapter/{i}": {"name": str(i)}
        for i in range(total_chapters, 0, -1)
    }

    return {
        "id": slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": format_type(raw_type),
        "status": format_status(raw_status),
        "last_update": last_update,
        "rating": format_rating(raw_rating),
        "favorites": favorites,
        "genres": genres,
        "is_novel": is_novel,
        "chapters": chapters_map
    }

def fetch_mangatime_catalog(session) -> list:
    print("جاري سحب الفهرس العام لموقع مانغاتايم عبر tRPC...")
    catalog = []
    page = 1

    while page <= MAX_PAGES_SAFETY:
        input_data = {
            "0": {
                "json": {
                    "filters": {
                        "genres": [],
                        "sortBy": "popularity-desc",
                        "rating": {},
                        "yearRange": {},
                        "chapterCount": {}
                    },
                    "limit": 24,
                    "page": page,
                    "sortBy": "popularity",
                    "sortOrder": "desc"
                }
            }
        }

        json_node = fetch_trpc(session, "search.searchSeries", input_data)
        items = extract_items_list(json_node)

        if not items:
            break

        new_in_page = 0
        for item in items:
            if not isinstance(item, dict): continue
            slug = item.get("slug") or item.get("seriesSlug") or ""
            title = item.get("title") or item.get("name") or ""
            if not slug or not title: continue

            if not any(entry["id"] == slug for entry in catalog):
                cover = item.get("coverUrl") or item.get("cover") or item.get("bannerUrl") or ""
                if cover.startswith("/"):
                    cover = f"{BASE_URL}{cover}"

                raw_type = item.get("type", "")
                is_novel = "رواية" in raw_type or "novel" in raw_type.lower() or "رواية" in title

                stats = item.get("stats") or {}
                raw_rating = stats.get("rating") or item.get("rating") or ""

                catalog.append({
                    "id": slug,
                    "title": title,
                    "url": f"{BASE_URL}/manga/{slug}",
                    "cover_url": cover,
                    "type": "رواية" if is_novel else format_type(raw_type),
                    "status": format_status(item.get("status", "")),
                    "rating": format_rating(raw_rating)
                })
                new_in_page += 1

        print(f"مانغاتايم [صفحة {page}]: تم تسجيل {new_in_page} عمل (المجموع: {len(catalog)})")
        if new_in_page == 0:
            break

        page += 1
        time.sleep(0.1)  # API سريع ومباشر لا يتطلب أي تأخير كبير

    print(f"تم الانتهاء من فهرسة {len(catalog)} عمل في MangaTime.")
    return catalog

def sync_mangatime_fast():
    session = get_session()
    catalog = fetch_mangatime_catalog(session)
    top_targets = catalog[:DETAILS_SYNC_LIMIT]

    for index, item in enumerate(top_targets, 1):
        slug = item["id"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")

        try:
            details = scrape_manga_details_mangatime(session, slug)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["total_chapters"] = len(details["chapters"])
            print(f"✓ [{index}/{len(top_targets)}] تم تجهيز مانغاتايم: {slug} ({len(details['chapters'])} فصل)")
            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ أثناء معالجة {slug}: {e}")

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"\n⚡ اكتملت مزامنة مانغاتايم الخاطفة! تم الحفظ في {CATALOG_FILE}")

if __name__ == "__main__":
    sync_mangatime_fast()
