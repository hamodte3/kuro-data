import sys
import os
import json
import re
import time
import urllib.parse
import subprocess
from bs4 import BeautifulSoup

try:
    import requests
except ImportError:
    from curl_cffi import requests

from curl_cffi import requests as cffi_requests

DATA_ROOT = "data"
os.makedirs(DATA_ROOT, exist_ok=True)

# ==========================================
# دوال مساعدة عامة لإدارة الكتالوج التراكمي
# ==========================================
def load_catalog_map(catalog_path: str) -> dict:
    """تحميل الكتالوج القديم كـ Dictionary لتفادي مسح الأرشيف"""
    if not os.path.exists(catalog_path):
        return {}
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            items = json.load(f)
            return {it["id"]: it for it in items if isinstance(it, dict) and "id" in it}
    except Exception:
        return {}

def save_catalog_map(catalog_path: str, catalog_map: dict):
    """حفظ الكتالوج المدمج (القديم + الجديد والمحدث)"""
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(list(catalog_map.values()), f, ensure_ascii=False, indent=2)

def normalize_http_url(raw_url: str, base_url: str) -> str:
    if not raw_url: return ""
    u = raw_url.strip()
    if u.startswith("//"): return f"https:{u}"
    if not u.startswith("http://") and not u.startswith("https://"):
        u = f"{base_url}{u}" if u.startswith("/") else f"{base_url}/{u}"
    return u.replace("http://", "https://")


