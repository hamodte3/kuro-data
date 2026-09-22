import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://azorafly.com"
DATA_DIR = "data"
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

# عدد الأعمال التي سنجهز فصولها في ملفات منفصلة
DETAILS_SYNC_LIMIT = 20
MAX_PAGES_SAFETY = 40  # تغطية الفهرس العام

os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
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
    return (
        url.replace("http://", "https://")
        .replace("azoramanga.com", "azorafly.com")
        .replace("/manga/", "/series/")
        .strip()
    )

def format_type(raw_type: str) -> str:
    t = raw_type.strip().lower()
    if any(k in t for k in ["novel", "رواية"]): return "رواية"
    if any(k in t for k in ["manhwa", "مانهوا"]): return "مانهوا"
    if any(k in t for k in ["manhua", "مانها"]): return "مانها"
    if any(k in t for k in ["webtoon", "ويبتون", "ويب تون"]): return "ويب تون"
    if any(k in t for k in ["comic", "كوميك"]): return "كوميك"
    if any(k in t for k in ["manga", "مانجا", "مانغا"]): return "مانغا"
    return raw_type if raw_type else "مانهوا"

def format_status(raw_status: str) -> str:
    s = raw_status.strip().lower()
    if any(k in s for k in ["ongoing", "مستمر", "مستمرة"]): return "مستمر"
    if any(k in s for k in ["completed", "مكتمل", "مكتملة"]): return "مكتمل"
    if any(k in s for k in ["hiatus", "متوقف", "متوقفة"]): return "متوقف مؤقتاً"
    return "مستمر"

def format_rating(raw_rating: str) -> str:
    clean = raw_rating.replace("★", "").replace("–", "").replace("-", "").strip()
    try:
        val = float(clean)
        return f"{val:.1f}" if val > 0 else ""
    except ValueError:
        return ""

def clean_html_text(text: str) -> str:
    if not text: return ""
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup.select("script, style, iframe, .ads, .watermark, .c-tabs-item, .post-title, .manga-action, .list-chapters, .chapters-list, ul, li, h1, h2, h3, h4, .post-status, .manga-info"):
        tag.decompose()
    return soup.get_text().strip()

def clean_description(raw_desc: str, title: str) -> str:
    cleaned = clean_html_text(raw_desc)
    lines = [l.strip() for l in cleaned.splitlines()]
    clean_lines = [
        l for l in lines 
        if l and l.lower() != title.lower() 
        and not any(bad in l.lower() for bad in ["تحديث:", "اجعل الكل مقروء", "أوضع علامة", "الفصول"])
    ]
    return "\n\n".join(clean_lines).strip() if clean_lines else "لا يوجد وصف."

