import json
import os
import subprocess
import time
from curl_cffi import requests

BASE_API = "https://despair-world.com/supabase-api/rest/v1"
API_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiaWF0IjoxNzkwMzczMDE4LCJleHAiOjE5NDgwNTMwMTh9."
    "xYgAlC_9SuUCdzdff6wBYGWzYFmAI9apP_mk4DCPaVI"
)

DATA_DIR = os.path.join("data", "despair")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

DETAILS_SYNC_LIMIT = 1000    # تحديث تفاصيل وفصول أحدث 20 عملاً
FETCH_LIMIT = 1000           # عدد الأعمال المسحوبة للمزامنة الدورية

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

HEADERS = {
    "apikey": API_KEY,
    "authorization": f"Bearer {API_KEY}",
    "accept-profile": "public",
    "Referer": "https://despair-world.com/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:158.0) Gecko/20100101 Firefox/158.0",
    "Accept": "application/json"
}

def get_session():
    # استخدام محاكاة بصمة كروم لتفادي حظر كلاودفلير
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def format_type(raw_type: str) -> str:
    if not raw_type:
        return "مانغا"
    t = raw_type.strip().lower()
    if any(k in t for k in ["manhwa", "مانهوا"]): return "مانهوا"
    if any(k in t for k in ["manhua", "مانها"]): return "مانها"
    if any(k in t for k in ["webtoon", "ويبتون"]): return "ويب تون"
    if any(k in t for k in ["novel", "رواية"]): return "رواية"
    if any(k in t for k in ["comic", "كوميك"]): return "كوميك"
    return "مانغا"

def format_status(raw_status: str) -> str:
    if not raw_status:
        return "مستمر"
    s = raw_status.strip().lower()
    if any(k in s for k in ["completed", "مكتمل"]): return "مكتمل"
    if any(k in s for k in ["hiatus", "متوقف"]): return "متوقف مؤقتاً"
    return "مستمر"

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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لديسبير في {GLOBAL_NEW_FILE}")

def fetch_work_chapters(session, work_id: str) -> dict:
    url = (
        f"{BASE_API}/chapters"
        f"?work_id=eq.{work_id}"
        f"&select=id,chapter_number,title,pages,created_at"
        f"&order=chapter_number.asc"
    )
    res = session.get(url, timeout=15)
    if res.status_code != 200:
        return {}

    chapters_map = {}
    for ch in res.json():
        num = ch.get("chapter_number", 0)
        clean_num = str(int(num)) if float(num).is_integer() else str(num)
        chapters_map[ch["id"]] = {
            "name": clean_num,
            "title": ch.get("title") or f"الفصل {clean_num}",
            "pages": ch.get("pages", [])
        }
    return chapters_map

