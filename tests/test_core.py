import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import (
    brand_and_model, clean_product_line, contains_model, find_gallery_images,
    model_match, perceptual_hash, read_products, safe_name, save_png, process, Candidate,
)

BRANDS = {"1STPLAYER": ["1stplayer.com"], "8BitDo": ["8bitdo.com"], "A4TECH": ["a4tech.com", "bloody.com"]}


class TestCore(unittest.TestCase):
    def test_clean_txt_and_table(self):
        self.assertEqual(clean_product_line("| 1STPLAYER KM2 Keyboard Mouse Combo |"), "1STPLAYER KM2 Keyboard Mouse Combo")
        self.assertEqual(clean_product_line("| ------ |"), "")
        self.assertEqual(clean_product_line(" 12. 8BitDo Retro 108 Keyboard"), "8BitDo Retro 108 Keyboard")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "products.txt"
            p.write_text("name one\nname one\nname two\n", encoding="utf-8")
            self.assertEqual(read_products(p), ["name one", "name two"])

    def test_brands_and_models(self):
        cases = [
            ("1STPLAYER KM2 Keyboard Mouse Combo", "1STPLAYER", "KM2", []),
            ("1STPLAYER NEO87 Lavender Switch RGB Mechanical Gaming Keyboard", "1STPLAYER", "NEO87", ["lavender"]),
            ("8BitDo Retro 108 Tri-Mode Wireless Mechanical Keyboard", "8BitDo", "108", []),
            ("A4TECH BLOODY B760 LIGHT STRIKE GAMING KEYBOARD - RED LED", "A4TECH", "B760", ["red"]),
            ("A4TECH KRS-83 Wired Multimedia Keyboard", "A4TECH", "KRS-83", []),
            ("A4tech Bloody S510R Brown Switch RGB Wired Mechanical Gaming Keyboard", "A4TECH", "S510R", ["brown"]),
        ]
        for name, brand, model, variants in cases:
            with self.subTest(name=name):
                self.assertEqual(brand_and_model(name, BRANDS)[:3], (brand, model, variants))

    def test_model_match_no_wrong_variant(self):
        self.assertTrue(contains_model("/keyboard/krs-83", "KRS-83"))
        self.assertTrue(model_match("A4Tech Bloody S510R Brown Switch", "p", "A4TECH", "S510R", ["brown"]))
        self.assertFalse(model_match("A4Tech Bloody S510R Red Switch", "p", "A4TECH", "S510R", ["brown"]))
        self.assertFalse(model_match("A4Tech Bloody S510N Brown Switch", "p", "A4TECH", "S510R", ["brown"]))
        self.assertTrue(model_match("8BitDo Retro 108 keyboard", "p", "8BitDo", "108", []))

    def test_html_gallery(self):
        html = '''<html><head><meta property="og:image" content="/product-big.png"></head><body>
<h1>1STPLAYER KM2 Keyboard Mouse Combo</h1>
<div class="product-gallery"><img src="/product-side.png" alt="KM2"></div>
<img src="/home-logo.png" alt="logo">
<script type="application/ld+json">{"@type":"Product","name":"KM2","image":["https://1stplayer.com/front.jpg","https://1stplayer.com/back.jpg"]}</script>
</body></html>'''
        title, images = find_gallery_images(html, "https://1stplayer.com/product/km2")
        self.assertIn("KM2", title)
        self.assertEqual(images[0][1], 0)
        self.assertNotIn("/home-logo.png", [url for url, _ in images])
        self.assertEqual(len(images), 4)

    def test_output_png_and_pixels(self):
        with tempfile.TemporaryDirectory() as td:
            im = Image.new("RGBA", (640, 480), (255, 1, 100, 145))
            png = save_png(im, Path(td), "1STPLAYER KM2")
            self.assertTrue(png.exists())
            self.assertFalse(png.with_suffix(".webp").exists())
            with Image.open(png) as saved:
                self.assertEqual(saved.size, im.size)
                self.assertEqual(saved.getpixel((2, 3)), im.getpixel((2, 3)))
            self.assertEqual(perceptual_hash(im), perceptual_hash(im.copy()))

    def test_process_outputs_and_resume_offline(self):
        import main
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            name = "1STPLAYER KM2 Keyboard Mouse Combo"
            image_one = Image.new("RGB", (800, 800), (125, 50, 40))
            image_two = Image.new("RGB", (800, 800), (40, 125, 20))
            # Distinct geometry avoids duplicate-hash rejection.
            for x in range(400):
                for y in range(200):
                    image_one.putpixel((x, y), (1, 1, 1))
                    image_two.putpixel((700-x, 700-y), (1, 1, 1))
            inputs = [Candidate("https://1stplayer.com/img1", "https://1stplayer.com/km2", name, "official", 0),
                      Candidate("https://1stplayer.com/img2", "https://1stplayer.com/km2", name, "official", 0)]
            args = SimpleNamespace(force=False, retry_incomplete=False, max_images=2, min_side=400, max_mb=25, browser=False, official_only=True)
            with patch.object(main, "official_search", return_value=(inputs, [])) as search, \
                    patch.object(main, "download_image", side_effect=[(image_one, 800, 800), (image_two, 800, 800)]) as down:
                d = process(name, folder, BRANDS, None, args)
                self.assertEqual(len(d["images"]), 2)
                self.assertEqual(d["status"], "COMPLETE")
                self.assertTrue((folder / "1STPLAYER" / (name + " 1.png")).exists())
                self.assertFalse((folder / "1STPLAYER" / (name + " 1.webp")).exists())
                self.assertEqual(down.call_count, 2)
            with patch.object(main, "official_search", side_effect=AssertionError("should skip")):
                resumed = process(name, folder, BRANDS, None, args)
                self.assertEqual(resumed["status"], "COMPLETE")

    def test_products_of_same_brand_share_folder(self):
        import main
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            first = "1STPLAYER KM2 Keyboard Mouse Combo"
            second = "1STPLAYER NEO87 Keyboard"
            image_one = Image.new("RGB", (800, 800), (125, 50, 40))
            image_two = Image.new("RGB", (800, 800), (40, 125, 20))
            candidate_one = Candidate("https://1stplayer.com/img1", "https://1stplayer.com/km2", first, "official", 0)
            candidate_two = Candidate("https://1stplayer.com/img2", "https://1stplayer.com/neo87", second, "official", 0)
            args = SimpleNamespace(force=False, retry_incomplete=False, max_images=1, min_side=400, max_mb=25, browser=False, official_only=True)
            with patch.object(main, "official_search", side_effect=[([candidate_one], []), ([candidate_two], [])]), \
                    patch.object(main, "download_image", side_effect=[(image_one, 800, 800), (image_two, 800, 800)]):
                process(first, folder, BRANDS, None, args)
                process(second, folder, BRANDS, None, args)

            brand_folder = folder / "1STPLAYER"
            self.assertTrue((brand_folder / (first + ".png")).exists())
            self.assertTrue((brand_folder / (second + ".png")).exists())
            self.assertTrue((brand_folder / f".{first}.manifest.json").exists())
            self.assertTrue((brand_folder / f".{second}.manifest.json").exists())

    def test_windows_safe(self):
        self.assertEqual(safe_name('A4tech: Gaming \\ Keyboard?'), 'A4tech- Gaming - Keyboard-')


if __name__ == "__main__":
    unittest.main()