# ==========================================
# 1. مانغاتايم (MangaTime) - مانهوا / مانغا / روايات
# ==========================================
def sync_mangatime():
    BASE_URL = "https://mangatime.org"
    API_URL = f"{BASE_URL}/trpc"
    DATA_DIR = os.path.join(DATA_ROOT, "mangatime")
    CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
    MAX_PAGES = 3

    os.makedirs(DATA_DIR, exist_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "X-MT-Platform": "app",
        "Origin": "https://localhost",
        "Referer": "https://localhost/"
    }
    session = cffi_requests.Session(impersonate="chrome120", headers=headers)

    def fetch_trpc(proc, inp):
        url = f"{API_URL}/{proc}?batch=1&input={urllib.parse.quote(json.dumps(inp))}"
        try:
            r = session.get(url, timeout=20).json()
            if isinstance(r, list) and r: r = r[0]
            res = r.get("result", {}).get("data", {})
            return res.get("json", res)
        except Exception:
            return None

    def get_items(node):
        if isinstance(node, list): return node
        if isinstance(node, dict):
            for k in ["results", "items", "works", "series", "data"]:
                if isinstance(node.get(k), list): return node[k]
        return []

    def get_type_path(raw_t):
        t = (raw_t or "").lower()
        if "manhwa" in t: return "manhwa"
        if "novel" in t or "رواية" in t: return "novel"
        if "manhua" in t: return "manhua"
        return "manga"

    print("\n--- [1/6] بدء مزامنة مانغاتايم (MangaTime) ---")
    catalog_map = load_catalog_map(CATALOG_FILE)
    print(f"📂 تم تحميل {len(catalog_map)} عمل مسبقاً من أرشيف مانغاتايم.")

    active_slugs = []

    for page in range(1, MAX_PAGES + 1):
        payload = {
            "0": {
                "json": {
                    "filters": {"genres": [], "sortBy": "popularity-desc"},
                    "limit": 48,
                    "page": page,
                    "sortBy": "popularity",
                    "sortOrder": "desc"
                }
            }
        }
        data = fetch_trpc("search.searchSeries", payload)
        items = get_items(data)
        if not items: break

        for it in items:
            slug = it.get("slug") or it.get("seriesSlug")
            title = it.get("title") or it.get("name")
            if not slug or not title: continue

            cover = it.get("coverUrl") or it.get("cover") or ""
            if cover.startswith("/"): cover = f"{BASE_URL}{cover}"

            raw_type = it.get("type", "manga")
            type_path = get_type_path(raw_type)

            entry = {
                "id": slug,
                "title": title,
                "url": f"{BASE_URL}/{type_path}/{slug}",
                "cover_url": cover,
                "type": "رواية" if type_path == "novel" else "مانهوا",
                "status": "مستمر",
                "rating": ""
            }

            if slug in catalog_map:
                catalog_map[slug].update(entry)
            else:
                catalog_map[slug] = entry

            if slug not in active_slugs:
                active_slugs.append(slug)

    save_catalog_map(CATALOG_FILE, catalog_map)

    print(f"⚡ فحص وتحديث فصول {len(active_slugs)} عمل نشط...")
    for idx, slug in enumerate(active_slugs, 1):
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        item_meta = catalog_map[slug]
        existing_data = {}
        existing_ch_count = 0

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    existing_ch_count = len(existing_data.get("chapters", {}))
            except Exception:
                pass

        try:
            series_data = fetch_trpc("content.getSeriesBySlug", {"0": {"json": {"slug": slug}}}) or {}
            stats = series_data.get("stats") or {}
            total_ch = int(stats.get("chapterCount") or series_data.get("chapterCount") or 0)

            if existing_ch_count >= total_ch and total_ch > 0:
                print(f"⚡ [{idx}/{len(active_slugs)}] متطابق ومكتمل: {slug} ({total_ch} فصل)")
                continue

            ch_data = fetch_trpc("content.getChapters", {"0": {"json": {"seriesSlug": slug, "limit": 100, "page": 1, "sortBy": "number-desc"}}})
            ch_array = get_items(ch_data)

            chapters_map = existing_data.get("chapters", {})
            type_path = get_type_path(series_data.get("type") or item_meta.get("type", ""))

            for ch in ch_array:
                num = str(ch.get("number", "0")).strip()
                clean_num = str(int(float(num))) if num.replace(".", "", 1).isdigit() and float(num).is_integer() else num
                ch_url = f"{BASE_URL}/{type_path}/{slug}/chapter/{clean_num}"
                chapters_map[ch_url] = {"name": clean_num, "images": []}

            if total_ch > len(chapters_map):
                for i in range(1, total_ch + 1):
                    ch_url = f"{BASE_URL}/{type_path}/{slug}/chapter/{i}"
                    if ch_url not in chapters_map:
                        chapters_map[ch_url] = {"name": str(i), "images": []}

            desc = series_data.get("description") or existing_data.get("description", "لا يوجد وصف")
            genres = [g.get("name") for g in series_data.get("genres", []) if isinstance(g, dict) and g.get("name")]

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump({
                    "id": slug,
                    "title": series_data.get("title") or item_meta["title"],
                    "cover_url": item_meta["cover_url"],
                    "description": desc,
                    "type": item_meta["type"],
                    "status": "مستمر",
                    "last_update": "",
                    "rating": str(stats.get("rating") or ""),
                    "favorites": str(stats.get("favorites") or ""),
                    "genres": genres if genres else ["أكشن", "فانتازيا"],
                    "is_novel": (type_path == "novel"),
                    "chapters": chapters_map
                }, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(active_slugs)}] مانغاتايم: {slug} ({len(chapters_map)} فصل)")
            time.sleep(0.15)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {slug}: {e}")


