import json
import os
import re
import subprocess
import time
import urllib.parse
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://kolnovel.com"
DATA_DIR = os.path.join("data", "kolnovel")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

DETAILS_SYNC_LIMIT = 20    # فحص أحدث 20 رواية طرأ عليها تحديث
MAX_DELTA_PAGES = 5        # فحص أول 5 صفحات فقط دورياً

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
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
        .replace("&#8230;", "...")
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

def extract_cover_image(container) -> str:
    """استخراج رابط الغلاف بأعلى دقة ممكنة وتخطي الصور الرمزية"""
    if not container:
        return ""
    img = container if container.name == "img" else container.select_one("img")
    if not img:
        return ""

    srcset = img.get("srcset", "").strip() or img.get("data-srcset", "").strip()
    if srcset:
        candidates = [s.strip().split(" ")[0] for s in srcset.split(",") if s.strip()]
        for c in reversed(candidates):
            if c and not c.startswith("data:") and "default" not in c:
                return normalize_url(c)

    for attr in ["data-src", "data-lazy-src", "src"]:
        val = img.get(attr, "").strip()
        if val and not val.startswith("data:") and "default" not in val:
            return normalize_url(val)

    return ""

def clean_and_rename_old_files():
    if not os.path.exists(DATA_DIR):
        return

    renamed_count = 0
    for filename in os.listdir(DATA_DIR):
        if filename == "catalog.json" or not filename.endswith(".json"):
            continue

        name_without_ext = filename[:-5]
        if "%" in name_without_ext or "25" in name_without_ext:
            try:
                decoded_name = name_without_ext
                for _ in range(2):
                    if "%" in decoded_name:
                        decoded_name = urllib.parse.unquote(decoded_name)

                clean_name = clean_title_text(decoded_name)
                if clean_name and clean_name != name_without_ext:
                    old_path = os.path.join(DATA_DIR, filename)
                    new_path = os.path.join(DATA_DIR, f"{clean_name}.json")
                    if not os.path.exists(new_path):
                        os.rename(old_path, new_path)
                        renamed_count += 1
            except Exception:
                pass

    if renamed_count > 0:
        print(f"✨ تم تصحيح أسماء {renamed_count} ملف قديم.")

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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لملوك الروايات في {GLOBAL_NEW_FILE}")

def parse_and_add_chapter(node, chapters_map: dict):
    a = node if node.name == "a" else node.select_one("a")
    if not a:
        return
    raw_href = a.get("href", "").strip()
    if not raw_href or "/pdf/" in raw_href or raw_href.startswith("#"):
        return

    ch_url = normalize_url(raw_href)
    if ch_url in chapters_map:
        return

    num_node = a.select_one("[data-kol-chapter-number], .kol-chapter-number, .epl-num")
    title_node = a.select_one("[data-kol-chapter-title], .kol-chapter-title, .epl-title")

    raw_num_text = num_node.text.strip() if num_node else ""
    raw_title_text = title_node.text.strip() if title_node else a.text.strip()

    num_match = re.search(r"الفصل\s*(\d+(?:\.\d+)?)", raw_num_text or raw_title_text)
    if num_match:
        clean_num = num_match.group(1)
    else:
        all_nums = re.findall(r"\d+(?:\.\d+)?", raw_num_text or raw_title_text)
        clean_num = all_nums[-1] if all_nums else ""

    formatted_num = ""
    if clean_num:
        try:
            v = float(clean_num)
            formatted_num = str(int(v)) if v.is_integer() else str(v)
        except ValueError:
            formatted_num = clean_num

    sub_title = re.sub(r"^الفصل\s*\d+[:\s·-]*", "", raw_title_text).strip()

    if formatted_num and sub_title and sub_title != formatted_num:
        display_name = f"الفصل {formatted_num}: {sub_title}"
    elif formatted_num:
        display_name = f"الفصل {formatted_num}"
    elif raw_title_text:
        display_name = raw_title_text
    else:
        display_name = "فصل"

    chapters_map[ch_url] = {"name": clean_title_text(display_name)}

