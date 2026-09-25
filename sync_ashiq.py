import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://3asq.online"
DATA_DIR = os.path.join("data", "ashiq")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

DETAILS_SYNC_LIMIT = 20    # فحص تفاصيل وفصول أحدث 20 عملاً
MAX_PAGES_SAFETY = 1000

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{BASE_URL}/",
    "Connection": "keep-alive"
}

def get_session():
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = f"{BASE_URL}{url}" if url.startswith("/") else f"{BASE_URL}/{url}"
    url = url.replace("3asq.org", "3asq.online")
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

    # دمج التحديثات وحذف المكرر لنفس العمل ورقم الفصل
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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد في {GLOBAL_NEW_FILE}")

def extract_chapters_ashiq(session, manga_url: str, post_id: str = "") -> dict:
    clean_url = normalize_url(manga_url)
    elements = []
    
    try:
        ajax_url = f"{clean_url}/ajax/chapters/"
        res = session.post(ajax_url, headers={"Referer": clean_url}, timeout=20)
        if res.status_code == 200 and "wp-manga-chapter" in res.text:
            soup = BeautifulSoup(res.text, "html.parser")
            elements = soup.select("li.wp-manga-chapter a, ul.main.version-chap li a")
    except Exception:
        elements = []

    if not elements and post_id:
        try:
            admin_ajax = f"{BASE_URL}/wp-admin/admin-ajax.php"
            data = {"action": "manga_get_chapters", "manga": post_id}
            res = session.post(admin_ajax, data=data, headers={"Referer": clean_url}, timeout=20)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                elements = soup.select("li.wp-manga-chapter a, ul.main.version-chap li a")
        except Exception:
            elements = []

    chapters_map = {}
    for a in elements:
        href = a.get("href", "").strip()
        if not href or href.endswith("#"):
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

    return chapters_map

def scrape_manga_details_ashiq(session, manga_url: str):
    clean_url = normalize_url(manga_url)
    res = session.get(clean_url, timeout=20)
    soup = BeautifulSoup(res.text, "html.parser")

    title_el = soup.select_one("div.post-title h1, h1")
    title = title_el.text.strip() if title_el else "بدون عنوان"

    img_el = soup.select_one("div.summary_image img")
    cover_url = ""
    if img_el:
        cover_url = (img_el.get("src") or img_el.get("data-src") or "").strip()
    if not cover_url:
        meta_img = soup.select_one("meta[property='og:image']")
        cover_url = meta_img.get("content", "").strip() if meta_img else ""
    cover_url = normalize_url(cover_url) if cover_url else ""

    desc_paragraphs = [
        p.text.strip()
        for p in soup.select("div.manga-excerpt p, div.summary__content p")
        if p.text.strip()
    ]
    description = "\n\n".join(desc_paragraphs) if desc_paragraphs else "لا يوجد وصف"

    rate_el = soup.select_one("#averagerate, span.total_votes, div.post-total-rating span.score")
    rating = format_rating(rate_el.text if rate_el else "")

    fav_el = soup.select_one("div.add-bookmark .action_detail span")
    favorites_text = fav_el.text if fav_el else ""
    fav_match = re.search(r"\d+", favorites_text)
    favorites = fav_match.group(0) if fav_match else ""

    genres = [a.text.strip() for a in soup.select("div.genres-content a") if a.text.strip()]

    raw_type = ""
    status = "مستمر"
    for item in soup.select("div.post-content_item"):
        heading = item.select_one("div.summary-heading")
        content = item.select_one("div.summary-content")
        if not heading or not content:
            continue
        
        h_text = heading.text.strip()
        if "النوع" in h_text:
            raw_type = content.text.strip()
        elif "الحالة" in h_text:
            status = format_status(content.text.strip())

    is_novel = any("رواية" in x for x in [raw_type, title] + genres)
    manga_type = format_type("رواية" if is_novel else (raw_type or (genres[0] if genres else "مانغا")))

    holder = soup.select_one("#manga-chapters-holder")
    post_id = holder.get("data-id", "") if holder else ""
    if not post_id:
        id_input = soup.select_one("input.rating-post-id, input#comment_post_ID")
        post_id = id_input.get("value", "") if id_input else ""

    chapters_map = extract_chapters_ashiq(session, clean_url, post_id)
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

