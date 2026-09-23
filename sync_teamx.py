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

# تجربة مبدئية لأول 10 أعمال لفحص السحابة
DETAILS_SYNC_LIMIT = 10
MAX_PAGES_SAFETY = 125

os.makedirs(DATA_DIR, exist_ok=True)

def get_session():
    # محاكاة متصفح Chrome حديث لتجاوز فحص Cloudflare
    session = requests.Session(impersonate="chrome124")
    session.headers.update({
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Referer": "https://olympustaff.com/",
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

def scrape_teamx_details(session, slug: str) -> dict:
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

    chapters_map = {}
    number_regex = re.compile(r"\d+(\.\d+)?")
    found_numbers = set()

    for a in soup.select("div.enhanced-chapters-grid div.chapter-card a.chapter-link, div.chapter-card a.chapter-link"):
        href = normalize_url(a.get("href", "")).rstrip("/")
        if f"/series/{slug}/" in href:
            num_node = a.select_one(".chapter-number") or a.select_one(".chapter-title")
            raw_num = num_node.get_text(strip=True) if num_node else href.split("/")[-1]
            match = number_regex.search(raw_num)
            clean_num = match.group(0) if match else raw_num
            chapters_map[href] = {"name": clean_num}
            try:
                found_numbers.add(int(float(clean_num)))
            except ValueError:
                pass

    total_match = re.search(r"(?:قائمة الفصول|الفصول)\s*\(([0-9]+)\)", html)
    total_count = int(total_match.group(1)) if total_match else 0
    max_ch = max(max(found_numbers) if found_numbers else 0, total_count)

    if max_ch > 0:
        for i in range(1, max_ch + 1):
            ch_url = f"{BASE_URL}/series/{slug}/{i}"
            if ch_url not in chapters_map:
                chapters_map[ch_url] = {"name": str(i)}

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
        "chapters": chapters_map
    }

def sync_teamx():
    session = get_session()
    print("🚀 بدء مزامنة تجريبية لمصدر Team X...")
    catalog = []

    for page in range(1, MAX_PAGES_SAFETY + 1):
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
            title = a_tag.get("title", "").strip() or slug
            img = it.select_one("img")
            cover = normalize_url(img.get("src", "") or img.get("data-src", ""))

            if not any(c["id"] == slug for c in catalog):
                catalog.append({
                    "id": slug,
                    "title": title,
                    "url": href,
                    "cover_url": cover,
                    "type": "مانهوا",
                    "status": "مستمر",
                    "rating": ""
                })

        print(f"Team X [صفحة {page}]: تم جمع {len(catalog)} عمل.")
        time.sleep(1)

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    targets = catalog[:DETAILS_SYNC_LIMIT]
    for idx, item in enumerate(targets, 1):
        slug = item["id"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        try:
            details = scrape_teamx_details(session, slug)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)
            print(f"✓ [{idx}/{len(targets)}] تم سحب Team X: {slug} ({len(details['chapters'])} فصل)")
            time.sleep(1)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {slug}: {e}")

    print("\n⚡ اكتملت مزامنة البيانات وحفظها محلياً في مجلد data/teamx!")

# 🎯 الدالة المسؤولة عن الرفع لـ GitHub تلقائياً
def auto_push_to_github():
    print("\n📤 جاري فرض رفع التحديثات إلى GitHub إجبارياً...")
    try:
        # فحص وجود أي تغييرات في مجلد البيانات
        status = subprocess.run(["git", "status", "--porcelain", "data/teamx/"], capture_output=True, text=True)
        if not status.stdout.strip():
            print("✨ لا توجد أي بيانات جديدة للرفع.")
            return

        # إضافة التغييرات والالتزام
        subprocess.run(["git", "add", "data/teamx/"], check=True)
        commit_msg = f"Force update: TeamX data ({time.strftime('%Y-%m-%d %H:%M')})"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)

        # 🎯 دفع إجباري مباشر يتجاوز أي فوارق أو تعارضات على السيرفر
        subprocess.run(["git", "push", "origin", "main", "--force"], check=True)
        print("⚡ تم فرض الرفع (Force Push) بنجاح إلى المستودع!")
    except subprocess.CalledProcessError as e:
        print(f"❌ حدث خطأ أثناء الرفع الإجباري: {e}")

if __name__ == "__main__":
    sync_teamx()
    auto_push_to_github()
