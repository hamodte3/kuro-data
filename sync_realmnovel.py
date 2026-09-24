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

# حدد عدد الصفحات التي تريد فحصها دورياً (مثلاً 2 أو 3 صفحات للجديد فقط)
MAX_PAGES = 10 

os.makedirs(DATA_DIR, exist_ok=True)

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

def load_existing_catalog() -> dict:
    """تحميل الكتالوج القديم كـ Dictionary لتسهيل الدمج والتحديث"""
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
            return {item["id"]: item for item in items if "id" in item}
    except Exception as e:
        print(f"⚠️ تعذر قراءة الكتالوج القديم، سيتم البدء بكتالوج جديد: {e}")
        return {}

def sync_realmnovel():
    session = get_session()
    print(f"🚀 بدء المزامنة التراكمية لعالم الروايات (فحص أول {MAX_PAGES} صفحات)...")

    # 1. تحميل البيانات القديمة دون حذفها
    catalog_map = load_existing_catalog()
    print(f"📂 تم تحميل {len(catalog_map)} رواية محفوظة مسبقاً في الأرشيف.")

    active_novels_this_run = []  # الروايات التي وُجدت في هذه الجولة لفحص تفاصيلها

    # 2. سحب الصفحات المحددة فقط
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
                if not nid: 
                    continue

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
                    "rating": str(item.get("rating", ""))
                }

                # دمج أو تحديث العمل بالكتالوج العام
                if nid in catalog_map:
                    catalog_map[nid].update(entry)
                else:
                    catalog_map[nid] = entry

                if nid not in active_novels_this_run:
                    active_novels_this_run.append(nid)

            print(f"الـ API [صفحة {page}]: تم فحص البيانات بنجاح.")
            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ أثناء جلب الفهرس: {e}")
            break

    # 3. حفظ الفهرس العام الشامل (القديم + المحدث والجديد)
    full_catalog_list = list(catalog_map.values())
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(full_catalog_list, f, ensure_ascii=False, indent=2)

    print(f"💾 تم تحديث الكتالوج العام بنجاح (المجموع الكلي: {len(full_catalog_list)} رواية).")

    # 4. تحديث ملفات الروايات الفردية (فقط للروايات النشطة في هذا التشغيل)
    print(f"\n⚡ فحص وتحديث فصول {len(active_novels_this_run)} رواية من الجولة الحالية...")
    
    for idx, nid in enumerate(active_novels_this_run, 1):
        file_path = os.path.join(DATA_DIR, f"{nid}.json")
        item_meta = catalog_map[nid]

        existing_chapters_count = 0
        existing_data = {}

        # فحص إذا كان الملف موجوداً مسبقاً لقراءة عدد الفصول المحفوظة
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

            # ⚡ كاش ذكي: إذا كانت الفصول متطابقة محلياً مع السيرفر، نتخطى إعادة البناء
            if existing_chapters_count == total_chapters and total_chapters > 0:
                print(f"⚡ [{idx}/{len(active_novels_this_run)}] متطابق ومكتمل: {nid} ({total_chapters} فصل)")
                continue

            # توليد خريطة الفصول وتحديث الجديد
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
                "title": details.get("title") or item_meta["title"],
                "cover_url": item_meta["cover_url"],
                "description": details.get("description") or existing_data.get("description", "لا يوجد وصف"),
                "type": "رواية",
                "status": details.get("status") or item_meta["status"],
                "last_update": "",
                "rating": str(details.get("rating") or item_meta["rating"]),
                "favorites": "",
                "genres": details.get("genres") or existing_data.get("genres", ["فنون قتال", "عالم آخر"]),
                "is_novel": True,
                "chapters": chapters_map
            }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(novel_payload, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(active_novels_this_run)}] تم التحديث: {nid} ({len(chapters_map)} فصل)")
            time.sleep(0.15)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {nid}: {e}")

    print("\n⚡ اكتملت المزامنة التراكمية لعالم الروايات بنجاح تام!")

def auto_push_to_github():
    print("\n📤 فحص ورفع تحديثات عالم الروايات إلى GitHub...")
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "data/realmnovel/"], 
            capture_output=True, 
            text=True
        )
        if not status.stdout.strip():
            print("✨ لا توجد ملفات جديدة للرفع.")
            return

        subprocess.run(["git", "add", "data/realmnovel/"], check=True)
        commit_msg = f"Incremental sync: RealmNovel ({time.strftime('%Y-%m-%d %H:%M')})"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        
        # دفع آمن بدون force
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("⚡ تم الرفع بنجاح إلى المستودع!")
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ أثناء الرفع لـ Git: {e}")

if __name__ == "__main__":
    sync_realmnovel()
    auto_push_to_github()
