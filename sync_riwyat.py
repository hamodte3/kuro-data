import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://cenele.com"
DATA_DIR = os.path.join("data", "riwyat")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

DETAILS_SYNC_LIMIT = 2000    # فحص وتجهيز فصول أحدث 20 رواية نشطة
MAX_DELTA_PAGES = 3000        # فحص أول 3 صفحات فقط كل ساعة لمراقبة الجديد

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Referer": f"{BASE_URL}/cont/",
    "Connection": "keep-alive"
}

def get_session():
    return requests.Session(impersonate="chrome124", headers=HEADERS)

def normalize_url(raw_url: str) -> str:
    if not raw_url: return ""
    u = raw_url.strip()
    if u.startswith("//"): return f"https:{u}"
    if not u.startswith("http://") and not u.startswith("https://"):
        u = f"{BASE_URL}{u}" if u.startswith("/") else f"{BASE_URL}/{u}"
    return u.replace("http://", "https://")

def load_existing_catalog() -> dict:
    """تحميل الأرشيف القديم لمنع مسح أو تصفير أي رواية سابقة"""
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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لفضاء الروايات في {GLOBAL_NEW_FILE}")

def scrape_riwyat_details(session, slug: str, manga_url: str) -> dict:
    cache_url = f"{manga_url}?_t={int(time.time())}"
    custom_headers = {
        **HEADERS,
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache"
    }

    res = session.get(cache_url, headers=custom_headers, timeout=25)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code}")

    soup = BeautifulSoup(res.text, "html.parser")

    title_node = soup.select_one("h1.nhv-novel-title") or soup.select_one("h1")
    title = title_node.get_text(strip=True) if title_node else slug

    cover_node = soup.select_one(".nhv-novel-cover img") or soup.select_one("meta[property='og:image']")
    cover_url = normalize_url(cover_node.get("src", "") if cover_node and cover_node.name == "img" else (cover_node.get("content", "") if cover_node else ""))

    desc_node = soup.select_one(".nhv-novel-synopsis")
    description = desc_node.get_text("\n", strip=True) if desc_node else "لا يوجد وصف"

    rating_node = soup.select_one(".nhv-simple-rating__avg")
    rating = rating_node.get_text(strip=True) if rating_node else ""

    # استخراج التصنيفات الحقيقية حصراً بدون نصوص فارغة
    genres = [a.get_text(strip=True) for a in soup.select(".nhv-novel-genres a") if a.get_text(strip=True)]

    # استخراج معرف الرواية الداخلي (Post ID)
    post_id = None
    post_id_elem = soup.select_one("[data-post-id], [data-manga-id], input#wp-manga-current-chap, link[rel='shortlink']")
    if post_id_elem:
        post_id = post_id_elem.get("data-post-id") or post_id_elem.get("data-manga-id")
        if not post_id and post_id_elem.get("href"):
            match = re.search(r"p=(\d+)", post_id_elem.get("href", ""))
            if match: post_id = match.group(1)

    chapters_map = {}
    chapter_num_pattern = re.compile(r"الفصل\s*(\d+(?:\.\d+)?)")
    any_number_pattern = re.compile(r"\d+(?:\.\d+)?")

    # 1. طلب فصول Madara عبر AJAX
    ajax_chapters_url = f"{BASE_URL}/cont/{slug}/ajax/chapters/?_t={int(time.time())}"
    ch_res = session.post(ajax_chapters_url, headers={"Referer": manga_url, "X-Requested-With": "XMLHttpRequest"}, timeout=25)
    ch_soup = BeautifulSoup(ch_res.text, "html.parser") if ch_res.status_code == 200 else None

    # 2. طلب بديل عبر admin-ajax.php
    if (not ch_soup or not ch_soup.select("li.wp-manga-chapter a")) and post_id:
        admin_ajax_url = f"{BASE_URL}/wp-admin/admin-ajax.php"
        ch_res_admin = session.post(admin_ajax_url, data={"action": "manga_get_chapters", "manga": post_id}, headers={"Referer": manga_url}, timeout=25)
        if ch_res_admin.status_code == 200:
            ch_soup = BeautifulSoup(ch_res_admin.text, "html.parser")

    final_soup = ch_soup if (ch_soup and ch_soup.select("li.wp-manga-chapter a")) else soup
    ch_elements = final_soup.select("li.wp-manga-chapter a, ul.nhv-novel-chapters-list li a")

    for a in ch_elements:
        href = normalize_url(a.get("href", "")).rstrip("/")
        if not href or href.endswith("/cont") or href == manga_url.rstrip("/"):
            continue

        raw_text = a.get_text(strip=True)
        ch_match = chapter_num_pattern.search(raw_text)
        if ch_match:
            clean_num = ch_match.group(1)
        else:
            all_nums = any_number_pattern.findall(raw_text)
            clean_num = all_nums[-1] if all_nums else raw_text

        if href not in chapters_map:
            chapters_map[href] = {
                "name": clean_num,
                "images": []
            }

    return {
        "id": slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": "رواية",
        "status": "مستمر",
        "last_update": "",
        "rating": rating,
        "favorites": "",
        "genres": genres,
        "is_novel": True,
        "chapters": chapters_map
    }