# ==========================================
# 2. عالم الروايات (RealmNovel) - API مباشر
# ==========================================
def sync_realmnovel():
    API_BASE = "http://62.171.141.197:5007"
    WEB_BASE = "https://realmnovel.com"
    DATA_DIR = os.path.join(DATA_ROOT, "realmnovel")
    CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
    MAX_PAGES = 3

    os.makedirs(DATA_DIR, exist_ok=True)
    headers = {
        "user-agent": "Dart/3.9 (dart:io)",
        "content-type": "application/json",
        "x-app-version": "10",
        "accept-encoding": "gzip"
    }
    s = requests.Session()
    s.headers.update(headers)

    print("\n--- [2/6] بدء مزامنة عالم الروايات (RealmNovel) ---")
    catalog_map = load_catalog_map(CATALOG_FILE)
    print(f"📂 تم تحميل {len(catalog_map)} رواية مسبقاً من أرشيف عالم الروايات.")

    active_novels = []

    for page in range(1, MAX_PAGES + 1):
        try:
            r = s.get(f"{API_BASE}/novels/latest?page={page}&limit=20", timeout=15).json()
            data = r.get("data", [])
            if not data: break
            for it in data:
                nid = it.get("_id")
                if not nid: continue
                entry = {
                    "id": nid,
                    "title": it.get("title") or it.get("titleEn") or nid,
                    "url": f"{WEB_BASE}/novel/{nid}",
                    "cover_url": f"{WEB_BASE}/img/novel/{nid}.jpg",
                    "type": "رواية",
                    "status": it.get("status", "مستمرة"),
                    "rating": str(it.get("rating", ""))
                }
                if nid in catalog_map:
                    catalog_map[nid].update(entry)
                else:
                    catalog_map[nid] = entry

                if nid not in active_novels:
                    active_novels.append(nid)
        except Exception:
            break

    save_catalog_map(CATALOG_FILE, catalog_map)

    print(f"⚡ فحص وتحديث فصول {len(active_novels)} رواية نشطة...")
    for idx, nid in enumerate(active_novels, 1):
        file_path = os.path.join(DATA_DIR, f"{nid}.json")
        item_meta = catalog_map[nid]
        existing_data = {}
        existing_ch_count = 0

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    existing_ch_count = len(existing_data.get("chapters", {}))
            except Exception:
                pass

        try:
            det = s.get(f"{API_BASE}/novels/{nid}", timeout=15).json().get("data", {})
            total = int(det.get("chaptersCount") or det.get("totalChapters") or existing_ch_count)

            if existing_ch_count >= total and total > 0:
                print(f"⚡ [{idx}/{len(active_novels)}] متطابق ومكتمل: {nid} ({total} فصل)")
                continue

            chapters_map = existing_data.get("chapters", {})
            for c in range(1, total + 1):
                ch_url = f"{WEB_BASE}/novel/{nid}/chapter/{c}"
                if ch_url not in chapters_map:
                    chapters_map[ch_url] = {"name": str(c), "images": []}

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump({
                    "id": nid,
                    "title": det.get("title") or item_meta["title"],
                    "cover_url": item_meta["cover_url"],
                    "description": det.get("description") or existing_data.get("description", "لا يوجد وصف"),
                    "type": "رواية",
                    "status": det.get("status") or item_meta["status"],
                    "last_update": "",
                    "rating": str(det.get("rating") or item_meta["rating"]),
                    "favorites": "",
                    "genres": det.get("genres") or existing_data.get("genres", ["فنون قتال"]),
                    "is_novel": True,
                    "chapters": chapters_map
                }, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(active_novels)}] عالم الروايات: {nid} ({len(chapters_map)} فصل)")
            time.sleep(0.15)
        except Exception:
            pass


