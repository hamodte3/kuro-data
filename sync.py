import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://azorafly.com"
DATA_DIR = "data"
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join(DATA_DIR, "new.json")

DETAILS_SYNC_LIMIT = 20  # تحديث تفاصيل وفصول أحدث 20 عملاً
MAX_DELTA_PAGES = 5      # فحص أول 5 صفحات فقط من الكاتلوج

os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{BASE_URL}/",
    "Origin": BASE_URL,
    "Connection": "keep-alive"
}

def get_session():
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = f"{BASE_URL}{url}" if url.startswith("/") else f"{BASE_URL}/{url}"
    return (
        url.replace("http://", "https://")
        .replace("azoramanga.com", "azorafly.com")
        .replace("/manga/", "/series/")
        .strip()
    )

def format_chapter_number(raw_num) -> str:
    """تنسيق رقم الفصل مع الحفاظ التام على الأرقام العشرية (مثل 89.5 و 3.5)"""
    try:
        val = float(raw_num)
        return str(int(val)) if val.is_integer() else str(val)
    except (ValueError, TypeError):
        return str(raw_num).strip()

def format_type(raw_type: str) -> str:
    t = (raw_type or "").strip().lower()
    if any(k in t for k in ["novel", "رواية"]): return "رواية"
    if any(k in t for k in ["manhwa", "مانهوا"]): return "مانهوا"
    if any(k in t for k in ["manhua", "مانها"]): return "مانها"
    if any(k in t for k in ["webtoon", "ويبتون", "ويب تون"]): return "ويب تون"
    if any(k in t for k in ["comic", "كوميك"]): return "كوميك"
    if any(k in t for k in ["manga", "مانجا", "مانغا"]): return "مانغا"
    return raw_type if raw_type else "مانهوا"

def format_status(raw_status: str) -> str:
    s = (raw_status or "").strip().lower()
    if any(k in s for k in ["ongoing", "مستمر", "مستمرة"]): return "مستمر"
    if any(k in s for k in ["completed", "مكتمل", "مكتملة"]): return "مكتمل"
    if any(k in s for k in ["hiatus", "متوقف", "متوقفة"]): return "متوقف مؤقتاً"
    return "مستمر"

def clean_description(raw_desc: str, title: str) -> str:
    if not raw_desc:
        return "لا يوجد وصف."
    soup = BeautifulSoup(raw_desc, "html.parser")
    text = soup.get_text().strip()
    lines = [l.strip() for l in text.splitlines()]
    clean_lines = [
        l for l in lines 
        if l and l.lower() != title.lower() 
        and not any(bad in l.lower() for bad in ["تحديث:", "اجعل الكل مقروء", "الفصول"])
    ]
    return "\n\n".join(clean_lines).strip() if clean_lines else "لا يوجد وصف."

def load_existing_catalog() -> dict:
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["id"]: item for item in data if "id" in item}
    except Exception as e:
        print(f"خطأ قراءة الكاتلوج القديم: {e}")
        return {}

