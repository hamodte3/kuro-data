import json
import os
import time
import subprocess

try:
    import requests
except ImportError:
    from curl_cffi import requests

API_BASE = "http://62.171.141.197:5007"
WEB_BASE = "https://realmnovel.com"
DATA_DIR = os.path.join("data", "realmnovel")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

# فحص أول 10 صفحات لجلب أحدث التحديثات الدورية
MAX_PAGES = 10 

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
        json.dump(deduped[:10], f, ensure_ascii=False, indent=2)
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
    print(f"🚀 بدء المزامنة التراكمية لعالم الروايات (فحص أول {MAX_PAGES} صفحات)...")

    # 1. تحميل الكتالوج القديم كأرشيف للمقارنة والدمج
    old_catalog = []
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE, "r", encoding="utf-8") as f:
                old_catalog = json.load(f)
        except Exception as e:
            print(f"⚠️ تعذر قراءة الكتالوج القديم: {e}")
            old_catalog = []

    old_map = {item["id"]: item for item in old_catalog if "id" in item}
    print(f"📂 تم تحميل {len(old_map)} رواية محفوظة مسبقاً في الأرشيف.")

    freshly_scraped = []
    seen_fresh_ids = set()

    # 2. سحب أحدث الروايات من الـ API
    for page in range(1, MAX_PAGES + 1):
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

            for item in data:
                nid = item.get("_id")
                if not nid or nid in seen_fresh_ids:
                    continue

                seen_fresh_ids.add(nid)
                title = item.get("title") or item.get("titleEn") or nid
                cover = f"{WEB_BASE}/img/novel/{nid}.jpg"
                web_url = f"{WEB_BASE}/novel/{nid}"

                entry = {
                    "id": nid,
                    "title": title,
                    "url": web_url,
                    "cover_url": cover,
                    "type": "رواية",
                    "status": item.get("status", "مستمرة"),
                    "rating": str(item.get("rating", "")),
                    "total_chapters": old_map.get(nid, {}).get("total_chapters", 0)
                }
                freshly_scraped.append(entry)

            print(f"الـ API [صفحة {page}]: تم فحص البيانات بنجاح.")
            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ أثناء جلب الفهرس: {e}")
            break

    # 3. تحديث ملفات الروايات الفردية وكشف الفصول الجديدة
    print(f"\n⚡ فحص وتحديث فصول {len(freshly_scraped)} رواية من الجولة الحالية...")
    new_releases = []

    for idx, item in enumerate(freshly_scraped, 1):
        nid = item["id"]
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
                existing_chapters_count
            )

            item["total_chapters"] = total_chapters
            prev_chaps = old_map.get(nid, {}).get("total_chapters", 0)

            # التقاط إشعارات الروايات الجديدة والفصول المحدثة
            if nid not in old_map:
                new_releases.append({
                    "id": nid,
                    "title": item["title"],
                    "chapter": f"الفصل {total_chapters}" if total_chapters > 0 else "رواية جديدة",
                    "type": "رواية",
                    "cover_url": item["cover_url"]
                })
            elif total_chapters > prev_chaps and total_chapters > 0:
                new_releases.append({
                    "id": nid,
                    "title": item["title"],
                    "chapter": f"الفصل {total_chapters}",
                    "type": "رواية",
                    "cover_url": item["cover_url"]
                })

            # كاش ذكي: إذا كانت الفصول مطابقة لا نعيد كتابة الملف
            if existing_chapters_count == total_chapters and total_chapters > 0:
                print(f"⚡ [{idx}/{len(freshly_scraped)}] متطابق ومكتمل: {nid} ({total_chapters} فصل)")
                continue

            # توليد خريطة الفصول
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
                "genres": details.get("genres") or existing_data.get("genres", ["فنون قتال", "عالم آخر"]),
                "is_novel": True,
                "chapters": chapters_map
            }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(novel_payload, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(freshly_scraped)}] تم التحديث: {nid} ({len(chapters_map)} فصل)")
            time.sleep(0.15)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {nid}: {e}")

    # ================== 4. الدمج الذكي للكاتلوج ==================
    # أحدث الروايات المصحوبة بتحديثات تتصدر الكاتلوج (Index 0)، والأرشيف القديم يبقى كاملاً في الخلف
    fresh_ids = {x["id"] for x in freshly_scraped}
    remaining_old = [x for x in old_catalog if x.get("id") not in fresh_ids]
    final_merged_catalog = freshly_scraped + remaining_old

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    print(f"\n💾 تم حفظ الكتالوج المدمج بنجاح: {len(final_merged_catalog)} رواية (الجديد في الصدارة).")

    # تحديث ملف الإشعارات العام
    if new_releases:
        update_global_new_releases(new_releases)

    print("⚡ اكتملت المزامنة التراكمية لعالم الروايات بنجاح تام!")

def auto_push_to_github():
    print("\n📤 فحص ورفع التحديثات إلى GitHub...")
    try:
        # فحص مجلد data كاملاً ليشمل الكاتلوج وملف data/new.json
        status = subprocess.run(
            ["git", "status", "--porcelain", "data/"], 
            capture_output=True, 
            text=True
        )
        if not status.stdout.strip():
            print("✨ لا توجد ملفات جديدة للرفع.")
            return

        subprocess.run(["git", "add", "data/"], check=True)
        commit_msg = f"Incremental sync: RealmNovel & New Releases ({time.strftime('%Y-%m-%d %H:%M')})"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "pull", "--rebase"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("⚡ تم الرفع بنجاح إلى المستودع!")
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ أثناء الرفع لـ Git: {e}")

if __name__ == "__main__":
    sync_realmnovel()
    auto_push_to_github()
