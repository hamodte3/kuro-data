import json
import os
import time
import subprocess
from curl_cffi import requests

BASE_WEB = "https://rewayat.club"
API_BASE = "https://api.rewayat.club/api"
DATA_DIR = os.path.join("data", "rewayatclub")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

# 🎯 حدد عدد الصفحات للجولات السريعة (مثلاً 3 أو 5 صفحات لتحديث الجديد فقط)
MAX_PAGES = 10 

os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://rewayat.club/",
    "Origin": "https://rewayat.club"
}

def get_session():
    return requests.Session(impersonate="chrome124", headers=HEADERS)

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

def load_existing_catalog() -> dict:
    """تحميل الكتالوج المحفوظ مسبقاً كـ Dictionary لتحديثه بدون فقدان القديم"""
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
            return {item["id"]: item for item in items if "id" in item}
    except Exception as e:
        print(f"⚠️ خطأ أثناء قراءة الكتالوج القديم: {e}")
        return {}

def sync_rewayatclub():
    session = get_session()
    print(f"🚀 بدء المزامنة التراكمية لنادي الروايات (فحص أول {MAX_PAGES} صفحات)...")

    # 1. استرجاع الأرشيف المخزن مسبقاً
    catalog_map = load_existing_catalog()
    print(f"📂 تم تحميل {len(catalog_map)} رواية محفوظة مسبقاً.")

    active_slugs_this_run = []  # الروايات التي تم رصدها في هذه الجولة فقط
    page = 1
    consecutive_empty = 0

    # 2. سحب الصفحات المحددة وتحديث بياناتها
    while page <= MAX_PAGES:
        url = f"{API_BASE}/novels/?ordering=-num_chapters&page={page}"
        res = safe_get(session, url, timeout=20)
        
        if not res:
            print(f"⚠️ تخطي صفحة {page} بسبب انقطاع الاستجابة...")
            page += 1
            consecutive_empty += 1
            if consecutive_empty >= 5:
                print("🛑 توقف الفهرس بعد 5 أخطاء متتالية.")
                break
            continue

        consecutive_empty = 0
        try:
            payload = res.json()
        except Exception:
            break

        results = payload.get("results") or payload.get("novels") or []
        if not results:
            print(f"🏁 وصلنا لآخر صفحة متاحة: {page - 1}")
            break

        for novel in results:
            slug = novel.get("slug")
            if not slug: continue

            title = novel.get("arabic") or novel.get("english") or slug
            cover = normalize_cover(novel.get("poster_url") or novel.get("poster") or "")
            genres = [g.get("arabic") for g in novel.get("genre", []) if isinstance(g, dict) and g.get("arabic")]
            total_chapters = novel.get("num_chapters", 0)

            entry = {
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

            # تحديث أو إضافة الرواية للكتالوج التراكمي
            if slug in catalog_map:
                catalog_map[slug].update(entry)
            else:
                catalog_map[slug] = entry

            if slug not in active_slugs_this_run:
                active_slugs_this_run.append(slug)

        print(f"نادي الروايات [صفحة {page}]: تم فحص البيانات بنجاح.")
        page += 1
        time.sleep(0.15)

    # 3. حفظ الكتالوج العام المدمج (القديم + الجديد والمحدث)
    full_catalog_list = list(catalog_map.values())
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(full_catalog_list, f, ensure_ascii=False, indent=2)

    print(f"\n✨ تم حفظ الكتالوج المدمج بنجاح (المجموع الإجمالي: {len(full_catalog_list)} رواية).")

    # 4. تحديث ملفات الفصول للروايات المرصودة في هذه الجولة فقط
    print(f"\n⚡ فحص وتحديث فصول {len(active_slugs_this_run)} رواية من الجولة الحالية...")

    for idx, slug in enumerate(active_slugs_this_run, 1):
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        item_meta = catalog_map[slug]
        target_total_ch = item_meta.get("total_chapters", 0)

        existing_chapters_count = 0
        existing_data = {}

        # فحص إذا كان الملف موجوداً مسبقاً
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    existing_chapters_count = len(existing_data.get("chapters", {}))
            except Exception:
                pass

        # ⚡ كاش ذكي: تخطي فوري إذا كانت الفصول متطابقة محلياً
        if existing_chapters_count >= target_total_ch and target_total_ch > 0:
            print(f"⚡ [{idx}/{len(active_slugs_this_run)}] متطابق ومكتمل: {slug} ({target_total_ch} فصل)")
            continue

        # توليد أو استكمال روابط الفصول الناقصة فقط
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
            "title": item_meta["title"],
            "cover_url": item_meta["cover_url"],
            "description": existing_data.get("description", "لا يوجد وصف"),
            "type": "رواية",
            "status": item_meta["status"],
            "last_update": "",
            "rating": "",
            "favorites": "",
            "genres": item_meta.get("genres", []),
            "is_novel": True,
            "chapters": chapters_map
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        print(f"✓ [{idx}/{len(active_slugs_this_run)}] تم تحديث الفصول: {item_meta['title']} ({len(chapters_map)} فصل)")

    print("\n🎉 اكتملت المزامنة التراكمية لنادي الروايات بنجاح!")

def auto_push_to_github():
    print("\n📤 فحص ورفع البيانات إلى GitHub...")
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "data/rewayatclub/"], 
            capture_output=True, 
            text=True
        )
        if not status.stdout.strip():
            print("✨ لا توجد ملفات جديدة للرفع.")
            return

        subprocess.run(["git", "add", "data/rewayatclub/"], check=True)
        commit_msg = f"Incremental sync: RewayatClub ({time.strftime('%Y-%m-%d %H:%M')})"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        
        # دفع آمن بدون force
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("⚡ تم الرفع بنجاح إلى المستودع!")
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ أثناء الرفع لـ Git: {e}")

if __name__ == "__main__":
    sync_rewayatclub()
    auto_push_to_github()