# ==========================================
# 3. فضاء الروايات (Riwyat) - روايات
# ==========================================
def sync_riwyat():
    BASE_URL = "https://cenele.com"
    DATA_DIR = os.path.join(DATA_ROOT, "riwyat")
    CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
    MAX_PAGES = 3

    os.makedirs(DATA_DIR, exist_ok=True)
    session = cffi_requests.Session(impersonate="chrome124")

    print("\n--- [3/6] بدء مزامنة فضاء الروايات (Riwyat) ---")
    catalog_map = load_catalog_map(CATALOG_FILE)
    print(f"📂 تم تحميل {len(catalog_map)} رواية مسبقاً من أرشيف فضاء الروايات.")

    active_novels = []

    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE_URL}/cont/page/{page}/?m_orderby=latest" if page > 1 else f"{BASE_URL}/cont/?m_orderby=latest"
        r = session.get(url, timeout=20)
        if r.status_code != 200: break
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("article.nhv-library-card")
        if not cards: break

        for card in cards:
            a = card.select_one("h2.nhv-library-card__title a")
            if not a: continue
            href = normalize_http_url(a.get("href", ""), BASE_URL).rstrip("/")
            slug = href.split("/")[-1]
            img = card.select_one(".nhv-library-card__cover img")
            cover = normalize_http_url(img.get("src", "") if img else "", BASE_URL)

            entry = {
                "id": slug,
                "title": a.get_text(strip=True),
                "url": href,
                "cover_url": cover,
                "type": "رواية",
                "status": "مستمر",
                "rating": ""
            }

            if slug in catalog_map:
                catalog_map[slug].update(entry)
            else:
                catalog_map[slug] = entry

            if slug not in active_novels:
                active_novels.append(slug)

    save_catalog_map(CATALOG_FILE, catalog_map)

    print(f"⚡ فحص وتحديث فصول {len(active_novels)} رواية نشطة...")
    for idx, slug in enumerate(active_novels, 1):
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        item_meta = catalog_map[slug]
        existing_data = {}

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            except Exception:
                pass

        try:
            r = session.get(item_meta["url"], timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")
            desc = soup.select_one(".nhv-novel-synopsis")
            desc_text = desc.get_text("\n", strip=True) if desc else existing_data.get("description", "لا يوجد وصف")

            ch_res = session.post(f"{BASE_URL}/cont/{slug}/ajax/chapters/", timeout=20)
            ch_soup = BeautifulSoup(ch_res.text, "html.parser") if ch_res.status_code == 200 else soup

            chapters_map = existing_data.get("chapters", {})
            for a in ch_soup.select("li.wp-manga-chapter a"):
                href = normalize_http_url(a.get("href", ""), BASE_URL).rstrip("/")
                num = re.search(r"\d+(\.\d+)?", a.get_text(strip=True))
                clean_num = num.group(0) if num else a.get_text(strip=True)
                chapters_map[href] = {"name": clean_num, "images": []}

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump({
                    "id": slug,
                    "title": item_meta["title"],
                    "cover_url": item_meta["cover_url"],
                    "description": desc_text,
                    "type": "رواية",
                    "status": "مستمر",
                    "last_update": "",
                    "rating": "",
                    "favorites": "",
                    "genres": [g.get_text(strip=True) for g in soup.select(".nhv-novel-genres a")],
                    "is_novel": True,
                    "chapters": chapters_map
                }, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(active_novels)}] فضاء الروايات: {slug} ({len(chapters_map)} فصل)")
            time.sleep(0.3)
        except Exception:
            pass


