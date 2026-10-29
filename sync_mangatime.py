import json
import os
import re
import time
import urllib.parse
from curl_cffi import requests

BASE_URL = "https://mangatime.org"
API_URL = f"{BASE_URL}/trpc"
DATA_DIR = os.path.join("data", "mangatime")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

DETAILS_SYNC_LIMIT = 20    # فحص وتجهيز فصول أحدث 20 عملاً تم تحديثها
MAX_DELTA_PAGES = 5        # فحص أول 3 صفحات فقط كل ساعة (تغطي حتى 144 عملاً محدثاً)

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "X-MT-Platform": "app",
    "Origin": "https://localhost",
    "Referer": "https://localhost/",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site"
}

def get_session():
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def load_existing_catalog() -> dict:
    """تحميل الأرشيف التراكمي لمنع مسح أي عمل سابق عند تقليل الصفحات"""
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["id"]: item for item in data if "id" in item}
    except Exception as e:
        print(f"خطأ أثناء قراءة كاتلوج مانغاتايم القديم: {e}")
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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لمانغاتايم في {GLOBAL_NEW_FILE}")

def fetch_trpc(session, procedure: str, input_data: dict):
    """إرسال استعلام tRPC رسمي واستخراج البيانات النظيفة"""
    input_encoded = urllib.parse.quote(json.dumps(input_data))
    url = f"{API_URL}/{procedure}?batch=1&input={input_encoded}"
    try:
        res = session.get(url, timeout=20)
        data = res.json()
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        result = data.get("result", {}).get("data", {})
        return result.get("json", result)
    except Exception as e:
        print(f"❌ خطأ أثناء طلب {procedure}: {e}")
        return None

def extract_items_list(json_node) -> list:
    if isinstance(json_node, list):
        return json_node
    if isinstance(json_node, dict):
        for k in ["results", "items", "works", "series", "data"]:
            if k in json_node and isinstance(json_node[k], list):
                return json_node[k]
    return []

def get_type_url_path(raw_type: str) -> str:
    t = (raw_type or "").strip().lower()
    if "manhwa" in t: return "manhwa"
    if "novel" in t or "رواية" in t: return "novel"
    if "manhua" in t: return "manhua"
    return "manga"

def format_type(raw_type: str) -> str:
    t = (raw_type or "").strip().lower()
    if "manhwa" in t: return "مانهوا"
    if "manhua" in t: return "مانها"
    if "manga" in t: return "مانغا"
    if "novel" in t: return "رواية"
    if "webtoon" in t: return "ويب تون"
    if "comic" in t: return "كوميك"
    return raw_type if raw_type else "مانغا"

def format_status(raw_status: str) -> str:
    s = (raw_status or "").strip().lower()
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

