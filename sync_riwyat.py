import json
import os
import re
import time
import subprocess
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://cenele.com"
DATA_DIR = os.path.join("data", "riwyat")
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")
GLOBAL_NEW_FILE = os.path.join("data", "new.json")

# 🎯 عدد الصفحات للجولات السريعة (تحديث الجديد فقط)
MAX_PAGES = 3 

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

def get_session():
    session = requests.Session(impersonate="chrome124")
    session.headers.update({
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Referer": f"{BASE_URL}/cont/",
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
    print(f"🔔 تم تسجيل {len(new_releases)} تحديث جديد لفضاء الروايات في {GLOBAL_NEW_FILE}")

def scrape_riwyat_details(session, slug: str, manga_url: str) -> dict:
    res = session.get(manga_url, timeout=25)
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

    genres = [a.get_text(strip=True) for a in soup.select(".nhv-novel-genres a")]

    # استخراج معرف الرواية الداخلي (Post ID)
    post_id = None
    post_id_elem = soup.select_one("[data-post-id], [data-manga-id], input#wp-manga-current-chap, link[rel='shortlink']")
    if post_id_elem:
        post_id = post_id_elem.get("data-post-id") or post_id_elem.get("data-manga-id")
        if not post_id and post_id_elem.get("href"):
            match = re.search(r"p=(\d+)", post_id_elem.get("href", ""))
            if match: post_id = match.group(1)

    chapters_map = {}
    number_regex = re.compile(r"\d+(\.\d+)?")

    # 1. طلب فصول Madara عبر AJAX
    ajax_chapters_url = f"{BASE_URL}/cont/{slug}/ajax/chapters/"
    ch_res = session.post(ajax_chapters_url, timeout=25)
    ch_soup = BeautifulSoup(ch_res.text, "html.parser") if ch_res.status_code == 200 else None

    # 2. طلب بديل عبر admin-ajax.php
    if (not ch_soup or not ch_soup.select("li.wp-manga-chapter a")) and post_id:
        admin_ajax_url = f"{BASE_URL}/wp-admin/admin-ajax.php"
        ch_res_admin = session.post(admin_ajax_url, data={"action": "manga_get_chapters", "manga": post_id}, timeout=25)
        if ch_res_admin.status_code == 200:
            ch_soup = BeautifulSoup(ch_res_admin.text, "html.parser")

    final_soup = ch_soup if (ch_soup and ch_soup.select("li.wp-manga-chapter a")) else soup
    ch_elements = final_soup.select("li.wp-manga-chapter a, ul.nhv-novel-chapters-list li a")

    for a in ch_elements:
        href = normalize_url(a.get("href", "")).rstrip("/")
        if not href or href.endswith("/cont") or href == manga_url.rstrip("/"):
            continue
        raw_text = a.get_text(strip=True)
        num_match = number_regex.search(raw_text)
        clean_num = num_match.group(0) if num_match else raw_text

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
    print(f"🚀 بدء المزامنة التراكمية لفضاء الروايات (فحص أول {MAX_PAGES} صفحات)...")

    # 1. تحميل الأرشيف المحفوظ مسبقاً
    old_catalog = []
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE, "r", encoding="utf-8") as f:
                old_catalog = json.load(f)
        except Exception as e:
            print(f"⚠️ تعذر قراءة الكتالوج القديم: {e}")
            old_catalog = []

    old_map = {item["id"]: item for item in old_catalog if "id" in item}
    print(f"📂 تم تحميل {len(old_map)} رواية محفوظة مسبقاً في الأرشيف.")

    freshly_scraped = []
    seen_fresh_ids = set()

    # 2. سحب الصفحات المحددة فقط
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE_URL}/cont/page/{page}/?m_orderby=latest" if page > 1 else f"{BASE_URL}/cont/?m_orderby=latest"
        res = session.get(url, timeout=25)
        if res.status_code != 200:
            print(f"توقف الفهرس عند صفحة {page}: رمز {res.status_code}")
            break

        soup = BeautifulSoup(res.text, "html.parser")
        cards = soup.select("article.nhv-library-card")
        if not cards:
            break

        for card in cards:
            a_tag = card.select_one("h2.nhv-library-card__title a")
            if not a_tag: continue
            href = normalize_url(a_tag.get("href", "")).rstrip("/")
            slug = href.split("/")[-1]
            if slug in seen_fresh_ids: continue

            seen_fresh_ids.add(slug)
            title = a_tag.get_text(strip=True) or slug
            img = card.select_one(".nhv-library-card__cover img")
            cover = normalize_url(img.get("src", "") if img else "")

            entry = {
                "id": slug,
                "title": title,
                "url": href,
                "cover_url": cover,
                "type": "رواية",
                "status": "مستمر",
                "rating": "",
                "total_chapters": old_map.get(slug, {}).get("total_chapters", 0)
            }
            freshly_scraped.append(entry)

        print(f"فضاء الروايات [صفحة {page}]: تم فحص الأعمال المحدثة.")
        time.sleep(0.3)

    # 3. تحديث تفاصيل وفصول الروايات النشطة وكشف الإشعارات
    print(f"\n⚡ تحديث فصول {len(freshly_scraped)} رواية نشطة...")
    new_releases = []

    for idx, item in enumerate(freshly_scraped, 1):
        slug = item["id"]
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

            # 🛡️ دمج الفصول التراكمي
            merged_chapters = existing_data.get("chapters", {})
            merged_chapters.update(new_details["chapters"])
            new_details["chapters"] = merged_chapters
            
            total_chapters = len(merged_chapters)
            item["total_chapters"] = total_chapters
            item["rating"] = new_details["rating"]
            prev_chaps = old_map.get(slug, {}).get("total_chapters", 0)

            # كشف التحديثات لإشعارات التطبيق
            if slug not in old_map:
                new_releases.append({
                    "id": slug,
                    "title": item["title"],
                    "chapter": f"الفصل {total_chapters}" if total_chapters > 0 else "رواية جديدة",
                    "type": "رواية",
                    "cover_url": item["cover_url"]
                })
            elif total_chapters > prev_chaps and total_chapters > 0:
                new_releases.append({
                    "id": slug,
                    "title": item["title"],
                    "chapter": f"الفصل {total_chapters}",
                    "type": "رواية",
                    "cover_url": item["cover_url"]
                })

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(new_details, f, ensure_ascii=False, indent=2)

            print(f"✓ [{idx}/{len(freshly_scraped)}] تم التحديث: {slug} ({total_chapters} فصل)")
            time.sleep(0.3)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {slug}: {e}")

    # ================== 4. الدمج الذكي للكاتلوج ==================
    fresh_ids = {x["id"] for x in freshly_scraped}
    remaining_old = [x for x in old_catalog if x.get("id") not in fresh_ids]
    final_merged_catalog = freshly_scraped + remaining_old

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(final_merged_catalog, f, ensure_ascii=False, indent=2)

    print(f"\n💾 تم حفظ الفهرس العام المدمج: {len(final_merged_catalog)} رواية (الجديد في الصدارة).")

    # تحديث ملف الإشعارات العام
    if new_releases:
        update_global_new_releases(new_releases)

    print("🎉 اكتملت المزامنة التراكمية لفضاء الروايات بنجاح تام!")

def auto_push_to_github():
    print("\n📤 فحص ورفع تحديثات فضاء الروايات إلى GitHub...")
    try:
        # فحص مجلد data/ بالكامل لضمان شمل الكاتلوج وملف الإشعارات data/new.json
        status = subprocess.run(
            ["git", "status", "--porcelain", "data/"], 
            capture_output=True, 
            text=True
        )
        if not status.stdout.strip():
            print("✨ لا توجد ملفات جديدة للرفع.")
            return

        subprocess.run(["git", "add", "data/"], check=True)
        commit_msg = f"Incremental sync: Riwyat & New Releases ({time.strftime('%Y-%m-%d %H:%M')})"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "pull", "--rebase"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("⚡ تم الرفع بنجاح إلى المستودع!")
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ أثناء الرفع لـ Git: {e}")

if __name__ == "__main__":
    sync_riwyat()
    auto_push_to_github()
