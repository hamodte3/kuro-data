import json
import os

DATA_ROOT = "data"

def enrich_all_catalogs():
    for source in os.listdir(DATA_ROOT):
        source_dir = os.path.join(DATA_ROOT, source)
        catalog_path = os.path.join(source_dir, "catalog.json")

        if not os.path.isdir(source_dir) or not os.path.exists(catalog_path):
            continue

        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        except Exception:
            continue

        updated_count = 0
        for item in catalog:
            slug = item.get("id")
            if not slug:
                continue

            # إذا كان العمل يحتوي على ملف تفاصيل فردي، نسحب منه الـ genres
            detail_file = os.path.join(source_dir, f"{slug}.json")
            if os.path.exists(detail_file):
                try:
                    with open(detail_file, "r", encoding="utf-8") as df:
                        detail_data = json.load(df)
                        genres = detail_data.get("genres", [])
                        if genres:
                            item["genres"] = genres
                            updated_count += 1
                except Exception:
                    pass
            
            # ضمان وجود الحقل كقائمة حتى لا يضرب كود أندرويد
            if "genres" not in item:
                item["genres"] = []

        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, indent=2)

        print(f"✓ المصدر [{source}]: تم حقن التصنيفات في {updated_count} عمل داخل catalog.json")

if __name__ == "__main__":
    enrich_all_catalogs()
