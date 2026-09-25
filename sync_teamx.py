import json
import os
import re
import time
import subprocess
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://olympustaff.com"
DATA_DIR = os.path.join("data", "teamx")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

# 🎯 3 صفحات كافية وسريعة جداً لرصد أحدث التحديثات
MAX_PAGES = 3 

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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لـ Team X في {GLOBAL_NEW_FILE}")

def scrape_teamx_details(session, slug: str, existing_chapters: dict = None) -> dict:
    url = f"{BASE_URL}/series/{slug}"
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
    print(f"🚀 بدء المزامنة التراكمية لمصدر Team X (فحص أول {MAX_PAGES} صفحات)...")

    # 1. استرجاع الأرشيف المخزن مسبقاً
    old_catalog = []
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE, "r", encoding="utf-8") as f:
                old_catalog = json.load(f)
        except Exception as e:
            print(f"⚠️ تعذر قراءة الكتالوج القديم: {e}")
            old_catalog = []

    old_map = {item["id"]: item for item in old_catalog if "id" in item}
    print(f"📂 تم تحميل {len(old_map)} عمل محفوظ مسبقاً في الأرشيف.")

    freshly_scraped = []
    seen_fresh_ids = set()

    # 2. فحص الصفحات المحددة فقط
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE_URL}/series?page={page}" if page > 1 else f"{BASE_URL}/series"
        res = session.get(url, timeout=25)
        if res.status_code != 200:
            print(f"توقف عند صفحة {page}: رمز الاستجابة {res.status_code}")
            break

        soup = BeautifulSoup(res.text, "html.parser")
        items = soup.select("div.listupd div.bsx, div.bsx")
        if not items:
            break

        for it in items:
            a_tag = it.select_one("a")
            if not a_tag: continue
            href = normalize_url(a_tag.get("href", "")).rstrip("/")
            slug = href.split("/")[-1]
            if slug in seen_fresh_ids: continue

            seen_fresh_ids.add(slug)
            title = a_tag.get("title", "").strip() or slug
            img = it.select_one("img")
            cover = normalize_url(img.get("src", "") or img.get("data-src", ""))

            entry = {
                "id": slug,
                "title": title,
                "url": href,
                "cover_url": cover,
                "type": "مانهوا",
                "status": "مستمر",
                "rating": "",
                "total_chapters": old_map.get(slug, {}).get("total_chapters", 0)
            }
            freshly_scraped.append(entry)

        print(f"Team X [صفحة {page}]: تم فحص الأعمال بنجاح.")
        time.sleep(0.4)

    # 3. تحديث تفاصيل وفصول الأعمال النشطة وكشف الإشعارات
    print(f"\n⚡ فحص وتحديث فصول {len(freshly_scraped)} عمل نشط من الجولة الحالية...")
    new_releases = []

    for idx, item in enumerate(freshly_scraped, 1):
        slug = item["id"]
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

            prev_chaps = old_map.get(slug, {}).get("total_chapters", 0)

            # التقاط إشعارات الأعمال الجديدة والفصول المحدثة
            if slug not in old_map:
                new_releases.append({
                    "id": slug,
                    "title": item["title"],
                    "chapter": f"الفصل {target_total}" if target_total > 0 else "عمل جديد",
                    "type": "مانهوا",
                    "cover_url": item["cover_url"]
                })
            elif target_total > prev_chaps and target_total > 0:
                new_releases.append({
                    "id": slug,
                    "title": item["title"],
                    "chapter": f"الفصل {target_total}",
                    "type": "مانهوا",
                    "cover_url": item["cover_url"]
                })

            # ⚡ كاش ذكي
            if existing_chapters_count >= target_total and target_total > 0:
                print(f"⚡ [{idx}/{len(freshly_scraped)}] متطابق ومكتمل: {details['title']} ({target_total} فصل)")
                continue

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(freshly_scraped)}] تم التحديث: {details['title']} ({len(details['chapters'])} فصل)")
            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {slug}: {e}")

    # ================== 4. الدمج الذكي للكتالوج ==================
    fresh_ids = {x["id"] for x in freshly_scraped}
    remaining_old = [x for x in old_catalog if x.get("id") not in fresh_ids]
    final_merged_catalog = freshly_scraped + remaining_old

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    print(f"\n💾 تم حفظ الكتالوج المدمج: {len(final_merged_catalog)} عمل (الأحدث في الصدارة).")

    # تحديث ملف الإشعارات العام
    if new_releases:
        update_global_new_releases(new_releases)

    print("🎉 اكتملت المزامنة التراكمية لمصدر Team X بنجاح تام!")

def auto_push_to_github():
    print("\n📤 فحص ورفع تحديثات Team X إلى GitHub...")
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
        commit_msg = f"Incremental sync: TeamX & New Releases ({time.strftime('%Y-%m-%d %H:%M')})"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "pull", "--rebase"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("⚡ تم الرفع بنجاح إلى المستودع!")
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ أثناء الرفع لـ Git: {e}")

if __name__ == "__main__":
    sync_teamx()
    auto_push_to_github()
