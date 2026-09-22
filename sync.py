import json
import os
from bs4 import BeautifulSoup
from curl_cffi import requests

# قائمة روابط المانجا التي تريد متابعتها وتحديثها
TRACKED_MANGA = [
    "https://azoramanga.com/manga/can-i-cry-now/",
]

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

def get_session():
    # محاكاة متصفح حقيقي لتجاوز Cloudflare تلقائياً
    return requests.Session(impersonate="chrome120")

def scrape_chapter_images(session, chapter_url):
    """استخراج روابط الصور لفصل محدد"""
    res = session.get(chapter_url)
    soup = BeautifulSoup(res.text, "html.parser")
    images = []
    for img in soup.select(".page-break img, .reading-content img"):
        src = img.get("data-src") or img.get("src")
        if src:
            images.append(src.strip())
    return images

def sync_manga(session, manga_url):
    slug = manga_url.strip("/").split("/")[-1]
    file_path = os.path.join(DATA_DIR, f"{slug}.json")

    existing_data = {"id": slug, "title": "", "chapters": {}}
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            existing_data = json.load(f)

    # فحص صفحة العمل في الموقع
    res = session.get(manga_url)
    soup = BeautifulSoup(res.text, "html.parser")

    title_elem = soup.select_one(".post-title h1")
    if title_elem:
        existing_data["title"] = title_elem.text.strip()

    chapter_items = soup.select(".wp-manga-chapter a")
    new_chapters_found = 0

    # المرور على الفصول من الأقدم إلى الأحدث
    for item in reversed(chapter_items):
        ch_url = item.get("href")
        ch_title = item.text.strip()

        # إذا كان الفصل مسجلاً بصوره مسبقاً، نتخطاه فوراً
        if ch_url in existing_data["chapters"]:
            continue

        print(f"فصل جديد: {ch_title} - جاري استخراج الصور...")
        images = scrape_chapter_images(session, ch_url)
        
        if images:
            existing_data["chapters"][ch_url] = {
                "title": ch_title,
                "images": images
            }
            new_chapters_found += 1

    # حفظ التحديثات إذا تم العثور على فصول جديدة
    if new_chapters_found > 0 or not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=2)
        print(f"تم تحديث {new_chapters_found} فصل لـ {slug}")
    else:
        print(f"لا توجد فصول جديدة لـ {slug}")

if __name__ == "__main__":
    session = get_session()
    for url in TRACKED_MANGA:
        try:
            sync_manga(session, url)
        except Exception as e:
            print(f"خطأ أثناء معالجة {url}: {e}")