def git_commit_and_push():
    """فحص مجلد data ورفع أي تغييرات تلقائياً إلى GitHub"""
    try:
        status = subprocess.run(["git", "status", "--porcelain", "data/"], capture_output=True, text=True, check=True)
        if not status.stdout.strip():
            print("📁 لا توجد تغييرات جديدة لرفعها إلى المستودع.")
            return

        print("\n🚀 جاري رفع التحديثات الجديدة إلى المستودع (Git Push)...")
        subprocess.run(["git", "add", "data/"], check=True)
        subprocess.run(["git", "commit", "-m", "Auto update: despair sync [skip ci]"], check=True)
        subprocess.run(["git", "pull", "--rebase"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("✅ تم رفع التحديثات إلى GitHub بنجاح!")
    except subprocess.CalledProcessError as e:
        print(f"❌ تعذر الرفع عبر Git: {e}")
    except Exception as e:
        print(f"❌ خطأ غير متوقع أثناء الرفع: {e}")

def sync_despair_fast():
    session = get_session()
    print("⚡ بدء المزامنة السحابية فائقة السرعة لديسبير...")

    catalog_dict = load_existing_catalog()
    
    url = (
        f"{BASE_API}/manga_works"
        f"?select=id,title_ar,title_en,slug,cover_url,type,status,genres,synopsis,chapters_count,rating_avg,updated_at"
        f"&order=updated_at.desc"
        f"&limit={FETCH_LIMIT}"
    )
    
    try:
        res = session.get(url, timeout=15)
        res.raise_for_status()
        works = res.json()
    except Exception as e:
        print(f"خطأ أثناء جلب قائمة الأعمال: {e}")
        return

    ordered_recent_ids = []
    
    for item in works:
        work_id = item["id"]
        title = item.get("title_ar") or item.get("title_en") or "بدون عنوان"
        cover_url = item.get("cover_url", "")
        raw_type = item.get("type", "manhwa")
        raw_status = item.get("status", "مستمر")
        genres = item.get("genres", [])
        chapters_count = item.get("chapters_count", 0)

        if work_id in catalog_dict:
            catalog_dict[work_id]["title"] = title
            catalog_dict[work_id]["cover_url"] = cover_url
            catalog_dict[work_id]["status"] = format_status(raw_status)
            catalog_dict[work_id]["genres"] = genres
            catalog_dict[work_id]["total_chapters"] = chapters_count
        else:
            catalog_dict[work_id] = {
                "id": work_id,
                "title": title,
                "url": f"https://despair-world.com/series/manga/{item.get('slug', work_id)}",
                "cover_url": cover_url,
                "type": format_type(raw_type),
                "status": format_status(raw_status),
                "total_chapters": chapters_count,
                "genres": genres
            }

        ordered_recent_ids.append(work_id)

    targets_ids = ordered_recent_ids[:DETAILS_SYNC_LIMIT]
    new_releases = []

    for index, work_id in enumerate(targets_ids, 1):
        work_meta = next((w for w in works if w["id"] == work_id), None)
        if not work_meta:
            continue

        file_path = os.path.join(DATA_DIR, f"{work_id}.json")
        prev_chaps = catalog_dict[work_id].get("total_chapters", 0)

        try:
            chapters_map = fetch_work_chapters(session, work_id)
            current_chaps = len(chapters_map)

            details_data = {
                "id": work_id,
                "title": catalog_dict[work_id]["title"],
                "cover_url": work_meta.get("cover_url", ""),
                "description": work_meta.get("synopsis", "لا يوجد وصف"),
                "type": format_type(work_meta.get("type", "manhwa")),
                "status": format_status(work_meta.get("status", "مستمر")),
                "last_update": (work_meta.get("updated_at") or "").split("T")[0],
                "rating": str(work_meta.get("rating_avg", "")),
                "favorites": "",
                "genres": work_meta.get("genres", []),
                "is_novel": False,
                "chapters": chapters_map
            }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details_data, f, ensure_ascii=False, indent=2)

            catalog_dict[work_id]["total_chapters"] = current_chaps
            print(f"✓ [{index}/{len(targets_ids)}] تم تحديث ديسبير: {work_id} ({current_chaps} فصل)")

            if current_chaps > prev_chaps and current_chaps > 0:
                new_releases.append({
                    "id": work_id,
                    "title": catalog_dict[work_id]["title"],
                    "chapter": f"الفصل {current_chaps}" if prev_chaps > 0 else "عمل جديد",
                    "type": catalog_dict[work_id].get("type", "مانغا"),
                    "cover_url": catalog_dict[work_id].get("cover_url", "")
                })

            time.sleep(0.1)
        except Exception as e:
            print(f"خطأ أثناء تحديث فصول {work_id}: {e}")

    seen_ids = set(ordered_recent_ids)
    final_merged_catalog = [catalog_dict[i] for i in ordered_recent_ids if i in catalog_dict] + [
        item for i, item in catalog_dict.items() if i not in seen_ids
    ]

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    if new_releases:
        update_global_new_releases(new_releases)

    print(f"\n⚡ اكتملت المزامنة المحلية! إجمالي الأعمال: {len(final_merged_catalog)}")

    # الرفع التلقائي إلى GitHub فور انتهاء التحديثات
    git_commit_and_push()

if __name__ == "__main__":
    sync_despair_fast()
