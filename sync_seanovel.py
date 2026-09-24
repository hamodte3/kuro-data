import json
import os
import re
import time
import subprocess
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://seanovel.org"
DATA_DIR = os.path.join("data", "seanovel")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

# عدد الروايات المحدثة حديثاً التي يتم فحص تفاصيلها وفصولها في كل دورة سريعة
DETAILS_SYNC_LIMIT = 1000 

os.makedirs(DATA_DIR, exist_ok=True)

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
    """تحميل الكتالوج القديم كـ Dictionary لتسهيل الدمج والتحديث التراكمي"""
    if not os.path.exists(CATALOG_FILE):
        return {}
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
            return {item["id"]: item for item in items if "id" in item}
    except Exception as e:
        print(f"⚠️ تعذر قراءة الكتالوج القديم: {e}")
        return {}

def scrape_novel_details(session, slug: str, novel_url: str) -> dict:
    res = session.get(novel_url, timeout=25)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code}")

    soup = BeautifulSoup(res.text, "html.parser")

    book_schema = {}
    faq_schema = {}

    # استخراج Schema.org للرواية
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

    # استخراج العنوان
    title = book_schema.get("name")
    if not title:
        title_node = soup.select_one("h1.novel-title, h1")
        title = title_node.get_text(strip=True) if title_node else slug

    # استخراج الغلاف
    cover_url = book_schema.get("image")
    if not cover_url:
        cover_node = soup.select_one("img.novel-cover, meta[property='og:image']")
        cover_url = cover_node.get("src") or cover_node.get("content") or f"{BASE_URL}/api/novel/{slug}/cover"
    cover_url = normalize_url(cover_url)

    # استخراج الوصف
    description = book_schema.get("description")
    if not description:
        desc_node = soup.select_one("p.novel-description-para, .novel-about-card-ios")
        description = desc_node.get_text("\n", strip=True) if desc_node else "لا يوجد وصف"

    # استخراج التصنيفات
    genres = book_schema.get("genre")
    if not genres or not isinstance(genres, list):
        genres = [a.get_text(strip=True) for a in soup.select(".tags-scroll-container-ios a, a.genre-pill-modern-ios")]

    # استخراج الحالة
    status = "مستمر"
    for item in faq_schema.get("mainEntity", []):
        ans = item.get("acceptedAnswer", {}).get("text", "")
        if "حالة ترجمة" in ans:
            status = "مكتملة" if "مكتملة" in ans else "مستمرة"
            break

    # استخراج إجمالي الفصول
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
    print("🚀 بدء المزامنة التراكمية لبحر الروايات (SeaNovel)...")

    # 1. استرجاع الأرشيف المخزن مسبقاً
    catalog_map = load_existing_catalog()
    print(f"📂 تم تحميل {len(catalog_map)} رواية محفوظة مسبقاً في الأرشيف.")

    active_slugs_this_run = []  # الروايات الأحدث المعروضة في الواجهة
    discovered_slugs = set()

    # 2. فحص الصفحة الرئيسية (أحدث الروايات المحدثة والنشطة)
    try:
        home_res = session.get(BASE_URL, timeout=20)
        if home_res.status_code == 200:
            home_slugs = re.findall(r"/novels/([a-zA-Z0-9_\-]+)", home_res.text)
            for s in home_slugs:
                if s not in ["search", "chapters", "api"]:
                    if s not in active_slugs_this_run:
                        active_slugs_this_run.append(s)
                    discovered_slugs.add(s)
            print(f"الصفحة الرئيسية: تم رصد {len(active_slugs_this_run)} رواية نشطة.")
    except Exception as e:
        print(f"تنبيه أثناء قراءة الصفحة الرئيسية: {e}")

    # 3. سحب الفهرس الشامل من خريطة الموقع (Sitemap) لاكتشاف أي روايات جديدة كلياً
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

    # 4. دمج الروايات المكتشفة في الكتالوج بدون مسح بيانات الأعمال القديمة
    for s in discovered_slugs:
        if s not in catalog_map:
            catalog_map[s] = {
                "id": s,
                "title": s.replace("-", " "),
                "url": f"{BASE_URL}/novels/{s}",
                "cover_url": f"{BASE_URL}/api/novel/{s}/cover",
                "type": "رواية",
                "status": "مستمر",
                "rating": ""
            }

    # 5. تجهيز وتحديث ملفات الفصول للروايات النشطة في هذا التشغيل فقط
    targets = active_slugs_this_run[:DETAILS_SYNC_LIMIT]
    print(f"\n⚡ فحص وتحديث فصول {len(targets)} رواية نشطة من التحديثات الأخيرة...")

    for idx, slug in enumerate(targets, 1):
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        item_meta = catalog_map.get(slug, {})
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

            # تحديث بيانات الكتالوج بالبيانات العربية الحقيقية
            catalog_map[slug].update({
                "title": details["title"],
                "cover_url": details["cover_url"],
                "status": details["status"]
            })

            # ⚡ كاش ذكي: إذا كانت الفصول مكتملة ومتطابقة محلياً نتخطى الحفظ
            if existing_chapters_count >= target_total and target_total > 0:
                print(f"⚡ [{idx}/{len(targets)}] متطابق ومكتمل: {details['title']} ({target_total} فصل)")
                continue

            # توليد ودمج الفصول التراكمي
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
            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {slug}: {e}")

    # 6. حفظ الكتالوج المدمج الشامل (القديم + المحدث والجديد)
    full_catalog_list = list(catalog_map.values())
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(full_catalog_list, f, ensure_ascii=False, indent=2)

    print(f"\n✨ تم حفظ الفهرس العام بنجاح (المجموع الإجمالي: {len(full_catalog_list)} رواية).")
    print("🎉 اكتملت المزامنة التراكمية لبحر الروايات بنجاح تام!")

def auto_push_to_github():
    print("\n📤 فحص ورفع تحديثات بحر الروايات إلى GitHub...")
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "data/seanovel/"], 
            capture_output=True, 
            text=True
        )
        if not status.stdout.strip():
            print("✨ لا توجد ملفات جديدة للرفع.")
            return

        subprocess.run(["git", "add", "data/seanovel/"], check=True)
        commit_msg = f"Incremental sync: SeaNovel data ({time.strftime('%Y-%m-%d %H:%M')})"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        
        # دفع آمن بدون force
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("⚡ تم الرفع بنجاح إلى المستودع!")
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ أثناء الرفع لـ Git: {e}")

if __name__ == "__main__":
    sync_seanovel()
    auto_push_to_github()