def update_global_new_releases(new_releases: list):
    if not new_releases:
        return
    existing = []
    if os.path.exists(GLOBAL_NEW_FILE):
        try:
            with open(GLOBAL_NEW_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    combined = new_releases + existing
    seen = set()
    deduped = []
    for item in combined:
        key = (item.get("id"), item.get("chapter"))
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    with open(GLOBAL_NEW_FILE, "w", encoding="utf-8") as f:
        json.dump(deduped[:15], f, ensure_ascii=False, indent=2)
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد في {GLOBAL_NEW_FILE}")

# 🎯 جلب التفاصيل والفصول بالـ Pure API
def scrape_manga_details(session, manga_url: str):
    clean_url = normalize_url(manga_url).rstrip("/")
    series_slug = clean_url.split("/")[-1]

    # 1. طلب تفاصيل العمل عبر postSlug
    post_api_url = f"https://api.azorafly.com/api/post?postSlug={series_slug}"
    res = session.get(post_api_url, timeout=20)
    
    if res.status_code != 200:
        raise Exception(f"فشل جلب تفاصيل {series_slug} من الـ API: كود {res.status_code}")

    data = res.json()
    post = data.get("post", {})
    post_id = post.get("id")

    title = post.get("postTitle") or series_slug
    cover_url = post.get("featuredImage") or ""
    if cover_url and not cover_url.startswith("http"):
        cover_url = f"{BASE_URL}{cover_url}"
    cover_url = cover_url.replace("http://", "https://")

    description = clean_description(post.get("postContent") or "", title)
    genres = [g.get("name").strip() for g in post.get("genres", []) if g.get("name")]
    manga_type = format_type(post.get("seriesType") or "مانهوا")
    status = format_status(post.get("seriesStatus") or "مستمر")

    avg_rate = post.get("averageRating") or data.get("averageRating")
    rating = f"{float(avg_rate):.1f}" if avg_rate and float(avg_rate) > 0 else ""
    last_update = post.get("updatedAt", "").split("T")[0]

    # 2. طلب مصفوفة الفصول كاملة عبر الـ API الرسمي
    chapters_map = {}
    if post_id:
        ch_api_url = f"https://api.azorafly.com/api/chapters?postId={post_id}&take=200"
        ch_res = session.get(ch_api_url, timeout=20)
        
        if ch_res.status_code == 200:
            chapters_list = ch_res.json().get("post", {}).get("chapters", [])
            for ch in chapters_list:
                slug = ch.get("slug")
                num_val = ch.get("number")
                if slug and num_val is not None:
                    clean_name = format_chapter_number(num_val)
                    full_url = f"{BASE_URL}/series/{series_slug}/{slug}"
                    chapters_map[full_url] = {"name": clean_name}

    return {
        "id": series_slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": manga_type,
        "status": status,
        "last_update": last_update,
        "rating": rating,
        "genres": genres,
        "is_novel": False,
        "chapters": chapters_map
    }

def sync_fast():
    session = get_session()
    print(f"🚀 بدء المزامنة السحابية لأزورا (فحص أول {MAX_DELTA_PAGES} صفحات)...")
    
    catalog_dict = load_existing_catalog()
    recent_targets = []

    # 1. فحص الصفحات الأولى من الكاتلوج لالتقاط الأعمال المحدثة
    for page in range(1, MAX_DELTA_PAGES + 1):
        url = f"{BASE_URL}/series" if page == 1 else f"{BASE_URL}/series?page={page}"
        try:
            res = session.get(url, timeout=25)
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("div:has(a.text-foreground[href^='/series/'])")
            if not cards: break

            new_in_page = 0
            for container in cards:
                link = container.select_one("a.text-foreground[href^='/series/']:not([href*='/chapter'])")
                if not link: continue
                title = link.text.strip()
                if "الحالة" in title or not title: continue

                manga_url = normalize_url(link.get("href", ""))
                slug = manga_url.rstrip("/").split("/")[-1]

                cover_el = container.select_one("img.object-cover")
                cover = cover_el.get("src", "").strip() if cover_el else ""
                if cover and not cover.startswith("http"): cover = f"{BASE_URL}{cover}"
                cover_url = cover.replace("http://", "https://")

                if slug in catalog_dict:
                    catalog_dict[slug]["title"] = title
                    catalog_dict[slug]["url"] = manga_url
                    if cover_url:
                        catalog_dict[slug]["cover_url"] = cover_url
                    item_ref = catalog_dict.pop(slug)
                    catalog_dict = {slug: item_ref, **catalog_dict}
                else:
                    catalog_dict = {
                        slug: {
                            "id": slug,
                            "title": title,
                            "url": manga_url,
                            "cover_url": cover_url,
                            "type": "مانهوا",
                            "total_chapters": 0,
                            "genres": []
                        },
                        **catalog_dict
                    }

                if not any(t["id"] == slug for t in recent_targets):
                    recent_targets.append(catalog_dict[slug])

                new_in_page += 1

            if new_in_page == 0: break
            time.sleep(0.1)
        except Exception as e:
            print(f"خطأ في صفحة {page}: {e}")
            break

    # 2. تحديث تفاصيل وفصول أحدث 20 عملاً بالـ API النظيف
    targets_to_scrape = recent_targets[:DETAILS_SYNC_LIMIT]
    new_releases = []

    for item in targets_to_scrape:
        slug = item["id"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        prev_chaps = item.get("total_chapters", 0)

        try:
            details = scrape_manga_details(session, item["url"])
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            current_chaps = len(details["chapters"])
            item["type"] = details["type"]
            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["total_chapters"] = current_chaps
            item["genres"] = details.get("genres", [])
            
            print(f"✓ تم تجهيز: {slug} ({current_chaps} فصل) - التصنيفات: {item['genres']}")

            if current_chaps > prev_chaps and current_chaps > 0:
                new_releases.append({
                    "id": slug,
                    "title": item["title"],
                    "chapter": f"الفصل {current_chaps}",
                    "type": item.get("type", "مانهوا"),
                    "cover_url": item.get("cover_url", "")
                })

            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ مع {slug}: {e}")

    # 3. حفظ الكاتلوج المحدث
    final_merged_catalog = list(catalog_dict.values())
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    # 4. تحديث سجل الإشعارات
    if new_releases:
        update_global_new_releases(new_releases)

    print(f"\n⚡ اكتملت المزامنة بنجاح! الكاتلوج يضم {len(final_merged_catalog)} عملاً.")

if __name__ == "__main__":
    sync_fast()
