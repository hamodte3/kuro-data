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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ar,en-US;q=0.8,en;q=0.5",
    "Referer": f"{BASE_URL}/",
    "Origin": BASE_URL,
    "x-app-source": "mangatek",
    "Connection": "keep-alive"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ar,en-US;q=0.8,en;q=0.5",
    "Referer": f"{BASE_URL}/",
    "Origin": BASE_URL,
    "x-app-source": "mangatek",
    "Connection": "keep-alive"
}

def get_session():
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def fetch_mangatek_catalog(session) -> list:
    print("جاري سحب الفهرس العام لموقع مانجا تيك عبر REST API...")
    catalog = []
    page = 1

    # تفعيل الجلسة
    try:
        session.get(f"{API_BASE_URL}/session/pulse", timeout=15)
    except Exception as e:
        print(f"تنبيه pulse: {e}")

    while page <= MAX_PAGES_SAFETY:
        url = f"{API_BASE_URL}/manga?page={page}"
        try:
            res = session.get(url, timeout=20)
            if res.status_code != 200:
                print(f"مانجا تيك توقف عند صفحة {page} بكود استجابة: {res.status_code} | النص: {res.text[:100]}")
                break
            
            data = res.json()
            data_array = data.get("data") or data.get("manga") or data.get("items") or []

            if not data_array:
                print(f"لا توجد أعمال إضافية في صفحة {page}.")
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
            time.sleep(0.2)
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
