# Deshi Plus — Product Image Downloader

`main.py` এখন download, PNG conversion, JPG/WebP duplicate cleanup এবং manifest merge একসঙ্গে করে। আলাদা করে `convert_to_png.py`, `delete_webp.py` বা `merge_json.py` চালাতে হবে না। নতুন image সরাসরি PNG হিসেবে save হয়; resolution ও transparency বজায় থাকে, upscale/crop করা হয় না। Official product-page search ও exact-model fallback আগের মতো আছে।

## Setup

Python 3.10+ install করে একবার `setup_windows.bat` চালান। আগে setup করা থাকলে আবার লাগবে না।

## Product list দিয়ে চালানো

প্রতি লাইনে একটি product name দিন; file UTF-8 encoding-এ save করুন। `.txt`, `.tsx` অথবা অন্য extension চলবে। `.tsx`-এর ভিতরেও সাধারণ product-name list থাকবে, React/TypeScript code নয়। ফাঁকা line, `#` comment ও একই নামের duplicate line বাদ দেওয়া হয়।

Project folder-এর PowerShell-এ:

```powershell
# Terminal 1
.\run_all.bat Mouse.tsx

# Terminal 2 — প্রথম terminal চলার সময়ই চালাতে পারবেন
.\run_all.bat Printer.tsx

# Filename-এ space থাকলে quotes দিন
.\run_all.bat "Gaming Mouse.txt"

# Filename না দিলে আগের Products Name.txt ব্যবহার হবে
.\run_all.bat

# নির্বাচিত list-এর প্রথম 10টি product
.\run_10.bat Mouse.tsx
```

Relative filename project folder থেকে খোঁজা হয়। অন্য folder-এর file হলে absolute path দিন:

```powershell
.\run_all.bat "D:\Product Lists\Printer.tsx"
```

## Output ও একসঙ্গে একাধিক run

Filename থেকে default output category হয়:

| Input | Images | Reports |
| --- | --- | --- |
| `Mouse.tsx` | `output/Mouse/<Brand>/` | `output/Mouse/_reports/` |
| `Printer.tsx` | `output/Printer/<Brand>/` | `output/Printer/_reports/` |
| `Products Name.txt` | `output/Accessories/Keyboard/<Brand>/` | `output/Accessories/Keyboard/_reports/` |

একই category-তে একসঙ্গে দ্বিতীয় run শুরু করলে error দেবে, যাতে image overwrite না হয়। ভিন্ন category একসঙ্গে চলবে। একই basename-এর input (যেমন `Mouse.txt` ও `Mouse.tsx`) একই default category পাবে; আলাদা চাইলে `--category` দিন।

সব category-এর metadata project root-এর **`all_products_manifest.json`**-এ যোগ হয়। আগে থাকা entries রাখা হয়; একই product/category-এর entry update হয়। File lock ও atomic save থাকার কারণে একাধিক terminal-এর manifest update একে অপরের তথ্য হারায় না। `.lock` files থাকা স্বাভাবিক; script বন্ধ হলে OS নিজেই lock ছেড়ে দেয়।

প্রতি brand-এর folder-এ naming আগের মতো: `Product Name.png`, `Product Name 1.png`, `Product Name 2.png` ইত্যাদি। `--max-images 5` মানে মোট ৫টি distinct image; `1` থেকে `20` পর্যন্ত দেওয়া যায়। প্রতিটি product-এর সব requested image পাওয়া নিশ্চিত নয়।

প্রতি category-এর `_reports/` folder-এ `products_status.csv` এবং `image_sources.csv` থাকে। নতুন report-এ PNG filename, source URL, product page ও resolution থাকে।

## পুরোনো images ও resume

নির্বাচিত list-এর প্রতিটি product-এর পুরোনো metadata merged JSON অথবা local `.manifest.json` থেকে পড়া হয়। PNG অনুপস্থিত/invalid হলে সেই record-এর JPG/JPEG/WebP থেকে PNG তৈরি করে। PNG যাচাই করার পর একই basename-এর JPG/JPEG/WebP copy মুছে দেয়। অন্য product/category-এর images cleanup করে না।

