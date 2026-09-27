import json
import os
import time

try:
    from curl_cffi import requests
except ImportError:
    import requests

API_BASE = "http://62.171.141.197:5007"
WEB_BASE = "https://realmnovel.com"
DATA_DIR = os.path.join("data", "realmnovel")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

DETAILS_SYNC_LIMIT = 20000   # تجهيز وتحديث ملفات فصول أحدث 20 رواية فقط
MAX_DELTA_PAGES = 5000       # فحص أول 5 صفحات فقط كل ساعة (100 رواية محدثة)

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

HEADERS = {
    "user-agent": "Dart/3.9 (dart:io)",
    "content-type": "application/json",
    "x-app-version": "10",
    "accept-encoding": "gzip"
}

def get_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s

def clean_genres(raw_genres) -> list:
    """استخراج التصنيفات الحقيقية بمرونة وتنظيفها من أي نصوص فارغة"""
    genres = []
    if not raw_genres or not isinstance(raw_genres, list):
        return genres

    for g in raw_genres:
        if isinstance(g, dict):
            name = g.get("arabic") or g.get("name") or g.get("title") or ""
            if name and name.strip():
                genres.append(name.strip())
        elif isinstance(g, str) and g.strip():
            genres.append(g.strip())

    return list(dict.fromkeys(genres))  # إزالة أي تكرار مع الحفاظ على الترتيب

def load_existing_catalog() -> dict:
    """تحميل الأرشيف القديم لمنع مسح أو تصفير أي رواية سابقة"""
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["id"]: item for item in data if "id" in item}
    except Exception as e:
        print(f"⚠️ تعذر قراءة الكتالوج القديم: {e}")
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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لعالم الروايات في {GLOBAL_NEW_FILE}")

def fetch_novel_api_details(session, novel_id: str) -> dict:
    url = f"{API_BASE}/novels/{novel_id}"
    try:
        res = session.get(url, timeout=15)
        if res.status_code == 200:
            body = res.json()
            return body.get("data", {})
    except Exception:
        pass
    return {}

