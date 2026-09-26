import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://olympustaff.com"
DATA_DIR = os.path.join("data", "teamx")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

DETAILS_SYNC_LIMIT = 20    # فحص وتجهيز فصول أحدث 20 عملاً تم تحديثها
MAX_DELTA_PAGES = 3        # فحص أول 3 صفحات فقط كل ساعة لمراقبة الجديد

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

def get_session():
    session = requests.Session(impersonate="chrome124")
    session.headers.update({
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Referer": f"{BASE_URL}/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    })
    return session

def normalize_url(raw_url: str) -> str:
    if not raw_url: return ""
    u = raw_url.strip()
    if u.startswith("//"): return f"https:{u}"
    if not u.startswith("http://") and not u.startswith("https://"):
        u = f"{BASE_URL}{u}" if u.startswith("/") else f"{BASE_URL}/{u}"
    return u.replace("http://", "https://")

def load_existing_catalog() -> dict:
    """تحميل الأرشيف القديم لمنع مسح أو تصفير أي عمل سابق"""
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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لـ Team X في {GLOBAL_NEW_FILE}")

def scrape_teamx_details(session, slug: str, existing_chapters: dict = None) -> dict:
    url = f"{BASE_URL}/series/{slug}?_t={int(time.time())}"
    res = session.get(url, timeout=25)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code}")

    soup = BeautifulSoup(res.text, "html.parser")
    html = res.text

    title_node = soup.select_one("div.author-info-title h1, h1")
    title = title_node.get_text(strip=True) if title_node else slug

    cover_node = soup.select_one("div.text-right img, img[alt='Manga Image']")
    cover_url = cover_node.get("src", "") if cover_node else ""
    if not cover_url or "data:image" in cover_url:
        meta_og = soup.select_one("meta[property='og:image']")
        cover_url = meta_og.get("content", "") if meta_og else ""
    cover_url = normalize_url(cover_url)

    desc_node = soup.select_one("div.review-content p")
    description = desc_node.get_text(strip=True) if desc_node else "لا يوجد وصف"

    rating_node = soup.select_one("#average_rating")
    raw_rating = rating_node.get_text(strip=True) if rating_node else ""

    genres = [a.get_text(strip=True) for a in soup.select("div.review-author-info a")]

    chapters_map = existing_chapters.copy() if existing_chapters else {}
    number_regex = re.compile(r"\d+(\.\d+)?")
    found_numbers = set()

    for a in soup.select("div.enhanced-chapters-grid div.chapter-card a.chapter-link, div.chapter-card a.chapter-link"):
        href = normalize_url(a.get("href", "")).rstrip("/")
        if f"/series/{slug}/" in href:
            num_node = a.select_one(".chapter-number") or a.select_one(".chapter-title")
            raw_num = num_node.get_text(strip=True) if num_node else href.split("/")[-1]
            match = number_regex.search(raw_num)
            clean_num = match.group(0) if match else raw_num
            
            chapters_map[href] = {
                "name": clean_num,
                "images": []
            }
            try:
                found_numbers.add(int(float(clean_num)))
            except ValueError:
                pass

    total_match = re.search(r"(?:قائمة الفصول|الفصول)\s*\(([0-9]+)\)", html)
    total_count = int(total_match.group(1)) if total_match else 0
    max_ch = max(max(found_numbers) if found_numbers else 0, total_count)

    # توليد الفصول المتسلسلة الناقصة
    if max_ch > 0:
        for i in range(1, max_ch + 1):
            ch_url = f"{BASE_URL}/series/{slug}/{i}"
            if ch_url not in chapters_map:
                chapters_map[ch_url] = {
                    "name": str(i),
                    "images": []
                }

    return {
        "id": slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": "مانهوا",
        "status": "مستمر",
        "last_update": "",
        "rating": raw_rating,
        "favorites": "",
        "genres": genres,
        "is_novel": False,
        "total_chapters": max_ch,
        "chapters": chapters_map
    }

