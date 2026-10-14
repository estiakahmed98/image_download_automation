import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "output"
OUTPUT = ROOT / "all_products_manifest.json"

all_products = []
failed = []
source_files = []

# Step 1: Read all manifest files
for file in SOURCE.rglob("*.manifest.json"):
    try:
        with open(file, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

        all_products.append({
            "source_file": str(file.relative_to(SOURCE)),
            "data": data
        })

        source_files.append(file)
        print(f"[OK] {file.name}")

    except Exception as e:
        failed.append((str(file), str(e)))
        print(f"[FAILED] {file.name}: {e}")

# Step 2: Stop if any file failed
if failed:
    print("\nMerge stopped! Some files could not be read.")
    print("No original JSON files were deleted.")
    raise SystemExit(1)

if not source_files:
    print("\nNo manifest files found.")
    raise SystemExit(0)

# Step 3: Save merged JSON safely
TEMP = ROOT / "all_products_manifest.json.tmp"

try:
    with open(TEMP, "w", encoding="utf-8") as f:
        json.dump(all_products, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())

    # Step 4: Verify saved JSON
    with open(TEMP, "r", encoding="utf-8") as f:
        verified = json.load(f)

    assert verified == all_products
    assert len(verified) == len(source_files)

    # Replace output only after verification
    os.replace(TEMP, OUTPUT)

except Exception as e:
    print(f"\nMerge verification failed: {e}")
    print("Original files preserved.")
    TEMP.unlink(missing_ok=True)
    raise SystemExit(1)

# Step 5: Delete original manifest files
deleted = 0
delete_failed = 0

for file in source_files:
    try:
        file.unlink()
        deleted += 1
        print(f"[DELETED] {file.name}")

    except OSError as e:
        delete_failed += 1
        print(f"[DELETE FAILED] {file.name}: {e}")

print("\n========== COMPLETED ==========")
print(f"Total Merged: {len(all_products)}")
print(f"Deleted: {deleted}")
print(f"Delete Failed: {delete_failed}")
print(f"Saved: {OUTPUT}")