# ==========================================
# 4. بحر الروايات (SeaNovel) - Next.js
# ==========================================
def sync_seanovel():
    BASE_URL = "https://seanovel.org"
    DATA_DIR = os.path.join(DATA_ROOT, "seanovel")
    CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

    os.makedirs(DATA_DIR, exist_ok=True)
    session = cffi_requests.Session(impersonate="chrome124")

    print("\n--- [4/6] بدء مزامنة بحر الروايات (SeaNovel) ---")
    catalog_map = load_catalog_map(CATALOG_FILE)
    print(f"📂 تم تحميل {len(catalog_map)} رواية مسبقاً من أرشيف بحر الروايات.")

    active_slugs = []

    try:
        home_res = session.get(BASE_URL, timeout=20)
        if home_res.status_code == 200:
            for s in re.findall(r"/novels/([a-zA-Z0-9_\-]+)", home_res.text):
                if s not in ["search", "chapters", "api"] and s not in active_slugs:
                    active_slugs.append(s)
    except Exception:
        pass

    try:
        r = session.get(f"{BASE_URL}/sitemap-novels.xml", timeout=20)
        if r.status_code == 200:
            for s in re.findall(r"/novels/([a-zA-Z0-9_\-]+)", r.text):
                if s not in ["search", "chapters", "api"] and s not in catalog_map:
                    catalog_map[s] = {
                        "id": s,
                        "title": s.replace("-", " "),
                        "url": f"{BASE_URL}/novels/{s}",
                        "cover_url": f"{BASE_URL}/api/novel/{s}/cover",
                        "type": "رواية",
                        "status": "مستمر",
                        "rating": ""
                    }
    except Exception:
        pass

    targets = active_slugs[:25]
    print(f"⚡ فحص وتحديث فصول {len(targets)} رواية نشطة...")

    for idx, slug in enumerate(targets, 1):
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        item_meta = catalog_map.get(slug, {})
        existing_data = {}
        existing_ch_count = 0

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    existing_ch_count = len(existing_data.get("chapters", {}))
            except Exception:
                pass

        try:
            r = session.get(f"{BASE_URL}/novels/{slug}", timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")
            book_meta = {}

            for sc in soup.find_all("script", attrs={"type": "application/ld+json"}):
                try:
                    d = json.loads(sc.string or "{}")
                    for node in d.get("@graph", [d]):
                        if node.get("@type") == "Book": book_meta = node
                except Exception: pass

            title = book_meta.get("name") or soup.select_one("h1.novel-title")
            title_text = title if isinstance(title, str) else (title.get_text(strip=True) if title else item_meta.get("title", slug))
            total = int(book_meta.get("numberOfPages") or existing_ch_count or 50)

            catalog_map[slug].update({
                "title": title_text,
                "cover_url": normalize_http_url(book_meta.get("image") or item_meta.get("cover_url", ""), BASE_URL)
            })

            if existing_ch_count >= total and total > 0:
                print(f"⚡ [{idx}/{len(targets)}] متطابق ومكتمل: {title_text} ({total} فصل)")
                continue

            chapters_map = existing_data.get("chapters", {})
            for i in range(1, total + 1):
                ch_url = f"{BASE_URL}/novels/{slug}/chapters/{i}"
                if ch_url not in chapters_map:
                    chapters_map[ch_url] = {"name": str(i), "images": []}

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump({
                    "id": slug,
                    "title": title_text,
                    "cover_url": catalog_map[slug]["cover_url"],
                    "description": book_meta.get("description") or existing_data.get("description", "لا يوجد وصف"),
                    "type": "رواية",
                    "status": "مستمر",
                    "last_update": "",
                    "rating": "",
                    "favorites": "",
                    "genres": book_meta.get("genre", ["خيال"]),
                    "is_novel": True,
                    "chapters": chapters_map
                }, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(targets)}] بحر الروايات: {title_text} ({len(chapters_map)} فصل)")
            time.sleep(0.3)
        except Exception:
            pass

    save_catalog_map(CATALOG_FILE, catalog_map)


