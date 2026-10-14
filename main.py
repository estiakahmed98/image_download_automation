"""Official-first product image downloader. Python 3.10+.

Search results are hints, not model proof. Downloaded image metadata and origin
are recorded so that a human can review every uncertain match.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import logging
import os
import random
import re
import sys
import time
from tempfile import NamedTemporaryFile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, unquote
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, UnidentifiedImageError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
USER_AGENT = "DeshiPlusProductImageResearch/1.0 (respect robots.txt; contact site owner for permission)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*;q=0.9,*/*;q=0.8"}
BAD_IMG_WORDS = ("logo", "icon", "banner", "payment", "avatar", "sprite", "favicon", "placeholder", "loading", "badge")
COLOR_WORDS = {"red", "blue", "brown", "black", "white", "gray", "grey", "pink", "purple", "green", "lavender", "gelato", "cranberry"}
MODEL_GENERIC = {"2.4g", "5g", "5.0", "2.0", "4in1", "3in1", "rgb", "2024", "2025", "2026"}
REPORT_COLUMNS = ["product", "status", "downloaded", "reason", "folder", "completed_at"]
IMAGE_COLUMNS = ["product", "file_png", "source_type", "product_page", "image_url", "source_title", "width", "height"]


@contextmanager
def file_lock(path: Path, *, wait: bool = True):
    """OS locks are released automatically, including after a crashed terminal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        acquired = False
        try:
            while not acquired:
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if not wait:
                        raise ValueError(f"Another terminal is already using this output category: {path.parent}")
                    time.sleep(0.1)
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=".manifest-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if json.loads(temporary.read_text(encoding="utf-8")) != data:
            raise ValueError("Manifest verification failed")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def manifest_key(source_file: str) -> str:
    return source_file.replace("\\", "/").casefold()


def read_merged_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    entries = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(entries, list):
        raise ValueError(f"Merged manifest must contain a list: {path}")
    result = {}
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("source_file"), str)
                or not isinstance(entry.get("data"), dict)):
            raise ValueError(f"Invalid merged manifest entry: {path}")
        result[manifest_key(entry["source_file"])] = entry
    return result