Shared manifest-এ verify করে save হওয়ার পর product-এর local `.manifest.json` মুছে যায়। Merge ব্যর্থ হলে local manifest রাখা হয়, যাতে একই command আবার চালিয়ে recover করা যায়। আগের `merge_json.py` দিয়ে local manifest মুছে ফেললেও merged JSON থেকে resume হবে। পুরোনো merged entries নতুনভাবে process হলে তাদের WebP filename সরিয়ে PNG metadata রাখা হয়।

Windows-এ editor/antivirus সাময়িকভাবে file আটকে রাখলে save ১০ সেকেন্ড পর্যন্ত retry করে। Shared JSON save তখনও ব্যর্থ হলে download বন্ধ হয় না: metadata local manifest-এ রেখে বাকি products চেষ্টা করে, ৬০ সেকেন্ড পর এবং শেষে আবার merge চেষ্টা করে। শেষে pending merge থাকলে warning দেখায়; একই command আবার চালালে saved images reuse করে merge করবে। CSV report আটকে থাকলেও বাকি downloads চলবে।

একই command আবার চালালে valid existing PNG-সহ products skip হয়। Missing image ও image-বিহীন FAILED product আবার চেষ্টা করা হয়। কম image পাওয়া product-এর বাকি images খুঁজতে `--retry-incomplete` ব্যবহার করুন।

## Options

Slow/failed URL automatic retry হয় না; default connect/read timeout ৬ সেকেন্ড। Official search সর্বোচ্চ ২টি page চেষ্টা করে। প্রতি product-এর search/download budget default ৬০ সেকেন্ড; budget শেষ হলে নতুন request শুরু করে না। ১টি image পেলেই সেটি রাখা হয়, তারপর অতিরিক্ত images-এর জন্য সর্বোচ্চ প্রায় ১২ সেকেন্ড চেষ্টা করে। টানা ৩টি failed/duplicate/unusable image অথবা মোট ১২টি image attempt হলে পরের product-এ যায়। চলমান request শেষ হতে timeout পর্যন্ত অতিরিক্ত সময় লাগতে পারে। ৫টি image পাওয়া বাধ্যতামূলক নয়; কিছু পাওয়া গেলে শুধু সংখ্যা বাড়াতে নতুন fallback search করে না। `--timeout` ও `--product-timeout` দিয়ে সময় বদলানো যায়।

Batch scripts অতিরিক্ত options-ও `main.py`-তে পাঠায়:

```powershell
.\run_all.bat Mouse.tsx --max-images 8
.\run_all.bat Printer.tsx --category "Office/Printer"
.\run_all.bat Mouse.tsx --retry-incomplete
.\run_all.bat Mouse.tsx --min-side 250 --retry-incomplete
.\run_all.bat Mouse.tsx --official-only
.\run_all.bat Mouse.tsx --force

# সরাসরি Python দিয়েও চলবে
.\.venv\Scripts\python.exe main.py Mouse.tsx
.\.venv\Scripts\python.exe main.py --input "Printer.tsx"
```

`--force` সেই product-এর recorded images বাদ দিয়ে নতুন করে download করে। `--output` দিয়ে অন্য output root দিলে default merged JSON সেই output folder-এর `all_products_manifest.json` হয়। `--manifest` দিয়ে JSON destination আলাদা করা যায়; একই output root ব্যবহার করা সব concurrent run-এ একই manifest destination ব্যবহার করুন।

JavaScript gallery render দরকার হলে optional Playwright install করে `--browser` দিন:

```powershell
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\python.exe -m playwright install chromium
.\run_all.bat Mouse.tsx --browser
```

## Brand configuration

`brands.json`-এ brand-এর নাম ও official domains দিন। Product name-এর শুরুতে brand রাখুন। Unknown brand `Unknown` folder-এ যায়; reliable model token না পাওয়া গেলে manual review দরকার হয়।

Search matching আগের text heuristic ব্যবহার করে। Reports-এ `COMPLETE`, `PARTIAL`, `FAILED` ও third-party image-এর জন্য `REVIEW_RIGHTS` থাকে। Search rate limits, robots.txt ও site access restrictions download আটকাতে পারে।