# ==========================================
# 5. تيم إكس (TeamX) - مانهوا
# ==========================================
def sync_teamx():
    BASE_URL = "https://olympustaff.com"
    DATA_DIR = os.path.join(DATA_ROOT, "teamx")
    CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
    MAX_PAGES = 3

    os.makedirs(DATA_DIR, exist_ok=True)
    session = cffi_requests.Session(impersonate="chrome124")

    print("\n--- [5/6] بدء مزامنة تيم إكس (TeamX) ---")
    catalog_map = load_catalog_map(CATALOG_FILE)
    print(f"📂 تم تحميل {len(catalog_map)} عمل مسبقاً من أرشيف Team X.")

    active_slugs = []

    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE_URL}/series?page={page}" if page > 1 else f"{BASE_URL}/series"
        r = session.get(url, timeout=20)
        if r.status_code != 200: break
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select(".listupd .bsx")
        if not items: break

        for it in items:
            a = it.select_one("a")
            if not a: continue
            href = normalize_http_url(a.get("href", ""), BASE_URL).rstrip("/")
            slug = href.split("/")[-1]
            img = it.select_one("img")
            cover = normalize_http_url(img.get("src", "") or img.get("data-src", "") if img else "", BASE_URL)

            entry = {
                "id": slug,
                "title": a.get("title", "").strip() or slug,
                "url": href,
                "cover_url": cover,
                "type": "مانهوا",
                "status": "مستمر",
                "rating": ""
            }

            if slug in catalog_map:
                catalog_map[slug].update(entry)
            else:
                catalog_map[slug] = entry

            if slug not in active_slugs:
                active_slugs.append(slug)

    save_catalog_map(CATALOG_FILE, catalog_map)

    print(f"⚡ فحص وتحديث فصول {len(active_slugs)} عمل نشط...")
    for idx, slug in enumerate(active_slugs, 1):
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        item_meta = catalog_map[slug]
        existing_data = {}
        existing_ch_count = 0

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    existing_ch_count = len(existing_data.get("chapters", {}))
            except Exception:
                pass

        try:
            r = session.get(item_meta["url"], timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")
            total_m = re.search(r"(?:قائمة الفصول|الفصول)\s*\(([0-9]+)\)", r.text)
            total = int(total_m.group(1)) if total_m else existing_ch_count

            if existing_ch_count >= total and total > 0:
                print(f"⚡ [{idx}/{len(active_slugs)}] متطابق ومكتمل: {slug} ({total} فصل)")
                continue

            chapters_map = existing_data.get("chapters", {})
            for a in soup.select("div.chapter-card a.chapter-link"):
                href = normalize_http_url(a.get("href", ""), BASE_URL).rstrip("/")
                num_node = a.select_one(".chapter-number") or a.select_one(".chapter-title")
                raw_num = num_node.get_text(strip=True) if num_node else href.split("/")[-1]
                match = re.search(r"\d+(\.\d+)?", raw_num)
                clean_num = match.group(0) if match else raw_num
                chapters_map[href] = {"name": clean_num, "images": []}

            if total > len(chapters_map):
                for i in range(1, total + 1):
                    u = f"{BASE_URL}/series/{slug}/{i}"
                    if u not in chapters_map:
                        chapters_map[u] = {"name": str(i), "images": []}

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump({
                    "id": slug,
                    "title": item_meta["title"],
                    "cover_url": item_meta["cover_url"],
                    "description": existing_data.get("description", "لا يوجد وصف"),
                    "type": "مانهوا",
                    "status": "مستمر",
                    "last_update": "",
                    "rating": "",
                    "favorites": "",
                    "genres": [g.get_text(strip=True) for g in soup.select("div.review-author-info a")],
                    "is_novel": False,
                    "chapters": chapters_map
                }, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(active_slugs)}] تيم إكس: {slug} ({len(chapters_map)} فصل)")
            time.sleep(0.4)
        except Exception:
            pass