def extract_chapters_kolnovel(session, soup: BeautifulSoup, novel_url: str) -> dict:
    chapters_map = {}

    # 1. فحص العناصر إذا كانت موجودة بالصفحة الرئيسية
    for el in soup.select("ul[data-kol-chapter-volume-content] li, .eplister ul li, .kol-series-editorial__volume-scroll li"):
        parse_and_add_chapter(el, chapters_map)

    # 2. في حال كانت فارغة بالقالب الجديد، جلب صفحة الفصول المباشرة
    if not chapters_map:
        vol_details = soup.select_one(".kol-series-editorial__volume[data-kol-volume-pages]")
        total_vol_pages = 1
        if vol_details:
            try:
                total_vol_pages = int(vol_details.get("data-kol-volume-pages", 1))
            except ValueError:
                total_vol_pages = 1

        for vpage in range(1, total_vol_pages + 1):
            fallback_vurl = f"{novel_url}/?chapter_volume&chapter_page={vpage}#chapters"
            try:
                res = session.get(fallback_vurl, timeout=15)
                if res.status_code == 200:
                    vsoup = BeautifulSoup(res.text, "html.parser")
                    page_items = vsoup.select("ul[data-kol-chapter-volume-content] li, .kol-series-editorial__volume-scroll li, a[data-kol-chapter-transition-link]")
                    for node in page_items:
                        parse_and_add_chapter(node, chapters_map)
            except Exception:
                break

    # 3. محاولة الروابط المختصرة (الفصل الأول والأخير) كاحتياط
    if not chapters_map:
        for sc in soup.select(".kol-series-editorial__chapter-shortcuts a"):
            parse_and_add_chapter(sc, chapters_map)

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

    title_el = soup.select_one("h1.kol-series-editorial__title, h1.entry-title, h1[itemprop=name]")
    raw_title = title_el.text.strip() if title_el else "بدون عنوان"
    title = clean_title_text(raw_title)

    img_node = soup.select_one(".kol-series-editorial__cover, .sertothumb, .sertocover-col")
    cover_url = extract_cover_image(img_node) if img_node else ""

    status_el = soup.select_one(".kol-series-editorial__status, .sertostat span")
    status = format_status(status_el.text.strip() if status_el else "مستمرة")

    desc_el = soup.select_one(".kol-series-editorial__prose, .sersys.entry-content, .sersysn .sersys")
    if desc_el:
        desc_soup = BeautifulSoup(str(desc_el), "html.parser")
        for bad in desc_soup.select(".shola-widget, style, script, .shola-progress-wrap, .shola-modal-overlay"):
            bad.decompose()
        paragraphs = [p.text.strip() for p in desc_soup.select("p") if p.text.strip()]
        description = "\n\n".join(paragraphs) if paragraphs else desc_soup.text.strip()
    else:
        description = "لا يوجد وصف."

    genres = [a.text.strip().lstrip("#").strip() for a in soup.select(".kol-series-editorial__genres a, .sertogenre a") if a.text.strip()]

    rate_el = soup.select_one(".kol-series-archive__rating b, #kol-series-rating .custom-rating-value, .numscore")
    rating = format_rating(rate_el.text.split("/")[0].strip() if rate_el else "")

    fav_el = soup.select_one(".kol-library-btn__count, .bookmark-count")
    fav_match = re.search(r"\d+", fav_el.text) if fav_el else None
    favorites = fav_match.group(0) if fav_match else ""

    update_el = soup.select_one(".updated, .epl-date")
    last_update = update_el.text.strip() if update_el else ""

    chapters_map = extract_chapters_kolnovel(session, soup, clean_url)
    slug = title

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

