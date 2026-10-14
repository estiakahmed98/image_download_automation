import argparse
from pathlib import Path
from tempfile import NamedTemporaryFile
from PIL import Image, ImageOps, UnidentifiedImageError

# Resolve relative to this script, regardless of the terminal's current folder.
IMAGE_FOLDER = Path(__file__).resolve().parent / "output"

# Supported formats
FORMATS = {".jpg", ".jpeg", ".webp"}


def is_valid_png(path):
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                return False
            image.verify()
        return True
    except (OSError, ValueError, UnidentifiedImageError):
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Convert images in output and all subfolders to PNG. Originals are kept."
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Recreate PNG files even when a valid PNG already exists."
    )
    args = parser.parse_args()

    if not IMAGE_FOLDER.is_dir():
        print(f"[ERROR] Image folder does not exist: {IMAGE_FOLDER}")
        return 1

    files = sorted(
        file for file in IMAGE_FOLDER.rglob("*")
        if file.is_file() and file.suffix.lower() in FORMATS
    )
    print(f"Image folder: {IMAGE_FOLDER}")
    print(f"Found {len(files)} images to check.", flush=True)

    converted = 0
    skipped = 0
    failed = 0

    for index, file in enumerate(files, start=1):
        output = file.with_suffix(".png")
        relative_path = file.relative_to(IMAGE_FOLDER)
        prefix = f"[{index}/{len(files)}]"

        if not args.overwrite and is_valid_png(output):
            print(f"{prefix} [ALREADY PNG] {output.relative_to(IMAGE_FOLDER)}", flush=True)
            skipped += 1
            continue

        temporary_path = None
        try:
            with Image.open(file) as source:
                # Animated inputs become a static PNG of their first frame.
                source.seek(0)
                img = ImageOps.exif_transpose(source)
                has_alpha = "A" in img.getbands() or "transparency" in img.info
                img = img.convert("RGBA" if has_alpha else "RGB")
                # Finish writing before replacing an existing PNG.
                with NamedTemporaryFile(
                    dir=output.parent, prefix=".convert-", suffix=".tmp", delete=False
                ) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                img.save(temporary_path, format="PNG")
                if not is_valid_png(temporary_path):
                    raise ValueError("PNG validation failed")
                temporary_path.replace(output)

            print(f"{prefix} [OK] {relative_path} -> {output.name}", flush=True)
            converted += 1

        except (OSError, ValueError, UnidentifiedImageError) as e:
            print(f"{prefix} [FAILED] {relative_path}: {e}", flush=True)
            failed += 1
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    print("\nConversion completed!")
    print(f"Converted: {converted}")
    print(f"Already converted (valid PNG exists): {skipped}")
    print(f"Failed: {failed}")
    print("Original WebP/JPG/JPEG files are kept alongside their PNG copies.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
