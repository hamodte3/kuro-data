import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://kolnovel.com"
DATA_DIR = os.path.join("data", "kolnovel")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

DETAILS_SYNC_LIMIT = 20   # تجهيز أحدث 20 رواية طرأ عليها تحديث
MAX_DELTA_PAGES = 5       # فحص أول 5 صفحات فقط كل ساعة بدلاً من 1000

os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Referer": f"{BASE_URL}/",
    "Connection": "keep-alive"
}

def get_session():
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = f"{BASE_URL}{url}" if url.startswith("/") else f"{BASE_URL}/{url}"
    return url.replace("http://", "https://").rstrip("/")

def clean_title_text(raw: str) -> str:
    text = re.sub(r"<[^>]*>", "", raw)
    text = (
        text.replace("&amp;", "&")
        .replace("&#8211;", "-")
        .replace("&quot;", '"')
        .replace("&#8217;", "'")
        .replace("&#8216;", "'")
    )
    text = re.sub(r"[*_%@^<>\[\]]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def format_rating(raw_rating: str) -> str:
    clean = raw_rating.replace("★", "").replace("–", "").replace("-", "").strip()
    try:
        val = float(clean)
        return f"{val:.1f}" if val > 0 else ""
    except ValueError:
        return ""

def format_status(raw_status: str) -> str:
    s = raw_status.strip().lower()
    if any(k in s for k in ["ongoing", "مستمر", "مستمرة"]): return "مستمر"
    if any(k in s for k in ["completed", "مكتمل", "مكتملة"]): return "مكتمل"
    if any(k in s for k in ["hiatus", "متوقف"]): return "متوقف مؤقتاً"
    return "مستمر"

def extract_chapters_kolnovel(soup: BeautifulSoup) -> dict:
    chapters_map = {}
    chapter_num_pattern = re.compile(r"الفصل\s*(\d+(?:\.\d+)?)")
    any_number_pattern = re.compile(r"\d+(?:\.\d+)?")

    for li in soup.select(".eplister ul li"):
        a_node = li.select_one("a:not(.dlpdf):not([href*='/pdf/'])")
        if not a_node:
            continue

        raw_href = a_node.get("href", "").strip()
        if not raw_href or "/pdf/" in raw_href:
            continue

        ch_url = normalize_url(raw_href)
        if ch_url in chapters_map:
            continue

        raw_num = a_node.select_one(".epl-num")
        raw_num_text = raw_num.text.strip() if raw_num else ""
        raw_title = a_node.select_one(".epl-title")
        raw_title_text = raw_title.text.strip() if raw_title else ""

        ch_match = chapter_num_pattern.search(raw_num_text)
        if ch_match:
            clean_num = ch_match.group(1)
        else:
            all_nums = any_number_pattern.findall(raw_num_text)
            clean_num = all_nums[-1] if all_nums else ""

        if clean_num:
            try:
                v = float(clean_num)
                formatted_num = str(int(v)) if v.is_integer() else str(v)
            except ValueError:
                formatted_num = clean_num
        else:
            formatted_num = ""

        sub_title = re.sub(r"^الفصل\s*\d+[:\s-]*", "", raw_title_text).strip()

        if formatted_num and sub_title:
            display_name = f"الفصل {formatted_num}: {sub_title}"
        elif formatted_num:
            display_name = f"الفصل {formatted_num}"
        elif raw_title_text:
            display_name = raw_title_text
        else:
            display_name = "فصل"

        chapters_map[ch_url] = {"name": clean_title_text(display_name)}

    return chapters_map

def scrape_novel_details_kolnovel(session, novel_url: str):
    clean_url = normalize_url(novel_url)
    cache_url = f"{clean_url}?_t={int(time.time())}"
    custom_headers = {
        **HEADERS,
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache"
    }

    res = session.get(cache_url, headers=custom_headers, timeout=25)
    soup = BeautifulSoup(res.text, "html.parser")

    title_el = soup.select_one("h1.entry-title, h1[itemprop=name]")
    raw_title = title_el.text.strip() if title_el else "بدون عنوان"
    title = clean_title_text(raw_title)

    img_node = soup.select_one(".sertothumb img, .sertocover-col img")
    cover = img_node.get("src", "").strip() if img_node else ""
    cover_url = normalize_url(cover) if cover else ""

    status_el = soup.select_one(".sertostat span")
    status = format_status(status_el.text.strip() if status_el else "مستمرة")

    desc_el = soup.select_one(".sersys.entry-content, .sersysn .sersys")
    description = desc_el.text.strip() if desc_el else "لا يوجد وصف."

    genres = [a.text.strip().lstrip("#").strip() for a in soup.select(".sertogenre a") if a.text.strip()]

    rate_el = soup.select_one("#kol-series-rating .custom-rating-value, .numscore")
    raw_rating = rate_el.text.split("/")[0].strip() if rate_el else ""
    rating = format_rating(raw_rating)

    fav_el = soup.select_one(".kol-library-btn__count, .bookmark-count")
    fav_match = re.search(r"\d+", fav_el.text) if fav_el else None
    favorites = fav_match.group(0) if fav_match else ""

    update_el = soup.select_one(".epl-date, .updated")
    last_update = update_el.text.strip() if update_el else ""

    chapters_map = extract_chapters_kolnovel(soup)
    slug = clean_url.rstrip("/").split("/")[-1]

    return {
        "id": slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": "رواية",
        "status": status,
        "last_update": last_update,
        "rating": rating,
        "favorites": favorites,
        "genres": genres,
        "is_novel": True,
        "chapters": chapters_map
    }

def load_existing_catalog() -> dict:
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["id"]: item for item in data if "id" in item}
    except Exception as e:
        print(f"خطأ أثناء قراءة الفهرس القديم: {e}")
        return {}

