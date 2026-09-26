import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://seanovel.org"
DATA_DIR = os.path.join("data", "seanovel")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

DETAILS_SYNC_LIMIT = 20    # فحص وتجهيز فصول أحدث 20 رواية نشطة
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

def get_session():
    session = requests.Session(impersonate="chrome124")
    session.headers.update({
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Referer": f"{BASE_URL}/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
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
    """تحميل الأرشيف القديم لمنع مسح أي عمل سابق"""
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["id"]: item for item in data if "id" in item}
    except Exception as e:
        print(f"⚠️ خطأ أثناء قراءة الكتالوج القديم: {e}")
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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لبحر الروايات في {GLOBAL_NEW_FILE}")

def scrape_novel_details(session, slug: str, novel_url: str) -> dict:
    cache_url = f"{novel_url}?_t={int(time.time())}"
    custom_headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache"
    }

    res = session.get(cache_url, headers=custom_headers, timeout=25)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code}")

    soup = BeautifulSoup(res.text, "html.parser")

    book_schema = {}
    faq_schema = {}

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            content = script.string or script.text or ""
            data = json.loads(content)
            graph = data.get("@graph", [data]) if isinstance(data, dict) else []
            for node in graph:
                if node.get("@type") == "Book":
                    book_schema = node
                elif node.get("@type") == "FAQPage":
                    faq_schema = node
        except Exception:
            continue

    title = book_schema.get("name")
    if not title:
        title_node = soup.select_one("h1.novel-title, h1")
        title = title_node.get_text(strip=True) if title_node else slug

    cover_url = book_schema.get("image")
    if not cover_url:
        cover_node = soup.select_one("img.novel-cover, meta[property='og:image']")
        cover_url = cover_node.get("src") or cover_node.get("content") or f"{BASE_URL}/api/novel/{slug}/cover"
    cover_url = normalize_url(cover_url)

    description = book_schema.get("description")
    if not description:
        desc_node = soup.select_one("p.novel-description-para, .novel-about-card-ios")
        description = desc_node.get_text("\n", strip=True) if desc_node else "لا يوجد وصف"

    genres = book_schema.get("genre")
    if not genres or not isinstance(genres, list):
        genres = [a.get_text(strip=True) for a in soup.select(".tags-scroll-container-ios a, a.genre-pill-modern-ios")]

    status = "مستمر"
    for item in faq_schema.get("mainEntity", []):
        ans = item.get("acceptedAnswer", {}).get("text", "")
        if "حالة ترجمة" in ans:
            status = "مكتملة" if "مكتملة" in ans else "مستمرة"
            break

    total_chapters = int(book_schema.get("numberOfPages") or 0)
    if total_chapters <= 0:
        stat_nodes = soup.select(".stat-col-ios")
        for node in stat_nodes:
            lbl = node.select_one(".stat-lbl-ios")
            val = node.select_one(".stat-val-ios")
            if lbl and "فصول" in lbl.text:
                num_match = re.search(r"\d+", val.text if val else "")
                if num_match:
                    total_chapters = int(num_match.group(0))

    if total_chapters <= 0:
        total_chapters = 50

    return {
        "id": slug,
        "title": title,
        "cover_url": cover_url,
        "description": description,
        "type": "رواية",
        "status": status,
        "last_update": "",
        "rating": "",
        "favorites": "",
        "genres": genres,
        "is_novel": True,
        "total_chapters": total_chapters
    }