def scrape_manga_details_mangatime(session, target):
    if isinstance(target, dict):
        slug = target.get("id", "")
        catalog_title = target.get("title", "")
        catalog_cover = target.get("cover_url", "")
        catalog_type = target.get("type", "")
        catalog_status = target.get("status", "")
        catalog_rating = target.get("rating", "")
    else:
        slug = str(target)
        catalog_title = catalog_cover = catalog_type = catalog_status = catalog_rating = ""

    # 1. طلب تفاصيل العمل
    info_input = {"0": {"json": {"slug": slug}}}
    series_data = fetch_trpc(session, "content.getSeriesBySlug", info_input) or {}

    title = series_data.get("title") or catalog_title or "بدون عنوان"
    cover_url = series_data.get("coverUrl") or series_data.get("cover") or series_data.get("bannerUrl") or catalog_cover or ""
    if cover_url.startswith("/"):
        cover_url = f"{BASE_URL}{cover_url}"

    description = series_data.get("description") or series_data.get("synopsis") or "لا يوجد وصف"
    raw_type = series_data.get("type") or catalog_type or "manga"
    type_path = get_type_url_path(raw_type)
    raw_status = series_data.get("status") or catalog_status
    is_novel = "رواية" in raw_type or "novel" in raw_type.lower() or "رواية" in title

    stats = series_data.get("stats") or {}
    raw_rating = stats.get("rating") or series_data.get("rating") or catalog_rating or ""
    favorites = str(stats.get("favorites") or series_data.get("favorites") or "")
    last_update = format_last_update(series_data.get("updatedAt", ""))

    # استخراج التصنيفات الحقيقية بمرونة
    genres = []
    for g in series_data.get("genres", []):
        if isinstance(g, dict) and g.get("name"):
            genres.append(g["name"].strip())
        elif isinstance(g, str) and g.strip():
            genres.append(g.strip())

    # 2. جلب قائمة الفصول
    chapters_map = {}
    number_regex = re.compile(r"\d+(\.\d+)?")

    direct_chapters = series_data.get("chapters")
    chapters_array = direct_chapters if isinstance(direct_chapters, list) else []

    if not chapters_array:
        page = 1
        while True:
            chapters_input = {
                "0": {
                    "json": {
                        "seriesSlug": slug,
                        "limit": 100,
                        "page": page,
                        "sortBy": "number-desc"
                    }
                }
            }
            ch_data = fetch_trpc(session, "content.getChapters", chapters_input)
            batch = extract_items_list(ch_data)
            if not batch:
                break
            chapters_array.extend(batch)
            if len(batch) < 100:
                break
            page += 1

    if chapters_array:
        for ch in chapters_array:
            if not isinstance(ch, dict): continue
            ch_num = str(ch.get("number", "")).strip()
            ch_title = str(ch.get("title", "") or "").strip()

            raw_name = f"{ch_num}: {ch_title}" if ch_title and ch_title != "null" else ch_num
            match = number_regex.search(raw_name)
            if match:
                val = float(match.group(0))
                clean_name = str(int(val)) if val.is_integer() else str(val)
            else:
                clean_name = ch_num if ch_num else "0"

            full_chapter_url = f"{BASE_URL}/{type_path}/{slug}/chapter/{clean_name}"
            chapters_map[full_chapter_url] = {
                "name": clean_name,
                "images": []
            }

    # 3. خطة طوارئ: توليد تسلسلي إن لم تتوفر مصفوفة الفصول
    if not chapters_map:
        total = stats.get("chapterCount") or series_data.get("chapterCount") or 0
        try:
            total_int = int(total)
            if total_int > 0:
                for i in range(total_int, 0, -1):
                    chapters_map[f"{BASE_URL}/{type_path}/{slug}/chapter/{i}"] = {
                        "name": str(i),
                        "images": []
                    }
        except Exception:
            pass

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

