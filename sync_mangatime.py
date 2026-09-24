import json
import os
import re
import time
import urllib.parse
from curl_cffi import requests

BASE_URL = "https://mangatime.org"
# 🎯 المسار المباشر الصحيح بدون /api لتفادي أخطاء الـ 404
API_URL = f"{BASE_URL}/trpc"
DATA_DIR = os.path.join("data", "mangatime")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

MAX_PAGES_SAFETY = 70      # تغطية كافة صفحات الفهرس

os.makedirs(DATA_DIR, exist_ok=True)

# 🎯 محاكاة ترويسات التطبيق الأصلية لتخطي حماية وكلاودفلير الويب
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

def fetch_trpc(session, procedure: str, input_data: dict):
    """إرسال طلب tRPC نظيف وفك العقدة المباشرة"""
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
    """استخراج مصفوفة العناصر بمرونة من استجابات tRPC المختلفة"""
    if isinstance(json_node, list):
        return json_node
    if isinstance(json_node, dict):
        for k in ["results", "items", "works", "series", "data"]:
            if k in json_node and isinstance(json_node[k], list):
                return json_node[k]
    return []

def get_type_url_path(raw_type: str) -> str:
    """تحديد المسار الفعلي للعمل (manga / manhwa / novel)"""
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

    # 1. طلب بيانات العمل الأساسية
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

    genres = [g.get("name") for g in series_data.get("genres", []) if isinstance(g, dict) and g.get("name")]

    # 2. جلب الفصول
    chapters_map = {}
    number_regex = re.compile(r"\d+(\.\d+)?")

    # فحص إذا كانت الفصول معادة مباشرة داخل كائن السلسلة
    direct_chapters = series_data.get("chapters")
    chapters_array = direct_chapters if isinstance(direct_chapters, list) else []

    # إذا لم تكن موجودة، نطلبها عبر content.getChapters
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

    # استخراج الفصول وبناء الروابط الصحيحة
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

    # 3. خطة طوارئ بديلة: توليد الفصول تسلسلياً إذا كان العمل مقفولاً في الـ API
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

def fetch_mangatime_catalog(session) -> list:
    print("🚀 جاري سحب الفهرس العام لموقع مانغاتايم (بمعدل 48 عملاً بالطلب)...")
    catalog = []
    page = 1

    while page <= MAX_PAGES_SAFETY:
        # 🎯 استخدام limit: 48 لتسريع الفهرسة للضعف
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
                    "limit": 48,
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
                type_path = get_type_url_path(raw_type)

                stats = item.get("stats") or {}
                raw_rating = stats.get("rating") or item.get("rating") or ""

                catalog.append({
                    "id": slug,
                    "title": title,
                    "url": f"{BASE_URL}/{type_path}/{slug}",
                    "cover_url": cover,
                    "type": "رواية" if is_novel else format_type(raw_type),
                    "status": format_status(item.get("status", "")),
                    "rating": format_rating(raw_rating)
                })
                new_in_page += 1

        print(f"مانغاتايم [صفحة {page}]: +{new_in_page} عمل جديد | الإجمالي: {len(catalog)}")
        if new_in_page == 0:
            break

        page += 1
        time.sleep(0.1)

    print(f"✨ تم الانتهاء من فهرسة {len(catalog)} عملاً.")
    return catalog

def sync_mangatime_all():
    session = get_session()
    catalog = fetch_mangatime_catalog(session)
    total_items = len(catalog)

    print(f"\n⚡ بدء معالجة وتوليد الفصول لجميع الأعمال ({total_items} عمل)...")

    for index, item in enumerate(catalog, 1):
        slug = item["id"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")

        # ⚡ كاش ذكي: العمل الجاهز محلياً وفيه فصول لا نكرر طلبه من الشبكة
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    if cached_data.get("chapters") and len(cached_data["chapters"]) > 0:
                        item["status"] = cached_data["status"]
                        item["rating"] = cached_data["rating"]
                        item["total_chapters"] = len(cached_data["chapters"])
                        print(f"⚡ [{index}/{total_items}] من الكاش: {slug} ({item['total_chapters']} فصل)")
                        continue
            except Exception:
                pass

        try:
            details = scrape_manga_details_mangatime(session, item)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["total_chapters"] = len(details["chapters"])
            print(f"✓ [{index}/{total_items}] تم الجلب بنجاح: {slug} ({len(details['chapters'])} فصل)")
            time.sleep(0.15)
        except Exception as e:
            print(f"❌ خطأ أثناء معالجة {slug}: {e}")

    # حفظ الفهرس الشامل بعد تحديث الفصول
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 اكتملت مزامنة مانغاتايم بالكامل! تم حفظ الفهرس في {CATALOG_FILE}")

if __name__ == "__main__":
    sync_mangatime_all()