def sync_kolnovel_fast():
    session = get_session()
    catalog_dict = load_existing_catalog()
    recent_targets = []

    print(f"جاري مراقبة التحديثات من أول {MAX_DELTA_PAGES} صفحات...")

    for page in range(1, MAX_DELTA_PAGES + 1):
        url = f"{BASE_URL}/series/?order=update" if page == 1 else f"{BASE_URL}/series/?page={page}&order=update"
        try:
            res = session.get(url, timeout=20)
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("div.listupd article.maindet, div.listupd article.bs")

            if not cards:
                break

            for card in cards:
                link_node = card.select_one("h2 a, .mdthumb a, .bsx a")
                if not link_node:
                    continue

                novel_url = normalize_url(link_node.get("href", ""))
                slug = novel_url.rstrip("/").split("/")[-1]

                title_el = card.select_one("h2 a, h2, .ntitle")
                raw_title = title_el.text.strip() if title_el else link_node.get("title", "").strip()
                title = clean_title_text(raw_title)
                if not title:
                    continue

                img_node = card.select_one("img.ts-post-image, img")
                raw_cover = ""
                if img_node:
                    raw_cover = img_node.get("src", "").strip()
                    if not raw_cover or "data:image" in raw_cover:
                        raw_cover = img_node.get("data-src", "").strip()
                cover_url = normalize_url(raw_cover) if raw_cover else ""

                score_el = card.select_one(".mdminf, .numscore")
                raw_score = re.sub(r"[^0-9.]", "", score_el.text).strip() if score_el else ""
                rating = format_rating(raw_score)

                novel_entry = {
                    "id": slug,
                    "title": title,
                    "url": novel_url,
                    "cover_url": cover_url,
                    "type": "رواية",
                    "is_novel": True,
                    "rating": rating
                }

                # تحديث ورفع الرواية إلى رأس القائمة
                if slug in catalog_dict:
                    catalog_dict[slug].update(novel_entry)
                    item_ref = catalog_dict.pop(slug)
                    catalog_dict = {slug: item_ref, **catalog_dict}
                else:
                    catalog_dict = {slug: novel_entry, **catalog_dict}

                if not any(t["id"] == slug for t in recent_targets):
                    recent_targets.append(catalog_dict[slug])

            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ أثناء فحص صفحة {page}: {e}")
            break

    # تحديث تفاصيل الفصول للروايات الحديثة فقط
    sync_targets = recent_targets[:DETAILS_SYNC_LIMIT]
    for index, item in enumerate(sync_targets, 1):
        slug = item["id"]
        novel_url = item["url"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")

        try:
            details = scrape_novel_details_kolnovel(session, novel_url)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["total_chapters"] = len(details["chapters"])
            print(f"✓ [{index}/{len(sync_targets)}] تم تحديث الرواية: {slug} ({len(details['chapters'])} فصل)")
            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء معالجة تفاصيل {slug}: {e}")

    # حفظ الفهرس التراكمي الشامل بدون فقدان أي عمل سابق
    full_catalog = list(catalog_dict.values())
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(full_catalog, f, ensure_ascii=False, indent=2)

    print(f"\n⚡ اكتملت مزامنة ملوك الروايات التراكمية! إجمالي الأعمال في الفهرس: {len(full_catalog)}")

if __name__ == "__main__":
    sync_kolnovel_fast()