def fetch_ashiq_catalog(session) -> list:
    print("جاري سحب الفهرس العام لموقع العاشق...")
    catalog = []
    page = 1

    while page <= MAX_PAGES_SAFETY:
        url = f"{BASE_URL}/manga/" if page == 1 else f"{BASE_URL}/manga/page/{page}/"
        try:
            res = session.get(url, timeout=20)
            if res.status_code != 200:
                break

            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("div.page-item-detail.manga")

            if not cards:
                break

            new_in_page = 0
            for card in cards:
                link = card.select_one("div.post-title h3 a[href*='/manga/'], h3.h5 a[href*='/manga/']")
                if not link:
                    continue

                title = link.text.strip()
                manga_url = normalize_url(link.get("href", ""))
                slug = manga_url.split("/")[-1]

                if not any(item["id"] == slug for item in catalog):
                    img = card.select_one("div.item-thumb img")
                    cover = ""
                    if img:
                        cover = (img.get("src") or img.get("data-src") or "").strip()
                    if cover:
                        cover = normalize_url(cover)

                    score_el = card.select_one("span.score, span.total_votes")
                    rating = format_rating(score_el.text if score_el else "")

                    badges = card.select_one("span.manga-title-badges")
                    badges_text = badges.text if badges else ""
                    is_novel = "رواية" in badges_text or "رواية" in title

                    catalog.append({
                        "id": slug,
                        "title": title,
                        "url": manga_url,
                        "cover_url": cover,
                        "type": "رواية" if is_novel else format_type(badges_text),
                        "rating": rating
                    })
                    new_in_page += 1

            if new_in_page == 0:
                break

            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء سحب صفحة {page}: {e}")
            break

    print(f"تم الانتهاء من فهرسة {len(catalog)} عمل في العاشق.")
    return catalog

def sync_ashiq_fast():
    session = get_session()
    freshly_scraped = fetch_ashiq_catalog(session)

    if not freshly_scraped:
        print("⚠️ لم يتم العثور على أي أعمال في الفهرس.")
        return

    top_targets = freshly_scraped[:DETAILS_SYNC_LIMIT]

    for index, item in enumerate(top_targets, 1):
        slug = item["id"]
        manga_url = item["url"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")

        try:
            details = scrape_manga_details_ashiq(session, manga_url)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            item["status"] = details["status"]
            item["total_chapters"] = len(details["chapters"])
            print(f"✓ [{index}/{len(top_targets)}] تم تجهيز العاشق: {slug} ({len(details['chapters'])} فصل)")
            time.sleep(0.4)
        except Exception as e:
            print(f"خطأ أثناء معالجة {slug}: {e}")

    # ================== الدمج الذكي وتوليد الإشعارات ==================
    old_catalog = []
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE, "r", encoding="utf-8") as f:
                old_catalog = json.load(f)
        except Exception:
            old_catalog = []

    old_map = {item["id"]: item for item in old_catalog}
    new_releases = []

    # كشف الأعمال والفصول الجديدة
    for item in freshly_scraped:
        m_id = item["id"]
        current_chaps = item.get("total_chapters", 0)
        prev_chaps = old_map.get(m_id, {}).get("total_chapters", 0)

        if m_id not in old_map:
            new_releases.append({
                "id": m_id,
                "title": item["title"],
                "chapter": f"الفصل {current_chaps}" if current_chaps > 0 else "عمل جديد",
                "type": item.get("type", "مانغا"),
                "cover_url": item.get("cover_url", "")
            })
        elif current_chaps > prev_chaps and current_chaps > 0:
            new_releases.append({
                "id": m_id,
                "title": item["title"],
                "chapter": f"الفصل {current_chaps}",
                "type": item.get("type", "مانغا"),
                "cover_url": item.get("cover_url", "")
            })

    # دمج: الجديد دائماً في الصدارة، والقديم غير المكرر يبقى في الأسفل
    fresh_ids = {x["id"] for x in freshly_scraped}
    remaining_old = [x for x in old_catalog if x["id"] not in fresh_ids]
    final_merged_catalog = freshly_scraped + remaining_old

    # حفظ الكاتلوج المدمج النهائي
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    # تحديث إشعارات new.json
    if new_releases:
        update_global_new_releases(new_releases)

    print(f"\n⚡ اكتملت مزامنة العاشق! الكاتلوج يحتوي {len(final_merged_catalog)} عملاً محفوظاً.")

if __name__ == "__main__":
    sync_ashiq_fast()
