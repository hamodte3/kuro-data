import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://mangalik.net"
DATA_DIR = os.path.join("data", "mangalik")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

DETAILS_SYNC_LIMIT = 20    # عدد الأعمال المطلوب تجهيز فصولها
MAX_DELTA_PAGES = 5        # فحص أول 5 صفحات فقط كل ساعة لمراقبة الجديد

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

def get_session():
    # محاكاة كروم 124 مع إعدادات هيدرز طبيعية للمتصفح لتجاوز الحماية
    s = requests.Session(impersonate="chrome124")
    s.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Referer": f"{BASE_URL}/",
        "Connection": "keep-alive"
    })
    return s

def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = f"{BASE_URL}{url}" if url.startswith("/") else f"{BASE_URL}/{url}"
    return url.replace("http://", "https://").rstrip("/")

def format_type(raw_type: str) -> str:
    t = raw_type.strip().lower()
    if any(k in t for k in ["manhwa", "مانهوا"]): return "مانهوا"
    if any(k in t for k in ["manhua", "مانها"]): return "مانها"
    if any(k in t for k in ["webtoon", "ويبتون", "ويب تون"]): return "ويب تون"
    if any(k in t for k in ["novel", "رواية"]): return "رواية"
    if any(k in t for k in ["comic", "كوميك"]): return "كوميك"
    if any(k in t for k in ["manga", "مانجا", "مانغا"]): return "مانغا"
    return raw_type if raw_type else "مانغا"

def format_status(raw_status: str) -> str:
    s = raw_status.strip().lower()
    if any(k in s for k in ["ongoing", "مستمر", "مستمرة"]): return "مستمر"
    if any(k in s for k in ["completed", "مكتمل", "مكتملة"]): return "مكتمل"
    if any(k in s for k in ["hiatus", "متوقف"]): return "متوقف مؤقتاً"
    return "مستمر"

def format_rating(raw_rating: str) -> str:
    clean = raw_rating.replace("★", "").replace("–", "").replace("-", "").strip()
    try:
        val = float(clean)
        return f"{val:.1f}" if val > 0 else ""
    except ValueError:
        return ""

def load_existing_catalog() -> dict:
    """تحميل الأرشيف القديم لمنع مسح أي عمل سابق"""
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["id"]: item for item in data if "id" in item}
    except Exception as e:
        print(f"خطأ أثناء قراءة كاتلوج مانجا ليك القديم: {e}")
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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لمانجا ليك في {GLOBAL_NEW_FILE}")

def scrape_manga_details_mangalik(session, manga_url: str):
    clean_url = normalize_url(manga_url)
    res = session.get(f"{clean_url}/?_t={int(time.time())}", headers={"Referer": f"{BASE_URL}/"}, timeout=20)
    
    if res.status_code != 200:
        print(f"⚠️ فشل فتح العمل {clean_url} | كود: {res.status_code}")
        return None

    soup = BeautifulSoup(res.text, "html.parser")

    # 1. العنوان والغلاف
    title_el = soup.select_one("div.post-title h1, h1")
    title = title_el.text.strip() if title_el else "بدون عنوان"

    img_el = soup.select_one("div.summary_image img")
    cover_url = ""
    if img_el:
        cover_url = img_el.get("src", "").strip()
        if not cover_url or "data:image" in cover_url:
            cover_url = img_el.get("data-src", "").strip() or img_el.get("data-lazy-src", "").strip()
    if not cover_url:
        meta_img = soup.select_one("meta[property='og:image']")
        cover_url = meta_img.get("content", "").strip() if meta_img else ""
    if cover_url:
        cover_url = normalize_url(cover_url)

    # 2. القصة والتقييم والمفضلة
    desc_el = soup.select_one("div.description-summary .summary__content, div.manga-excerpt")
    description = desc_el.text.replace("<!-- -->", "").strip() if desc_el else "لا يوجد وصف"

    rate_el = soup.select_one("#averagerate, div.post-total-rating span.score")
    rating = format_rating(rate_el.text if rate_el else "")

    fav_el = soup.select_one("div.add-bookmark .action_detail span")
    favorites_text = fav_el.text if fav_el else ""
    fav_match = re.search(r"\d+", favorites_text)
    favorites = fav_match.group(0) if fav_match else ""

    # 3. التصنيفات والنوع
    genres = [a.text.strip() for a in soup.select("div.genres-content a") if a.text.strip()]
    badges = soup.select_one("span.manga-title-badges, div.genres-content")
    badges_text = badges.text if badges else ""

    raw_type_el = soup.find(lambda tag: tag.name in ["div", "span"] and "النوع" in tag.text)
    raw_type = raw_type_el.text if raw_type_el else ""
    is_novel = any("رواية" in x for x in [badges_text, raw_type, title])
    manga_type = format_type("رواية" if is_novel else (raw_type or (genres[0] if genres else badges_text)))

    # 4. الحالة
    status_el = soup.find(lambda tag: tag.name in ["div", "span"] and "الحالة" in tag.text)
    status_text = ""
    if status_el:
        parent_status = status_el.find_parent("div", class_="post-content_item")
        val_el = parent_status.select_one(".summary-content") if parent_status else None
        status_text = val_el.text if val_el else status_el.text
    status = format_status(status_text)

    # 5. استخراج الفصول
    chapters_map = {}
    chapter_links = soup.select("ul.main.version-chap li.wp-manga-chapter a, div.listing-chapters_wrap li a")
    
    for a in chapter_links:
        href = a.get("href", "").strip()
        if not href or href == "#":
            continue

        full_url = normalize_url(href)
        if full_url in chapters_map:
            continue

        raw_title = a.text.strip()
        num_match = re.search(r"\d+(\.\d+)?", raw_title)
        if num_match:
            val = float(num_match.group(0))
            clean_name = str(int(val)) if val.is_integer() else str(val)
        else:
            clean_name = raw_title if raw_title else "0"

        chapters_map[full_url] = {"name": clean_name}

    slug = clean_url.rstrip("/").split("/")[-1]

    return {
        "id": slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": manga_type,
        "status": status,
        "rating": rating,
        "favorites": favorites,
        "genres": genres,
        "is_novel": is_novel,
        "chapters": chapters_map
    }