def merge_product(path: Path, source_file: str, data: dict[str, Any]) -> None:
    # Read again inside the lock: another terminal may have added products.
    with file_lock(path.with_name(path.name + ".lock")):
        entries = read_merged_manifest(path)
        entries[manifest_key(source_file)] = {"source_file": source_file, "data": data}
        write_json(path, list(entries.values()))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_product_line(line: str) -> str:
    value = line.strip().removeprefix("\ufeff").strip()
    if not value or value.startswith("#") or re.fullmatch(r"[|:\-\s]+", value):
        return ""
    if value.startswith("|") and value.endswith("|"):
        value = value[1:-1].strip()
    if re.fullmatch(r"[|:\-\s]+", value):
        return ""
    value = re.sub(r"^\s*\d+[.)]\s+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def read_products(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    products, seen = [], set()
    for line in lines:
        product = clean_product_line(line)
        if product and product.casefold() not in seen:
            products.append(product)
            seen.add(product.casefold())
    return products


def safe_name(name: str, limit: int = 155) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    value = re.sub(r"\s+", " ", value).strip(" .")[:limit].rstrip(" .")
    if not value:
        return "Unnamed"
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        value = "_" + value
    return value


def valid_https_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return False
        h = parsed.hostname.lower()
        if h in {"localhost", "localhost.localdomain"} or h.endswith(".local"):
            return False
        if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", h):
            return False
        if ":" in h:  # No IP literals
            return False
        return "." in h
    except ValueError:
        return False


def hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def under_domains(url: str, domains: list[str]) -> bool:
    host = hostname(url)
    return any(host == d.lower().removeprefix("www.") or host.endswith("." + d.lower().removeprefix("www.")) for d in domains)


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", unquote(s).lower()).strip()


def contains_model(s: str, model: str) -> bool:
    parts = [re.escape(part) for part in normalize(model).split()]
    if not parts:
        return False
    pattern = r"(?<![a-z0-9])" + r"[\s_\-/]*".join(parts) + r"(?![a-z0-9])"
    return bool(re.search(pattern, s.lower()))


def brand_and_model(product: str, brand_domains: dict[str, list[str]]) -> tuple[str, str | None, list[str], list[str]]:
    candidate_brands = sorted(brand_domains, key=len, reverse=True)
    brand = ""
    for b in candidate_brands:
        if re.match(r"^" + re.escape(b) + r"(?=\s|\b)", product, flags=re.I):
            brand = b
            break
    # Accept alias "A4tech Bloody" -> A4TECH/Bloody models
    after = product[len(brand):].strip() if brand else product
    after = re.sub(r"^bloody\s+", "", after, flags=re.I)
    tokens = re.findall(r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)*", after)
    model = None
    for tok in tokens:
        cleaned = tok.upper()
        compact = tok.lower().replace("-", "").replace(".", "")
        if compact in MODEL_GENERIC or tok.lower() in {"1stplayer", "8bitdo", "a4tech"}:
            continue
        if (len(tok) >= 2 and re.search(r"\d", tok) and (re.search(r"[A-Za-z]", tok) or len(tok) >= 2)):
            model = cleaned
            break
    variants = sorted({tok for tok in re.findall(r"\b[a-z]+\b", product.lower()) if tok in COLOR_WORDS})
    return brand, model, variants, brand_domains.get(brand, [])


def model_match(label: str, product: str, brand: str, model: str | None, variants: list[str], *, require_brand: bool = True) -> bool:
    """Conservative textual evidence; not a guarantee image depicts the right item."""
    if not model:
        return False  # Require a manually identifiable model
    words = normalize(label)
    if not contains_model(words, model):
        return False
    if require_brand and brand and normalize(brand) not in words and not (brand.lower() == "a4tech" and "bloody" in words):
        return False
    if variants and not all(re.search(r"\b" + re.escape(v) + r"\b", words) for v in variants):
        return False
    return True


@dataclass
class Candidate:
    image_url: str
    product_page: str
    source_title: str
    source_type: str
    priority: int


class Internet:
    def __init__(self, timeout: float, delay: float):
        self.timeout = timeout
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # Respect 429; do not retry indefinitely or circumvent access restrictions.
        retry = Retry(total=2, backoff_factor=1.0, status_forcelist=[500, 502, 503, 504], allowed_methods=["GET"], respect_retry_after_header=True)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.robots: dict[str, RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        if not valid_https_url(url):
            return False
        site = urlparse(url)
        origin = f"{site.scheme}://{site.netloc}"
        if origin not in self.robots:
            rp = RobotFileParser()
            rp.set_url(origin + "/robots.txt")
            try:
                response = self.session.get(origin + "/robots.txt", timeout=min(self.timeout, 8))
                if response.status_code in {401, 403}:
                    rp.disallow_all = True
                elif response.ok:
                    rp.parse(response.text.splitlines())
                else:
                    rp.allow_all = True
                self.robots[origin] = rp
            except requests.RequestException:
                # Unknown robots policy: do not automatically bypass it.
                self.robots[origin] = None
        parser = self.robots[origin]
        return parser is not None and parser.can_fetch(USER_AGENT, url)

    def get(self, url: str, *, stream: bool = False) -> requests.Response:
        if not self.allowed(url):
            raise PermissionError(f"Disallowed/unknown robots.txt or invalid URL: {url}")
        time.sleep(self.delay + random.uniform(0, self.delay * 0.3))
        resp = self.session.get(url, timeout=self.timeout, stream=stream)
        if resp.status_code in (403, 429):
            resp.close()
            raise PermissionError(f"HTTP {resp.status_code}: {url}")
        resp.raise_for_status()
        if not valid_https_url(resp.url):
            resp.close()
            raise PermissionError("Unsafe redirect")
        return resp


def _srcset_largest(srcset: str) -> str:
    pairs = []
    for item in srcset.split(","):
        tokens = item.strip().split()
        if tokens:
            try:
                size = int(re.sub(r"[^0-9]", "", tokens[-1])) if len(tokens) > 1 else 0
            except ValueError:
                size = 0
            pairs.append((size, tokens[0]))
    return max(pairs, default=(0, ""))[1]


def find_gallery_images(html: str, page_url: str) -> tuple[str, list[tuple[str, int]]]:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.find("h1")
    meta_title = soup.find("meta", attrs={"property": "og:title"})
    title = (title_node.get_text(" ", strip=True) if title_node else "") or (meta_title.get("content", "") if meta_title else "") or (soup.title.get_text(" ", strip=True) if soup.title else "")
    images: list[tuple[str, int]] = []

    def add(value: Any, priority: int):
        if isinstance(value, list):
            for x in value:
                add(x, priority)
        elif isinstance(value, dict):
            add(value.get("url") or value.get("contentUrl"), priority)
        elif isinstance(value, str):
            u = urljoin(page_url, value.strip())
            if valid_https_url(u) and not any(w in unquote(urlparse(u).path).lower() for w in BAD_IMG_WORDS):
                images.append((u, priority))

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
            if isinstance(obj, dict):
                kind = obj.get("@type", "")
                if ("Product" in kind) if isinstance(kind, (str, list)) else False:
                    add(obj.get("image"), 0)
                stack.extend(v for k, v in obj.items() if k in ("@graph", "mainEntity"))

    for prop in ("og:image", "og:image:secure_url", "twitter:image"):
        tags = soup.find_all("meta", attrs={"property": prop}) + soup.find_all("meta", attrs={"name": prop})
        for tag in tags:
            add(tag.get("content"), 1)

    gallery_marker = re.compile(r"product.{0,25}(gallery|media|image)|gallery|woocommerce-product-gallery|swiper-slide", re.I)
    for tag in soup.find_all("img"):
        classes = " ".join(tag.get("class", []))
        ancestor = tag.find_parent(class_=gallery_marker)
        alt = tag.get("alt", "")
        src = tag.get("src") or tag.get("data-src") or tag.get("data-original") or tag.get("data-zoom-image")
        gallery = bool(gallery_marker.search(classes) or ancestor or tag.get("data-media-id"))
        # Do not scrape unrelated recommended-product tiles from generic pages.
        if not (gallery or (alt and normalize(title) and normalize(title) in normalize(alt))):
            continue
        for attr in ("data-zoom-image", "data-full-size", "data-original", "data-src", "src"):
            add(tag.get(attr), 2 if gallery else 3)
        for attr in ("srcset", "data-srcset"):
            if tag.get(attr):
                add(_srcset_largest(tag[attr]), 2 if gallery else 3)
    # Stable deduplication preserving best priority.
    unique: dict[str, int] = {}
    for url, priority in images:
        unique[url] = min(priority, unique.get(url, 999))
    return title, sorted(unique.items(), key=lambda x: x[1])


def browser_html(url: str, timeout: int) -> str:
    """Optional, user-enabled Chromium rendering for JavaScript-only galleries."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.wait_for_timeout(1400)
            return page.content()
        finally:
            browser.close()


def page_candidates(page_url: str, product: str, brand: str, model: str, variants: list[str], net: Internet, *, browser: bool) -> list[Candidate]:
    response = net.get(page_url)
    if "html" not in response.headers.get("Content-Type", "").lower() and "<!doctype" not in response.text[:150].lower():
        return []
    title, images = find_gallery_images(response.text, response.url)
    if not model_match(title, product, brand, model, variants, require_brand=False):
        return []
    if browser and len(images) < 5:
        try:
            dyn_title, dyn_images = find_gallery_images(browser_html(response.url, int(net.timeout)), response.url)
            if model_match(dyn_title, product, brand, model, variants, require_brand=False):
                images += dyn_images
        except Exception as ex:
            logging.debug("Browser render failed for %s: %s", response.url, ex)
    seen, choices = set(), []
    for image_url, priority in sorted(images, key=lambda x: x[1]):
        if image_url not in seen:
            seen.add(image_url)
            choices.append(Candidate(image_url, response.url, title, "official", priority))
    return choices[:55]


def ddgs_results(method: str, query: str, max_results: int) -> list[dict[str, Any]]:
    from ddgs import DDGS
    search = DDGS(timeout=15)
    if method == "text":
        return list(search.text(query, max_results=max_results, safesearch="moderate"))
    return list(search.images(query, max_results=max_results, safesearch="moderate"))


def official_search(product: str, brand: str, model: str | None, variants: list[str], domains: list[str], net: Internet, browser: bool) -> tuple[list[Candidate], list[str]]:
    candidates = []
    errors = []
    if not domains or not model:
        return candidates, ["Brand's official domain or model not configured/identified"]
    for domain in domains:
        try:
            found = ddgs_results("text", f'site:{domain} "{model}" product', 8)
        except Exception as ex:
            errors.append(f"Official search failed ({domain}): {ex}")
            continue
        for item in found:
            page = item.get("href") or item.get("url") or ""
            if not valid_https_url(page) or not under_domains(page, domains):
                continue
            if not contains_model(normalize(page + " " + item.get("title", "")), model):
                continue
            try:
                candidates.extend(page_candidates(page, product, brand, model, variants, net, browser=browser))
            except (requests.RequestException, PermissionError, ValueError) as ex:
                errors.append(f"Cannot open official page: {type(ex).__name__}: {page}")
            if len(candidates) >= 12:
                break
        if len(candidates) >= 12:
            break
    candidates.sort(key=lambda c: c.priority)
    return candidates, errors


def fallback_search(product: str, brand: str, model: str | None, variants: list[str], domains: list[str]) -> tuple[list[Candidate], list[str]]:
    errors = []
    if not model:
        return [], ["No reliable model token; fallback skipped"]
    result = []
    for query in (f'"{product}" product photo', f'"{brand} {model}" product images'):
        try:
            images = ddgs_results("images", query, 25)
        except Exception as ex:
            errors.append(f"Image search error: {ex}")
            continue
        for item in images:
            img = item.get("image", "")
            source = item.get("url") or item.get("source") or ""
            title = item.get("title", "")
            if not valid_https_url(img) or not valid_https_url(source):
                continue
            # Require exact model and variant in a source's descriptive context.
            # Filename alone is not enough if the result title is a generic image.
            label = " ".join([title, unquote(urlparse(source).path)])
            if not model_match(label, product, brand, model, variants, require_brand=True):
                continue
            trusted = under_domains(source, domains)
            if not trusted and ("stock" in title.lower() or "wallpaper" in title.lower()):
                continue
            result.append(Candidate(img, source, title, "official_image_search" if trusted else "third_party_review", 4 if trusted else 8))
        if len(result) >= 20:
            break
    # Third-party images demand human rights/identity review before publishing.
    unique = {}
    for c in result:
        unique.setdefault(c.image_url, c)
    return sorted(unique.values(), key=lambda c: c.priority)[:50], errors


def perceptual_hash(im: Image.Image) -> int:
    px = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    values = list(px.getdata())
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (values[y * 9 + x] > values[y * 9 + x + 1])
    return bits


def download_image(url: str, net: Internet, max_bytes: int, min_side: int) -> tuple[Image.Image, int, int] | None:
    response = net.get(url, stream=True)
    try:
        ctype = response.headers.get("Content-Type", "").lower()
        if ctype and not (ctype.startswith("image/") or "octet-stream" in ctype):
            return None
        length = int(response.headers.get("Content-Length", "0") or 0)
        if length > max_bytes:
            return None
        b = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            b.extend(chunk)
            if len(b) > max_bytes:
                return None
        try:
            with Image.open(io.BytesIO(b)) as loaded:
                if getattr(loaded, "is_animated", False):
                    loaded.seek(0)
                loaded.load()
                im = ImageOps.exif_transpose(loaded)
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGBA" if "transparency" in im.info else "RGB")
                else:
                    im = im.copy()
        except (UnidentifiedImageError, OSError, ValueError):
            return None
        w, h = im.size
        if min(w, h) < min_side or max(w, h) / max(1, min(w, h)) > 5:
            return None
        return im, w, h
    finally:
        response.close()


def valid_png(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                return False
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def save_png(image: Image.Image, destination: Path, base: str) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    png = destination / f"{base}.png"
    temporary = None
    try:
        with NamedTemporaryFile(dir=destination, prefix=".image-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        image.save(temporary, format="PNG", optimize=True)
        if not valid_png(temporary):
            raise ValueError("PNG validation failed")
        temporary.replace(png)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return png


def local_image(target: Path, filename: Any) -> Path | None:
    if (not isinstance(filename, str) or not filename or filename in {".", ".."}
            or "/" in filename or "\\" in filename or ":" in filename
            or Path(filename).name != filename):
        return None
    path = target / filename
    # Reject symlinks that point outside the brand folder.
    return path if path.resolve().parent == target.resolve() else None


def prepare_existing_image(row: dict[str, Any], target: Path) -> dict[str, Any] | None:
    """Convert old JPG/WebP records and remove copies only after validating PNG."""
    original = local_image(target, row.get("png")) or local_image(target, row.get("webp"))
    if original is None:
        return None
    png = original.with_suffix(".png")
    if local_image(target, png.name) is None:
        return None
    alternatives = [original, local_image(target, row.get("webp"))]
    if target.is_dir():
        alternatives.extend(file for file in target.iterdir()
                            if file.stem.casefold() == png.stem.casefold()
                            and file.suffix.lower() in {".jpg", ".jpeg", ".webp"}
                            and local_image(target, file.name) is not None)
    if not valid_png(png):
        for source in alternatives:
            if source is None or not source.is_file():
                continue
            try:
                with Image.open(source) as image:
                    image.seek(0)
                    image = ImageOps.exif_transpose(image)
                    image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
                    save_png(image, target, png.stem)
                break
            except (OSError, ValueError) as ex:
                logging.warning("Cannot convert %s: %s", source, ex)
        if not valid_png(png):
            return None
    with Image.open(png) as image:
        image.load()
        width, height = image.size
    for source in set(alternatives):
        if source is not None and source != png and source.suffix.lower() in {".jpg", ".jpeg", ".webp"}:
            source.unlink(missing_ok=True)
    result = dict(row)
    result.pop("webp", None)
    result.update(png=png.name, width=width, height=height)
    return result


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def process(product: str, out_dir: Path, domains: dict[str, list[str]], net: Internet,
            args: argparse.Namespace, existing_data: dict[str, Any] | None = None) -> dict[str, Any]:
    brand, model, variants, official_domains = brand_and_model(product, domains)
    target = out_dir / safe_name(brand or "Unknown")
    manifest_path = target / f".{safe_name(product)}.manifest.json"
    old = existing_data or {}
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(old, dict):
                raise ValueError(f"Invalid product manifest: {manifest_path}")
        except (OSError, json.JSONDecodeError) as ex:
            logging.warning("Cannot read %s: %s", manifest_path, ex)
            old = existing_data or {}
    if old and old.get("product") != product:
        raise ValueError(f"Product filename collision: {product} and {old.get('product')}")
    if args.force:
        for row in old.get("images", []):
            for key in ("png", "webp"):
                original = local_image(target, row.get(key))
                if original is not None:
                    original.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        old = {}
    errors: list[str] = []
    downloaded: list[dict[str, Any]] = []
    seen_hashes: list[int] = []
    seen_urls: set[str] = set()
    for row in old.get("images", []):
        prepared = prepare_existing_image(row, target)
        if prepared is not None:
            downloaded.append(prepared)
            if prepared.get("image_url"):
                seen_urls.add(prepared["image_url"])
            with Image.open(target / prepared["png"]) as image:
                seen_hashes.append(perceptual_hash(image))
    if downloaded and len(downloaded) == len(old.get("images", [])) and (
            not args.retry_incomplete or len(downloaded) >= args.max_images):
        data = dict(old, images=downloaded)
        write_json(manifest_path, data)
        logging.info("SKIP (resume, PNG ready) %s", product)
        return data
    official, errs = official_search(product, brand, model, variants, official_domains, net, args.browser)
    errors.extend(errs)
    candidates = official
    # Fallback only after exhausting download-eligible official galleries.
    def take(choices: list[Candidate]) -> None:
        for choice in choices:
            if len(downloaded) >= args.max_images:
                break
            if choice.image_url in seen_urls:
                continue
            seen_urls.add(choice.image_url)
            try:
                image_data = download_image(choice.image_url, net, args.max_mb * 1024 * 1024, args.min_side)
                if not image_data:
                    continue
                im, w, h = image_data
                hval = perceptual_hash(im)
                if any((hval ^ old).bit_count() <= 2 for old in seen_hashes):
                    continue
                idx = len(downloaded)
                used_names = {row["png"].casefold() for row in downloaded}
                idx = 0
                while True:
                    base = safe_name(product) + ("" if idx == 0 else f" {idx}")
                    if (base + ".png").casefold() not in used_names:
                        break
                    idx += 1
                png = save_png(im, target, base)
                seen_hashes.append(hval)
                downloaded.append({
                    "png": png.name, "image_url": choice.image_url,
                    "product_page": choice.product_page, "source_title": choice.source_title,
                    "source_type": choice.source_type, "width": w, "height": h,
                })
                logging.info("  IMAGE %d/%d %sx%s %s", len(downloaded), args.max_images, w, h, choice.source_type)
            except (OSError, PermissionError, requests.RequestException, ValueError) as ex:
                logging.debug("Image unavailable %s (%s)", choice.image_url, ex)
                continue

    take(candidates)
    if len(downloaded) < args.max_images and not args.official_only:
        fallback, errs = fallback_search(product, brand, model, variants, official_domains)
        errors.extend(errs)
        take(fallback)
    if not model:
        reason = "Unclear model number: manual review required"
    elif not downloaded:
        reason = "No model-verified downloadable images (check search, page access and image rights)"
    elif len(downloaded) < args.max_images:
        reason = "Fewer than requested images; review recommended"
    else:
        reason = ""
    third_party = any(r["source_type"] == "third_party_review" for r in downloaded)
    status = "REVIEW_RIGHTS" if third_party else ("COMPLETE" if len(downloaded) >= args.max_images else ("PARTIAL" if downloaded else "FAILED"))
    data = {
        "product": product, "brand": brand, "model": model, "variants": variants,
        "status": status, "reason": reason, "images": downloaded,
        "errors": errors[:12], "completed_at": now_iso(),
    }
    write_json(manifest_path, data)
    return data


def run(args: argparse.Namespace) -> None:
    source = Path(args.input).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"File not found: {source}")
    categories = [safe_name(x) for x in re.split(r"[/\\]+", args.category) if x.strip() not in ("", ".", "..")]
    output_root = Path(args.output).expanduser().resolve()
    path = output_root.joinpath(*categories)
    path.mkdir(parents=True, exist_ok=True)
    brands_path = Path(args.brands).expanduser().resolve()
    brands = json.loads(brands_path.read_text(encoding="utf-8"))
    if not isinstance(brands, dict):
        raise ValueError("brands.json must contain a dictionary")
    products = read_products(source)
    if args.limit:
        products = products[:args.limit]
    print(f"Products: {len(products)} | output: {path}")
    net = Internet(args.timeout, args.delay)
    report, image_report = [], []
    for index, product in enumerate(products, start=1):
        logging.info("[%d/%d] %s", index, len(products), product)
        try:
            data = process(product, path, brands, net, args)
        except Exception as ex:
            logging.exception("Unexpected error for %s", product)
            data = {"product": product, "status": "FAILED", "images": [], "reason": f"Unexpected error: {ex}", "completed_at": now_iso()}
        images = data.get("images", [])
        brand = brand_and_model(product, brands)[0]
        report.append({
            "product": product, "status": data.get("status", "FAILED"),
            "downloaded": len(images), "reason": data.get("reason", ""),
            "folder": str(path / safe_name(brand or "Unknown")), "completed_at": data.get("completed_at", now_iso()),
        })
        for im in images:
            image_report.append({
                "product": product, "file_png": im["png"], "file_webp": im["webp"],
                "source_type": im["source_type"], "product_page": im["product_page"],
                "image_url": im["image_url"], "source_title": im["source_title"],
                "width": im["width"], "height": im["height"],
            })
        write_csv(output_root / "reports" / "products_status.csv", REPORT_COLUMNS, report)
        write_csv(output_root / "reports" / "image_sources.csv", IMAGE_COLUMNS, image_report)
        count = len(images)
        print(f"{index:>4}/{len(products)} | {data.get('status')} | {count} images | {product}")
    print("Done. Reports:", output_root / "reports")


def cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download product photos from official brand product pages first, and exact-model image search second.")
    p.add_argument("--input", default=str(ROOT / "Products Name.txt"))
    p.add_argument("--brands", default=str(ROOT / "brands.json"))
    p.add_argument("--output", default=str(ROOT / "output"))
    p.add_argument("--category", default="Accessories/Keyboard")
    p.add_argument("--max-images", type=int, default=5, help="TOTAL distinct images, 1-5. Example 5 -> base and suffixes 1..4")
    p.add_argument("--limit", type=int, default=0, help="0 processes all products")
    p.add_argument("--min-side", type=int, default=400, help="Smallest dimension in pixels; reduce if images are skipped")
    p.add_argument("--max-mb", type=int, default=25)
    p.add_argument("--timeout", type=float, default=20)
    p.add_argument("--delay", type=float, default=1.5, help="Polite pause between website requests")
    p.add_argument("--official-only", action="store_true", help="Never fetch photos from third-party sites")
    p.add_argument("--browser", action="store_true", help="Render JS product galleries via optional Playwright+Chromium")
    p.add_argument("--retry-incomplete", action="store_true", help="Retry products with fewer than max-images (keep existing files)")
    p.add_argument("--force", action="store_true", help="Redo completed products and overwrite pairs from image 1")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    if not 1 <= args.max_images <= 20:
        p.error("--max-images must be 1 to 20")
    if args.min_side < 50 or args.max_mb < 1 or args.delay < 0 or args.limit < 0:
        p.error("Invalid limits")
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.DEBUG if args.verbose else logging.INFO)
    return args


if __name__ == "__main__":
    try:
        run(cli())
    except (OSError, ValueError) as ex:
        print(f"ERROR: {ex}", file=sys.stderr)
        sys.exit(1)