def scrape_manga_details(session, manga_url: str):
    clean_url = normalize_url(manga_url).rstrip("/")
    res = session.get(clean_url, timeout=20)
    html = res.text
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("h1[itemprop=name]") or soup.select_one("h1")
    title = title_el.text.strip() if title_el else "بدون عنوان"

    cover_el = soup.select_one("img[itemprop=image]")
    cover_url = cover_el.get("src", "").strip() if cover_el else ""
    if not cover_url:
        meta_img = soup.select_one("meta[property='og:image']")
        cover_url = meta_img.get("content", "").strip() if meta_img else ""
    if cover_url and not cover_url.startswith("http"): cover_url = f"{BASE_URL}{cover_url}"
    cover_url = cover_url.replace("http://", "https://")

    status_el = soup.select_one(".post-content_item:contains(الحالة), .post-status")
    status = format_status(status_el.text if status_el else "")

    time_el = soup.find(lambda tag: tag.name in ["span", "div", "p"] and "منذ" in tag.text)
    last_update = time_el.text.strip() if time_el else ""

    novel_badge = any("رواية" in s.text.strip() for s in soup.select("span.blue, span.bg-blue"))
    is_novel = novel_badge or ("رواية" in title) or ("/novel/" in clean_url)
    type_el = soup.select_one("div:has(h1:contains(النوع)) div.inline span")
    manga_type = format_type("رواية" if is_novel else (type_el.text.strip() if type_el else "مانهوا"))

    genres = [a.text.strip() for a in soup.select(".genres-content a, .manga-tags a") if a.text.strip()]
    desc_el = soup.select_one(".review-content p, div.summary__content p, div[itemprop=description] p")
    final_desc = clean_description(str(desc_el) if desc_el else "", title)

    rate_el = soup.select_one(".score.font-bold, .post-total-rating .score")
    rating = format_rating(rate_el.text if rate_el else "")

    # استخراج قائمة روابط وأسماء الفصول فقط (بدون الدخول إليها)
    series_slug = clean_url.split("/")[-1]
    chapters_map = {}

    for a in soup.select("a[href*='/chapter-'], a[href*='/chapter_']"):
        href = a.get("href", "").strip()
        if href:
            full_url = normalize_url(href if href.startswith("http") else f"{BASE_URL}{href}")
            slug = full_url.rstrip("/").split("/")[-1].split("?")[0]
            raw_num = slug.lower().replace("chapter-", "").replace("chapter_", "").replace("_", ".").replace("-", ".")
            num_match = re.search(r"\d+(\.\d+)?", raw_num)
            clean_name = num_match.group(0) if num_match else raw_num
            chapters_map[full_url] = {"name": clean_name}

    for match in re.finditer(r"chapter-[0-9]+(?:[-._][0-9a-zA-Z]+)*", html, re.IGNORECASE):
        slug = match.group(0)
        full_url = f"{BASE_URL}/series/{series_slug}/{slug}"
        if full_url not in chapters_map:
            raw_num = slug.lower().replace("chapter-", "").replace("_", ".").replace("-", ".")
            num_match = re.search(r"\d+(\.\d+)?", raw_num)
            clean_name = num_match.group(0) if num_match else raw_num
            chapters_map[full_url] = {"name": clean_name}

    return {
        "id": series_slug,
        "title": title,
        "cover_url": cover_url,
        "description": final_desc,
        "type": manga_type,
        "status": status,
        "last_update": last_update,
        "rating": rating,
        "genres": genres,
        "is_novel": is_novel,
        "chapters": chapters_map
    }

def sync_fast():
    session = get_session()
    print("بدء المزامنة الخفيفة (Metadata Only)...")
    catalog = []
    page = 1

    # 1. سحب الفهرس العام
    while page <= MAX_PAGES_SAFETY:
        url = f"{BASE_URL}/series" if page == 1 else f"{BASE_URL}/series?page={page}"
        try:
            res = session.get(url, timeout=20)
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select("div:has(a.text-foreground[href^='/series/'])")
            if not cards: break

            new_in_page = 0
            for container in cards:
                link = container.select_one("a.text-foreground[href^='/series/']:not([href*='/chapter'])")
                if not link: continue
                title = link.text.strip()
                if "الحالة" in title or not title: continue

                manga_url = normalize_url(link.get("href", ""))
                slug = manga_url.rstrip("/").split("/")[-1]

                if not any(item["id"] == slug for item in catalog):
                    cover_el = container.select_one("img.object-cover")
                    cover = cover_el.get("src", "").strip() if cover_el else ""
                    if cover and not cover.startswith("http"): cover = f"{BASE_URL}{cover}"

                    catalog.append({
                        "id": slug,
                        "title": title,
                        "url": manga_url,
                        "cover_url": cover.replace("http://", "https://")
                    })
                    new_in_page += 1

            if new_in_page == 0: break
            page += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"خطأ في صفحة {page}: {e}")
            break

    print(f"تم فهرسة {len(catalog)} عمل بنجاح.")

    # 2. جلب تفاصيل وفهرس فصول أول 20 عملاً فقط (طلب واحد لكل عمل)
    for item in catalog[:DETAILS_SYNC_LIMIT]:
        slug = item["id"]
        file_path = os.path.join(DATA_DIR, f"{slug}.json")
        try:
            details = scrape_manga_details(session, item["url"])
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)

            item["type"] = details["type"]
            item["status"] = details["status"]
            item["rating"] = details["rating"]
            item["total_chapters"] = len(details["chapters"])
            print(f"✓ تم تجهيز: {slug} ({len(details['chapters'])} فصل)")
        except Exception as e:
            print(f"خطأ مع {slug}: {e}")

    # 3. حفظ الفهرس العام
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"\n⚡ اكتملت المزامنة الخاطفة خلال ثوانٍ معدودة!")

if __name__ == "__main__":
    sync_fast()