def git_commit_and_push():
    """رفع تحديثات ملوك الروايات تلقائياً إلى GitHub"""
    try:
        status = subprocess.run(["git", "status", "--porcelain", "data/"], capture_output=True, text=True, check=True)
        if not status.stdout.strip():
            print("📁 لا توجد تغييرات جديدة لرفعها.")
            return

        print("\n🚀 جاري رفع التحديثات إلى مستودع GitHub...")
        subprocess.run(["git", "add", "data/"], check=True)
        subprocess.run(["git", "commit", "-m", "Auto update: kolnovel sync [skip ci]"], check=True)
        subprocess.run(["git", "pull", "--rebase"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("✅ تم رفع تحديثات ملوك الروايات بنجاح!")
    except subprocess.CalledProcessError as e:
        print(f"❌ تعذر الرفع عبر Git: {e}")
    except Exception as e:
        print(f"❌ خطأ غير متوقع أثناء الرفع: {e}")

def sync_kolnovel_fast():
    clean_and_rename_old_files()

    session = get_session()
    catalog_dict = load_existing_catalog()
    recent_targets = []

    print(f"⚡ جاري فحص أول {MAX_DELTA_PAGES} صفحات من ملوك الروايات...")

    for page in range(1, MAX_DELTA_PAGES + 1):
        url = f"{BASE_URL}/series/?order=update" if page == 1 else f"{BASE_URL}/series/page/{page}/?order=update"
        try:
            res = session.get(url, timeout=20)
            if res.status_code != 200:
                break

            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("article.kol-series-archive__item, div.listupd article.maindet, div.listupd article.bs")

            if not cards:
                break

            for card in cards:
                link_node = card.select_one("h3 a[data-kol-series-title-source], h3 a, a.kol-series-archive__cover, h2 a")
                if not link_node:
                    continue

                novel_url = normalize_url(link_node.get("href", ""))
                title_el = card.select_one("h3 a, [data-kol-series-title-source], h2 a, h2")
                raw_title = title_el.text.strip() if title_el else link_node.get("title", "").strip()
                title = clean_title_text(raw_title)
                if not title:
                    continue

                slug = title
                cover_url = extract_cover_image(card.select_one(".kol-series-archive__cover-column, .kol-ui-cover, .mdthumb, .bsx"))

                rate_el = card.select_one(".kol-series-archive__rating b, .mdminf, .numscore")
                rating = format_rating(rate_el.text.strip() if rate_el else "")

                status_el = card.select_one(".kol-series-archive__status, .sertostat")
                status = format_status(status_el.text.strip() if status_el else "مستمرة")

                genres = [s.text.strip() for s in card.select(".kol-series-archive__genres-list span") if s.text.strip() and s.text.strip() != "·"]

                if slug in catalog_dict:
                    catalog_dict[slug]["title"] = title
                    catalog_dict[slug]["url"] = novel_url
                    if cover_url:
                        catalog_dict[slug]["cover_url"] = cover_url
                    if rating:
                        catalog_dict[slug]["rating"] = rating
                    if genres:
                        catalog_dict[slug]["genres"] = genres
                    item_ref = catalog_dict.pop(slug)
                    catalog_dict = {slug: item_ref, **catalog_dict}
                else:
                    catalog_dict = {
                        slug: {
                            "id": slug,
                            "title": title,
                            "url": novel_url,
                            "cover_url": cover_url,
                            "type": "رواية",
                            "status": status,
                            "is_novel": True,
                            "rating": rating,
                            "total_chapters": 0,
                            "genres": genres
                        },
                        **catalog_dict
                    }

                if not any(t["id"] == slug for t in recent_targets):
                    recent_targets.append(catalog_dict[slug])

            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ أثناء فحص صفحة {page}: {e}")
            break

    sync_targets = recent_targets[:DETAILS_SYNC_LIMIT]
    new_releases = []

    for index, item in enumerate(sync_targets, 1):
        slug = item["id"]
        novel_url = item["url"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        prev_chaps = item.get("total_chapters", 0)

        try:
            details = scrape_novel_details_kolnovel(session, novel_url)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            current_chaps = len(details["chapters"])
            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["total_chapters"] = current_chaps
            item["genres"] = details.get("genres", [])
            if details.get("cover_url"):
                item["cover_url"] = details["cover_url"]

            print(f"✓ [{index}/{len(sync_targets)}] تم تحديث: {slug} ({current_chaps} فصل)")

            if current_chaps > prev_chaps and current_chaps > 0:
                new_releases.append({
                    "id": slug,
                    "title": item["title"],
                    "chapter": f"الفصل {current_chaps}",
                    "type": "رواية",
                    "cover_url": item.get("cover_url", "")
                })

            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء معالجة تفاصيل {slug}: {e}")

    full_catalog = list(catalog_dict.values())
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(full_catalog, f, ensure_ascii=False, indent=2)

    if new_releases:
        update_global_new_releases(new_releases)

    print(f"\n⚡ اكتملت مزامنة ملوك الروايات! إجمالي الروايات: {len(full_catalog)}")
    
    # الرفع التلقائي إلى GitHub
    git_commit_and_push()

if __name__ == "__main__":
    sync_kolnovel_fast()