def sync_mangatime_fast():
    session = get_session()
    print(f"🚀 بدء المزامنة الخاطفة لمانغاتايم (فحص أحدث التحديثات عبر tRPC)...")

    catalog_dict = load_existing_catalog()
    ordered_recent_slugs = []

    # 1. فحص أحدث التحديثات باستخدام الإجراء الرسمي
    for page in range(1, MAX_DELTA_PAGES + 1):
        input_data = {
            "0": {
                "json": {
                    "page": page,
                    "limit": 48
                }
            }
        }
        json_node = fetch_trpc(session, "homepage.getLatestReleases", input_data)
        items = extract_items_list(json_node)

        # احتياطي: إذا لم يستجب، نطلب البحث بفرز تاريخ التحديث
        if not items:
            input_data_backup = {
                "0": {
                    "json": {
                        "filters": {"genres": [], "sortBy": "updatedAt-desc", "rating": {}, "yearRange": {}, "chapterCount": {}},
                        "limit": 48,
                        "page": page,
                        "sortBy": "updatedAt",
                        "sortOrder": "desc"
                    }
                }
            }
            json_node = fetch_trpc(session, "search.searchSeries", input_data_backup)
            items = extract_items_list(json_node)

        if not items:
            break

        new_in_page = 0
        for item in items:
            if not isinstance(item, dict): continue
            slug = item.get("seriesSlug") or item.get("slug") or ""
            title = item.get("seriesTitle") or item.get("title") or item.get("name") or ""
            if not slug or not title: continue

            cover = item.get("coverUrl") or item.get("cover") or item.get("bannerUrl") or ""
            if cover.startswith("/"):
                cover = f"{BASE_URL}{cover}"

            raw_type = item.get("type", "")
            is_novel = "رواية" in raw_type or "novel" in raw_type.lower() or "رواية" in title
            type_path = get_type_url_path(raw_type)

            stats = item.get("stats") or {}
            raw_rating = stats.get("rating") or item.get("rating") or ""

            # استخراج التصنيفات السريعة المتاحة في كائن الفهرس مباشرة
            quick_genres = []
            for g in item.get("genres", []):
                if isinstance(g, dict) and g.get("name"):
                    quick_genres.append(g["name"].strip())
                elif isinstance(g, str) and g.strip():
                    quick_genres.append(g.strip())

            # التحديث الآمن مع الحفاظ على الأرشيف القديم
            if slug in catalog_dict:
                catalog_dict[slug]["title"] = title
                catalog_dict[slug]["url"] = f"{BASE_URL}/{type_path}/{slug}"
                if cover:
                    catalog_dict[slug]["cover_url"] = cover
                if raw_rating:
                    catalog_dict[slug]["rating"] = format_rating(raw_rating)
                if quick_genres and not catalog_dict[slug].get("genres"):
                    catalog_dict[slug]["genres"] = quick_genres
            else:
                catalog_dict[slug] = {
                    "id": slug,
                    "title": title,
                    "url": f"{BASE_URL}/{type_path}/{slug}",
                    "cover_url": cover,
                    "type": "رواية" if is_novel else format_type(raw_type),
                    "status": format_status(item.get("status", "")),
                    "rating": format_rating(raw_rating),
                    "total_chapters": 0,
                    "genres": quick_genres
                }

            if slug not in ordered_recent_slugs:
                ordered_recent_slugs.append(slug)

            new_in_page += 1

        print(f"مانغاتايم [صفحة {page}]: رصد {new_in_page} عمل محدث")
        if new_in_page == 0:
            break

        time.sleep(0.15)

    # 2. تحديث تفاصيل وفصول أحدث 20 عملاً
    targets_slugs = ordered_recent_slugs[:DETAILS_SYNC_LIMIT]
    new_releases = []

    for index, slug in enumerate(targets_slugs, 1):
        item = catalog_dict[slug]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        prev_chaps = item.get("total_chapters", 0)

        try:
            details = scrape_manga_details_mangatime(session, item)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            current_chaps = len(details["chapters"])
            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["type"] = details["type"]
            item["total_chapters"] = current_chaps
            
            # حفظ التصنيفات الحقيقية في الكاتلوج
            item["genres"] = details.get("genres", [])

            print(f"✓ [{index}/{len(targets_slugs)}] تم تحديث مانغاتايم: {details['title']} ({current_chaps} فصل) - تصنيفات: {item['genres']}")

            # إشعار دقيق لجديد الفصول
            if current_chaps > prev_chaps and current_chaps > 0:
                new_releases.append({
                    "id": slug,
                    "title": item["title"],
                    "chapter": f"الفصل {current_chaps}" if prev_chaps > 0 else "عمل جديد",
                    "type": item.get("type", "مانغا"),
                    "cover_url": item.get("cover_url", "")
                })

            time.sleep(0.15)
        except Exception as e:
            print(f"❌ خطأ أثناء معالجة {slug}: {e}")

    # 3. حفظ الفهرس الشامل (الأحدث في المقدمة ثم بقية الأرشيف)
    seen_slugs = set(ordered_recent_slugs)
    final_merged_catalog = [catalog_dict[s] for s in ordered_recent_slugs] + [
        item for s, item in catalog_dict.items() if s not in seen_slugs
    ]

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    # 4. تحديث الإشعارات العامة
    if new_releases:
        update_global_new_releases(new_releases)

    print(f"\n⚡ اكتملت مزامنة مانغاتايم الذكية! إجمالي الأعمال المحفوظة: {len(final_merged_catalog)}")

if __name__ == "__main__":
    sync_mangatime_fast()
