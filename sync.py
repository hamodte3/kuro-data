import json
import os
import re
from bs4 import BeautifulSoup
from curl_cffi import requests

BASE_URL = "https://azorafly.com"

# قائمة الأعمال التي تريد متابعتها (مع الرابط الصحيح)
TRACKED_MANGA = [
    "https://azorafly.com/series/en-travesti-i-became-a-fake-prince1",
]

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{BASE_URL}/",
    "Connection": "keep-alive"
}

def get_session():
    # محاكاة متصفح لتخطي Cloudflare
    return requests.Session(impersonate="chrome120", headers=HEADERS)

def normalize_url(url: str) -> str:
    return (
        url.replace("http://", "https://")
        .replace("azoramanga.com", "azorafly.com")
        .replace("/manga/", "/series/")
        .strip()
    )

def filter_clean_image_urls(raw_urls: list) -> list:
    """تصفية الإعلانات والصور المشوهة بنفس منطق كوتلن"""
    cleaned = []
    for url in raw_urls:
        u = url.strip().lower()
        if not u or u.endswith(".gif"):
            continue
        if any(bad in u for bad in ["banner", "advertisement", "tracking", "pixel", "logo", "avatar"]):
            continue
        if url not in cleaned:
            cleaned.append(url)
    return cleaned

def scrape_chapter_images(session, chapter_url: str) -> list:
    """استخراج الصور بنفس كلاسات كوتلن الخاصة بـ Azora"""
    try:
        res = session.get(normalize_url(chapter_url), timeout=25)
        soup = BeautifulSoup(res.text, "html.parser")

        raw_images = []
        # نفس محددات كوتلن الخاصة بك
        selectors = [
            "div.comic-images-wrapper img[data-reader-page-image]",
            "div.comic-images-wrapper figure.image-container img",
            "img[data-reader-page-image]"
        ]
        
        comic_images = soup.select(", ".join(selectors))
        for img in comic_images:
            src = img.get("src", "").strip() or img.get("data-src", "").strip()
            if src:
                full_src = src if src.startswith("http") else f"{BASE_URL}{src}"
                raw_images.append(full_src.replace("http://", "https://"))

        # Fallback من الـ Meta
        if not raw_images:
            for meta in soup.select("section[itemprop=articleBody] meta[itemprop=image]"):
                content = meta.get("content", "").strip()
                if content:
                    raw_images.append(content.replace("http://", "https://"))

        return filter_clean_image_urls(raw_images)
    except Exception as e:
        print(f"خطأ أثناء جلب صور الفصل {chapter_url}: {e}")
        return []

def extract_chapters(session, manga_url: str) -> list:
    """استخراج قائمة الفصول والتوليد التلقائي للفصول الناقصة"""
    res = session.get(normalize_url(manga_url), timeout=25)
    html = res.text
    soup = BeautifulSoup(html, "html.parser")

    series_slug = normalize_url(manga_url).rstrip("/").split("/")[-1]
    found_chapters = {}

    # 1. استخراج الروابط من عناصر <a>
    for a in soup.select("a[href*='/chapter-'], a[href*='/chapter_']"):
        href = a.get("href", "").strip()
        if href:
            full_url = normalize_url(href if href.startswith("http") else f"{BASE_URL}{href}")
            slug = full_url.rstrip("/").split("/")[-1].split("?")[0]
            
            raw_num = (
                slug.lower()
                .replace("chapter-", "")
                .replace("chapter_", "")
                .replace("_", ".")
                .replace("-", ".")
            )
            num_match = re.search(r"\d+(\.\d+)?", raw_num)
            clean_name = num_match.group(0) if num_match else raw_num
            found_chapters[full_url] = clean_name

    # 2. استخراج الفصول من الـ HTML عبر Regex
    for match in re.finditer(r"chapter-[0-9]+(?:[-._][0-9a-zA-Z]+)*", html, re.IGNORECASE):
        slug = match.group(0)
        full_url = f"{BASE_URL}/series/{series_slug}/{slug}"
        if full_url not in found_chapters:
            raw_num = slug.lower().replace("chapter-", "").replace("_", ".").replace("-", ".")
            num_match = re.search(r"\d+(\.\d+)?", raw_num)
            clean_name = num_match.group(0) if num_match else raw_num
            found_chapters[full_url] = clean_name

    # تحويل القاموس لقائمة مرتبة
    return [{"name": name, "url": url} for url, name in found_chapters.items()]

def sync_manga(session, manga_url: str):
    clean_url = normalize_url(manga_url).rstrip("/")
    slug = clean_url.split("/")[-1]
    file_path = os.path.join(DATA_DIR, f"{slug}.json")

    existing_data = {"id": slug, "title": "", "chapters": {}}
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            existing_data = json.load(f)

    print(f"جاري فحص: {slug}")
    chapters = extract_chapters(session, clean_url)
    print(f"تم اكتشاف {len(chapters)} فصل في صفحة العمل.")

    new_count = 0
    # المرور على الفصول
    for ch in chapters:
        ch_url = ch["url"]
        ch_name = ch["name"]

        # التشييك الذكي: إذا الفصل وصوره مخزنين من قبل، نتخطاه فوراً!
        if ch_url in existing_data["chapters"]:
            continue

        print(f"--> جلب صور الفصل الجديد: {ch_name}")
        images = scrape_chapter_images(session, ch_url)
        if images:
            existing_data["chapters"][ch_url] = {
                "name": ch_name,
                "images": images
            }
            new_count += 1

    # حفظ النتائج
    if new_count > 0 or not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=2)
        print(f"تم حفظ {new_count} فصول جديدة بنجاح في {file_path}!")
    else:
        print("لا توجد أي فصول جديدة للإضافة.")

if __name__ == "__main__":
    session = get_session()
    for manga in TRACKED_MANGA:
        try:
            sync_manga(session, manga)
        except Exception as e:
            print(f"خطأ أثناء معالجة {manga}: {e}")