def sync_seanovel():
    session = get_session()
    print("🚀 بدء المزامنة الخاطفة لبحر الروايات (SeaNovel)...")

    catalog_dict = load_existing_catalog()
    ordered_recent_slugs = []
    discovered_slugs = set()

    # 1. فحص الصفحة الرئيسية لرصد أحدث الروايات النشطة
    try:
        home_res = session.get(BASE_URL, timeout=20)
        if home_res.status_code == 200:
            home_slugs = re.findall(r"/novels/([a-zA-Z0-9_\-]+)", home_res.text)
            for s in home_slugs:
                if s not in ["search", "chapters", "api"]:
                    if s not in ordered_recent_slugs:
                        ordered_recent_slugs.append(s)
                    discovered_slugs.add(s)
            print(f"الصفحة الرئيسية: تم رصد {len(ordered_recent_slugs)} رواية نشطة ومحدثة.")
    except Exception as e:
        print(f"تنبيه أثناء قراءة الصفحة الرئيسية: {e}")

    # 2. اكتشاف الأعمال الجديدة من الـ Sitemap
    try:
        sitemap_url = f"{BASE_URL}/sitemap-novels.xml"
        sm_res = session.get(sitemap_url, timeout=20)
        if sm_res.status_code == 200:
            extracted = re.findall(r"/novels/([a-zA-Z0-9_\-]+)", sm_res.text)
            for s in extracted:
                if s not in ["search", "chapters", "api"]:
                    discovered_slugs.add(s)
            print(f"خريطة الروايات (Sitemap): إجمالي المكتشف {len(discovered_slugs)} رواية.")
    except Exception as e:
        print(f"تنبيه أثناء قراءة Sitemap: {e}")

    # 3. تحديث فصول أحدث 20 رواية نشطة ورصد الإشعارات
    targets = ordered_recent_slugs[:DETAILS_SYNC_LIMIT]
    print(f"\n⚡ تحديث ملفات الفصول لأحدث {len(targets)} رواية نشطة...")

    new_releases = []

    for idx, slug in enumerate(targets, 1):
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        item_meta = catalog_dict.get(slug, {})
        novel_url = item_meta.get("url") or f"{BASE_URL}/novels/{slug}"

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
            details = scrape_novel_details(session, slug, novel_url)
            target_total = details["total_chapters"]
            prev_chaps = existing_chapters_count or catalog_dict.get(slug, {}).get("total_chapters", 0)

            # كشف التحديثات لملف new.json
            if target_total > prev_chaps and target_total > 0:
                new_releases.append({
                    "id": slug,
                    "title": details["title"],
                    "chapter": f"الفصل {target_total}" if prev_chaps > 0 else "رواية جديدة",
                    "type": "رواية",
                    "cover_url": details["cover_url"]
                })

            # تحديث بيانات العمل في القاموس التراكمي
            catalog_dict[slug] = {
                "id": slug,
                "title": details["title"],
                "url": novel_url,
                "cover_url": details["cover_url"],
                "type": "رواية",
                "status": details["status"],
                "rating": "",
                "total_chapters": target_total
            }

            # كاش ذكي: تخطي إعادة الكتابة إذا كانت الفصول متطابقة محلياً
            if existing_chapters_count >= target_total and target_total > 0:
                print(f"⚡ [{idx}/{len(targets)}] متطابق ومكتمل: {details['title']} ({target_total} فصل)")
                continue

            chapters_map = existing_data.get("chapters", {})
            for i in range(1, target_total + 1):
                ch_url = f"{BASE_URL}/novels/{slug}/chapters/{i}"
                if ch_url not in chapters_map:
                    chapters_map[ch_url] = {
                        "name": str(i),
                        "images": []
                    }

            novel_payload = {
                "id": slug,
                "title": details["title"],
                "cover_url": details["cover_url"],
                "description": details["description"],
                "type": "رواية",
                "status": details["status"],
                "last_update": "",
                "rating": "",
                "favorites": "",
                "genres": details["genres"],
                "is_novel": True,
                "chapters": chapters_map
            }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(novel_payload, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(targets)}] تم التحديث: {details['title']} ({len(chapters_map)} فصل)")
            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {slug}: {e}")

    # 4. دمج وحفظ الفهرس الشامل (الأحدث أولاً + بقية الأرشيف القديم والمكتشف)
    seen_slugs = set(ordered_recent_slugs)
    final_merged_catalog = [catalog_dict[s] for s in ordered_recent_slugs if s in catalog_dict]

    # إضافة باقي الأرشيف القديم
    for s, item in catalog_dict.items():
        if s not in seen_slugs:
            final_merged_catalog.append(item)
            seen_slugs.add(s)

    # إضافة أي أعمال جديدة تم اكتشافها عبر الـ Sitemap
    for s in discovered_slugs:
        if s not in seen_slugs:
            final_merged_catalog.append({
                "id": s,
                "title": s.replace("-", " "),
                "url": f"{BASE_URL}/novels/{s}",
                "cover_url": f"{BASE_URL}/api/novel/{s}/cover",
                "type": "رواية",
                "status": "مستمر",
                "rating": "",
                "total_chapters": 0
            })
            seen_slugs.add(s)

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    print(f"\n💾 تم حفظ الفهرس العام المدمج: {len(final_merged_catalog)} رواية (الأحدث في الصدارة).")

    # 5. تحديث الإشعارات المشتركة
    if new_releases:
        update_global_new_releases(new_releases)

    print("🎉 اكتملت مزامنة بحر الروايات الذكية بنجاح تام!")

if __name__ == "__main__":
    sync_seanovel()
