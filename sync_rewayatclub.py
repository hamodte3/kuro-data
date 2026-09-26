import json
import os
import time

try:
    from curl_cffi import requests
except ImportError:
    import requests

BASE_WEB = "https://rewayat.club"
API_BASE = "https://api.rewayat.club/api"
DATA_DIR = os.path.join("data", "rewayatclub")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

DETAILS_SYNC_LIMIT = 20    # فحص وتجهيز فصول أحدث 20 رواية تم تحديثها
MAX_DELTA_PAGES = 5        # فحص أول 5 صفحات فقط كل ساعة لمراقبة الجديد

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://rewayat.club/",
    "Origin": "https://rewayat.club"
}

def get_session():
    return requests.Session(impersonate="chrome124", headers=HEADERS)

def load_existing_catalog() -> dict:
    """تحميل الأرشيف القديم لمنع مسح أي رواية سابقة عند تقليل الصفحات"""
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["id"]: item for item in data if "id" in item}
    except Exception as e:
        print(f"⚠️ خطأ أثناء قراءة الكتالوج القديم: {e}")
        return {}

def update_global_new_releases(new_releases: list):
    """دمج الإشعارات الجديدة في data/new.json دون مسح تحديثات المصادر الأخرى"""
    if not new_releases:
        return

    existing_releases = []
    if os.path.exists(GLOBAL_NEW_FILE):
        try:
            with open(GLOBAL_NEW_FILE, "r", encoding="utf-8") as f:
                existing_releases = json.load(f)
        except Exception:
            existing_releases = []

    combined = new_releases + existing_releases
    seen = set()
    deduped = []
    for item in combined:
        key = (item.get("id"), item.get("chapter"))
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    with open(GLOBAL_NEW_FILE, "w", encoding="utf-8") as f:
        json.dump(deduped[:15], f, ensure_ascii=False, indent=2)
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لنادي الروايات في {GLOBAL_NEW_FILE}")

def safe_get(session, url: str, max_retries: int = 3, timeout: int = 25):
    for attempt in range(1, max_retries + 1):
        try:
            res = session.get(url, timeout=timeout)
            if res.status_code == 200:
                return res
            elif res.status_code in [429, 502, 503, 504]:
                time.sleep(attempt * 2)
        except Exception as e:
            if attempt == max_retries:
                print(f"⚠️ تعذر جلب {url} بعد {max_retries} محاولات: {e}")
                return None
            time.sleep(attempt * 2)
    return None

def normalize_cover(raw_cover: str) -> str:
    if not raw_cover: return ""
    c = raw_cover.strip()
    if c.startswith("//"): return f"https:{c}"
    if c.startswith("/media/"): return f"https://api.rewayat.club{c}"
    if not c.startswith("http"): return f"{BASE_WEB}/{c}"
    return c

