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

DETAILS_SYNC_LIMIT = 20  # تجهيز أفضل 20 رواية سحابياً
MAX_PAGES_SAFETY = 5     # فحص أول 5 صفحات من المكتبة

os.makedirs(DATA_DIR, exist_ok=True)

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

def scrape_riwyat_details(session, slug: str, manga_url: str) -> dict:
    res = session.get(manga_url, timeout=25)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code}")

    soup = BeautifulSoup(res.text, "html.parser")

    title_node = soup.select_one("h1.nhv-novel-title")
    title = title_node.get_text(strip=True) if title_node else slug

    cover_node = soup.select_one(".nhv-novel-cover img")
    cover_url = normalize_url(cover_node.get("src", "") if cover_node else "")

    desc_node = soup.select_one(".nhv-novel-synopsis")
    description = desc_node.get_text("\n", strip=True) if desc_node else "لا يوجد وصف"

    rating_node = soup.select_one(".nhv-simple-rating__avg")
    rating = rating_node.get_text(strip=True) if rating_node else ""

    genres = [a.get_text(strip=True) for a in soup.select(".nhv-novel-genres a")]

    # 🎯 جلب كافة الفصول عبر مسار Madara AJAX
    ajax_chapters_url = f"{BASE_URL}/cont/{slug}/ajax/chapters/"
    ch_res = session.post(ajax_chapters_url, timeout=25)
    
    chapters_map = {}
    ch_soup = BeautifulSoup(ch_res.text, "html.parser") if ch_res.status_code == 200 else soup

    # قراءة عناصر الفصول
    ch_elements = ch_soup.select("li.wp-manga-chapter a")
    number_regex = re.compile(r"\d+(\.\d+)?")

    for a in ch_elements:
        href = normalize_url(a.get("href", "")).rstrip("/")
        raw_text = a.get_text(strip=True)
        num_match = number_regex.search(raw_text)
        clean_num = num_match.group(0) if num_match else raw_text
        if href and href not in chapters_map:
            chapters_map[href] = {"name": clean_num}

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
    print("🚀 بدء مزامنة مصدر فضاء الروايات (Riwyat)...")
    catalog = []

    for page in range(1, MAX_PAGES_SAFETY + 1):
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
            title = a_tag.get_text(strip=True) or slug
            img = card.select_one(".nhv-library-card__cover img")
            cover = normalize_url(img.get("src", "") if img else "")

            if not any(c["id"] == slug for c in catalog):
                catalog.append({
                    "id": slug,
                    "title": title,
                    "url": href,
                    "cover_url": cover,
                    "type": "رواية",
                    "status": "مستمر",
                    "rating": ""
                })

        print(f"فضاء الروايات [صفحة {page}]: تم جمع {len(catalog)} رواية.")
        time.sleep(0.5)

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    targets = catalog[:DETAILS_SYNC_LIMIT]
    for idx, item in enumerate(targets, 1):
        slug = item["id"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        try:
            details = scrape_riwyat_details(session, slug, item["url"])
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)
            print(f"✓ [{idx}/{len(targets)}] تم سحب الرواية: {slug} ({len(details['chapters'])} فصل)")
            time.sleep(0.5)
        except Exception as e:
            print(f"خطأ أثناء تجهيز {slug}: {e}")

    print("\n⚡ اكتملت مزامنة فضاء الروايات وحفظت في data/riwyat!")

def auto_push_to_github():
    print("\n📤 جاري فحص ورفع تحديثات فضاء الروايات إلى GitHub...")
    try:
        status = subprocess.run(["git", "status", "--porcelain", "data/riwyat/"], capture_output=True, text=True)
        if not status.stdout.strip():
            print("✨ لا توجد ملفات جديدة للرفع.")
            return

        subprocess.run(["git", "add", "data/riwyat/"], check=True)
        commit_msg = f"Force update: Riwyat novels data ({time.strftime('%Y-%m-%d %H:%M')})"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "push", "origin", "main", "--force"], check=True)
        print("⚡ تم فرض الرفع (Force Push) بنجاح إلى المستودع!")
    except subprocess.CalledProcessError as e:
        print(f"❌ خطأ أثناء الرفع لـ Git: {e}")

if __name__ == "__main__":
    sync_riwyat()
    auto_push_to_github()