def sync_mangalik_fast():
    session = get_session()
    print(f"بدء المزامنة الخاطفة لمانجا ليك (فحص أول {MAX_DELTA_PAGES} صفحات)...")

    catalog_dict = load_existing_catalog()
    ordered_recent_slugs = []
    page = 1
    prev_url = f"{BASE_URL}/"

    # 1. سحب أول 5 صفحات فقط لحفظ الترتيب الزمني للأحدث
    while page <= MAX_DELTA_PAGES:
        url = f"{BASE_URL}/" if page == 1 else f"{BASE_URL}/page/{page}/"
        try:
            res = session.get(url, headers={"Referer": prev_url}, timeout=20)
            print(f"📡 فحص صفحة {page} | كود الاستجابة: {res.status_code}")

            if res.status_code != 200:
                print(f"⚠️ توقف عند صفحة {page} بسبب كود: {res.status_code}")
                break

            prev_url = url
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("div.page-item-detail.manga")

            if not cards:
                break

            new_in_page = 0
            for card in cards:
                title_links = card.select("h3.h5 a, .post-title a")
                if not title_links:
                    continue
                link = title_links[-1]
                title = link.text.strip()
                manga_url = normalize_url(link.get("href", ""))
                slug = manga_url.split("/")[-1]

                img = card.select_one("div.item-thumb img")
                cover = ""
                if img:
                    cover = img.get("src", "").strip()
                    if not cover or "data:image" in cover:
                        cover = img.get("data-src", "").strip() or img.get("data-lazy-src", "").strip()
                if cover:
                    cover = normalize_url(cover)

                score_el = card.select_one("span.score")
                rating = format_rating(score_el.text if score_el else "")

                badges = card.select_one("span.manga-title-badges")
                badges_text = badges.text if badges else ""
                is_novel = "رواية" in badges_text or "رواية" in title

                # التحديث الآمن مع الحفاظ على الفصول السابقة إن وجدت
                if slug in catalog_dict:
                    catalog_dict[slug]["title"] = title
                    catalog_dict[slug]["url"] = manga_url
                    if cover:
                        catalog_dict[slug]["cover_url"] = cover
                    if rating:
                        catalog_dict[slug]["rating"] = rating
                else:
                    catalog_dict[slug] = {
                        "id": slug,
                        "title": title,
                        "url": manga_url,
                        "cover_url": cover,
                        "type": "رواية" if is_novel else format_type(badges_text),
                        "rating": rating,
                        "total_chapters": 0
                    }

                if slug not in ordered_recent_slugs:
                    ordered_recent_slugs.append(slug)

                new_in_page += 1

            print(f"✓ تم استخراج {new_in_page} عمل من صفحة {page}")
            if new_in_page == 0:
                break

            page += 1
            time.sleep(1.2)  # حماية لتفادي كشف السكرابر
        except Exception as e:
            print(f"خطأ أثناء سحب صفحة {page}: {e}")
            break

    # 2. تجهيز فصول أحدث 20 عملاً ورصد الإشعارات
    targets_slugs = ordered_recent_slugs[:DETAILS_SYNC_LIMIT]
    new_releases = []

    for index, slug in enumerate(targets_slugs, 1):
        item = catalog_dict[slug]
        manga_url = item["url"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        prev_chaps = item.get("total_chapters", 0)

        try:
            details = scrape_manga_details_mangalik(session, manga_url)
            if details:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(details, f, ensure_ascii=False, indent=2)

                current_chaps = len(details["chapters"])
                item["status"] = details["status"]
                item["rating"] = details["rating"]
                item["type"] = details["type"]
                item["total_chapters"] = current_chaps
                print(f"✓ [{index}/{len(targets_slugs)}] تم تحديث مانجا ليك: {details['title']} ({current_chaps} فصل)")

                # فحص التحديث لإرسال التنبيه
                if current_chaps > prev_chaps and current_chaps > 0:
                    new_releases.append({
                        "id": slug,
                        "title": item["title"],
                        "chapter": f"الفصل {current_chaps}" if prev_chaps > 0 else "عمل جديد",
                        "type": item.get("type", "مانغا"),
                        "cover_url": item.get("cover_url", "")
                    })

            time.sleep(1.2)
        except Exception as e:
            print(f"خطأ أثناء معالجة {slug}: {e}")

    # 3. دمج الفهرس: الأحدث في البداية + بقية الأرشيف القديم
    seen_slugs = set(ordered_recent_slugs)
    final_merged_catalog = [catalog_dict[s] for s in ordered_recent_slugs] + [
        item for s, item in catalog_dict.items() if s not in seen_slugs
    ]

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    # 4. تحديث الإشعارات المشتركة
    if new_releases:
        update_global_new_releases(new_releases)

    print(f"\n⚡ اكتملت مزامنة مانجا ليك الذكية! إجمالي الأعمال المحفوظة: {len(final_merged_catalog)}")

if __name__ == "__main__":
    sync_mangalik_fast()