def sync_realmnovel():
    session = get_session()
    print(f"🚀 بدء المزامنة الخاطفة لعالم الروايات (فحص أول {MAX_DELTA_PAGES} صفحات)...")

    catalog_dict = load_existing_catalog()
    ordered_recent_slugs = []

    # 1. سحب أحدث 5 صفحات فقط من الـ API
    for page in range(1, MAX_DELTA_PAGES + 1):
        url = f"{API_BASE}/novels/latest?page={page}&limit=20"
        try:
            res = session.get(url, timeout=15)
            if res.status_code != 200:
                print(f"توقف الفهرس عند صفحة {page}: رمز {res.status_code}")
                break

            payload = res.json()
            data = payload.get("data", [])
            if not data:
                break

            new_in_page = 0
            for item in data:
                nid = item.get("_id")
                if not nid:
                    continue

                title = item.get("title") or item.get("titleEn") or nid
                cover = f"{WEB_BASE}/img/novel/{nid}.jpg"
                web_url = f"{WEB_BASE}/novel/{nid}"

                # استخراج عدد الفصول والتصنيفات من الفهرس السريع إن توفرت
                api_chaps = int(item.get("chaptersCount") or item.get("totalChapters") or item.get("chapters") or 0)
                quick_genres = clean_genres(item.get("genres") or item.get("genre") or item.get("tags"))

                # التحديث الآمن مع الحفاظ على الأرشيف القديم
                if nid in catalog_dict:
                    catalog_dict[nid]["title"] = title
                    catalog_dict[nid]["url"] = web_url
                    catalog_dict[nid]["cover_url"] = cover
                    catalog_dict[nid]["status"] = item.get("status", catalog_dict[nid].get("status", "مستمرة"))
                    catalog_dict[nid]["rating"] = str(item.get("rating") or catalog_dict[nid].get("rating", ""))
                    if api_chaps > 0:
                        catalog_dict[nid]["total_chapters"] = api_chaps
                    if quick_genres and not catalog_dict[nid].get("genres"):
                        catalog_dict[nid]["genres"] = quick_genres
                else:
                    catalog_dict[nid] = {
                        "id": nid,
                        "title": title,
                        "url": web_url,
                        "cover_url": cover,
                        "type": "رواية",
                        "status": item.get("status", "مستمرة"),
                        "rating": str(item.get("rating", "")),
                        "total_chapters": api_chaps,
                        "genres": quick_genres
                    }

                if nid not in ordered_recent_slugs:
                    ordered_recent_slugs.append(nid)

                new_in_page += 1

            print(f"الـ API [صفحة {page}]: رصد {new_in_page} رواية محدثة.")
            time.sleep(0.15)
        except Exception as e:
            print(f"خطأ أثناء جلب الفهرس: {e}")
            break

    # 2. فحص وتوليد الفصول وتحديث التصنيفات لأحدث 20 رواية
    targets_slugs = ordered_recent_slugs[:DETAILS_SYNC_LIMIT]
    new_releases = []

    print(f"\n⚡ تحديث ملفات الفصول لأحدث {len(targets_slugs)} رواية...")

    for idx, nid in enumerate(targets_slugs, 1):
        item = catalog_dict[nid]
        file_path = os.path.join(DATA_DIR, f"{nid}.json")

        existing_chapters_count = 0
        existing_data = {}

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    existing_chapters_count = len(existing_data.get("chapters", {}))
            except Exception:
                pass

        try:
            details = fetch_novel_api_details(session, nid)
            total_chapters = int(
                details.get("chaptersCount") or 
                details.get("totalChapters") or 
                details.get("chapters") or 
                item.get("total_chapters") or 
                existing_chapters_count
            )

            # استخراج التصنيفات الحقيقية حصراً من التفاصيل بدون أي فولباك وهمي
            real_genres = clean_genres(
                details.get("genres") or 
                details.get("genre") or 
                details.get("tags") or 
                existing_data.get("genres") or 
                item.get("genres")
            )

            prev_chaps = existing_chapters_count or item.get("total_chapters", 0)
            item["total_chapters"] = total_chapters
            item["genres"] = real_genres  # حفظ التصنيفات الحقيقية في الكاتلوج

            # كشف التحديث لتوليد التنبيه
            if total_chapters > prev_chaps and total_chapters > 0:
                new_releases.append({
                    "id": nid,
                    "title": item["title"],
                    "chapter": f"الفصل {total_chapters}" if prev_chaps > 0 else "رواية جديدة",
                    "type": "رواية",
                    "cover_url": item["cover_url"]
                })

            # توليد خريطة الفصول إذا وُجدت فصول جديدة
            if existing_chapters_count != total_chapters or not os.path.exists(file_path):
                chapters_map = existing_data.get("chapters", {})
                for ch in range(1, total_chapters + 1):
                    ch_url = f"{WEB_BASE}/novel/{nid}/chapter/{ch}"
                    if ch_url not in chapters_map:
                        chapters_map[ch_url] = {
                            "name": str(ch),
                            "images": []
                        }

                novel_payload = {
                    "id": nid,
                    "title": details.get("title") or item["title"],
                    "cover_url": item["cover_url"],
                    "description": details.get("description") or existing_data.get("description", "لا يوجد وصف"),
                    "type": "رواية",
                    "status": details.get("status") or item["status"],
                    "last_update": "",
                    "rating": str(details.get("rating") or item["rating"]),
                    "favorites": "",
                    "genres": real_genres,
                    "is_novel": True,
                    "chapters": chapters_map
                }

                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(novel_payload, f, ensure_ascii=False, indent=2)

                print(f"✓ [{idx}/{len(targets_slugs)}] تم التحديث: {nid} ({len(chapters_map)} فصل) - تصنيفات: {real_genres}")
            else:
                print(f"⚡ [{idx}/{len(targets_slugs)}] متطابق مسبقاً: {nid} ({total_chapters} فصل)")

            time.sleep(0.15)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {nid}: {e}")

    # 3. حفظ الفهرس التراكمي الشامل (الأحدث أولاً + بقية الأرشيف)
    seen_slugs = set(ordered_recent_slugs)
    final_merged_catalog = [catalog_dict[s] for s in ordered_recent_slugs] + [
        item for s, item in catalog_dict.items() if s not in seen_slugs
    ]

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    # 4. تحديث ملف الإشعارات العام
    if new_releases:
        update_global_new_releases(new_releases)

    print(f"\n💾 تم حفظ الكتالوج المدمج بنجاح: {len(final_merged_catalog)} رواية (حقيقية 100%).")
    print("⚡ اكتملت مزامنة عالم الروايات الذكية بنجاح تام!")

if __name__ == "__main__":
    sync_realmnovel()
