from pathlib import Path

ROOT = Path(__file__).resolve().parent
IMAGE_FOLDER = ROOT / "output" / "Accessories" / "Keyboard"

deleted = 0
skipped = 0
failed = 0

for webp in IMAGE_FOLDER.rglob("*"):
    if not webp.is_file() or webp.suffix.lower() != ".webp":
        continue

    png = webp.with_suffix(".png")

    # Delete only when matching PNG exists and is not empty
    if png.exists() and png.stat().st_size > 0:
        try:
            webp.unlink()
            print(f"[DELETED] {webp.name}")
            deleted += 1
        except OSError as e:
            print(f"[FAILED] {webp.name}: {e}")
            failed += 1
    else:
        print(f"[SKIP] No matching PNG: {webp.name}")
        skipped += 1

print("\n========== COMPLETED ==========")
print(f"Deleted WebP: {deleted}")
print(f"Skipped: {skipped}")
print(f"Failed: {failed}")