def sync_rewayatclub():
    session = get_session()
    print(f"🚀 بدء المزامنة الخاطفة لنادي الروايات (فحص أول {MAX_DELTA_PAGES} صفحات مرتبة بآخر التحديثات)...")

    catalog_dict = load_existing_catalog()
    ordered_recent_slugs = []
    page = 1
    consecutive_empty = 0

    # 1. سحب الصفحات الأولى مع الترتيب بالأحدث (-updated_at)
    while page <= MAX_DELTA_PAGES:
        url = f"{API_BASE}/novels/?ordering=-updated_at&page={page}"
        res = safe_get(session, url, timeout=20)
        
        if not res:
            print(f"⚠️ تخطي صفحة {page} بسبب انقطاع الاستجابة...")
            page += 1
            consecutive_empty += 1
            if consecutive_empty >= 3:
                break
            continue

        consecutive_empty = 0
        try:
            payload = res.json()
        except Exception:
            break

        results = payload.get("results") or payload.get("novels") or []
        if not results:
            break

        new_in_page = 0
        for novel in results:
            slug = novel.get("slug")
            if not slug:
                continue

            title = novel.get("arabic") or novel.get("english") or slug
            cover = normalize_cover(novel.get("poster_url") or novel.get("poster") or "")
            genres = [g.get("arabic") for g in novel.get("genre", []) if isinstance(g, dict) and g.get("arabic")]
            total_chapters = novel.get("num_chapters", 0)

            # التحديث الآمن مع الحفاظ على الأرشيف القديم
            if slug in catalog_dict:
                catalog_dict[slug]["title"] = title
                catalog_dict[slug]["url"] = f"{BASE_WEB}/novel/{slug}"
                if cover:
                    catalog_dict[slug]["cover_url"] = cover
                if total_chapters > 0:
                    catalog_dict[slug]["total_chapters"] = total_chapters
            else:
                catalog_dict[slug] = {
                    "id": slug,
                    "title": title,
                    "url": f"{BASE_WEB}/novel/{slug}",
                    "cover_url": cover,
                    "type": "رواية",
                    "status": "مكتملة" if novel.get("complete") else "مستمر",
                    "rating": "",
                    "total_chapters": total_chapters,
                    "genres": genres if genres else ["فنون قتال", "زراعة"]
                }

            if slug not in ordered_recent_slugs:
                ordered_recent_slugs.append(slug)

            new_in_page += 1

        print(f"نادي الروايات [صفحة {page}]: تم رصد {new_in_page} رواية محدثة.")
        page += 1
        time.sleep(0.15)

    # 2. فحص وتحديث فصول أحدث 20 رواية فقط
    targets_slugs = ordered_recent_slugs[:DETAILS_SYNC_LIMIT]
    new_releases = []

    print(f"\n⚡ تحديث ملفات الفصول لأحدث {len(targets_slugs)} رواية...")

    for idx, slug in enumerate(targets_slugs, 1):
        item = catalog_dict[slug]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        target_total_ch = item.get("total_chapters", 0)

        existing_chapters_count = 0
        existing_data = {}

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    existing_chapters_count = len(existing_data.get("chapters", {}))
            except Exception:
                pass

        prev_chaps = existing_chapters_count or item.get("total_chapters", 0)

        # التقاط إشعارات الروايات المحدثة
        if target_total_ch > prev_chaps and target_total_ch > 0:
            new_releases.append({
                "id": slug,
                "title": item["title"],
                "chapter": f"الفصل {target_total_ch}" if prev_chaps > 0 else "رواية جديدة",
                "type": "رواية",
                "cover_url": item["cover_url"]
            })

        # كاش ذكي: تخطي إذا كانت الفصول متطابقة
        if existing_chapters_count >= target_total_ch and target_total_ch > 0:
            print(f"⚡ [{idx}/{len(targets_slugs)}] متطابق مسبقاً: {slug} ({target_total_ch} فصل)")
            continue

        chapters_map = existing_data.get("chapters", {})
        if target_total_ch > 0:
            for c in range(1, target_total_ch + 1):
                ch_url = f"{BASE_WEB}/novel/{slug}/{c}"
                if ch_url not in chapters_map:
                    chapters_map[ch_url] = {
                        "name": str(c),
                        "images": []
                    }

        payload = {
            "id": slug,
            "title": item["title"],
            "cover_url": item["cover_url"],
            "description": existing_data.get("description", "لا يوجد وصف"),
            "type": "رواية",
            "status": item["status"],
            "last_update": "",
            "rating": "",
            "favorites": "",
            "genres": item.get("genres", []),
            "is_novel": True,
            "chapters": chapters_map
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        print(f"✓ [{idx}/{len(targets_slugs)}] تم تحديث الفصول: {item['title']} ({len(chapters_map)} فصل)")

    # 3. حفظ الكتالوج المدمج (الأحدث أولاً + بقية الأرشيف القديم)
    seen_slugs = set(ordered_recent_slugs)
    final_merged_catalog = [catalog_dict[s] for s in ordered_recent_slugs] + [
        item for s, item in catalog_dict.items() if s not in seen_slugs
    ]

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    print(f"\n💾 تم حفظ الكتالوج المدمج: {len(final_merged_catalog)} رواية.")

    # 4. تحديث الإشعارات المشتركة
    if new_releases:
        update_global_new_releases(new_releases)

    print("🎉 اكتملت مزامنة نادي الروايات الذكية بنجاح تام!")

if __name__ == "__main__":
    sync_rewayatclub()