# ==========================================
# 6. نادي الروايات (RewayatClub) - API وروايات
# ==========================================
def sync_rewayatclub():
    BASE_WEB = "https://rewayat.club"
    API_BASE = "https://api.rewayat.club/api"
    DATA_DIR = os.path.join(DATA_ROOT, "rewayatclub")[cite: 1]
    CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
    MAX_PAGES = 5  # مناسب جداً للمزامنة الدورية السريعة

    os.makedirs(DATA_DIR, exist_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://rewayat.club/",
        "Origin": "https://rewayat.club"
    }
    session = cffi_requests.Session(impersonate="chrome124", headers=headers)

    def safe_get(url: str, max_retries: int = 3):
        for attempt in range(1, max_retries + 1):
            try:
                res = session.get(url, timeout=20)
                if res.status_code == 200:
                    return res
                elif res.status_code in [429, 502, 503, 504]:
                    time.sleep(attempt * 1.5)
            except Exception:
                time.sleep(attempt * 1.5)
        return None

    def normalize_cover(raw_cover: str) -> str:
        if not raw_cover: return ""
        c = raw_cover.strip()
        if c.startswith("//"): return f"https:{c}"
        if c.startswith("/media/"): return f"https://api.rewayat.club{c}"
        if not c.startswith("http"): return f"{BASE_WEB}/{c}"
        return c

    print("\n--- [6/6] بدء مزامنة نادي الروايات (RewayatClub) ---")
    catalog_map = load_catalog_map(CATALOG_FILE)
    print(f"📂 تم تحميل {len(catalog_map)} رواية مسبقاً من أرشيف نادي الروايات.")

    active_slugs = []
    page = 1
    consecutive_empty = 0

    while page <= MAX_PAGES:
        url = f"{API_BASE}/novels/?ordering=-num_chapters&page={page}"
        res = safe_get(url)

        if not res:
            page += 1
            consecutive_empty += 1
            if consecutive_empty >= 3: break
            continue

        consecutive_empty = 0
        try:
            payload = res.json()
        except Exception:
            break

        results = payload.get("results") or payload.get("novels") or []
        if not results: break

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

            if slug in catalog_map:
                catalog_map[slug].update(entry)
            else:
                catalog_map[slug] = entry

            if slug not in active_slugs:
                active_slugs.append(slug)

        page += 1
        time.sleep(0.15)

    save_catalog_map(CATALOG_FILE, catalog_map)

    print(f"⚡ فحص وتحديث فصول {len(active_slugs)} رواية نشطة...")
    for idx, slug in enumerate(active_slugs, 1):
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        item_meta = catalog_map[slug]
        target_total_ch = item_meta.get("total_chapters", 0)

        existing_chapters_count = 0
        existing_data = {}

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    existing_chapters_count = len(existing_data.get("chapters", {}))
            except Exception:
                pass

        if existing_chapters_count >= target_total_ch and target_total_ch > 0:
            print(f"⚡ [{idx}/{len(active_slugs)}] متطابق ومكتمل: {slug} ({target_total_ch} فصل)")
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

        print(f"✓ [{idx}/{len(active_slugs)}] نادي الروايات: {item_meta['title']} ({len(chapters_map)} فصل)")


# ==========================================
# الدفع التراكمي الآمن إلى GitHub
# ==========================================
def push_all():
    print("\n📤 بدء فحص وتجهيز التحديثات للرفع إلى GitHub...")
    try:
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], 
            capture_output=True, text=True, check=True
        ).stdout.strip()
        os.chdir(git_root)

        # سحب تعديلات السحابة ودمجها أولاً لمنع رفض الـ push
        subprocess.run(["git", "pull", "origin", "main", "--no-rebase", "-X", "ours", "--no-edit"], capture_output=True, text=True)

        subprocess.run(["git", "add", "data/"], check=True)[cite: 1]

        diff_check = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff_check.returncode == 0:
            print("✨ لا توجد ملفات جديدة أو معدلة تستدعي الرفع.")
            return

        commit_msg = f"Incremental sync: Multi-source update ({time.strftime('%Y-%m-%d %H:%M')})"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)

        # دفع آمن بدون force
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("⚡ تم رفع جميع البيانات بنجاح إلى المستودع السحابي!")
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ أثناء الرفع لـ Git: {e}")


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    sources = {
        "mangatime": sync_mangatime,
        "realmnovel": sync_realmnovel,
        "riwyat": sync_riwyat,
        "seanovel": sync_seanovel,
        "teamx": sync_teamx,
        "rewayatclub": sync_rewayatclub,
    }

    if arg in sources:
        sources[arg]()
    else:
        for name, fn in sources.items():
            try: fn()
            except Exception as e: print(f"❌ خطأ في {name}: {e}")

    push_all()
