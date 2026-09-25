import json
import os
import time
import subprocess
from curl_cffi import requests

BASE_WEB = "https://rewayat.club"
API_BASE = "https://api.rewayat.club/api"
DATA_DIR = os.path.join("data", "rewayatclub")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

# 🎯 حدد عدد الصفحات للجولات السريعة (تحديث الجديد فقط)
MAX_PAGES = 10 

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
    print(f"🚀 بدء المزامنة التراكمية لنادي الروايات (فحص أول {MAX_PAGES} صفحات)...")

    # 1. استرجاع الأرشيف المخزن مسبقاً
    old_catalog = []
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE, "r", encoding="utf-8") as f:
                old_catalog = json.load(f)
        except Exception as e:
            print(f"⚠️ خطأ أثناء قراءة الكتالوج القديم: {e}")
            old_catalog = []

    old_map = {item["id"]: item for item in old_catalog if "id" in item}
    print(f"📂 تم تحميل {len(old_map)} رواية محفوظة مسبقاً في الأرشيف.")

    freshly_scraped = []
    seen_fresh_ids = set()
    page = 1
    consecutive_empty = 0

    # 2. سحب الصفحات المحددة
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
            if not slug or slug in seen_fresh_ids:
                continue

            seen_fresh_ids.add(slug)
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
            freshly_scraped.append(entry)

        print(f"نادي الروايات [صفحة {page}]: تم فحص البيانات بنجاح.")
        page += 1
        time.sleep(0.15)

    # 3. فحص وتحديث فصول الروايات المرصودة وكشف الإشعارات
    print(f"\n⚡ فحص وتحديث فصول {len(freshly_scraped)} رواية من الجولة الحالية...")
    new_releases = []

    for idx, item in enumerate(freshly_scraped, 1):
        slug = item["id"]
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

        prev_chaps = old_map.get(slug, {}).get("total_chapters", 0)

        # التقاط الإشعارات للروايات الجديدة والفصول المحدثة
        if slug not in old_map:
            new_releases.append({
                "id": slug,
                "title": item["title"],
                "chapter": f"الفصل {target_total_ch}" if target_total_ch > 0 else "رواية جديدة",
                "type": "رواية",
                "cover_url": item["cover_url"]
            })
        elif target_total_ch > prev_chaps and target_total_ch > 0:
            new_releases.append({
                "id": slug,
                "title": item["title"],
                "chapter": f"الفصل {target_total_ch}",
                "type": "رواية",
                "cover_url": item["cover_url"]
            })

        # كاش ذكي: تخطي فوري إذا كانت الفصول متطابقة محلياً
        if existing_chapters_count >= target_total_ch and target_total_ch > 0:
            print(f"⚡ [{idx}/{len(freshly_scraped)}] متطابق ومكتمل: {slug} ({target_total_ch} فصل)")
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

        print(f"✓ [{idx}/{len(freshly_scraped)}] تم تحديث الفصول: {item['title']} ({len(chapters_map)} فصل)")

    # ================== 4. الدمج الذكي للكاتلوج ==================
    fresh_ids = {x["id"] for x in freshly_scraped}
    remaining_old = [x for x in old_catalog if x.get("id") not in fresh_ids]
    final_merged_catalog = freshly_scraped + remaining_old

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    print(f"\n💾 تم حفظ الكتالوج المدمج: {len(final_merged_catalog)} رواية (الأحدث في الصدارة).")

    # تحديث إشعارات new.json
    if new_releases:
        update_global_new_releases(new_releases)

    print("🎉 اكتملت المزامنة التراكمية لنادي الروايات بنجاح!")

def auto_push_to_github():
    print("\n📤 فحص ورفع البيانات إلى GitHub...")
    try:
        # فحص مجلد data/ كاملاً لضمان رفع الكاتلوج وملف الإشعارات data/new.json
        status = subprocess.run(
            ["git", "status", "--porcelain", "data/"], 
            capture_output=True, 
            text=True
        )
        if not status.stdout.strip():
            print("✨ لا توجد ملفات جديدة للرفع.")
            return

        subprocess.run(["git", "add", "data/"], check=True)
        commit_msg = f"Incremental sync: RewayatClub & New Releases ({time.strftime('%Y-%m-%d %H:%M')})"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "pull", "--rebase"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("⚡ تم الرفع بنجاح إلى المستودع!")
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ أثناء الرفع لـ Git: {e}")

if __name__ == "__main__":
    sync_rewayatclub()
    auto_push_to_github()