def sync_teamx():
    session = get_session()
    print(f"🚀 بدء المزامنة الخاطفة لمصدر Team X (فحص أول {MAX_DELTA_PAGES} صفحات)...")

    catalog_dict = load_existing_catalog()
    ordered_recent_slugs = []

    # 1. سحب أول 3 صفحات مع الترتيب بالأحدث
    for page in range(1, MAX_DELTA_PAGES + 1):
        url = f"{BASE_URL}/series?page={page}" if page > 1 else f"{BASE_URL}/series"
        res = session.get(url, timeout=25)
        if res.status_code != 200:
            print(f"توقف عند صفحة {page}: رمز الاستجابة {res.status_code}")
            break

        soup = BeautifulSoup(res.text, "html.parser")
        items = soup.select("div.listupd div.bsx, div.bsx")
        if not items:
            break

        new_in_page = 0
        for it in items:
            a_tag = it.select_one("a")
            if not a_tag: continue
            href = normalize_url(a_tag.get("href", "")).rstrip("/")
            slug = href.split("/")[-1]

            title = a_tag.get("title", "").strip() or slug
            img = it.select_one("img")
            cover = normalize_url(img.get("src", "") or img.get("data-src", "") if img else "")

            # التحديث الآمن مع الحفاظ على الأرشيف السابق
            if slug in catalog_dict:
                catalog_dict[slug]["title"] = title
                catalog_dict[slug]["url"] = href
                if cover:
                    catalog_dict[slug]["cover_url"] = cover
            else:
                catalog_dict[slug] = {
                    "id": slug,
                    "title": title,
                    "url": href,
                    "cover_url": cover,
                    "type": "مانهوا",
                    "status": "مستمر",
                    "rating": "",
                    "total_chapters": 0
                }

            if slug not in ordered_recent_slugs:
                ordered_recent_slugs.append(slug)

            new_in_page += 1

        print(f"Team X [صفحة {page}]: رصد {new_in_page} عمل محدث.")
        time.sleep(0.3)

    # 2. تحديث فصول أحدث 20 عملاً فقط وتوليد الإشعارات
    targets_slugs = ordered_recent_slugs[:DETAILS_SYNC_LIMIT]
    new_releases = []

    print(f"\n⚡ تحديث فصول أحدث {len(targets_slugs)} عمل نشط...")

    for idx, slug in enumerate(targets_slugs, 1):
        item = catalog_dict[slug]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")

        existing_data = {}
        existing_chapters_count = 0
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    existing_chapters_count = len(existing_data.get("chapters", {}))
            except Exception:
                pass

        try:
            details = scrape_teamx_details(
                session, 
                slug, 
                existing_chapters=existing_data.get("chapters")
            )
            target_total = details.get("total_chapters", 0)
            item["total_chapters"] = target_total
            item["rating"] = details.get("rating", "")

            prev_chaps = existing_chapters_count or item.get("total_chapters", 0)

            # كشف التحديث لتوليد التنبيهات
            if target_total > prev_chaps and target_total > 0:
                new_releases.append({
                    "id": slug,
                    "title": item["title"],
                    "chapter": f"الفصل {target_total}" if prev_chaps > 0 else "عمل جديد",
                    "type": "مانهوا",
                    "cover_url": item["cover_url"]
                })

            # كاش ذكي: تخطي إعادة الكتابة إذا كانت الفصول متطابقة
            if existing_chapters_count >= target_total and target_total > 0:
                print(f"⚡ [{idx}/{len(targets_slugs)}] متطابق ومكتمل: {details['title']} ({target_total} فصل)")
                continue

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(targets_slugs)}] تم التحديث: {details['title']} ({len(details['chapters'])} فصل)")
            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {slug}: {e}")

    # 3. حفظ الفهرس الشامل (الأحدث أولاً + بقية الأرشيف القديم)
    seen_slugs = set(ordered_recent_slugs)
    final_merged_catalog = [catalog_dict[s] for s in ordered_recent_slugs] + [
        item for s, item in catalog_dict.items() if s not in seen_slugs
    ]

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    # 4. تحديث الإشعارات المشتركة
    if new_releases:
        update_global_new_releases(new_releases)

    print(f"\n💾 تم حفظ الكتالوج المدمج: {len(final_merged_catalog)} عمل.")
    print("🎉 اكتملت المزامنة التراكمية لـ Team X بنجاح تام!")

if __name__ == "__main__":
    sync_teamx()