def sync_riwyat():
    session = get_session()
    print(f"🚀 بدء المزامنة الخاطفة لفضاء الروايات (فحص أول {MAX_DELTA_PAGES} صفحات)...")

    catalog_dict = load_existing_catalog()
    ordered_recent_slugs = []

    # 1. سحب أول 3 صفحات مع الترتيب بالأحدث
    for page in range(1, MAX_DELTA_PAGES + 1):
        url = f"{BASE_URL}/cont/page/{page}/?m_orderby=latest" if page > 1 else f"{BASE_URL}/cont/?m_orderby=latest"
        res = session.get(url, timeout=25)
        if res.status_code != 200:
            print(f"توقف الفهرس عند صفحة {page}: رمز {res.status_code}")
            break

        soup = BeautifulSoup(res.text, "html.parser")
        cards = soup.select("article.nhv-library-card")
        if not cards:
            break

        new_in_page = 0
        for card in cards:
            a_tag = card.select_one("h2.nhv-library-card__title a")
            if not a_tag: continue
            href = normalize_url(a_tag.get("href", "")).rstrip("/")
            slug = href.split("/")[-1]

            title = a_tag.get_text(strip=True) or slug
            img = card.select_one(".nhv-library-card__cover img")
            cover = normalize_url(img.get("src", "") if img else "")

            # التحديث الآمن مع الحفاظ على الأرشيف القديم
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
                    "type": "رواية",
                    "status": "مستمر",
                    "rating": "",
                    "total_chapters": 0,
                    "genres": []  # تهيئة حقل التصنيفات دائماً
                }

            if slug not in ordered_recent_slugs:
                ordered_recent_slugs.append(slug)

            new_in_page += 1

        print(f"فضاء الروايات [صفحة {page}]: رصد {new_in_page} عمل محدث.")
        time.sleep(0.3)

    # 2. تحديث فصول أحدث 20 رواية وتوثيق التصنيفات
    targets_slugs = ordered_recent_slugs[:DETAILS_SYNC_LIMIT]
    new_releases = []

    print(f"\n⚡ تحديث فصول وتصنيفات أحدث {len(targets_slugs)} رواية...")

    for idx, slug in enumerate(targets_slugs, 1):
        item = catalog_dict[slug]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")

        existing_data = {}
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            except Exception:
                pass

        try:
            new_details = scrape_riwyat_details(session, slug, item["url"])

            # دمج الفصول التراكمي لضمان عدم ضياع أي فصل سابق
            merged_chapters = existing_data.get("chapters", {})
            merged_chapters.update(new_details["chapters"])
            new_details["chapters"] = merged_chapters
            
            total_chapters = len(merged_chapters)
            item["total_chapters"] = total_chapters
            item["rating"] = new_details["rating"]
            
            # حفظ التصنيفات الحقيقية في الكاتلوج
            item["genres"] = new_details.get("genres", [])
            
            prev_chaps = len(existing_data.get("chapters", {}))

            # كشف التحديث لتوليد التنبيهات
            if total_chapters > prev_chaps and total_chapters > 0:
                new_releases.append({
                    "id": slug,
                    "title": item["title"],
                    "chapter": f"الفصل {total_chapters}" if prev_chaps > 0 else "رواية جديدة",
                    "type": "رواية",
                    "cover_url": item["cover_url"]
                })

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(new_details, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(targets_slugs)}] تم التحديث: {slug} ({total_chapters} فصل) - تصنيفات: {item['genres']}")
            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {slug}: {e}")

    # 3. حفظ الفهرس الشامل (الأحدث أولاً + بقية الأرشيف)
    seen_slugs = set(ordered_recent_slugs)
    final_merged_catalog = [catalog_dict[s] for s in ordered_recent_slugs] + [
        item for s, item in catalog_dict.items() if s not in seen_slugs
    ]

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    # 4. تحديث ملف الإشعارات العام
    if new_releases:
        update_global_new_releases(new_releases)

    print(f"\n💾 تم حفظ الفهرس العام المدمج: {len(final_merged_catalog)} رواية (بيانات حقيقية 100%).")
    print("🎉 اكتملت المزامنة التراكمية لفضاء الروايات بنجاح تام!")

if __name__ == "__main__":
    sync_riwyat()
